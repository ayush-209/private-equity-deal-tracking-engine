"""End-to-end against a real database. Set TEST_DATABASE_URL to run; skipped otherwise.
The test database is wiped."""
import os

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def engine():
    from src.database.models import metadata
    eng = create_engine(URL, future=True)
    with eng.begin() as c:
        for v in ["v_financials_latest", "v_market_latest", "v_ownership_latest"]:
            c.execute(text(f"DROP VIEW IF EXISTS {v}"))
    metadata.drop_all(eng)
    return eng


def test_pipeline_end_to_end(engine):
    from src.data_ingestion.demo import DemoAdapter
    from src.data_ingestion.pipeline import ingest
    from src.database import repository as repo
    from src.screening.universe import build_universe, persist_screen

    ad = DemoAdapter(n_per_industry=6)
    uni = ad.universe()
    ingest(ad, uni, engine=engine)
    n1 = repo.read_df("SELECT COUNT(*) n FROM financials", engine=engine).n[0]

    # Re-ingesting identical data appends nothing (append-only, no duplicates)
    ingest(ad, uni, engine=engine)
    assert repo.read_df("SELECT COUNT(*) n FROM financials", engine=engine).n[0] == n1

    # A restated figure is appended as a new version; the latest view returns it, history is kept
    t0 = uni.ticker.iloc[0]
    orig = ad._fin[t0].copy()
    ad._fin[t0] = orig.assign(revenue=orig.revenue * 1.01)
    ingest(ad, uni.head(1), jobs=("financials",), engine=engine)
    cid = repo.read_df("SELECT company_id FROM companies WHERE ticker=:t", {"t": t0}, engine=engine).company_id[0]
    allv = repo.read_df("SELECT * FROM financials WHERE company_id=:c", {"c": int(cid)}, engine=engine)
    latest = repo.read_df("SELECT * FROM v_financials_latest WHERE company_id=:c", {"c": int(cid)}, engine=engine)
    assert len(allv) == 2 * len(orig) and len(latest) == len(orig)
    assert latest.sort_values("fiscal_year").revenue.values == pytest.approx(orig.revenue.values * 1.01)

    u = build_universe(engine=engine)
    assert len(u.table) > 0 and len(u.excluded) > 0
    assert u.table.total_score.between(0, 100).all()
    rid = persist_screen(u, {"growth": 20, "profitability": 20, "cash_flow": 20, "leverage": 15, "valuation": 15,
                             "working_capital": 10}, engine=engine)
    sr = repo.read_df("SELECT * FROM screening_results WHERE calc_run_id=:r", {"r": rid}, engine=engine)
    assert len(sr) == len(u.table)


def test_point_in_time_excludes_later_filings(engine):
    import datetime as dt
    from src.database import repository as repo
    f = repo.load_financials(dt.date(2022, 6, 30), engine=engine)
    assert (pd.to_datetime(f.filing_date) <= pd.Timestamp("2022-06-30")).all()
    assert f.fiscal_year.max() <= 2022


def test_pipeline_persists(engine):
    from src.database import repository as repo
    cid = int(repo.load_companies(engine).company_id.iloc[0])
    repo.save_pipeline_entry(cid, "Screened", thesis="t", engine=engine)
    repo.save_pipeline_entry(cid, "Under Review", thesis="t2", engine=engine)
    p = repo.load_pipeline(engine)
    assert p.set_index("company_id").loc[cid, "status"] == "Under Review"
    h = repo.read_df("SELECT * FROM pipeline_history WHERE company_id=:c", {"c": cid}, engine=engine)
    assert len(h) == 2
    with pytest.raises(ValueError):
        repo.save_pipeline_entry(cid, "Bogus", engine=engine)
