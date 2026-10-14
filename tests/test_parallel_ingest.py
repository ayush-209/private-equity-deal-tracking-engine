import pandas as pd
import pytest
from sqlalchemy import create_engine

from src.data_ingestion.demo import DemoAdapter
from src.data_ingestion.pipeline import ingest
from src.database import repository as repo


class Flaky(DemoAdapter):
    def __init__(self, bad, **kw):
        super().__init__(**kw)
        self.bad = bad

    def financials(self, ticker):
        if ticker in self.bad:
            raise RuntimeError("Yahoo said no")
        return super().financials(ticker)


@pytest.fixture
def engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'p.db'}", future=True)


def _run(engine):
    return repo.read_df("SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT 1", engine=engine).iloc[0]


def test_parallel_load_matches_serial_load(tmp_path):
    ad = DemoAdapter(n_per_industry=3)
    out = {}
    for w in (1, 4):
        eng = create_engine(f"sqlite:///{tmp_path / f'w{w}.db'}", future=True)
        ingest(ad, ad.universe(), engine=eng, workers=w)
        out[w] = (repo.read_df("SELECT COUNT(*) n FROM companies", engine=eng).n[0],
                  repo.read_df("SELECT COUNT(*) n FROM financials", engine=eng).n[0],
                  round(float(repo.read_df("SELECT SUM(revenue) s FROM financials", engine=eng).s[0]), 4))
    assert out[1] == out[4] and out[4][0] > 30


def test_one_failing_company_does_not_stop_the_run(engine):
    ad = Flaky(bad=set(), n_per_industry=3)
    bad = set(ad.universe().ticker.head(2))
    ad.bad = bad
    ingest(ad, ad.universe(), engine=engine, workers=4)
    run = _run(engine)
    assert run.status == "partial" and run.n_failed == 2
    assert run.n_ok == len(ad.universe()) - 2
    assert all(t in run.message for t in bad)


def test_time_limit_skips_the_rest_but_keeps_what_was_loaded(engine):
    import time
    ad = DemoAdapter(n_per_industry=3)
    orig = ad.financials

    def slow(ticker):
        time.sleep(0.1)
        return orig(ticker)
    ad.financials = slow
    ingest(ad, ad.universe(), engine=engine, workers=1, max_minutes=0.0035)     # about 0.2 s
    run = _run(engine)
    assert run.status == "partial" and run.n_ok >= 1 and run.n_failed > 0
    assert "time limit" in run.message
    assert repo.read_df("SELECT COUNT(*) n FROM financials", engine=engine).n[0] > 0
