"""Pipeline: source -> raw snapshot -> validation -> PostgreSQL (append-only) -> run log.

Raw API responses are written to data/raw/<run_id>/ as CSV before validation, so any figure in the
database can be traced back to what the source returned on that day.
"""
from __future__ import annotations

import datetime as dt
import logging

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


def ingest(adapter, universe: pd.DataFrame, jobs=("financials", "market", "ownership"), engine=None,
           limit: int | None = None) -> int:
    engine = engine or repo.get_engine()
    repo.init_db(engine)
    run_id = repo.start_run("ingest:" + "+".join(jobs), adapter.source, engine)
    raw_dir = RAW_DIR / str(run_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    ok, failed, issues, errors = 0, 0, [], []
    existing = _existing_fin_versions(engine) if "financials" in jobs else {}
    now = dt.datetime.now()
    uni = universe.head(limit) if limit else universe
    for _, c in uni.iterrows():
        try:
            cid = repo.upsert_company({k: c.get(k) for k in
                                       ["company_name", "ticker", "isin", "exchange", "sector", "industry", "source"]},
                                      engine)
            if "financials" in jobs:
                fin = adapter.financials(c.ticker)
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
                mk = adapter.market(c.ticker)
                if mk is not None and not mk.empty:
                    mk = mk.assign(company_id=cid, ingested_at=now)
                    repo.append_market_rows(_clean(mk.to_dict("records")), engine)
                    repo.upsert_company({"ticker": c.ticker, "market_cap": float(mk.iloc[-1].market_cap)}, engine)
                else:
                    issues.append({"company_id": cid, "fiscal_year": None, "check_name": "api_empty",
                                   "severity": "warn", "detail": "No market data returned"})
            if "ownership" in jobs:
                ow = adapter.ownership(c.ticker)
                if ow is not None and not ow.empty:
                    repo.append_rows(m.ownership, _clean(ow.assign(company_id=cid, ingested_at=now).to_dict("records")),
                                     engine)
            ok += 1
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not stop the run
            failed += 1
            errors.append(f"{c.ticker}: {exc}")
            log.exception("Ingestion failed for %s", c.ticker)
            issues.append({"company_id": None, "fiscal_year": None, "check_name": "api_failure", "severity": "error",
                           "detail": f"{c.ticker}: {exc}"[:500]})
    repo.log_issues(run_id, _clean(issues), engine)
    status = "success" if failed == 0 else ("partial" if ok else "failed")
    repo.finish_run(run_id, status, ok, failed, "\n".join(errors[:50]), engine)
    log.info("Run %s finished: %s ok, %s failed", run_id, ok, failed)
    return run_id
