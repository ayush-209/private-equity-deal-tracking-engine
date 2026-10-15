"""Every page renders without an exception against a small demo database."""
import os
from pathlib import Path

import pytest

streamlit_testing = pytest.importorskip("streamlit.testing.v1")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo_env(tmp_path_factory):
    d = tmp_path_factory.mktemp("appdata")
    old = {k: os.environ.get(k) for k in ("PE_DATABASE_URL", "PE_NO_DOWNLOAD", "PE_NO_LIVE")}
    os.environ.update({"PE_DATABASE_URL": f"sqlite:///{d}/pe.db", "PE_NO_DOWNLOAD": "1", "PE_NO_LIVE": "1"})
    from src.database import repository as repo
    repo.get_engine.cache_clear() if hasattr(repo.get_engine, "cache_clear") else None
    from scripts import run_pipeline
    import sys
    argv = sys.argv
    sys.argv = ["run_pipeline", "demo"]
    try:
        run_pipeline.main()
    finally:
        sys.argv = argv
    yield
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


PAGES = ["executive_dashboard", "deal_universe", "company_deep_dive", "comparables", "deal_economics", "risk_due_diligence", "ai_research", "investment_committee", "deal_pipeline", "start", "screen", "company", "deals", "portfolio", "track", "historical_validation", "data_quality"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(demo_env, page):
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app_pages" / f"{page}.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("view", ["Price", "Peers", "Risks & diligence", "Deal economics", "Memo", "Portfolio fit", "AI note"])
def test_company_views(demo_env, view):
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app_pages" / "company.py"), default_timeout=120)
    at.run()
    at.radio(key="company_view").set_value(view).run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("view", ["More PE filters", "Market overview"])
def test_screen_views(demo_env, view):
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app_pages" / "screen.py"), default_timeout=120)
    at.run()
    at.radio[0].set_value(view).run()
    assert not at.exception, [e.value for e in at.exception]


def test_full_app_and_save_flow(demo_env):
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    pt = streamlit_testing.AppTest.from_file(str(ROOT / "app_pages" / "portfolio.py"), default_timeout=120)
    pt.run()
    assert not pt.exception
    save = [b for b in pt.button if b.label == "Save to Track"][0]
    save.click().run()
    assert not pt.exception, [e.value for e in pt.exception]
    tr = streamlit_testing.AppTest.from_file(str(ROOT / "app_pages" / "track.py"), default_timeout=120)
    tr.run()
    assert not tr.exception, [e.value for e in tr.exception]


def test_deal_shortlist_adds_to_pipeline(demo_env):
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app_pages" / "deals.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    [b for b in at.button if b.label == "Add all to the deal pipeline"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    from src.database import repository as repo
    assert len(repo.load_pipeline()) >= 3


ALL_PAGES = ["start.py", "executive_dashboard.py", "deal_universe.py", "screen.py", "deals.py", "company.py", "company_deep_dive.py",
             "comparables.py", "deal_economics.py", "risk_due_diligence.py", "ai_research.py", "investment_committee.py",
             "portfolio.py", "track.py", "deal_pipeline.py", "historical_validation.py", "data_quality.py"]


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_page_through_the_real_menu(demo_env, page):
    """Pages run under app.py, which draws shared sidebar widgets; a page that repeats one would crash here only."""
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    at.switch_page(f"app_pages/{page}").run()
    assert not at.exception, [e.value for e in at.exception]
