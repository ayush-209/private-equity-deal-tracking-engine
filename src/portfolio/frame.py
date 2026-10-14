"""Assemble the class-basis frame from the database: one row per company with every metric the score needs."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.config import portfolio_config
from src.database import repository as repo
from src.portfolio import metrics as pm
from src.portfolio.scoring import score_frame
from src.screening.universe import exclusion_reason


@dataclass
class ClassData:
    frame: pd.DataFrame                 # all companies with metrics, segment, eligibility
    benchmark_symbol: str | None
    prices: pd.DataFrame                # long monthly panel (company_id, date, share_price)
    bench_returns: pd.Series
    notes: list = field(default_factory=list)


def build_class_frame(engine=None, cfg: dict | None = None) -> ClassData:
    cfg = cfg or portfolio_config()
    engine = engine or repo.get_engine()
    companies = repo.load_companies(engine)
    fin = repo.load_financials(engine=engine)
    market = repo.load_market_latest(engine=engine)
    panel = repo.load_price_panel(engine)
    bench = repo.load_benchmark(engine)
    notes: list[str] = []
    if fin.empty or market.empty:
        return ClassData(pd.DataFrame(), None, panel, pd.Series(dtype=float), ["No financial or market data loaded."])

    snap = pm.fundamentals_snapshot(fin)
    f = companies[["company_id", "company_name", "ticker", "sector", "industry"]] \
        .merge(market[["company_id", "market_cap", "share_price", "date"]].rename(
            columns={"date": "price_date"}), on="company_id", how="inner") \
        .merge(snap, on="company_id", how="left")
    f["pe"] = np.where(f.pat > 0, f.market_cap / f.pat, np.nan)
    f["pb"] = [pm.price_to_book(m, e) for m, e in zip(f.market_cap, f.total_equity)]
    f["peg"] = [pm.peg_ratio(p, g) for p, g in zip(f.pe, f.eps_growth)]

    bret, sym = pm.pick_benchmark(bench, cfg["benchmark"]["symbols"], cfg["benchmark"]["min_months"])
    if sym is None:
        notes.append("No benchmark index series loaded: beta is unavailable and the risk dimension uses "
                     "standard deviation only. Run the market job to load it.")
    px = pm.price_metrics(panel, bret, cfg["benchmark"]["min_months"], cfg["benchmark"]["max_months"]) \
        if not panel.empty else pd.DataFrame({"company_id": []})
    f = f.merge(px, on="company_id", how="left")

    f = pd.concat([f, pm.classify_cap(f["market_cap"], cfg["cap_segments"])], axis=1)

    f["is_financial"] = [exclusion_reason(s, i) is not None for s, i in zip(f.sector, f.industry)]
    return ClassData(f, sym, panel, bret, notes)


def apply_eligibility(f: pd.DataFrame, cfg: dict | None = None, include_financials: bool | None = None,
                      exclude_loss_making: bool | None = None) -> pd.DataFrame:
    """Adds `eligible` and `ineligible_reason`. Cap rank is computed before this step, over every company."""
    cfg = cfg or portfolio_config()
    el = cfg["eligibility"]
    inc = el["include_financials"] if include_financials is None else include_financials
    loss = el["exclude_loss_making"] if exclude_loss_making is None else exclude_loss_making
    x = f.copy()
    reason = pd.Series("", index=x.index)
    reason[x.cap_segment.isna()] = "no market capitalisation"
    if not inc:
        reason[(reason == "") & x.is_financial] = "financial business (not comparable on ROCE / sales growth)"
    if loss:
        reason[(reason == "") & (x.pat.fillna(0) <= 0)] = "loss-making or earnings missing"
    reason[(reason == "") & (x.last_price.isna() | (x.last_price <= 0))] = "no price history"
    x["ineligible_reason"] = reason
    x["eligible"] = reason == ""
    return x


def class_scores(data: ClassData, weights: dict | None = None, include_financials: bool | None = None,
                 exclude_loss_making: bool | None = None, cfg: dict | None = None) -> pd.DataFrame:
    """Eligible companies, scored. Ineligible ones are returned too (class_score NaN) with the reason."""
    cfg = cfg or portfolio_config()
    x = apply_eligibility(data.frame, cfg, include_financials, exclude_loss_making)
    scored = score_frame(x[x.eligible], weights, cfg)
    out = x.merge(scored[["company_id", *[c for c in scored.columns if c not in x.columns]]],
                  on="company_id", how="left")
    return out
