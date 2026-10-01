"""Deterministic financial metrics.

Every function is pure and returns NaN (never a fabricated value) when a metric is not meaningful,
e.g. CAGR across a negative base, ROE on negative equity, net debt/EBITDA on negative EBITDA.
`not_meaningful_reason` documents why, so the UI can say "n.m." with an explanation instead of 0.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.config import DEFAULT_TAX_RATE

NAN = float("nan")
ICR_CAP = 99.0  # interest coverage when interest expense is zero: reported as 99x ("no interest burden")


def _ok(*xs) -> bool:
    return all(x is not None and not (isinstance(x, float) and math.isnan(x)) for x in xs)


def safe_div(a, b):
    if not _ok(a, b) or b == 0:
        return NAN
    return a / b


def cagr(start, end, years: int):
    """Compound annual growth. Undefined when either endpoint is <= 0 (sign change has no CAGR)."""
    if not _ok(start, end) or years <= 0 or start <= 0 or end <= 0:
        return NAN
    return (end / start) ** (1 / years) - 1


def growth(prev, curr):
    """YoY growth; undefined on a non-positive base."""
    if not _ok(prev, curr) or prev <= 0:
        return NAN
    return curr / prev - 1


def effective_tax_rate(tax, pretax):
    """Reported ETR bounded to [0, 35%]; falls back to the 115BAA rate when not computable."""
    r = safe_div(tax, pretax)
    if math.isnan(r) or pretax is None or pretax <= 0:
        return DEFAULT_TAX_RATE
    return min(max(r, 0.0), 0.35)


def nopat(ebit, tax, pretax):
    if not _ok(ebit):
        return NAN
    return ebit * (1 - effective_tax_rate(tax, pretax))


def invested_capital(equity, debt, cash):
    """Operating invested capital = equity + debt - cash. Undefined if <= 0."""
    if not _ok(equity, debt):
        return NAN
    ic = equity + debt - (cash if _ok(cash) else 0.0)
    return ic if ic > 0 else NAN


def roic(nopat_value, ic_open, ic_close):
    """NOPAT / average invested capital (closing only when opening is unavailable)."""
    ics = [x for x in (ic_open, ic_close) if _ok(x)]
    if not ics or not _ok(nopat_value):
        return NAN
    return nopat_value / (sum(ics) / len(ics))


def roe(pat, eq_open, eq_close):
    eqs = [x for x in (eq_open, eq_close) if _ok(x)]
    if not eqs or not _ok(pat) or min(eqs) <= 0:
        return NAN
    return pat / (sum(eqs) / len(eqs))


def free_cash_flow(ocf, capex):
    """FCF = OCF - capex. Capex is stored as a positive outflow."""
    if not _ok(ocf, capex):
        return NAN
    return ocf - abs(capex)


def fcf_conversion(fcf, ebitda):
    """FCF / EBITDA; only meaningful for positive EBITDA."""
    if not _ok(fcf, ebitda) or ebitda <= 0:
        return NAN
    return fcf / ebitda


def net_debt(debt, cash):
    if not _ok(debt):
        return NAN
    return debt - (cash if _ok(cash) else 0.0)


def net_debt_to_ebitda(nd, ebitda):
    """Undefined for EBITDA <= 0 (the leverage engine treats that case as distressed if net debt > 0)."""
    if not _ok(nd, ebitda) or ebitda <= 0:
        return NAN
    return nd / ebitda


def interest_coverage(ebit, interest):
    if not _ok(ebit):
        return NAN
    if not _ok(interest) or interest == 0:
        return ICR_CAP if ebit > 0 else NAN
    return min(ebit / abs(interest), ICR_CAP)


def debt_to_equity(debt, equity):
    if not _ok(debt, equity) or equity <= 0:
        return NAN
    return debt / equity


def fcf_to_debt(fcf, debt):
    if not _ok(fcf, debt):
        return NAN
    if debt == 0:
        return NAN  # no debt: metric not meaningful, leverage handled by other sub-metrics
    return fcf / debt


def days(balance, flow):
    """Balance / annual flow * 365."""
    if not _ok(balance, flow) or flow <= 0:
        return NAN
    return balance / flow * 365


def ccc(dso, dio, dpo):
    parts = [dso, dio if _ok(dio) else 0.0, dpo if _ok(dpo) else 0.0]
    if not _ok(dso):
        return NAN
    return parts[0] + parts[1] - parts[2]


def not_meaningful_reason(metric: str, row: pd.Series) -> str | None:
    """Plain-language explanation for a NaN metric."""
    e, eq, rev = row.get("ebitda"), row.get("total_equity"), row.get("revenue")
    if metric in {"net_debt_to_ebitda", "fcf_conversion", "ev_ebitda"} and _ok(e) and e <= 0:
        return "EBITDA is negative or zero"
    if metric in {"roe", "debt_to_equity"} and _ok(eq) and eq <= 0:
        return "Equity is negative"
    if metric.startswith("revenue_cagr") or metric.startswith("ebitda_cagr") or metric.startswith("eps_cagr"):
        return "Insufficient history or non-positive base year"
    if metric == "roic":
        return "Invested capital is zero/negative or EBIT missing"
    if metric in {"dso", "dio", "dpo"} and not _ok(rev):
        return "Revenue missing"
    return None


# ---------------------------------------------------------------- per-company time series
def compute_company_metrics(fin: pd.DataFrame) -> pd.DataFrame:
    """Compute all metrics for one company's annual statements (any order). Returns one row per FY."""
    f = fin.sort_values("fiscal_year").reset_index(drop=True).copy()
    out = pd.DataFrame({"company_id": f["company_id"], "fiscal_year": f["fiscal_year"]})
    arr = {c: pd.to_numeric(f[c], errors="coerce").to_numpy(dtype=float) for c in f.columns
           if c not in {"company_id", "fiscal_year", "period_end", "filing_date", "source", "ingested_at"}}
    n_rows = len(f)

    def g(col, i):
        return float(arr[col][i]) if col in arr and 0 <= i < n_rows else NAN
    fy = f["fiscal_year"].tolist()

    def back(i, n):  # index of the year exactly n fiscal years earlier, if present
        target = fy[i] - n
        return fy.index(target) if target in fy else -1

    rows = []
    for i in range(len(f)):
        p1, p3, p5 = back(i, 1), back(i, 3), back(i, 5)
        rev, ebitda, ebit, pat = g("revenue", i), g("ebitda", i), g("ebit", i), g("pat", i)
        fcf = g("free_cash_flow", i)
        if not _ok(fcf):
            fcf = free_cash_flow(g("operating_cash_flow", i), g("capex", i))
        nd = net_debt(g("total_debt", i), g("cash", i))
        ic_close = invested_capital(g("total_equity", i), g("total_debt", i), g("cash", i))
        ic_open = invested_capital(g("total_equity", p1), g("total_debt", p1), g("cash", p1)) if p1 >= 0 else NAN
        cogs = g("cost_of_revenue", i)
        cogs = cogs if _ok(cogs) and cogs > 0 else NAN
        dso_v = days(g("receivables", i), rev)
        dio_v = days(g("inventory", i), cogs if _ok(cogs) else rev)
        dpo_v = days(g("payables", i), cogs if _ok(cogs) else rev)
        r = {
            "revenue": rev, "ebitda": ebitda, "pat": pat, "free_cash_flow": fcf,
            "operating_cash_flow": g("operating_cash_flow", i),
            "total_debt": g("total_debt", i), "receivables": g("receivables", i),
            "revenue_growth": growth(g("revenue", p1), rev) if p1 >= 0 else NAN,
            "revenue_cagr_3y": cagr(g("revenue", p3), rev, 3) if p3 >= 0 else NAN,
            "revenue_cagr_5y": cagr(g("revenue", p5), rev, 5) if p5 >= 0 else NAN,
            "ebitda_growth": growth(g("ebitda", p1), ebitda) if p1 >= 0 else NAN,
            "ebitda_cagr_3y": cagr(g("ebitda", p3), ebitda, 3) if p3 >= 0 else NAN,
            "eps_cagr_3y": cagr(g("eps", p3), g("eps", i), 3) if p3 >= 0 else NAN,
            "pat_growth": growth(g("pat", p1), pat) if p1 >= 0 else NAN,
            "receivables_growth": growth(g("receivables", p1), g("receivables", i)) if p1 >= 0 else NAN,
            "debt_growth": growth(g("total_debt", p1), g("total_debt", i)) if p1 >= 0 else NAN,
            "ebitda_margin": safe_div(ebitda, rev) if _ok(rev) and rev > 0 else NAN,
            "ebit_margin": safe_div(ebit, rev) if _ok(rev) and rev > 0 else NAN,
            "pat_margin": safe_div(pat, rev) if _ok(rev) and rev > 0 else NAN,
            "roic": roic(nopat(ebit, g("tax_expense", i), g("pretax_income", i)), ic_open, ic_close),
            "roe": roe(pat, g("total_equity", p1) if p1 >= 0 else NAN, g("total_equity", i)),
            "fcf_margin": safe_div(fcf, rev) if _ok(rev) and rev > 0 else NAN,
            "fcf_conversion": fcf_conversion(fcf, ebitda),
            "ocf_to_ebitda": fcf_conversion(g("operating_cash_flow", i), ebitda),
            "net_debt": nd,
            "net_debt_to_ebitda": net_debt_to_ebitda(nd, ebitda),
            "interest_coverage": interest_coverage(ebit, g("interest_expense", i)),
            "debt_to_equity": debt_to_equity(g("total_debt", i), g("total_equity", i)),
            "fcf_to_debt": fcf_to_debt(fcf, g("total_debt", i)),
            "dso": dso_v, "dio": dio_v, "dpo": dpo_v, "cash_conversion_cycle": ccc(dso_v, dio_v, dpo_v),
            "dio_dpo_basis": "COGS" if _ok(cogs) else "Revenue (COGS unavailable)",
        }
        rows.append(r)
    out = pd.concat([out, pd.DataFrame(rows)], axis=1)
    return out


def trailing_stats(metrics: pd.DataFrame) -> dict:
    """Stability/direction statistics over the latest window, used by scoring and red flags."""
    mm = metrics.sort_values("fiscal_year")
    last5 = mm.tail(5)

    def change(col, n=3):
        s = mm[col].dropna()
        if len(s) < 2:
            return NAN
        fy = mm.loc[s.index, "fiscal_year"]
        latest_fy = fy.iloc[-1]
        base = s[fy == latest_fy - n]
        return float(s.iloc[-1] - base.iloc[0]) if len(base) else NAN

    def slope(col):
        s = last5[[col, "fiscal_year"]].dropna()
        if len(s) < 3:
            return NAN
        return float(np.polyfit(s["fiscal_year"], s[col], 1)[0])

    def std(col, window):
        s = window[col].dropna()
        return float(s.std(ddof=0)) if len(s) >= 3 else NAN

    e = mm.tail(3)
    ocf = e["operating_cash_flow"].sum(min_count=1)
    eb = e["ebitda"].sum(min_count=1)
    ocf_ebitda_3y = safe_div(ocf, eb) if _ok(eb) and eb > 0 else NAN

    return {
        "revenue_growth_volatility": std("revenue_growth", last5),
        "ebitda_margin_volatility": std("ebitda_margin", last5),
        "ebitda_margin_change_3y": change("ebitda_margin"),
        "ebitda_margin_slope": slope("ebitda_margin"),
        "roic_change_3y": change("roic"),
        "ccc_change_3y": change("cash_conversion_cycle"),
        "dso_change_3y": change("dso"),
        "dio_change_3y": change("dio"),
        "ocf_to_ebitda_3y": ocf_ebitda_3y,
        "positive_fcf_years_5y": float((last5["free_cash_flow"] > 0).sum()) if len(last5) else NAN,
        "years_of_history": int(mm["revenue"].notna().sum()),
    }


def compute_all(financials: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (full metric history, latest-year snapshot with trailing statistics)."""
    hist, snaps = [], []
    for cid, grp in financials.groupby("company_id"):
        mm = compute_company_metrics(grp)
        hist.append(mm)
        latest = mm.iloc[-1].to_dict()
        prev = mm.iloc[-2].to_dict() if len(mm) > 1 else {}
        latest.update(trailing_stats(mm))
        latest.update({f"prev_{k}": v for k, v in prev.items() if k not in {"company_id"}})
        snaps.append(latest)
    if not hist:
        return pd.DataFrame(), pd.DataFrame()
    return pd.concat(hist, ignore_index=True), pd.DataFrame(snaps)
