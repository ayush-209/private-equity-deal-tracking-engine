"""Assembles the analytical universe from the database: metrics -> multiples -> peers -> quality ->
red flags -> screen. Used by the Streamlit app, the scheduled screening job and historical validation."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.config import sector_config
from src.data_validation.validator import grade_data_quality
from src.database import models as m
from src.database import repository as repo
from src.financial_metrics.metrics import compute_all
from src.risk.red_flags import red_flags
from src.screening.engine import DIMENSIONS, screen
from src.valuation.peers import assign_peer_groups, compute_multiples, peer_members


@dataclass
class Universe:
    as_of: dt.date | None
    companies: pd.DataFrame
    financials: pd.DataFrame
    history: pd.DataFrame            # metric time series, all companies
    table: pd.DataFrame              # one row per company: metrics + multiples + scores
    detail: pd.DataFrame             # sub-metric percentiles (explainability)
    flags: dict = field(default_factory=dict)   # company_id -> list of flag dicts
    excluded: pd.DataFrame = field(default_factory=pd.DataFrame)
    sources: list = field(default_factory=list)
    base: pd.DataFrame = field(default_factory=pd.DataFrame)   # pre-scoring frame, for instant re-weighting

    def rescore(self, weights: dict) -> "Universe":
        table, detail = screen(self.base, weights)
        return Universe(self.as_of, self.companies, self.financials, self.history, table, detail, self.flags,
                        self.excluded, self.sources, self.base)

    @property
    def is_synthetic(self) -> bool:
        return any("SYNTHETIC" in str(s) for s in self.sources)


def exclusion_reason(sector: str, industry: str) -> str | None:
    cfg = sector_config()
    if sector in cfg["excluded_from_screen"]:
        return f"{sector}: lender/insurer economics need a separate framework (NIM, asset quality, capital)"
    if any(k.lower() in str(industry).lower() for k in cfg["excluded_industries_keywords"]):
        return f"{industry}: financial-sector business excluded from operating-company screen"
    return None


def build_universe(as_of: dt.date | None = None, weights: dict | None = None, engine=None) -> Universe:
    engine = engine or repo.get_engine()
    companies = repo.load_companies(engine)
    fin = repo.load_financials(as_of, engine)
    if fin.empty:
        return Universe(as_of, companies, fin, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    hist, snap = compute_all(fin)
    market = repo.load_market_latest(as_of, engine)
    mult = compute_multiples(snap, market) if not market.empty else pd.DataFrame({"company_id": snap.company_id})
    u = companies[["company_id", "company_name", "ticker", "exchange", "sector", "industry"]] \
        .merge(snap, on="company_id", how="inner").merge(mult, on="company_id", how="left")
    u["exclusion_reason"] = [exclusion_reason(s, i) for s, i in zip(u.sector, u.industry)]
    excluded = u[u.exclusion_reason.notna()][["company_id", "company_name", "ticker", "sector", "industry",
                                              "exclusion_reason"]]
    u = u[u.exclusion_reason.isna()].reset_index(drop=True)

    issues = repo.load_issues(engine=engine)
    quality = grade_data_quality(fin, issues, set(market.company_id) if not market.empty else set())
    u = u.merge(quality, on="company_id", how="left", suffixes=("", "_q"))
    if "years_of_history_q" in u:
        u = u.drop(columns="years_of_history_q")
    own = repo.load_ownership_latest(engine)
    if not own.empty:
        u = u.merge(own[["company_id", "promoter_holding", "institutional_holding", "public_holding"]],
                    on="company_id", how="left")

    u["peer_group"] = assign_peer_groups(u, "ev_ebitda")
    u["scoring_peer_group"] = assign_peer_groups(u, "revenue")
    u["peer_median_ev_ebitda"] = [peer_members(u, g).loc[lambda d: d.company_id != cid, "ev_ebitda"].median()
                                  for cid, g in zip(u.company_id, u.peer_group)]
    u["ev_ebitda_premium"] = np.where(u.peer_median_ev_ebitda > 0, u.ev_ebitda / u.peer_median_ev_ebitda - 1, np.nan)

    flags = {}
    for _, r in u.iterrows():
        flags[r.company_id] = red_flags(hist[hist.company_id == r.company_id], r, r.industry)
    u["n_flags"] = u.company_id.map(lambda c: len(flags[c]))
    u["critical_flags"] = u.company_id.map(lambda c: sum(f["severity"] == "critical" for f in flags[c]))

    table, detail = screen(u, weights)
    srcs = repo.data_sources(engine)["source"].unique().tolist()
    return Universe(as_of, companies, fin, hist, table, detail, flags, excluded, srcs, u)


def persist_screen(uni: Universe, weights: dict, engine=None) -> int:
    """Store metrics, valuation and screening results as a new calculation run (history is kept)."""
    engine = engine or repo.get_engine()
    run_id = repo.start_run("screen", "calc", engine)
    today = uni.as_of or dt.date.today()
    metric_cols = [c.name for c in m.calculated_metrics.columns if c.name not in {"id", "calc_run_id"}]
    h = uni.history.copy()
    h = h[[c for c in metric_cols if c in h]].replace([np.inf, -np.inf], np.nan)
    h = h.astype(object).where(h.notna(), None)
    repo.append_rows(m.calculated_metrics, [{**r, "calc_run_id": run_id} for r in h.to_dict("records")], engine)
    t = uni.table.replace([np.inf, -np.inf], np.nan)
    vrows = [{"company_id": int(r.company_id), "date": today, "ev_ebitda": r.ev_ebitda, "ev_sales": r.ev_sales,
              "pe": r.pe, "fcf_yield": r.fcf_yield, "peer_group": r.peer_group,
              "peer_median_ev_ebitda": r.peer_median_ev_ebitda, "calc_run_id": run_id} for _, r in t.iterrows()]
    srows = [{"company_id": int(r.company_id), "screening_date": today,
              **{f"{d}_score": r[f"{d}_score"] for d in DIMENSIONS}, "total_score": r.total_score,
              "archetype": r.archetype, "data_quality": r.data_quality, "passes_screen": bool(r.passes_screen),
              "weights": json.loads(json.dumps(weights)), "calc_run_id": run_id} for _, r in t.iterrows()]
    clean = lambda rows: [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in x.items()} for x in rows]  # noqa: E731
    repo.append_rows(m.valuation, clean(vrows), engine)
    repo.append_rows(m.screening_results, clean(srows), engine)
    repo.finish_run(run_id, "success", len(t), 0, f"{int(t.passes_screen.sum())} companies pass", engine)
    return run_id
