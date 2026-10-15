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
    exclude_ids: frozenset = frozenset()     # companies the user removed
    include_ids: frozenset = frozenset()     # companies the user insists on (picked first, ignoring rank and sector limit)


FLOOR_SLACK = 0.05


def derive_params(capital: float, n_stocks: int, mix: dict, max_position: float = 0.12, max_sector: float = 0.30,
                  weighting: str = "score", **kw) -> Params:
    """Everything follows from three choices: how many stocks, how to split capital across company sizes, and the
    two risk limits. Names per segment = n_stocks x mix (largest-remainder rounding, at least one name for any segment
    with a share). The minimum weight of a segment is its mix share minus 5 points, so sizing can lean a little toward
    better ideas without abandoning the mix the user chose."""
    mix = {s: max(float(v), 0.0) for s, v in mix.items()}
    tot = sum(mix.values())
    if tot <= 0 or n_stocks < 1:
        raise ValueError("Need at least one stock and a positive mix")
    mix = {s: v / tot for s, v in mix.items()}
    raw = {s: n_stocks * v for s, v in mix.items()}
    names = {s: int(raw[s]) for s in raw}
    for s in sorted(raw, key=lambda k: raw[k] - names[k], reverse=True):
        if sum(names.values()) >= n_stocks:
            break
        names[s] += 1
    for s, v in mix.items():
        if v > 0 and names[s] == 0:
            names[s] = 1
    floors = {s: max(0.0, v - FLOOR_SLACK) for s, v in mix.items()}
    return Params(capital=float(capital), names_per_segment={s: n for s, n in names.items() if n > 0},
                  min_segment_weight=floors, max_position=max_position, max_sector=max_sector, weighting=weighting, **kw)


def select_with_reasons(scored: pd.DataFrame, p: Params, freq: pd.Series | None = None,
                        extra_rows: int = 3) -> tuple[pd.DataFrame, list[str], pd.DataFrame]:
    """Top-k by class score within each segment, with a decision recorded for every candidate looked at.
    Order of rules: (1) names the user excluded are never considered; (2) names the user included are picked first;
    (3) the rest are taken best-first, skipping a name when its sector would exceed floor(max sector x number of names)
    names, or when it is not picked often enough across random weightings (if that filter is on).
    Returns (selected rows, notes, decisions) where decisions has one row per candidate shown."""
    notes: list[str] = []
    total = sum(p.names_per_segment.values())
    max_per_sector = max(1, int(math.floor(p.max_sector * total)))
    chosen, decisions, sector_count = [], [], {}
    base = scored[scored.eligible & scored.class_score.notna()]
    for seg, k in p.names_per_segment.items():
        seg_all = base[base.cap_segment == seg].sort_values("class_score", ascending=False)
        n_seg = len(seg_all)
        forced = seg_all[seg_all.company_id.isin(p.include_ids)]
        got, shown_after = 0, 0
        for _, r in forced.iterrows():
            chosen.append(r)
            sector_count[r.sector] = sector_count.get(r.sector, 0) + 1
            got += 1
            decisions.append(_decision(r, seg, "Picked", "Added by you", n_seg))
        for _, r in seg_all.iterrows():
            if r.company_id in p.include_ids:
                continue
            if r.company_id in p.exclude_ids:
                if got < k or shown_after < extra_rows:
                    decisions.append(_decision(r, seg, "Skipped", "Removed by you", n_seg))
                continue
            if got >= k:
                if shown_after < extra_rows:
                    decisions.append(_decision(r, seg, "Not picked", f"Ranks below the {k} places for {seg}-cap", n_seg))
                    shown_after += 1
                continue
            if sector_count.get(r.sector, 0) >= max_per_sector:
                decisions.append(_decision(r, seg, "Skipped",
                                           f"Sector limit: {r.sector} already has {sector_count[r.sector]} "
                                           f"(max {max_per_sector} for a {total}-stock portfolio)", n_seg))
                continue
            if freq is not None and p.min_freq > 0 and (freq.get(r.company_id, 0) or 0) < p.min_freq:
                decisions.append(_decision(r, seg, "Skipped",
                                           f"Picked in only {freq.get(r.company_id, 0):.0%} of random weightings "
                                           f"(you asked for {p.min_freq:.0%})", n_seg))
                continue
            chosen.append(r)
            sector_count[r.sector] = sector_count.get(r.sector, 0) + 1
            got += 1
            decisions.append(_decision(r, seg, "Picked", "Best remaining score in its size group", n_seg))
        if got < k:
            notes.append(f"Only {got} of {k} requested {seg}-cap names were available after eligibility and limits.")
    sel = pd.DataFrame(chosen).reset_index(drop=True) if chosen else pd.DataFrame()
    return sel, notes, pd.DataFrame(decisions)


def _decision(r, seg, status, reason, n_seg) -> dict:
    return {"company_id": r.company_id, "company_name": r.company_name, "ticker": r.ticker, "cap_segment": seg,
            "sector": r.sector, "rank": int(r.rank_in_segment) if pd.notna(r.get("rank_in_segment")) else None,
            "of": n_seg, "score": r.class_score, "decision": status, "reason": reason}


def select_names(scored: pd.DataFrame, p: Params, freq: pd.Series | None = None) -> tuple[pd.DataFrame, list[str]]:
    sel, notes, _ = select_with_reasons(scored, p, freq)
    return sel, notes


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
    sel, notes, decisions = select_with_reasons(scored, p, freq)
    if sel.empty:
        return {"holdings": pd.DataFrame(), "notes": notes + ["No eligible names."], "risk": {}, "cash": p.capital,
                "decisions": decisions}
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
            "decisions": decisions,
            "segment_weights": seg_w, "sector_weights": h.groupby("sector").weight.sum().sort_values(ascending=False)}
