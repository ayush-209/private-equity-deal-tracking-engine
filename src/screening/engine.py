"""PE screening engine: sector-relative percentile scoring, leverage classes, archetypes.

Why percentiles inside peer groups: a 15% EBITDA margin is weak for software and strong for
distribution. Absolute thresholds would just rank sectors. Each sub-metric is converted to a
percentile inside the company's industry (widened to sector/universe if too few peers), averaged
inside its dimension, then weighted.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.config import screening_config
from src.valuation.peers import assign_peer_groups

DIMENSIONS = ["growth", "profitability", "cash_flow", "leverage", "valuation", "working_capital"]
DIM_LABELS = {"growth": "Growth", "profitability": "Profitability", "cash_flow": "Cash flow",
              "leverage": "Leverage", "valuation": "Valuation", "working_capital": "Working capital"}


def normalise_weights(weights: dict) -> dict:
    """Re-scale so weights sum to 100. All-zero weights fall back to the config defaults."""
    w = {d: max(float(weights.get(d, 0)), 0.0) for d in DIMENSIONS}
    total = sum(w.values())
    if total == 0:
        return normalise_weights(screening_config()["weights"])
    return {d: v / total * 100 for d, v in w.items()}


def _scoring_inputs(u: pd.DataFrame) -> pd.DataFrame:
    """Adjust values whose NaN means 'bad' rather than 'unknown', so they are not silently dropped."""
    x = u.copy()
    neg_ebitda = x["ebitda"].notna() & (x["ebitda"] <= 0)
    # Negative EBITDA with debt: worst-possible leverage; with net cash: leverage metric not meaningful.
    x.loc[neg_ebitda & (x["net_debt"] > 0), "net_debt_to_ebitda"] = np.inf
    # Negative EBITDA / earnings: multiple not meaningful for valuation, scored as worst.
    x.loc[neg_ebitda & x["market_cap"].notna(), "ev_ebitda"] = np.inf
    x.loc[(x["pat"] <= 0) & x["market_cap"].notna(), "pe"] = np.inf
    x.loc[neg_ebitda, "fcf_conversion"] = -np.inf
    return x


def _percentile(series: pd.Series, groups: pd.Series, higher_better: bool) -> pd.Series:
    def rank(s):
        valid = s.dropna()
        n = len(valid)
        r = pd.Series(np.nan, index=s.index)
        if n == 0:
            return r
        r[valid.index] = (valid.rank(method="average") - 0.5) / n
        return r
    pct = series.groupby(groups, group_keys=False).apply(rank)
    return pct if higher_better else 1 - pct


def score_universe(universe: pd.DataFrame, weights: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (scores per company, long table of sub-metric percentiles for explainability)."""
    cfg = screening_config()
    w = normalise_weights(weights or cfg["weights"])
    x = _scoring_inputs(universe)
    groups = x["scoring_peer_group"] if "scoring_peer_group" in x else assign_peer_groups(x, "revenue")
    detail_rows, dim_scores = [], pd.DataFrame(index=x.index)
    for dim in DIMENSIONS:
        subs = cfg["dimensions"][dim]
        pcts = pd.DataFrame(index=x.index)
        for sm in subs:
            col = sm["metric"]
            if col not in x:
                continue
            p = _percentile(x[col].replace([np.inf, -np.inf], [1e12, -1e12]), groups, sm["direction"] == "higher")
            pcts[col] = p
            for idx in x.index:
                detail_rows.append({"company_id": x.at[idx, "company_id"], "dimension": dim, "metric": col,
                                    "value": universe.at[idx, col] if col in universe else np.nan,
                                    "direction": sm["direction"], "percentile": p.at[idx],
                                    "peer_group": groups.at[idx]})
        coverage = pcts.notna().sum(axis=1) / max(len(subs), 1)
        dim_scores[dim] = pcts.mean(axis=1).where(coverage >= cfg["min_submetric_coverage"])
    out = pd.DataFrame({"company_id": x["company_id"]})
    avail_weight = sum((dim_scores[d].notna() * w[d]) for d in DIMENSIONS)
    total = sum(dim_scores[d].fillna(0) * w[d] for d in DIMENSIONS)
    for d in DIMENSIONS:
        out[f"{d}_score"] = dim_scores[d] * w[d]          # points out of the dimension weight
        out[f"{d}_max"] = w[d]
    out["total_score"] = (total / avail_weight.replace(0, np.nan)) * 100
    out["dimensions_missing"] = [", ".join(DIM_LABELS[d] for d in DIMENSIONS if pd.isna(dim_scores.at[i, d]))
                                 for i in x.index]
    out["score_is_partial"] = out["dimensions_missing"] != ""
    out["scoring_peer_group"] = groups.values
    return out, pd.DataFrame(detail_rows)


def leverage_class(nd_ebitda, icr, ebitda, net_debt) -> str:
    c = screening_config()["leverage_classes"]
    if pd.notna(net_debt) and net_debt <= 0:
        return "Low"
    if pd.notna(ebitda) and ebitda <= 0 and pd.notna(net_debt) and net_debt > 0:
        return "Distressed"
    if pd.notna(icr) and icr < 1.5:
        return "Distressed"
    if pd.isna(nd_ebitda):
        return "Unknown"
    if nd_ebitda < c["low"]:
        return "Low"
    if nd_ebitda < c["moderate"]:
        return "Moderate"
    if nd_ebitda < c["high"]:
        return "High"
    return "Distressed"


def _fmt_pct(v):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.1%}"


def assign_archetype(r: pd.Series, ev_discount: float | None) -> tuple[str, list[str]]:
    """Rules evaluated in priority order. Returns (archetype, reasons). Reasons cite the metrics used."""
    a = screening_config()["archetypes"]
    g = lambda k: r.get(k, np.nan)  # noqa: E731
    gt = lambda k, t: pd.notna(g(k)) and g(k) >= t  # noqa: E731

    c = a["compounder"]
    if gt("revenue_cagr_3y", c["revenue_cagr_3y"]) and gt("roic", c["roic"]) and gt("fcf_conversion", c["fcf_conversion"]):
        return "Compounder", [f"3y revenue CAGR {_fmt_pct(g('revenue_cagr_3y'))} ≥ {c['revenue_cagr_3y']:.0%}",
                              f"ROIC {_fmt_pct(g('roic'))} ≥ {c['roic']:.0%}",
                              f"FCF conversion {_fmt_pct(g('fcf_conversion'))} ≥ {c['fcf_conversion']:.0%}"]
    c = a["cash_flow_compounder"]
    if gt("revenue_cagr_3y", c["revenue_cagr_3y"]) and gt("fcf_conversion", c["fcf_conversion"]) and gt("fcf_margin", c["fcf_margin"]):
        return "Cash Flow Compounder", [f"3y revenue CAGR {_fmt_pct(g('revenue_cagr_3y'))} ≥ {c['revenue_cagr_3y']:.0%}",
                                        f"FCF conversion {_fmt_pct(g('fcf_conversion'))} ≥ {c['fcf_conversion']:.0%}",
                                        f"FCF margin {_fmt_pct(g('fcf_margin'))} ≥ {c['fcf_margin']:.0%}"]
    c = a["turnaround"]
    past_roic = g("roic") - g("roic_change_3y") if pd.notna(g("roic_change_3y")) else np.nan
    if pd.notna(past_roic) and past_roic <= c["past_roic_max"] and gt("revenue_growth", c["recent_revenue_growth_min"]) \
            and gt("ebitda_margin_change_3y", c["margin_change_min"]) and g("roic_change_3y") > 0:
        return "Turnaround Candidate", [f"ROIC three years ago ≈{_fmt_pct(past_roic)} (weak base)",
                                        f"ROIC now {_fmt_pct(g('roic'))}; latest revenue growth {_fmt_pct(g('revenue_growth'))}",
                                        f"EBITDA margin +{g('ebitda_margin_change_3y')*100:.1f}pp over 3 years"]
    c = a["margin_expansion"]
    if gt("revenue_cagr_3y", c["revenue_cagr_3y_min"]) and gt("ebitda_margin_change_3y", c["ebitda_margin_change_3y"]) \
            and pd.notna(g("ebitda_margin_slope")) and g("ebitda_margin_slope") > 0:
        return "Margin Expansion", [f"3y revenue CAGR {_fmt_pct(g('revenue_cagr_3y'))} (stable growth)",
                                    f"EBITDA margin +{g('ebitda_margin_change_3y')*100:.1f}pp over 3 years",
                                    "5-year margin trend line slopes upward"]
    c = a["value_opportunity"]
    fundamentals = np.nanmean([g("growth_pct"), g("profitability_pct"), g("cash_flow_pct")]) \
        if any(pd.notna(g(k)) for k in ["growth_pct", "profitability_pct", "cash_flow_pct"]) else np.nan
    if ev_discount is not None and pd.notna(ev_discount) and ev_discount <= -c["ev_ebitda_discount"] \
            and pd.notna(fundamentals) and fundamentals >= c["min_fundamental_score"]:
        return "Value Opportunity", [f"EV/EBITDA {abs(ev_discount):.0%} below peer median",
                                     f"Growth/profitability/cash-flow percentile average {fundamentals:.0%} "
                                     f"(≥ {c['min_fundamental_score']:.0%})"]
    c = a["leveraged_opportunity"]
    nd = g("net_debt_to_ebitda")
    nd_ok = (pd.notna(g("net_debt")) and g("net_debt") <= 0) or (pd.notna(nd) and nd <= c["max_net_debt_to_ebitda"])
    if gt("fcf_conversion", c["fcf_conversion"]) and nd_ok and gt("interest_coverage", c["min_interest_coverage"]) \
            and pd.notna(g("ebitda_margin_volatility")) and g("ebitda_margin_volatility") <= c["max_margin_volatility"]:
        return "Leveraged Opportunity", [f"FCF conversion {_fmt_pct(g('fcf_conversion'))} supports debt service",
                                         f"Net debt/EBITDA {('net cash' if g('net_debt') <= 0 else f'{nd:.1f}x')} leaves capacity",
                                         f"EBITDA margin volatility {g('ebitda_margin_volatility')*100:.1f}pp (stable)"]
    return "Further Investigation", ["No archetype rule satisfied — signals are mixed or data is incomplete"]


def screen(universe: pd.DataFrame, weights: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full screen: scores + leverage class + archetype + pass/fail. `universe` must already contain metrics,
    multiples, peer_group, ev_ebitda_premium, data_quality and critical_flags columns."""
    cfg = screening_config()["pass_rules"]
    scores, detail = score_universe(universe, weights)
    u = universe.drop(columns=[c for c in scores.columns if c != "company_id" and c in universe.columns])
    u = u.merge(scores, on="company_id", how="left")
    for d in ["growth", "profitability", "cash_flow"]:
        u[f"{d}_pct"] = u[f"{d}_score"] / u[f"{d}_max"]
    u["leverage_class"] = [leverage_class(a, b, c, d) for a, b, c, d in
                           zip(u.net_debt_to_ebitda, u.interest_coverage, u.ebitda, u.net_debt)]
    arche = [assign_archetype(r, r.get("ev_ebitda_premium")) for _, r in u.iterrows()]
    u["archetype"] = [a for a, _ in arche]
    u["archetype_reasons"] = [r for _, r in arche]
    u["passes_screen"] = (
        (u["total_score"] >= cfg["min_total_score"])
        & u["data_quality"].isin(cfg["allowed_data_quality"])
        & (u["critical_flags"].fillna(0) <= cfg["max_critical_flags"])
        & ~u["score_is_partial"]
    )
    u["fail_reasons"] = [_fail_reasons(r, cfg) for _, r in u.iterrows()]
    return u, detail


def _fail_reasons(r, cfg) -> str:
    reasons = []
    if pd.isna(r.total_score) or r.total_score < cfg["min_total_score"]:
        reasons.append(f"Score below {cfg['min_total_score']}")
    if r.data_quality not in cfg["allowed_data_quality"]:
        reasons.append(f"{r.data_quality} data quality")
    if r.get("critical_flags", 0) > cfg["max_critical_flags"]:
        reasons.append(f"{int(r.critical_flags)} critical red flag(s)")
    if r.score_is_partial:
        reasons.append(f"Missing: {r.dimensions_missing}")
    return "; ".join(reasons)
