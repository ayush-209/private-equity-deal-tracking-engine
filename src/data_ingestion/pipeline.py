"""Pipeline: source -> raw snapshot -> validation -> PostgreSQL (append-only) -> run log.

Raw API responses are written to data/raw/<run_id>/ as CSV before validation, so any figure in the
database can be traced back to what the source returned on that day.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from src.config import ROOT
from src.data_validation.validator import validate_financials
from src.database import models as m
from src.database import repository as repo

log = logging.getLogger(__name__)
RAW_DIR = ROOT / "data" / "raw"


def _clean(rows: list[dict]) -> list[dict]:
    return [{k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.items()} for r in rows]


def _existing_fin_versions(engine) -> dict:
    """(company_id, fiscal_year) -> latest stored tuple of values, so unchanged statements are not re-appended."""
    df = repo.load_financials(engine=engine)
    if df.empty:
        return {}
    cols = [c for c in m.FINANCIAL_FIELDS]
    return {(r.company_id, r.fiscal_year): tuple(None if pd.isna(r[c]) else round(float(r[c]), 4) for c in cols)
            for _, r in df.iterrows()}


def _fetch_company(adapter, ticker: str, jobs, deadline: float | None):
    """Network part of one company, run in a worker thread. Never raises: errors are returned and logged by the caller."""
    out = {"fin": None, "mk": None, "ow": None, "err": None, "skipped": False}
    if deadline is not None and time.time() > deadline:
        out["skipped"] = True
        return out
    try:
        if "financials" in jobs:
            out["fin"] = adapter.financials(ticker)
        if "market" in jobs:
            out["mk"] = adapter.market(ticker)
        if "ownership" in jobs:
            out["ow"] = adapter.ownership(ticker)
    except Exception as exc:  # noqa: BLE001
        out["err"] = exc
    return out


def _write_company(c, got: dict, adapter, jobs, engine, existing, now, raw_dir, issues) -> None:
    """Database part of one company, run in the main thread. Raises on failure; the caller records it."""
    if got["err"] is not None:
        raise got["err"]
    cid = repo.upsert_company({k: c.get(k) for k in
                               ["company_name", "ticker", "isin", "exchange", "sector", "industry", "source"]}, engine)
    if "financials" in jobs:
        fin = got["fin"]
        if fin is not None and not fin.empty:
            fin = fin.assign(company_id=cid)
            fin.to_csv(raw_dir / f"{c.ticker}_financials.csv", index=False)
            clean, iss = validate_financials(fin)
            issues += iss
            rows = []
            for r in clean.to_dict("records"):
                key = (cid, int(r["fiscal_year"]))
                sig = tuple(None if pd.isna(r.get(f)) else round(float(r[f]), 4) for f in m.FINANCIAL_FIELDS)
                if existing.get(key) == sig:
                    continue  # identical to stored version: nothing new to append
                r.update(company_id=cid, ingested_at=now, fiscal_year=int(r["fiscal_year"]))
                rows.append({k: r.get(k) for k in [col.name for col in m.financials.columns if col.name != "id"]})
            repo.append_rows(m.financials, _clean(rows), engine)
        else:
            issues.append({"company_id": cid, "fiscal_year": None, "check_name": "api_empty",
                           "severity": "warn", "detail": f"{adapter.source} returned no financials"})
    if "market" in jobs:
        mk = got["mk"]
        if mk is not None and not mk.empty:
            mk = mk.assign(company_id=cid, ingested_at=now)
            repo.append_market_rows(_clean(mk.to_dict("records")), engine)
            repo.upsert_company({"ticker": c.ticker, "market_cap": float(mk.iloc[-1].market_cap)}, engine)
        else:
            issues.append({"company_id": cid, "fiscal_year": None, "check_name": "api_empty",
                           "severity": "warn", "detail": "No market data returned"})
    if "ownership" in jobs:
        ow = got["ow"]
        if ow is not None and not ow.empty:
            repo.append_rows(m.ownership, _clean(ow.assign(company_id=cid, ingested_at=now).to_dict("records")), engine)


def ingest(adapter, universe: pd.DataFrame, jobs=("financials", "market", "ownership"), engine=None,
           limit: int | None = None, workers: int = 4, max_minutes: float | None = None) -> int:
    """Fetches companies in parallel (`workers` threads, I/O bound) and writes them one at a time. If `max_minutes`
    is reached the remaining companies are skipped and the run is recorded as partial, so a slow source never costs
    the whole run."""
    engine = engine or repo.get_engine()
    repo.init_db(engine)
    run_id = repo.start_run("ingest:" + "+".join(jobs), adapter.source, engine)
    raw_dir = RAW_DIR / str(run_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    ok, failed, issues, errors = 0, 0, [], []
    existing = _existing_fin_versions(engine) if "financials" in jobs else {}
    now = dt.datetime.now()
    uni = universe.head(limit) if limit else universe
    deadline = time.time() + max_minutes * 60 if max_minutes else None
    companies = [row for _, row in uni.iterrows()]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        fetched = pool.map(lambda c: _fetch_company(adapter, c.ticker, jobs, deadline), companies)
        for n, (c, got) in enumerate(zip(companies, fetched), 1):
            if got["skipped"]:
                failed += 1
                errors.append(f"{c.ticker}: skipped, time limit reached")
                continue
            try:
                _write_company(c, got, adapter, jobs, engine, existing, now, raw_dir, issues)
                ok += 1
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not stop the run
                failed += 1
                errors.append(f"{c.ticker}: {exc}")
                log.exception("Ingestion failed for %s", c.ticker)
                issues.append({"company_id": None, "fiscal_year": None, "check_name": "api_failure",
                               "severity": "error", "detail": f"{c.ticker}: {exc}"[:500]})
            if n % 50 == 0:
                log.info("Progress: %s / %s companies", n, len(companies))
    repo.log_issues(run_id, _clean(issues), engine)
    status = "success" if failed == 0 else ("partial" if ok else "failed")
    repo.finish_run(run_id, status, ok, failed, "\n".join(errors[:50]), engine)
    log.info("Run %s finished: %s ok, %s failed", run_id, ok, failed)
    return run_id


def ingest_benchmark(adapter, symbols: list[str], engine=None) -> int:
    """Load the market-index series used for beta. Failure is logged, never fatal: the class-basis score then
    simply has no beta and says so."""
    engine = engine or repo.get_engine()
    repo.init_db(engine)
    try:
        df = adapter.benchmark(symbols)
    except Exception:  # noqa: BLE001
        log.exception("Benchmark download failed")
        return 0
    if df is None or df.empty:
        log.warning("No benchmark data returned for %s", symbols)
        return 0
    df = df.assign(ingested_at=dt.datetime.now())
    n = repo.append_benchmark_rows(_clean(df.to_dict("records")), engine)
    log.info("Benchmark: %s new monthly rows (%s)", n, ", ".join(sorted(df.symbol.unique())))
    return n
