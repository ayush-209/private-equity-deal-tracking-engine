"""Historical validation of the screening methodology.

For a past screening date D:
  1. Use only statements with filing_date <= D and market data dated <= D (point-in-time on filing date).
  2. Run the identical screen.
  3. Track what happened to fundamentals afterwards (base = last FY public at D, end = latest FY).
  4. Compare shortlisted companies with the rest of the screenable universe, and test whether each
     company's archetype thesis held.

Limitation: free sources return restated, latest-version figures. Without as-reported vintages this is
point-in-time on *availability*, not on *values*. The UI states this next to every result.
This validates whether the screen picks companies whose fundamentals behave as the thesis implies;
it is not evidence of investment returns.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from src.financial_metrics.metrics import cagr, compute_all
from src.screening.universe import build_universe

OUTCOMES = {
    "revenue_cagr_after": "Revenue CAGR after screen",
    "ebitda_cagr_after": "EBITDA CAGR after screen",
    "fcf_growth_after": "Cumulative FCF growth (vs base-year FCF × years)",
    "roic_change_after": "ROIC change (pp)",
    "margin_change_after": "EBITDA margin change (pp)",
    "leverage_change_after": "Net debt/EBITDA change (x)",
}


def forward_outcomes(full_fin: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    hist, _ = compute_all(full_fin)
    rows = []
    for cid, g in full_fin.groupby("company_id"):
        avail = g[pd.to_datetime(g.filing_date) <= pd.Timestamp(as_of)]
        if avail.empty:
            continue
        base_fy, end_fy = int(avail.fiscal_year.max()), int(g.fiscal_year.max())
        n = end_fy - base_fy
        if n < 1:
            continue
        h = hist[hist.company_id == cid].set_index("fiscal_year")
        b, e = h.loc[base_fy], h.loc[end_fy]
        after = h.loc[base_fy + 1:end_fy]
        base_fcf = b.free_cash_flow
        fcf_growth = (after.free_cash_flow.sum() / (base_fcf * n) - 1) if pd.notna(base_fcf) and base_fcf > 0 else np.nan
        rows.append({"company_id": cid, "base_fy": base_fy, "end_fy": end_fy, "years_after": n,
                     "revenue_cagr_after": cagr(b.revenue, e.revenue, n),
                     "ebitda_cagr_after": cagr(b.ebitda, e.ebitda, n),
                     "fcf_growth_after": fcf_growth,
                     "roic_change_after": e.roic - b.roic,
                     "margin_change_after": e.ebitda_margin - b.ebitda_margin,
                     "leverage_change_after": e.net_debt_to_ebitda - b.net_debt_to_ebitda})
    return pd.DataFrame(rows)


def thesis_held(r: pd.Series) -> tuple[bool | None, str]:
    a = r.archetype
    if a == "Compounder":
        ok = r.revenue_cagr_after >= 0.08 and r.roic_change_after > -0.03
        return ok, "Revenue CAGR ≥ 8% and ROIC fell < 3pp"
    if a == "Cash Flow Compounder":
        ok = r.fcf_growth_after > 0 and r.revenue_cagr_after > 0
        return ok, "FCF grew and revenue grew"
    if a == "Margin Expansion":
        return r.margin_change_after > 0, "EBITDA margin kept rising"
    if a == "Turnaround Candidate":
        return r.roic_change_after > 0 and r.revenue_cagr_after > 0, "ROIC and revenue kept improving"
    if a == "Value Opportunity":
        return r.revenue_cagr_after > 0 and r.margin_change_after > -0.02, "Fundamentals held (no value trap)"
    if a == "Leveraged Opportunity":
        lc = r.leverage_change_after
        return (pd.isna(lc) or lc < 0.5) and r.ebitda_cagr_after > 0, "EBITDA grew without leverage rising >0.5x"
    return None, "No thesis to test"


def validate(as_of: dt.date, weights: dict | None = None, engine=None, full_fin: pd.DataFrame | None = None) -> dict:
    uni_then = build_universe(as_of=as_of, weights=weights, engine=engine)
    if uni_then.table.empty:
        return {"error": f"No statements were public on {as_of}."}
    if full_fin is None:
        from src.database import repository as repo
        full_fin = repo.load_financials(engine=engine)
    outcomes = forward_outcomes(full_fin, as_of)
    t = uni_then.table[["company_id", "company_name", "sector", "total_score", "archetype", "passes_screen",
                        "data_quality"]].merge(outcomes, on="company_id", how="inner")
    if t.empty:
        return {"error": "No subsequent fiscal years available after this date."}
    held = [thesis_held(r) for _, r in t.iterrows()]
    t["thesis_held"] = [h for h, _ in held]
    t["thesis_test"] = [d for _, d in held]
    comp = []
    for col, label in OUTCOMES.items():
        s, r = t.loc[t.passes_screen, col].dropna(), t.loc[~t.passes_screen, col].dropna()
        comp.append({"outcome": label, "shortlist_median": s.median() if len(s) else np.nan,
                     "rest_median": r.median() if len(r) else np.nan, "n_shortlist": len(s), "n_rest": len(r)})
    by_arch = (t[t.passes_screen & t.thesis_held.notna()].groupby("archetype")["thesis_held"]
               .agg(["mean", "count"]).rename(columns={"mean": "hit_rate", "count": "n"}).reset_index())
    # score quintile monotonicity check
    t["score_quintile"] = pd.qcut(t.total_score.rank(method="first"), 5, labels=["Q1 (low)", "Q2", "Q3", "Q4", "Q5 (high)"])
    quint = t.groupby("score_quintile", observed=True)[list(OUTCOMES)].median().reset_index()
    return {"as_of": as_of, "detail": t, "comparison": pd.DataFrame(comp), "by_archetype": by_arch,
            "by_quintile": quint, "n_screenable": len(uni_then.table), "n_shortlist": int(t.passes_screen.sum())}
