"""Database access. All SQL lives here or in sql/queries.sql; UI and engines never touch the engine directly."""
from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.engine import Engine

from src.config import database_url
from src.database import models as m

log = logging.getLogger(__name__)


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True, future=True)


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    m.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in LATEST_VIEWS:
            conn.execute(text(stmt))


# Latest version per (company, period): ingestion is append-only, these views resolve the current figure.
LATEST_VIEWS = [
    "DROP VIEW IF EXISTS v_financials_latest",
    """CREATE VIEW v_financials_latest AS
       SELECT * FROM (
         SELECT f.*, ROW_NUMBER() OVER (PARTITION BY company_id, fiscal_year ORDER BY ingested_at DESC, id DESC) AS rn
         FROM financials f) t WHERE rn = 1""",
    "DROP VIEW IF EXISTS v_market_latest",
    """CREATE VIEW v_market_latest AS
       SELECT * FROM (
         SELECT md.*, ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY date DESC, ingested_at DESC) AS rn
         FROM market_data md) t WHERE rn = 1""",
    "DROP VIEW IF EXISTS v_ownership_latest",
    """CREATE VIEW v_ownership_latest AS
       SELECT * FROM (
         SELECT o.*, ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY date DESC, ingested_at DESC) AS rn
         FROM ownership o) t WHERE rn = 1""",
]


# ---------------------------------------------------------------- run log
def start_run(job: str, source: str | None, engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    with engine.begin() as conn:
        res = conn.execute(insert(m.pipeline_runs).values(job=job, source=source, status="running",
                                                          started_at=dt.datetime.now()))
        return int(res.inserted_primary_key[0])


def finish_run(run_id: int, status: str, n_ok: int, n_failed: int, message: str = "",
               engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(update(m.pipeline_runs).where(m.pipeline_runs.c.run_id == run_id).values(
            status=status, n_ok=n_ok, n_failed=n_failed, message=message[:4000], finished_at=dt.datetime.now()))


def log_issues(run_id: int, issues: list[dict], engine: Engine | None = None) -> None:
    if not issues:
        return
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(insert(m.data_issues), [{**i, "run_id": run_id} for i in issues])


# ---------------------------------------------------------------- writes
def upsert_company(row: dict, engine: Engine | None = None) -> int:
    """Companies are reference data: update descriptive fields in place, keyed by ticker."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        existing = conn.execute(select(m.companies.c.company_id).where(m.companies.c.ticker == row["ticker"])).scalar()
        if existing:
            conn.execute(update(m.companies).where(m.companies.c.company_id == existing)
                         .values(**{k: v for k, v in row.items() if v is not None}, updated_at=dt.datetime.now()))
            return int(existing)
        return int(conn.execute(insert(m.companies).values(**row)).inserted_primary_key[0])


def append_rows(table, rows: list[dict], engine: Engine | None = None) -> int:
    if not rows:
        return 0
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(insert(table), rows)
    return len(rows)


def append_market_rows(rows: list[dict], engine: Engine | None = None) -> int:
    """Market observations are unique per (company, date, source); skip ones already stored."""
    if not rows:
        return 0
    engine = engine or get_engine()
    with engine.begin() as conn:
        existing = set(conn.execute(select(m.market_data.c.company_id, m.market_data.c.date,
                                           m.market_data.c.source)).all())
        new = [r for r in rows if (r["company_id"], r["date"], r["source"]) not in existing]
        if new:
            conn.execute(insert(m.market_data), new)
    return len(new)


# ---------------------------------------------------------------- reads
def read_df(sql: str, params: dict | None = None, engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def load_companies(engine=None) -> pd.DataFrame:
    return read_df("SELECT * FROM companies ORDER BY company_name", engine=engine)


def load_financials(as_of: dt.date | None = None, engine=None) -> pd.DataFrame:
    """Latest version of every annual statement. With `as_of`, only figures public by that date
    (filing_date <= as_of) and ingested versions are still resolved to the latest — see METHODOLOGY on
    the restatement limitation."""
    if as_of is None:
        return read_df("SELECT * FROM v_financials_latest ORDER BY company_id, fiscal_year", engine=engine)
    return read_df("SELECT * FROM v_financials_latest WHERE filing_date <= :d ORDER BY company_id, fiscal_year",
                   {"d": as_of}, engine=engine)


def load_market_latest(as_of: dt.date | None = None, engine=None) -> pd.DataFrame:
    if as_of is None:
        return read_df("SELECT * FROM v_market_latest", engine=engine)
    return read_df("""SELECT * FROM (
        SELECT md.*, ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY date DESC, ingested_at DESC) rn
        FROM market_data md WHERE date <= :d) t WHERE rn = 1""", {"d": as_of}, engine=engine)


def load_market_history(company_id: int, engine=None) -> pd.DataFrame:
    return read_df("SELECT date, share_price, market_cap, enterprise_value FROM market_data "
                   "WHERE company_id = :c ORDER BY date", {"c": company_id}, engine=engine)


def load_ownership_latest(engine=None) -> pd.DataFrame:
    return read_df("SELECT * FROM v_ownership_latest", engine=engine)


def load_runs(limit: int = 50, engine=None) -> pd.DataFrame:
    return read_df("SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT :n", {"n": limit}, engine=engine)


def load_issues(limit: int = 2000, engine=None) -> pd.DataFrame:
    return read_df("""SELECT i.*, c.ticker FROM data_issues i LEFT JOIN companies c USING (company_id)
                      ORDER BY i.id DESC LIMIT :n""", {"n": limit}, engine=engine)


def data_sources(engine=None) -> pd.DataFrame:
    return read_df("""SELECT 'financials' AS dataset, source, MAX(ingested_at) AS last_ingested, COUNT(*) AS rows
                      FROM financials GROUP BY source
                      UNION ALL SELECT 'market_data', source, MAX(ingested_at), COUNT(*) FROM market_data GROUP BY source
                      UNION ALL SELECT 'ownership', source, MAX(ingested_at), COUNT(*) FROM ownership GROUP BY source""",
                   engine=engine)


def last_refresh(engine=None) -> dt.datetime | None:
    df = read_df("SELECT MAX(finished_at) AS t FROM pipeline_runs WHERE status IN ('success','partial')",
                 engine=engine)
    return None if df.empty or pd.isna(df.t.iloc[0]) else pd.to_datetime(df.t.iloc[0]).to_pydatetime()


# ---------------------------------------------------------------- deal pipeline
PIPELINE_STATUSES = ["New", "Screened", "Under Review", "Due Diligence", "IC Review", "Rejected", "Monitoring"]


def load_pipeline(engine=None) -> pd.DataFrame:
    return read_df("""SELECT p.*, c.company_name, c.ticker, c.sector FROM deal_pipeline p
                      JOIN companies c USING (company_id) ORDER BY p.updated_at DESC""", engine=engine)


def save_pipeline_entry(company_id: int, status: str, thesis: str = "", concerns: str = "",
                        next_steps: str = "", notes: str = "", engine=None) -> None:
    if status not in PIPELINE_STATUSES:
        raise ValueError(f"Unknown status {status!r}")
    engine = engine or get_engine()
    now = dt.datetime.now()
    vals = dict(status=status, thesis=thesis, concerns=concerns, next_steps=next_steps, notes=notes, updated_at=now)
    with engine.begin() as conn:
        prev = conn.execute(select(m.deal_pipeline.c.status).where(m.deal_pipeline.c.company_id == company_id)).scalar()
        if prev is None:
            conn.execute(insert(m.deal_pipeline).values(company_id=company_id, added_at=now, **vals))
        else:
            conn.execute(update(m.deal_pipeline).where(m.deal_pipeline.c.company_id == company_id).values(**vals))
        if prev != status:
            conn.execute(insert(m.pipeline_history).values(company_id=company_id, status=status, changed_at=now,
                                                           note=f"{prev or '—'} → {status}"))


def remove_pipeline_entry(company_id: int, engine=None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(m.deal_pipeline.delete().where(m.deal_pipeline.c.company_id == company_id))
        conn.execute(insert(m.pipeline_history).values(company_id=company_id, status="Removed",
                                                       changed_at=dt.datetime.now(), note="Removed from pipeline"))


def save_ai_output(company_id: int, model: str, fact_pack: dict, output: str, unverified: list, engine=None) -> None:
    append_rows(m.ai_outputs, [dict(company_id=company_id, model=model, fact_pack=fact_pack, output=output,
                                    unverified_numbers=unverified, created_at=dt.datetime.now())], engine)


def load_ai_outputs(company_id: int, engine=None) -> pd.DataFrame:
    return read_df("SELECT * FROM ai_outputs WHERE company_id = :c ORDER BY created_at DESC",
                   {"c": company_id}, engine=engine)
