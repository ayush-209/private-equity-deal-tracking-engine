"""An older database (no current_liabilities column, no benchmark table) is upgraded by init_db without manual SQL."""
from sqlalchemy import create_engine, inspect, text

from src.database import repository as repo


def test_init_db_adds_new_column_and_table_to_a_legacy_database(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with eng.begin() as c:
        c.execute(text("""CREATE TABLE financials (id INTEGER PRIMARY KEY, company_id INTEGER, fiscal_year INTEGER,
            period_end DATE, filing_date DATE, filing_date_estimated BOOLEAN, revenue FLOAT, source VARCHAR(64),
            ingested_at DATETIME)"""))
        c.execute(text("INSERT INTO financials (company_id, fiscal_year, revenue, source) VALUES (1, 2025, 100, 'old')"))
    repo.init_db(eng)
    insp = inspect(eng)
    assert "current_liabilities" in {c["name"] for c in insp.get_columns("financials")}
    assert "benchmark_prices" in insp.get_table_names()
    repo.init_db(eng)                                              # idempotent
    with eng.connect() as c:                                       # existing data intact, view sees the new column
        row = c.execute(text("SELECT revenue, current_liabilities FROM v_financials_latest")).one()
    assert row[0] == 100 and row[1] is None


def test_benchmark_rows_are_not_duplicated(tmp_path):
    import datetime as dt
    eng = create_engine(f"sqlite:///{tmp_path / 'b.db'}", future=True)
    repo.init_db(eng)
    rows = [{"symbol": "^X", "date": dt.date(2025, 1, 1), "close": 100.0, "source": "t"},
            {"symbol": "^X", "date": dt.date(2025, 2, 1), "close": 101.0, "source": "t"}]
    assert repo.append_benchmark_rows(rows, eng) == 2
    assert repo.append_benchmark_rows(rows, eng) == 0
    assert len(repo.load_benchmark(eng)) == 2
