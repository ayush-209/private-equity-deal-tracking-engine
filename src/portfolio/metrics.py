"""Class-basis metrics: ROCE, P/BV, PEG, EPS growth, beta, standard deviation, technicals, cap segments.

Same rules as src/financial_metrics: pure functions, NaN (never a fabricated number) when a metric is not
meaningful, and a plain-language reason available for the NaN.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.financial_metrics.metrics import NAN, _ok, cagr, growth, safe_div

MONTHS_PER_YEAR = 12


# ---------------------------------------------------------------- fundamentals
def capital_employed(total_assets, current_liabilities):
    """Total assets - current liabilities. Undefined if either is missing or the result is not positive."""
    if not _ok(total_assets, current_liabilities):
        return NAN
    ce = total_assets - current_liabilities
    return ce if ce > 0 else NAN


def roce(ebit, ce_open, ce_close):
    """EBIT / average capital employed (closing only if the opening figure is missing)."""
    ces = [x for x in (ce_open, ce_close) if _ok(x)]
    if not ces or not _ok(ebit):
        return NAN
    return ebit / (sum(ces) / len(ces))


def roce_approx(ebit, equity, debt):
    """Fallback when current liabilities are not available: EBIT / (equity + debt), closing balances.
    Overstates capital employed relative to the textbook definition, so it understates ROCE. Labelled in the UI."""
    if not _ok(ebit, equity, debt) or equity + debt <= 0:
        return NAN
    return ebit / (equity + debt)


def price_to_book(market_cap, equity):
    if not _ok(market_cap, equity) or equity <= 0:
        return NAN
    return market_cap / equity


def peg_ratio(pe, eps_growth):
    """P/E divided by earnings growth in percent (growth 0.25 -> 25). Not meaningful for growth <= 0."""
    if not _ok(pe, eps_growth) or pe <= 0 or eps_growth <= 0:
        return NAN
    return pe / (eps_growth * 100.0)


def eps_growth_measure(eps_hist: dict[int, float], pat_hist: dict[int, float], fy: int,
                       window: int = 3, sanity_gap: float = 0.5) -> tuple[float, str]:
    """Earnings growth for scoring and PEG: CAGR of EPS over `window` years, else YoY if the history is short.

    EPS from free sources is not always adjusted for splits/bonus issues, which fabricates growth. As a guard, the EPS
    figure is compared with growth in profit after tax over the same span; if they disagree by more than
    `sanity_gap` (50 percentage points of annualised growth) the PAT-based figure is used and labelled.
    Returns (growth, basis) where basis says which series was used.
    """
    def span(series, n):
        a, b = series.get(fy - n), series.get(fy)
        return cagr(a, b, n) if n > 1 else growth(a, b)

    for n in (window, 1):
        e = span(eps_hist, n)
        p = span(pat_hist, n)
        if _ok(e):
            if _ok(p) and abs(e - p) > sanity_gap:
                return p, f"PAT {n}y (EPS disagreed: possible split/bonus)"
            return e, f"EPS {n}y" if n > 1 else "EPS YoY"
        if _ok(p) and n == 1:
            return p, "PAT YoY (EPS missing)"
    return NAN, "n.m."


def fundamentals_snapshot(fin: pd.DataFrame) -> pd.DataFrame:
    """One row per company from the latest-version annual statements.
    Columns: company_id, fiscal_year, sales_yoy, eps_growth, eps_basis, roe, roce, roce_basis, pat, ebit, total_equity."""
    rows = []
    for cid, g in fin.groupby("company_id"):
        g = g.sort_values("fiscal_year")
        by = g.set_index("fiscal_year")
        fy = int(by.index.max())
        cur = by.loc[fy]
        prev = by.loc[fy - 1] if (fy - 1) in by.index else None
        has_cl = "current_liabilities" in by.columns

        def ce(row):
            return capital_employed(row.get("total_assets"), row.get("current_liabilities")) if (has_cl and row is not None) else NAN

        ce_c, ce_o = ce(cur), ce(prev) if prev is not None else NAN
        if _ok(ce_c):
            r_ce, basis = roce(cur.get("ebit"), ce_o, ce_c), "EBIT / avg(total assets - current liabilities)"
        else:
            r_ce, basis = roce_approx(cur.get("ebit"), cur.get("total_equity"), cur.get("total_debt")), \
                "approx: EBIT / (equity + debt), current liabilities unavailable"
        eg, ebasis = eps_growth_measure(by["eps"].dropna().to_dict(), by["pat"].dropna().to_dict(), fy)
        eq = [x for x in (prev.get("total_equity") if prev is not None else NAN, cur.get("total_equity")) if _ok(x)]
        roe_v = NAN if (not eq or min(eq) <= 0 or not _ok(cur.get("pat"))) else cur["pat"] / (sum(eq) / len(eq))
        rows.append({"company_id": cid, "fiscal_year": fy,
                     "sales_yoy": growth(prev.get("revenue") if prev is not None else NAN, cur.get("revenue")),
                     "eps_growth": eg, "eps_basis": ebasis, "roe": roe_v, "roce": r_ce,
                     "roce_basis": basis if _ok(r_ce) else "n.m.",
                     "pat": cur.get("pat"), "ebit": cur.get("ebit"), "total_equity": cur.get("total_equity")})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- cap segments
def classify_cap(market_cap: pd.Series, segments: dict) -> pd.DataFrame:
    """Rank by market cap (1 = largest) and map to segments by rank, AMFI-style. Companies without a market cap
    get no rank and no segment."""
    rank = market_cap.rank(ascending=False, method="first")
    seg = pd.Series(pd.NA, index=market_cap.index, dtype="object")
    for name, b in segments.items():
        seg[(rank >= b["rank_from"]) & (rank <= b["rank_to"])] = name
    return pd.DataFrame({"mcap_rank": rank, "cap_segment": seg})


# ---------------------------------------------------------------- price-based
def month_returns(prices: pd.Series) -> pd.Series:
    """Simple monthly returns from a close series indexed by date. Duplicate months collapse to the last close."""
    s = prices.dropna().copy()
    s.index = pd.PeriodIndex(pd.to_datetime(s.index), freq="M")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.pct_change(fill_method=None).dropna()


def beta_and_sd(stock_ret: pd.Series, bench_ret: pd.Series, min_months: int = 36, max_months: int = 60) -> dict:
    """Beta = cov(stock, market) / var(market) over the common months (latest `max_months`); needs `min_months`.
    SD = sample standard deviation of the stock's monthly returns x sqrt(12), over the same window."""
    j = pd.concat([stock_ret.rename("s"), bench_ret.rename("m")], axis=1, join="inner").dropna().tail(max_months)
    n = len(j)
    out = {"n_months": n, "beta": NAN, "sd": NAN, "corr_mkt": NAN}
    if n < min_months:
        return out
    var_m = j.m.var(ddof=1)
    if not var_m or math.isnan(var_m) or var_m <= 0:
        return out
    out["beta"] = float(j.s.cov(j.m) / var_m)
    out["sd"] = float(j.s.std(ddof=1) * math.sqrt(MONTHS_PER_YEAR))
    out["corr_mkt"] = float(j.s.corr(j.m))
    return out


def technicals(prices: pd.Series) -> dict:
    """Trend measures from monthly closes (latest observation = now):
    ret_6m    = P_t / P_{t-6} - 1
    mom_12_1  = P_{t-1} / P_{t-12} - 1   (12-month momentum skipping the latest month, to avoid short-term reversal)
    trend_10m = P_t / mean(last 10 monthly closes) - 1"""
    s = prices.dropna().copy()
    s.index = pd.PeriodIndex(pd.to_datetime(s.index), freq="M")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    out = {"ret_6m": NAN, "mom_12_1": NAN, "trend_10m": NAN}
    if len(s) >= 7:
        out["ret_6m"] = float(s.iloc[-1] / s.iloc[-7] - 1)
    if len(s) >= 13:
        out["mom_12_1"] = float(s.iloc[-2] / s.iloc[-13] - 1)
    if len(s) >= 10:
        out["trend_10m"] = float(s.iloc[-1] / s.iloc[-10:].mean() - 1)
    return out


def pick_benchmark(bench: pd.DataFrame, preference: list[str], min_months: int) -> tuple[pd.Series, str | None]:
    """First preferred symbol with enough history -> its monthly return series."""
    if bench is None or bench.empty:
        return pd.Series(dtype=float), None
    for sym in preference:
        d = bench[bench.symbol == sym]
        if len(d) > min_months:
            r = month_returns(d.set_index("date")["close"])
            if len(r) >= min_months:
                return r, sym
    return pd.Series(dtype=float), None


def price_metrics(panel: pd.DataFrame, bench_ret: pd.Series, min_months: int, max_months: int) -> pd.DataFrame:
    """panel: company_id, date, share_price. One row per company with beta, sd, n_months and technicals."""
    rows = []
    for cid, g in panel.groupby("company_id"):
        px = g.set_index("date")["share_price"]
        r = month_returns(px)
        bs = beta_and_sd(r, bench_ret, min_months, max_months) if len(bench_ret) else \
            {"n_months": len(r), "beta": NAN, "sd": NAN, "corr_mkt": NAN}
        if not len(bench_ret) and len(r) >= min_months:   # no index available: SD still computable
            bs["sd"] = float(r.tail(max_months).std(ddof=1) * math.sqrt(MONTHS_PER_YEAR))
        rows.append({"company_id": cid, **bs, **technicals(px), "last_price": float(px.dropna().iloc[-1]),
                     "last_price_date": pd.to_datetime(px.dropna().index[-1]).date()})
    return pd.DataFrame(rows)
