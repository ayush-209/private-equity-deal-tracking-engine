"""Automated red flags and rule-based due-diligence questions.

Every flag carries: category, metric, current value, previous value, threshold, explanation, severity.
DD questions are generated from the flags (not by an LLM), so each one is traceable to a data point.
"""
from __future__ import annotations

import math

import pandas as pd

from src.config import screening_config


def _v(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _pct(x):
    return f"{x:.1%}" if _v(x) else "n/a"


def _flag(category, name, metric, current, previous, threshold, explanation, severity, fmt="pct"):
    f = {"pct": _pct, "x": lambda v: f"{v:.2f}x" if _v(v) else "n/a",
         "days": lambda v: f"{v:.0f} days" if _v(v) else "n/a",
         "pp": lambda v: f"{v*100:+.1f}pp" if _v(v) else "n/a",
         "raw": lambda v: f"{v}" if _v(v) else "n/a"}[fmt]
    return {"category": category, "flag": name, "metric": metric, "current": f(current),
            "previous": f(previous), "threshold": threshold, "explanation": explanation, "severity": severity,
            "current_raw": current, "previous_raw": previous}


def red_flags(hist: pd.DataFrame, snap: pd.Series, industry: str | None = None) -> list[dict]:
    t = screening_config()["red_flags"]
    h = hist.sort_values("fiscal_year")
    flags: list[dict] = []
    if len(h) < 2:
        return [_flag("Data", "Insufficient history", "years_of_history", len(h), None, "≥ 2 years",
                      "Fewer than two annual periods; trend-based checks cannot run.", "high", "raw")]
    cur, prev = h.iloc[-1], h.iloc[-2]

    # ---- Revenue quality
    if _v(cur.revenue_growth) and cur.revenue_growth < 0:
        flags.append(_flag("Revenue quality", "Revenue decline", "revenue_growth", cur.revenue_growth,
                           prev.revenue_growth, "< 0%", f"Revenue fell {abs(cur.revenue_growth):.1%} YoY.",
                           "high" if cur.revenue_growth < -0.10 else "medium"))
    vol = snap.get("revenue_growth_volatility")
    if _v(vol) and vol > t["revenue_volatility"]:
        flags.append(_flag("Revenue quality", "Unstable growth", "revenue_growth_volatility", vol, None,
                           f"> {t['revenue_volatility']:.0%} std. dev.",
                           f"Standard deviation of annual revenue growth over 5 years is {vol:.1%}; "
                           "growth is not a reliable base for a projection.", "medium"))
    if _v(cur.receivables_growth) and _v(cur.revenue_growth) and \
            cur.receivables_growth - cur.revenue_growth > t["receivables_vs_revenue_gap"]:
        flags.append(_flag("Revenue quality", "Receivables risk", "receivables_growth", cur.receivables_growth,
                           cur.revenue_growth, f"gap > {t['receivables_vs_revenue_gap']*100:.0f}pp vs revenue",
                           f"Receivables increased {cur.receivables_growth:.0%} YoY while revenue increased "
                           f"{cur.revenue_growth:.0%}. Possible channel stuffing, looser credit terms or collection issues.",
                           "high"))

    # ---- Profitability
    if _v(cur.ebitda_margin) and _v(prev.ebitda_margin) and prev.ebitda_margin - cur.ebitda_margin > t["margin_compression_pp"]:
        flags.append(_flag("Profitability", "Margin compression", "ebitda_margin", cur.ebitda_margin, prev.ebitda_margin,
                           f"> {t['margin_compression_pp']*100:.0f}pp YoY decline",
                           f"EBITDA margin fell from {prev.ebitda_margin:.1%} to {cur.ebitda_margin:.1%}.", "medium"))
    if _v(cur.roic) and _v(prev.roic) and prev.roic - cur.roic > t["roic_decline_pp"]:
        flags.append(_flag("Profitability", "Falling ROIC", "roic", cur.roic, prev.roic,
                           f"> {t['roic_decline_pp']*100:.0f}pp YoY decline",
                           f"ROIC declined from {prev.roic:.1%} to {cur.roic:.1%}; incremental capital is earning less.",
                           "medium"))
    if _v(cur.ebitda_growth) and _v(cur.pat_growth) and cur.ebitda_growth - cur.pat_growth > t["pat_ebitda_divergence_pp"]:
        flags.append(_flag("Profitability", "EBITDA/PAT divergence", "pat_growth", cur.pat_growth, cur.ebitda_growth,
                           f"PAT growth lags EBITDA by > {t['pat_ebitda_divergence_pp']*100:.0f}pp",
                           f"EBITDA grew {cur.ebitda_growth:.0%} but PAT grew {cur.pat_growth:.0%}: check depreciation, "
                           "interest, exceptional items and tax.", "medium"))
    if _v(cur.ebitda) and cur.ebitda <= 0:
        flags.append(_flag("Profitability", "Negative EBITDA", "ebitda", cur.ebitda, prev.ebitda, "> 0",
                           "Operating loss at EBITDA level; multiple-based valuation and leverage capacity are not meaningful.",
                           "critical", "raw"))

    # ---- Cash flow
    if _v(cur.free_cash_flow) and cur.free_cash_flow < 0:
        neg_years = int((h.tail(3).free_cash_flow < 0).sum())
        flags.append(_flag("Cash flow", "Negative FCF", "free_cash_flow", cur.free_cash_flow, prev.free_cash_flow, "> 0",
                           f"Free cash flow negative in {neg_years} of the last 3 years.",
                           "critical" if neg_years == 3 else "high", "raw"))
    if _v(cur.fcf_conversion) and cur.fcf_conversion < t["low_fcf_conversion"]:
        flags.append(_flag("Cash flow", "Low FCF conversion", "fcf_conversion", cur.fcf_conversion, prev.fcf_conversion,
                           f"< {t['low_fcf_conversion']:.0%}",
                           f"Only {cur.fcf_conversion:.0%} of EBITDA converted to free cash flow.", "medium"))
    oe = snap.get("ocf_to_ebitda_3y")
    if _v(oe) and oe < t["ocf_ebitda_divergence"]:
        flags.append(_flag("Cash flow", "Persistent OCF/EBITDA divergence", "ocf_to_ebitda_3y", oe, None,
                           f"< {t['ocf_ebitda_divergence']:.0%} over 3 years",
                           f"Cumulative 3-year operating cash flow is {oe:.0%} of cumulative EBITDA; "
                           "earnings are not turning into cash.", "high"))

    # ---- Balance sheet
    if _v(cur.net_debt_to_ebitda) and _v(prev.net_debt_to_ebitda) and \
            cur.net_debt_to_ebitda - prev.net_debt_to_ebitda > t["leverage_increase_x"]:
        flags.append(_flag("Balance sheet", "Rising leverage", "net_debt_to_ebitda", cur.net_debt_to_ebitda,
                           prev.net_debt_to_ebitda, f"> +{t['leverage_increase_x']}x YoY",
                           f"Net debt/EBITDA rose from {prev.net_debt_to_ebitda:.1f}x to {cur.net_debt_to_ebitda:.1f}x.",
                           "high", "x"))
    if _v(cur.interest_coverage) and cur.interest_coverage < t["min_interest_coverage"]:
        flags.append(_flag("Balance sheet", "Weak interest coverage", "interest_coverage", cur.interest_coverage,
                           prev.interest_coverage, f"< {t['min_interest_coverage']}x",
                           f"EBIT covers interest {cur.interest_coverage:.1f}x.",
                           "critical" if cur.interest_coverage < 1.5 else "high", "x"))
    if _v(cur.debt_growth) and cur.debt_growth > t["debt_growth"] and _v(cur.total_debt) and cur.total_debt > 0:
        flags.append(_flag("Balance sheet", "Rapid debt accumulation", "debt_growth", cur.debt_growth, prev.debt_growth,
                           f"> {t['debt_growth']:.0%} YoY", f"Gross debt grew {cur.debt_growth:.0%} in one year.", "medium"))

    # ---- Working capital
    for col, name, thr in [("dso", "Rising DSO", t["dso_increase_days"]), ("dio", "Rising inventory days",
                                                                              t["dio_increase_days"]),
                           ("cash_conversion_cycle", "Deteriorating CCC", t["ccc_increase_days"])]:
        base = h[col].dropna()
        if len(base) >= 2:
            b = h[h.fiscal_year == cur.fiscal_year - 3][col]
            ref = b.iloc[0] if len(b) and _v(b.iloc[0]) else prev[col]
            if _v(cur[col]) and _v(ref) and cur[col] - ref > thr:
                flags.append(_flag("Working capital", name, col, cur[col], ref, f"> +{thr} days vs 3y ago",
                                   f"{name.replace('Rising ', '').replace('Deteriorating ', '')} moved from "
                                   f"{ref:.0f} to {cur[col]:.0f} days.", "medium", "days"))

    # ---- Accounting comparability (India-specific)
    sensitive = screening_config()["ind_as_116_sensitive_industries"]
    if industry and any(s.lower() in str(industry).lower() for s in sensitive):
        if h.fiscal_year.min() <= 2019 and h.fiscal_year.max() >= 2020:
            flags.append(_flag("Accounting", "Ind AS 116 break in series", "ebitda_margin", cur.ebitda_margin, None,
                               "History spans FY2019→FY2020",
                               "Ind AS 116 (from FY2020) moved lease rentals below EBITDA. Margin and CAGR comparisons "
                               "across FY2020 overstate improvement for this industry; use post-FY2020 data or "
                               "EBITDA after lease payments.", "medium"))
    return flags


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}

DD_TEMPLATES = {
    "Receivables risk": "Why have receivables grown faster than revenue? Obtain an ageing schedule, top-10 debtor list and any changes to credit terms or revenue recognition.",
    "Rising DSO": "Why have receivable days increased over the last three years? Is this customer-mix, credit-term or collection driven?",
    "Rising inventory days": "What explains higher inventory days — slow-moving stock, strategic build, or new product lines? Request inventory ageing and write-down history.",
    "Deteriorating CCC": "What is the normalised working-capital requirement, and how much cash would be released if the cycle returned to its 3-year-ago level?",
    "Margin compression": "What drove the EBITDA margin decline (input costs, pricing, mix, one-offs)? How much is structural?",
    "Falling ROIC": "Which recent investments have diluted ROIC, and what returns were they underwritten at?",
    "EBITDA/PAT divergence": "Reconcile EBITDA to PAT: what share of the gap is depreciation from new capex, interest, exceptional items or tax?",
    "Negative EBITDA": "What is the credible path to positive EBITDA, and how much funding is required before reaching it?",
    "Negative FCF": "What portion of current capex is maintenance versus growth capex? What does FCF look like on maintenance capex only?",
    "Low FCF conversion": "What is the sustainable free-cash-flow conversion rate once growth capex and working-capital build normalise?",
    "Persistent OCF/EBITDA divergence": "Why does operating cash flow persistently trail EBITDA? Test for aggressive revenue recognition, capitalised costs and unbilled revenue.",
    "Rising leverage": "What was the incremental debt used for, and what are the covenant headroom and maturity profile?",
    "Weak interest coverage": "What is the refinancing risk over the next 24 months, and are there related-party or promoter-pledged facilities?",
    "Rapid debt accumulation": "Itemise the debt raised in the last year: acquisition, capex, working capital or related-party funding?",
    "Revenue decline": "Is the revenue decline cyclical, share loss, or a deliberate exit from low-margin business?",
    "Unstable growth": "Which segments or customers drive revenue volatility, and what is the order book / visibility for the next 2 years?",
    "Ind AS 116 break in series": "Restate pre-FY2020 EBITDA on a post-Ind AS 116 basis (or present EBITDA after lease rentals) before comparing margins.",
    "Insufficient history": "Obtain at least five years of audited statements before relying on any trend metric.",
}


def dd_questions(flags: list[dict], valuation_premium: float | None, data_quality: str, snap: pd.Series) -> list[dict]:
    qs = []
    for f in sorted(flags, key=lambda x: SEVERITY_ORDER.get(x["severity"], 3)):
        if f["flag"] in DD_TEMPLATES:
            qs.append({"question": DD_TEMPLATES[f["flag"]], "triggered_by": f"{f['flag']} ({f['metric']} = {f['current']})",
                       "priority": f["severity"]})
    if _v(valuation_premium) and abs(valuation_premium) >= 0.15:
        word = "discount" if valuation_premium < 0 else "premium"
        qs.append({"question": f"Why does the company trade at a {abs(valuation_premium):.0%} {word} to peers? "
                               "Which of growth, returns, governance, free float or business mix explains it?",
                   "triggered_by": "Peer valuation gap", "priority": "medium"})
    if _v(snap.get("ebitda_margin_change_3y")) and snap["ebitda_margin_change_3y"] > 0.02:
        qs.append({"question": "What explains recent EBITDA margin expansion, and how much is pricing/mix versus "
                               "temporary input-cost relief?",
                   "triggered_by": f"EBITDA margin {snap['ebitda_margin_change_3y']*100:+.1f}pp over 3 years",
                   "priority": "medium"})
    if not _v(snap.get("cash_conversion_cycle")):
        qs.append({"question": "Obtain the latest receivables and inventory balances; the working-capital cycle cannot "
                               "be computed from the screening data.",
                   "triggered_by": "Cash conversion cycle not computable", "priority": "medium"})
    if data_quality != "High":
        qs.append({"question": "Obtain audited annual reports directly; the screening data for this company has gaps.",
                   "triggered_by": f"{data_quality} data quality", "priority": "high"})
    qs.append({"question": "What is the promoter's appetite for a transaction (stake sale, minority growth capital, "
                           "or control), and is any promoter holding pledged?",
               "triggered_by": "Indian listed-company deal structure", "priority": "medium"})
    seen, unique = set(), []
    for q in qs:
        if q["question"] not in seen:
            seen.add(q["question"])
            unique.append(q)
    return unique
