"""Price tracking: value a set of holdings, show profit and loss, and compare with the index.

All functions take plain DataFrames so they can be tested without a network or a database.
`prices` is a wide frame: index = dates, columns = tickers, values = closing price.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def parse_download(raw: pd.DataFrame, tickers: list[str], suffix: str = ".NS") -> pd.DataFrame:
    """Turn a yfinance download frame into a wide close-price frame keyed by plain ticker (no .NS).
    Handles the multi-ticker (MultiIndex columns) and single-ticker (flat columns) shapes."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        if "Close" in lvl0:                      # (field, ticker) layout
            close = raw["Close"]
        else:                                    # (ticker, field) layout
            close = pd.DataFrame({t: raw[t]["Close"] for t in lvl0.unique() if "Close" in raw[t].columns})
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0] + suffix}) if "Close" in raw else pd.DataFrame()
    close = close.copy()
    close.columns = [str(c).removesuffix(suffix) for c in close.columns]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.dropna(how="all").sort_index()


def fetch_daily(tickers: list[str], start, suffix: str = ".NS", index_symbols=("^CRSLDX", "^NSEI")) -> pd.DataFrame:
    """Daily closes from Yahoo for the tickers and the index symbols. Empty frame on any failure (callers fall back)."""
    import os
    if os.getenv("PE_NO_LIVE"):
        return pd.DataFrame()
    try:
        import yfinance as yf
        syms = [t + suffix for t in tickers] + list(index_symbols)
        raw = yf.download(syms, start=pd.to_datetime(start).date().isoformat(), interval="1d", auto_adjust=False,
                          progress=False, group_by="ticker", threads=True)
        out = parse_download(raw, syms, suffix="")
        out.columns = [c.removesuffix(suffix) if not c.startswith("^") else c for c in out.columns]
        return out
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def monthly_fallback(panel: pd.DataFrame, id_to_ticker: dict, bench: pd.DataFrame | None = None) -> pd.DataFrame:
    """Wide monthly close frame from the stored price panel (company_id, date, share_price) and benchmark rows."""
    if panel.empty:
        return pd.DataFrame()
    p = panel[panel.company_id.isin(id_to_ticker)].copy()
    p["ticker"] = p.company_id.map(id_to_ticker)
    p["date"] = pd.to_datetime(p["date"])
    wide = p.pivot_table(index="date", columns="ticker", values="share_price", aggfunc="last")
    if bench is not None and not bench.empty:
        b = bench.copy()
        b["date"] = pd.to_datetime(b["date"])
        wide = wide.join(b.pivot_table(index="date", columns="symbol", values="close", aggfunc="last"), how="outer")
    return wide.sort_index()


def latest_prices(prices: pd.DataFrame) -> pd.Series:
    return prices.ffill().iloc[-1] if len(prices) else pd.Series(dtype=float)


def value_holdings(h: pd.DataFrame, last: pd.Series) -> pd.DataFrame:
    """Per-holding value and profit/loss. Needs ticker, shares, buy_price. `last` = latest price by ticker."""
    d = h.copy()
    d["price"] = d.ticker.map(last)
    d["cost"] = d.shares * d.buy_price
    d["value"] = d.shares * d.price
    d["pnl"] = d.value - d.cost
    d["pnl_pct"] = np.where(d.cost > 0, d.pnl / d.cost, np.nan)
    tot = d.value.sum(min_count=1)
    d["weight"] = d.value / tot if pd.notna(tot) and tot > 0 else np.nan
    return d


def summary(d: pd.DataFrame) -> dict:
    missing = int(d.price.isna().sum())
    cost = float(d.cost.sum())
    val = float(d.value.sum(min_count=1)) if d.value.notna().any() else float("nan")
    # holdings without a price are left out of value and cost so the totals compare like with like
    ok = d[d.price.notna()]
    c_ok = float(ok.cost.sum())
    v_ok = float(ok.value.sum()) if len(ok) else float("nan")
    return {"cost": cost, "value": val, "pnl": v_ok - c_ok if len(ok) else float("nan"),
            "pnl_pct": (v_ok / c_ok - 1) if c_ok > 0 and len(ok) else float("nan"), "n_missing": missing}


def value_path(h: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Portfolio value and amount invested through time. A holding counts from its buy date onward, valued at the
    latest available price on each date (forward-filled over holidays)."""
    cols = [t for t in h.ticker.unique() if t in prices.columns]
    if not cols:
        return pd.DataFrame(columns=["value", "invested"])
    start = pd.to_datetime(h.buy_date).min()
    px = prices[cols].sort_index().ffill()
    px = px[px.index >= start - pd.Timedelta(days=7)]
    value = pd.Series(0.0, index=px.index)
    invested = pd.Series(0.0, index=px.index)
    for _, r in h.iterrows():
        if r.ticker not in px.columns:
            continue
        on = px.index >= pd.to_datetime(r.buy_date)
        value = value + np.where(on, r.shares * px[r.ticker].fillna(0.0), 0.0)
        invested = invested + np.where(on, r.shares * r.buy_price, 0.0)
    out = pd.DataFrame({"value": value, "invested": invested})
    return out[out.invested > 0]


def rebase(series: pd.Series, to: float) -> pd.Series:
    s = series.dropna()
    return s / s.iloc[0] * to if len(s) else s


def pick_index(prices: pd.DataFrame, preference=("^CRSLDX", "^NSEI")) -> str | None:
    for sym in preference:
        if sym in prices.columns and prices[sym].notna().sum() > 5:
            return sym
    return None


def index_return_since(prices: pd.DataFrame, sym: str, start) -> float:
    s = prices[sym].dropna()
    s0 = s[s.index <= pd.to_datetime(start)]
    base = s0.iloc[-1] if len(s0) else (s.iloc[0] if len(s) else np.nan)
    return float(s.iloc[-1] / base - 1) if len(s) and base else float("nan")


def stock_stats(px: pd.Series) -> dict:
    """Summary of one price series (daily or monthly): last, changes over standard windows, 52-week range,
    drawdown from the peak, annualised volatility. Windows shorter than the history are returned as NaN."""
    s = px.dropna().sort_index()
    out = {"last": float("nan"), "last_date": None}
    if s.empty:
        return out
    out["last"], out["last_date"] = float(s.iloc[-1]), s.index[-1]
    for label, days in [("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("3Y", 1095), ("5Y", 1826)]:
        cutoff = s.index[-1] - pd.Timedelta(days=days)
        past = s[s.index <= cutoff]
        out[f"ret_{label}"] = float(s.iloc[-1] / past.iloc[-1] - 1) if len(past) else float("nan")
    yr = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    out["hi_52w"], out["lo_52w"] = float(yr.max()), float(yr.min())
    out["from_hi"] = float(s.iloc[-1] / out["hi_52w"] - 1)
    out["max_drawdown"] = float((s / s.cummax() - 1).min())
    gaps = s.index.to_series().diff().dt.days.dropna()
    per_year = TRADING_DAYS if len(gaps) and gaps.median() < 5 else 12
    r = s.pct_change().dropna()
    out["vol"] = float(r.std(ddof=1) * math.sqrt(per_year)) if len(r) >= 12 else float("nan")
    return out
