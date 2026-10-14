"""Portfolio construction: pick the strongest names in each cap segment, size them under explicit constraints,
convert to whole shares, and measure the risk of the result.

Constraints (all editable): names per segment, minimum weight per segment, maximum position, maximum sector.
Sizing is a small least-squares problem: stay as close as possible to the target weights (score-, equal- or
inverse-volatility-proportional) subject to those constraints. If the constraints cannot all hold, the result says so
and which one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.portfolio.metrics import MONTHS_PER_YEAR, month_returns

WEIGHTINGS = ["equal", "score", "inverse_volatility"]


@dataclass
class Params:
    capital: float = 1e7
    names_per_segment: dict = field(default_factory=lambda: {"large": 5, "mid": 5, "small": 4})
    min_segment_weight: dict = field(default_factory=lambda: {"large": 0.30, "mid": 0.20, "small": 0.10})
    max_position: float = 0.12
    max_sector: float = 0.30
    weighting: str = "score"
    min_freq: float = 0.0          # optionally require selection frequency >= this (robust picks only)


def select_names(scored: pd.DataFrame, p: Params, freq: pd.Series | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Top-k by class score within each segment. Sector concentration is limited at selection time to
    floor(max_sector x total names) names per sector, so the weight cap is reachable afterwards."""
    notes: list[str] = []
    total = sum(p.names_per_segment.values())
    max_per_sector = max(1, int(math.floor(p.max_sector * total)))
    chosen, sector_count = [], {}
    pool = scored[scored.eligible & scored.class_score.notna()].copy()
    if freq is not None and p.min_freq > 0:
        pool = pool[pool.company_id.map(freq).fillna(0) >= p.min_freq]
    for seg, k in p.names_per_segment.items():
        cand = pool[pool.cap_segment == seg].sort_values("class_score", ascending=False)
        got = 0
        for _, r in cand.iterrows():
            if got >= k:
                break
            if sector_count.get(r.sector, 0) >= max_per_sector:
                continue
            chosen.append(r)
            sector_count[r.sector] = sector_count.get(r.sector, 0) + 1
            got += 1
        if got < k:
            notes.append(f"Only {got} of {k} requested {seg}-cap names were available after eligibility and sector limits.")
    return (pd.DataFrame(chosen).reset_index(drop=True) if chosen else pd.DataFrame()), notes


def target_weights(sel: pd.DataFrame, weighting: str) -> np.ndarray:
    if weighting == "equal":
        raw = np.ones(len(sel))
    elif weighting == "score":
        raw = sel.class_score.to_numpy(float)
    elif weighting == "inverse_volatility":
        sd = sel.sd.to_numpy(float)
        sd = np.where(np.isfinite(sd) & (sd > 0), sd, np.nanmedian(sd[np.isfinite(sd)]) if np.isfinite(sd).any() else 1.0)
        raw = 1.0 / sd
    else:
        raise ValueError(f"Unknown weighting {weighting!r}")
    return raw / raw.sum()


def size_positions(sel: pd.DataFrame, p: Params) -> tuple[np.ndarray, list[str]]:
    """Weights minimising squared distance to the target subject to the constraints. Returns (weights, notes)."""
    n = len(sel)
    notes: list[str] = []
    tgt = target_weights(sel, p.weighting)
    segs, sectors = sel.cap_segment.to_numpy(), sel.sector.to_numpy()
    present = [s for s in p.min_segment_weight if (segs == s).any()]
    missing = [s for s in p.min_segment_weight if s not in present and p.min_segment_weight[s] > 0]
    if missing:
        notes.append(f"No {', '.join(missing)}-cap names selected, so its minimum weight cannot apply.")
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    for s in present:
        m = (segs == s).astype(float)
        cons.append({"type": "ineq", "fun": lambda w, m=m, f=p.min_segment_weight[s]: m @ w - f})
    for sec in set(sectors):
        m = (sectors == sec).astype(float)
        cons.append({"type": "ineq", "fun": lambda w, m=m: p.max_sector - m @ w})
    cap = max(p.max_position, 1.0 / n)          # a cap below 1/n is arithmetically impossible
    if cap > p.max_position:
        notes.append(f"Maximum position {p.max_position:.0%} is below 1/{n}; raised to {cap:.1%}.")
    res = minimize(lambda w: float(((w - tgt) ** 2).sum()), x0=tgt, jac=lambda w: 2 * (w - tgt),
                   bounds=[(0.0, cap)] * n, constraints=cons, method="SLSQP", options={"maxiter": 500, "ftol": 1e-12})
    w = np.clip(res.x, 0, None)
    if not res.success or abs(w.sum() - 1) > 1e-6:
        notes.append("Constraints could not all be met at once; showing the target weights. Loosen a cap or a floor.")
        return tgt, notes
    w = w / w.sum()
    w[w < 1e-4] = 0.0
    return w / w.sum(), notes


def round_to_shares(weights: np.ndarray, prices: np.ndarray, capital: float) -> tuple[np.ndarray, float]:
    """Whole shares at the latest price. Round down, then spend the remainder one share at a time on the position
    furthest below its target that still fits. Returns (shares, cash left)."""
    prices = np.asarray(prices, float)
    ok = np.isfinite(prices) & (prices > 0)
    shares = np.where(ok, np.floor(weights * capital / np.where(ok, prices, 1.0)), 0).astype(int)
    cash = capital - float((shares * np.where(ok, prices, 0)).sum())
    while True:
        gap = weights * capital - shares * np.where(ok, prices, 0)
        fits = ok & (prices <= cash + 1e-9)
        if not fits.any():
            break
        i = int(np.argmax(np.where(fits, gap / np.where(ok, prices, 1.0), -np.inf)))
        if gap[i] <= 0:      # everything is at or above target: stop rather than overshoot
            break
        shares[i] += 1
        cash -= prices[i]
    return shares, cash


def returns_matrix(prices: pd.DataFrame, company_ids: list, months: int = 60) -> pd.DataFrame:
    cols = {}
    for cid in company_ids:
        g = prices[prices.company_id == cid]
        if len(g):
            cols[cid] = month_returns(g.set_index("date")["share_price"])
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).dropna().tail(months)


def portfolio_risk(weights: pd.Series, rets: pd.DataFrame, bench_ret: pd.Series | None = None) -> dict:
    """weights indexed by company_id. Uses the covariance of monthly returns over the common window.
    SD annualised x sqrt(12). Beta against the benchmark if available. Constant weights = monthly rebalancing."""
    ids = [c for c in weights.index if c in rets.columns]
    out = {"months": len(rets), "sd": np.nan, "beta": np.nan, "weighted_avg_sd": np.nan, "diversification": np.nan,
           "ann_return": np.nan, "max_drawdown": np.nan, "path": pd.Series(dtype=float)}
    if len(ids) < 2 or len(rets) < 12:
        return out
    w = weights.loc[ids].to_numpy(float)
    w = w / w.sum()
    R = rets[ids]
    cov = R.cov().to_numpy() * MONTHS_PER_YEAR
    sd_i = np.sqrt(np.diag(cov))
    port = R @ w
    out["sd"] = float(np.sqrt(w @ cov @ w))
    out["weighted_avg_sd"] = float(w @ sd_i)
    out["diversification"] = 1 - out["sd"] / out["weighted_avg_sd"] if out["weighted_avg_sd"] > 0 else np.nan
    out["ann_return"] = float((1 + port).prod() ** (MONTHS_PER_YEAR / len(port)) - 1)
    path = (1 + port).cumprod()
    out["path"] = path
    out["max_drawdown"] = float((path / path.cummax() - 1).min())
    if bench_ret is not None and len(bench_ret):
        j = pd.concat([port.rename("p"), bench_ret.rename("m")], axis=1, join="inner").dropna()
        if len(j) >= 12 and j.m.var() > 0:
            out["beta"] = float(j.p.cov(j.m) / j.m.var())
    return out


def build_portfolio(scored: pd.DataFrame, prices: pd.DataFrame, bench_ret: pd.Series, p: Params,
                    freq: pd.Series | None = None) -> dict:
    sel, notes = select_names(scored, p, freq)
    if sel.empty:
        return {"holdings": pd.DataFrame(), "notes": notes + ["No eligible names."], "risk": {}, "cash": p.capital}
    w, n2 = size_positions(sel, p)
    notes += n2
    shares, cash = round_to_shares(w, sel.last_price.to_numpy(float), p.capital)
    h = sel[["company_id", "company_name", "ticker", "sector", "cap_segment", "class_score", "market_cap",
             "last_price", "beta", "sd", "pe", "roe"]].copy()
    h["target_weight"] = w
    h["shares"] = shares
    h["invested"] = shares * h.last_price
    h["weight"] = h.invested / p.capital
    h["rank_in_segment"] = sel["rank_in_segment"].to_numpy() if "rank_in_segment" in sel else np.nan
    if freq is not None:
        h["selection_frequency"] = h.company_id.map(freq)
    h = h.sort_values(["cap_segment", "class_score"], ascending=[True, False]).reset_index(drop=True)
    rets = returns_matrix(prices, h.company_id.tolist())
    risk = portfolio_risk(h.set_index("company_id")["weight"], rets, bench_ret)
    wavg_beta = float((h.weight * h.beta).sum() / h.weight[h.beta.notna()].sum()) if h.beta.notna().any() else np.nan
    risk["weighted_avg_beta"] = wavg_beta
    seg_w = h.groupby("cap_segment").weight.sum()
    for s, fl in p.min_segment_weight.items():
        if s in seg_w.index and seg_w[s] < fl - 0.005:
            notes.append(f"After whole-share rounding the {s}-cap weight is {seg_w[s]:.1%}, a little under the {fl:.0%} floor.")
    return {"holdings": h, "notes": notes, "risk": risk, "cash": cash, "invested": float(h.invested.sum()),
            "segment_weights": seg_w, "sector_weights": h.groupby("sector").weight.sum().sort_values(ascending=False)}
