"""Valuation multiples and automatic peer groups.

Peer group = same industry; widened to sector, then the whole screenable universe, if fewer than
`min_peers` companies have a meaningful multiple. The level used is always reported.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.config import screening_config
from src.financial_metrics.metrics import safe_div

MULTIPLES = ["ev_ebitda", "ev_sales", "pe", "fcf_yield"]


def compute_multiples(snapshot: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """snapshot: latest metrics per company (needs revenue, ebitda, pat, free_cash_flow, net_debt).
    market: latest market_cap per company. EV is recomputed from market cap + latest net debt so that
    the multiple and its denominator refer to the same balance sheet."""
    df = snapshot.merge(market[["company_id", "market_cap", "date"]].rename(columns={"date": "price_date"}),
                        on="company_id", how="left")
    df["enterprise_value"] = df["market_cap"] + df["net_debt"].fillna(0)
    df["ev_ebitda"] = [safe_div(ev, e) if (e is not None and e > 0) else np.nan
                       for ev, e in zip(df.enterprise_value, df.ebitda)]
    df["ev_sales"] = [safe_div(ev, r) if (r is not None and r > 0) else np.nan
                      for ev, r in zip(df.enterprise_value, df.revenue)]
    df["pe"] = [safe_div(mc, p) if (p is not None and p > 0) else np.nan for mc, p in zip(df.market_cap, df.pat)]
    df["fcf_yield"] = [safe_div(f, mc) for f, mc in zip(df.free_cash_flow, df.market_cap)]
    for col in ["ev_ebitda", "ev_sales", "pe"]:  # negative EV (net cash > mcap) is not a meaningful multiple
        df.loc[df[col] <= 0, col] = np.nan
    return df[["company_id", "market_cap", "enterprise_value", "price_date", *MULTIPLES]]


def assign_peer_groups(universe: pd.DataFrame, metric: str = "ev_ebitda", min_peers: int | None = None) -> pd.Series:
    """Return a peer-group label per company: 'industry:<x>', 'sector:<x>' or 'universe'."""
    min_peers = min_peers or screening_config()["peer_groups"]["min_peers"]
    valid = universe[universe[metric].notna()] if metric in universe else universe
    ind_counts = valid.groupby("industry").size()
    sec_counts = valid.groupby("sector").size()
    labels = []
    for _, r in universe.iterrows():
        if ind_counts.get(r["industry"], 0) >= min_peers:
            labels.append(f"industry:{r['industry']}")
        elif sec_counts.get(r["sector"], 0) >= min_peers:
            labels.append(f"sector:{r['sector']}")
        else:
            labels.append("universe")
    return pd.Series(labels, index=universe.index)


def pretty_peer_group(label: str) -> str:
    if ":" not in str(label):
        return "Whole screenable universe"
    level, name = label.split(":", 1)
    return f"{name} ({level})"


def peer_members(universe: pd.DataFrame, label: str) -> pd.DataFrame:
    if label.startswith("industry:"):
        return universe[universe["industry"] == label.split(":", 1)[1]]
    if label.startswith("sector:"):
        return universe[universe["sector"] == label.split(":", 1)[1]]
    return universe


def peer_stats(universe: pd.DataFrame, company_id: int) -> pd.DataFrame:
    """Company multiple vs peer p25 / median / p75 and premium/discount to median, for every multiple."""
    row = universe.loc[universe.company_id == company_id].iloc[0]
    label = row["peer_group"]
    peers = peer_members(universe, label)
    peers = peers[peers.company_id != company_id]
    out = []
    for mtp in MULTIPLES:
        s = peers[mtp].dropna()
        val = row[mtp]
        med = s.median() if len(s) else np.nan
        prem = safe_div(val, med) - 1 if not math.isnan(val) and len(s) and med > 0 else np.nan
        out.append({"multiple": mtp, "company": val, "peer_p25": s.quantile(.25) if len(s) else np.nan,
                    "peer_median": med, "peer_p75": s.quantile(.75) if len(s) else np.nan,
                    "premium_discount": prem, "n_peers": len(s), "peer_group": label})
    return pd.DataFrame(out)


def explain_valuation(universe: pd.DataFrame, company_id: int) -> list[str]:
    """Characteristics that may explain a premium/discount. Deliberately does not conclude 'cheap = attractive'."""
    row = universe.loc[universe.company_id == company_id].iloc[0]
    peers = peer_members(universe, row["peer_group"])
    notes = []
    stats = peer_stats(universe, company_id).set_index("multiple")
    prem = stats.loc["ev_ebitda", "premium_discount"]
    if math.isnan(prem):
        return ["EV/EBITDA is not meaningful (negative/zero EBITDA or missing market data); "
                "valuation must be approached via EV/Sales or asset value."]
    direction = "discount" if prem < 0 else "premium"
    notes.append(f"Trades at a {abs(prem):.0%} {direction} to the peer-median EV/EBITDA "
                 f"({stats.loc['ev_ebitda','n_peers']} peers, {pretty_peer_group(row['peer_group'])}).")
    checks = [
        ("revenue_cagr_3y", "3-year revenue CAGR", True),
        ("roic", "ROIC", True),
        ("ebitda_margin", "EBITDA margin", True),
        ("fcf_conversion", "FCF conversion", True),
        ("net_debt_to_ebitda", "Net debt/EBITDA", False),
        ("revenue_growth_volatility", "Revenue growth volatility", False),
    ]
    for col, label, higher_good in checks:
        if col not in peers or pd.isna(row.get(col)):
            continue
        s = peers[col].dropna()
        if len(s) < 3:
            continue
        pct = (s < row[col]).mean()
        good = pct if higher_good else 1 - pct
        if good <= 0.25:
            notes.append(f"{label} is in the weakest quartile of peers — consistent with a "
                         f"{'justified discount' if prem < 0 else 'premium that is hard to explain'}.")
        elif good >= 0.75:
            notes.append(f"{label} is in the strongest quartile of peers — "
                         f"{'the discount is not explained by this metric' if prem < 0 else 'supports a premium'}.")
    if len(notes) == 1:
        notes.append("Fundamentals sit near the peer middle on the metrics checked; the gap may reflect "
                     "factors outside the data (governance, liquidity, free float, business mix).")
    return notes
