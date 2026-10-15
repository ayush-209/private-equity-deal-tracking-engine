"""Plain-language explanations: how many companies survive each step, and why each stock was picked."""
from __future__ import annotations

import numpy as np
import pandas as pd

DIMS = ["growth", "profitability", "valuation", "risk", "technical"]
DIM_PLAIN = {
    "growth": "growth (sales and earnings)", "profitability": "profitability (ROE, ROCE)",
    "valuation": "valuation (price vs earnings, growth, book)", "risk": "low risk (beta, volatility)",
    "technical": "recent price trend",
}
STYLES = {   # plain names for the weight sets in config/portfolio.yaml
    "base": ("Balanced", "Growth matters most, with profitability, valuation and risk equally behind it."),
    "growth_heavy": ("Growth-focused", "Pays up for fast growers; accepts more risk and richer valuations."),
    "quality_heavy": ("Quality-focused", "Prefers companies that earn high returns on capital."),
    "defensive": ("Defensive", "Prefers steadier, lower-volatility stocks."),
    "equal": ("Equal weights", "Every criterion counts the same."),
    "no_technical": ("Ignore price trend", "Fundamentals and risk only."),
}


def top_pct(pct_value: float) -> float:
    """Percentile score 0-1 (1 = best) -> 'top X%' number."""
    return max(1.0, round((1 - pct_value) * 100))


def why_text(row: pd.Series) -> str:
    """One sentence on where a company stands: rank, strongest and weakest criteria, in its own size group."""
    seg = str(row.get("cap_segment", "")).replace("nan", "")
    parts = []
    if pd.notna(row.get("rank_in_segment")):
        parts.append(f"Rank {int(row.rank_in_segment)} among {seg}-cap companies (score {row.class_score:.0f}/100).")
    vals = {d: row.get(f"{d}_pct") for d in DIMS if pd.notna(row.get(f"{d}_pct"))}
    if vals:
        order = sorted(vals, key=lambda d: vals[d], reverse=True)
        best, worst = order[0], order[-1]
        s = f"Strongest on {DIM_PLAIN[best]} (top {top_pct(vals[best]):.0f}% of its size group)"
        if len(order) > 1 and vals[worst] < 0.45:
            s += f"; weakest on {DIM_PLAIN[worst]} (bottom {top_pct(1 - vals[worst]):.0f}%)"
        parts.append(s + ".")
    return " ".join(parts)


def funnel(scored: pd.DataFrame) -> pd.DataFrame:
    """Companies left after each step of eligibility, so 'where did the 498 go?' has an answer."""
    n_all = len(scored)
    steps = [("All companies in the database", n_all)]
    has_cap = scored.cap_segment.notna()
    steps.append(("Have a market capitalisation (needed to place them in a size group)", int(has_cap.sum())))
    r = scored.ineligible_reason
    ok1 = has_cap & ~r.str.startswith("financial")
    steps.append(("Not a bank, NBFC or insurer (their ratios are not comparable)", int(ok1.sum())))
    ok2 = ok1 & ~r.str.startswith("loss")
    steps.append(("Profitable in the latest year", int(ok2.sum())))
    ok3 = ok2 & (r == "")
    steps.append(("Have a price history", int(ok3.sum())))
    ok4 = ok3 & scored.class_score.notna()
    steps.append(("Enough data to score (at least 4 of 5 criteria)", int(ok4.sum())))
    return pd.DataFrame(steps, columns=["step", "companies"])
