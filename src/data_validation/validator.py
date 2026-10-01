"""Validation applied to every batch before it reaches PostgreSQL, and per-company quality grades.

Checks: data types, missing critical fields, duplicates, unit normalisation, fiscal-year consistency,
accounting-identity outliers and implausible YoY jumps. Errors block the row; warnings are stored in
`data_issues` and feed the company's data-quality grade.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from src.database.models import FINANCIAL_FIELDS

CRITICAL_FIELDS = ["revenue", "ebitda", "ebit", "pat", "operating_cash_flow", "capex", "total_equity",
                   "total_debt", "cash"]
SECONDARY_FIELDS = ["receivables", "inventory", "payables", "interest_expense", "cost_of_revenue", "eps",
                    "tax_expense", "pretax_income"]
MAX_PLAUSIBLE_REVENUE_CR = 2_000_000  # ~₹20 lakh crore; above this the value is almost certainly in rupees


def _issue(cid, fy, check, sev, detail):
    return {"company_id": cid, "fiscal_year": fy, "check_name": check, "severity": sev, "detail": detail}


def validate_financials(df: pd.DataFrame, fy_end_month: int = 3) -> tuple[pd.DataFrame, list[dict]]:
    """Return (clean rows, issues). Rows with blocking errors are removed."""
    issues: list[dict] = []
    if df.empty:
        return df, issues
    df = df.copy()

    # Types
    for c in FINANCIAL_FIELDS:
        if c in df:
            coerced = pd.to_numeric(df[c], errors="coerce")
            bad = df[c].notna() & coerced.isna()
            for _, r in df[bad].iterrows():
                issues.append(_issue(r.company_id, r.fiscal_year, "data_type", "warn", f"{c} not numeric: {r[c]!r}"))
            df[c] = coerced
        else:
            df[c] = np.nan
    df["fiscal_year"] = pd.to_numeric(df["fiscal_year"], errors="coerce").astype("Int64")

    # Duplicates inside the batch
    dup = df.duplicated(["company_id", "fiscal_year"], keep="last")
    for _, r in df[dup].iterrows():
        issues.append(_issue(r.company_id, int(r.fiscal_year), "duplicate", "warn",
                             "Duplicate company/fiscal-year in batch; kept the last occurrence"))
    df = df[~dup]

    # Unit normalisation: values reported in rupees instead of crore
    for idx, r in df.iterrows():
        if pd.notna(r.revenue) and r.revenue > MAX_PLAUSIBLE_REVENUE_CR:
            df.loc[idx, FINANCIAL_FIELDS] = df.loc[idx, FINANCIAL_FIELDS].astype(float) / 1e7
            df.loc[idx, "eps"] = r.eps  # EPS is per share, not scaled
            issues.append(_issue(r.company_id, int(r.fiscal_year), "unit_normalisation", "info",
                                 "Values looked like rupees; converted to ₹ crore"))

    # Fiscal year consistency
    keep = pd.Series(True, index=df.index)
    for idx, r in df.iterrows():
        pe = pd.to_datetime(r.get("period_end"), errors="coerce")
        if pd.isna(r.fiscal_year) or r.fiscal_year < 1990 or r.fiscal_year > dt.date.today().year + 1:
            issues.append(_issue(r.company_id, None, "fiscal_year", "error", f"Invalid fiscal year {r.fiscal_year}"))
            keep[idx] = False
            continue
        if pd.notna(pe):
            if pe.month != fy_end_month:
                issues.append(_issue(r.company_id, int(r.fiscal_year), "fiscal_year", "warn",
                                     f"Period ends in month {pe.month}, expected {fy_end_month} (non-March year end "
                                     "or transition period; peer comparisons may be misaligned)"))
            expected_fy = pe.year if pe.month <= fy_end_month else pe.year + 1
            if expected_fy != r.fiscal_year:
                issues.append(_issue(r.company_id, int(r.fiscal_year), "fiscal_year", "error",
                                     f"Fiscal year {r.fiscal_year} inconsistent with period end {pe.date()}"))
                keep[idx] = False
    df = df[keep]

    # Missing values
    for _, r in df.iterrows():
        missing = [c for c in CRITICAL_FIELDS if pd.isna(r[c])]
        if missing:
            issues.append(_issue(r.company_id, int(r.fiscal_year), "missing_critical", "warn",
                                 f"Missing: {', '.join(missing)}"))

    # Accounting-identity outliers
    for _, r in df.iterrows():
        cid, fy = r.company_id, int(r.fiscal_year)
        if pd.notna(r.ebit) and pd.notna(r.ebitda) and r.ebit > r.ebitda + 0.01 * abs(r.ebitda) + 0.5:
            issues.append(_issue(cid, fy, "outlier", "warn", "EBIT exceeds EBITDA (negative D&A?)"))
        if pd.notna(r.ebitda) and pd.notna(r.revenue) and r.revenue > 0 and r.ebitda / r.revenue > 0.9:
            issues.append(_issue(cid, fy, "outlier", "warn", f"EBITDA margin {r.ebitda / r.revenue:.0%} is implausible"))
        if pd.notna(r.revenue) and r.revenue < 0:
            issues.append(_issue(cid, fy, "outlier", "warn", "Negative revenue"))
        if pd.notna(r.total_assets) and pd.notna(r.total_debt) and r.total_assets > 0 and r.total_debt > 1.5 * r.total_assets:
            issues.append(_issue(cid, fy, "outlier", "warn", "Debt exceeds 150% of total assets"))

    # Implausible YoY jumps (likely unit or restatement problems)
    for cid, g in df.sort_values("fiscal_year").groupby("company_id"):
        rev = g.set_index("fiscal_year")["revenue"]
        ratio = rev / rev.shift(1)
        for fy, x in ratio.items():
            if pd.notna(x) and (x > 5 or x < 0.2):
                issues.append(_issue(cid, int(fy), "outlier", "warn",
                                     f"Revenue changed {x:.1f}x YoY — check units, mergers or restatement"))
    return df, issues


def grade_data_quality(fin: pd.DataFrame, issues: pd.DataFrame, has_market: set[int]) -> pd.DataFrame:
    """High / Medium / Low per company, with the reasons that produced the grade."""
    rows = []
    for cid, g in fin.groupby("company_id"):
        years = g["revenue"].notna().sum()
        recent = g.sort_values("fiscal_year").tail(5)
        crit = recent[CRITICAL_FIELDS].notna().mean().mean()
        sec = recent[SECONDARY_FIELDS].notna().mean().mean()
        ci = issues[issues.company_id == cid] if not issues.empty else issues
        n_warn = int((ci.severity == "warn").sum()) if not ci.empty else 0
        est = bool(g.get("filing_date_estimated", pd.Series([True])).all())
        reasons = []
        if years < 5:
            reasons.append(f"{years} years of history")
        if crit < 0.95:
            reasons.append(f"critical fields {crit:.0%} complete")
        if sec < 0.75:
            reasons.append(f"working-capital/secondary fields {sec:.0%} complete")
        latest_sec = recent.tail(1)[SECONDARY_FIELDS].notna().mean(axis=1).iloc[0]
        if latest_sec < 0.75:
            reasons.append(f"latest year secondary fields {latest_sec:.0%} complete")
        if cid not in has_market:
            reasons.append("no market data")
        if n_warn > 3:
            reasons.append(f"{n_warn} validation warnings")
        if years >= 5 and crit >= 0.95 and sec >= 0.75 and latest_sec >= 0.75 and cid in has_market and n_warn <= 3:
            grade = "High"
        elif years >= 3 and crit >= 0.80 and cid in has_market:
            grade = "Medium"
        else:
            grade = "Low"
        rows.append({"company_id": cid, "data_quality": grade, "years_of_history": int(years),
                     "critical_completeness": crit, "secondary_completeness": sec, "validation_warnings": n_warn,
                     "filing_dates_estimated": est, "quality_reasons": "; ".join(reasons) or "Complete"})
    return pd.DataFrame(rows)
