"""SQLite is the default backend: the app downloads a published file; its own notes live in a separate file."""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine

from src.database import fetch
from src.database import repository as repo


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fetch, "DB_PATH", tmp_path / "pe.db")
    monkeypatch.delenv("PE_DATABASE_URL", raising=False)
    monkeypatch.delenv("PE_NO_DOWNLOAD", raising=False)
    monkeypatch.setattr(cfg, "database_url", lambda: f"sqlite:///{tmp_path / 'pe.db'}")
    monkeypatch.setattr(repo, "database_url", cfg.database_url)
    monkeypatch.setattr(cfg, "workspace_url", lambda: f"sqlite:///{tmp_path / 'workspace.db'}")
    monkeypatch.setattr(repo, "workspace_url", cfg.workspace_url)
    repo.get_engine.cache_clear()
    repo.get_workspace_engine.cache_clear()
    yield tmp_path
    repo.get_engine.cache_clear()
    repo.get_workspace_engine.cache_clear()


def test_dates_come_back_as_dates_not_text(isolated):
    eng = repo.get_engine()
    repo.init_db(eng)
    cid = repo.upsert_company({"company_name": "A", "ticker": "A", "source": "t"}, eng)
    repo.append_market_rows([{"company_id": cid, "date": dt.date(2025, 3, 1), "share_price": 10.0, "market_cap": 100.0,
                              "source": "t", "ingested_at": dt.datetime(2025, 3, 2)}], eng)
    d = repo.load_market_latest(engine=eng)
    assert isinstance(d.date.iloc[0], dt.date) and d.date.iloc[0] == dt.date(2025, 3, 1)
    assert len(repo.load_market_latest(dt.date(2025, 2, 1), eng)) == 0     # as-of filter works on stored dates
    assert len(repo.load_market_latest(dt.date(2025, 4, 1), eng)) == 1
    assert isinstance(repo.load_price_panel(eng).date.iloc[0], dt.date)


def test_notes_go_to_a_separate_file_and_survive_a_data_replacement(isolated):
    eng = repo.get_engine()
    repo.init_db(eng)
    cid = repo.upsert_company({"company_name": "Acme", "ticker": "ACME", "source": "t"}, eng)
    repo.save_pipeline_entry(cid, "Screened", thesis="good")
    repo.save_pipeline_entry(cid, "Due Diligence")
    assert (isolated / "workspace.db").exists()
    p = repo.load_pipeline()
    assert p.status.tolist() == ["Due Diligence"] and p.company_name.tolist() == ["Acme"]
    assert len(repo.load_pipeline_history()) == 2
    (isolated / "pe.db").unlink()                                          # a fresh data file arrives
    repo.get_engine.cache_clear()
    repo.init_db(repo.get_engine())
    assert len(repo.read_df("SELECT * FROM deal_pipeline", engine=repo.get_engine())) == 0
    assert len(repo.read_df("SELECT * FROM deal_pipeline", engine=repo.get_workspace_engine())) == 1


class _Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _sqlite_bytes(tmp_path):
    p = tmp_path / "src.db"
    c = create_engine(f"sqlite:///{p}").connect()
    c.exec_driver_sql("CREATE TABLE t (x INTEGER)")
    c.commit(); c.close()
    return p.read_bytes()


def test_download_replaces_file_and_failures_keep_local_copy(isolated, monkeypatch):
    good = _sqlite_bytes(isolated)
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: _Resp(good))
    assert fetch.ensure_local_db().startswith("downloaded")
    assert (isolated / "pe.db").read_bytes() == good
    assert fetch.ensure_local_db().startswith("local copy")               # fresh: no re-download
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: _Resp(b"", 404))
    assert "download failed" in fetch.ensure_local_db(force=True)
    assert (isolated / "pe.db").read_bytes() == good                       # untouched
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: _Resp(b"<html>not a db</html>"))
    assert "download failed" in fetch.ensure_local_db(force=True)
    assert (isolated / "pe.db").read_bytes() == good


def test_postgres_url_disables_download(isolated, monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(fetch, "is_sqlite", lambda: False)
    assert fetch.ensure_local_db() == "not used"
