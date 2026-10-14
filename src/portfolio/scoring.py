"""Class-basis scoring: five dimensions, percentile-scored inside the market-cap segment.

Why inside the segment: a 40x P/E or a 60% annualised standard deviation is ordinary for a small-cap and extreme for a
large-cap. Scoring against same-size companies keeps the three cap segments comparable and each able to supply
candidates, which is the "representation across segments" requirement.

The weights are policy inputs (config/portfolio.yaml, each with its stated rationale). Because they are arbitrary
in the end, this module also measures how much they matter: rank_stability() re-ranks under alternative weight sets
and selection_frequency() draws random weight vectors around the base. A pick that survives both is not a product of
the weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import portfolio_config
from src.screening.engine import _percentile

SEGMENTS = ["large", "mid", "small"]


def dim_names(cfg: dict | None = None) -> list[str]:
    return list((cfg or portfolio_config())["dimensions"])


def normalise(weights: dict, names: list[str]) -> dict:
    w = {d: max(float(weights.get(d, 0)), 0.0) for d in names}
    t = sum(w.values())
    if t == 0:
        raise ValueError("All weights are zero")
    return {d: v / t * 100 for d, v in w.items()}


def prepare_inputs(f: pd.DataFrame) -> pd.DataFrame:
    """Values whose NaN means 'bad' rather than 'unknown' are set to +inf (worst for lower-is-better metrics),
    so they are ranked last instead of silently dropped."""
    x = f.copy()
    loss = x["pat"].notna() & (x["pat"] <= 0)
    x.loc[loss & x["market_cap"].notna(), "pe"] = np.inf
    pe_ok = x["pe"].notna() & np.isfinite(x["pe"])
    x.loc[pe_ok & x["eps_growth"].notna() & (x["eps_growth"] <= 0), "peg"] = np.inf    # no growth to pay for
    x.loc[x["total_equity"].notna() & (x["total_equity"] <= 0) & x["market_cap"].notna(), "pb"] = np.inf
    return x


def peer_groups(x: pd.DataFrame, min_peers: int) -> pd.Series:
    """Segment label; segments with fewer than `min_peers` members are pooled into 'ALL' so percentiles stay stable."""
    counts = x["cap_segment"].value_counts()
    return x["cap_segment"].map(lambda s: s if counts.get(s, 0) >= min_peers else "ALL")


def dimension_scores(f: pd.DataFrame, cfg: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Returns (per-dimension 0-1 scores, long table of sub-metric percentiles, peer-group label per company).
    A dimension is computed when at least half of its metrics are available."""
    cfg = cfg or portfolio_config()
    x = prepare_inputs(f)
    groups = peer_groups(x, cfg["eligibility"]["min_segment_peers"])
    dims = pd.DataFrame(index=x.index)
    detail = []
    for d, spec in cfg["dimensions"].items():
        pcts = pd.DataFrame(index=x.index)
        for sm in spec["metrics"]:
            col = sm["metric"]
            if col not in x:
                continue
            vals = x[col].replace([np.inf, -np.inf], [1e12, -1e12])
            p = _percentile(vals, groups, sm["direction"] == "higher")
            pcts[col] = p
            detail.append(pd.DataFrame({"company_id": x["company_id"], "dimension": d, "metric": col,
                                        "value": f[col] if col in f else np.nan, "percentile": p,
                                        "direction": sm["direction"]}))
        need = max(1, int(np.ceil(len(spec["metrics"]) / 2)))
        dims[d] = pcts.mean(axis=1).where(pcts.notna().sum(axis=1) >= need)
    return dims, (pd.concat(detail, ignore_index=True) if detail else pd.DataFrame()), groups


def total_score(dims: pd.DataFrame, weights: dict, min_dimensions: int = 4) -> pd.Series:
    """Weighted mean of available dimensions, 0-100. NaN if fewer than `min_dimensions` are computable, because a
    score built on two dimensions is not comparable with one built on five."""
    w = normalise(weights, list(dims.columns))
    avail = dims.notna()
    wsum = sum(avail[d] * w[d] for d in dims.columns)
    num = sum(dims[d].fillna(0) * w[d] for d in dims.columns)
    s = (num / wsum.replace(0, np.nan)) * 100
    return s.where(avail.sum(axis=1) >= min_dimensions)


def score_frame(f: pd.DataFrame, weights: dict | None = None, cfg: dict | None = None) -> pd.DataFrame:
    """Frame with metrics + cap_segment -> same rows with dimension scores, total, rank in segment."""
    cfg = cfg or portfolio_config()
    w = weights or {d: s["weight"] for d, s in cfg["dimensions"].items()}
    dims, _detail, groups = dimension_scores(f, cfg)
    out = f.copy()
    for d in dims.columns:
        out[f"{d}_pct"] = dims[d]
    out["class_score"] = total_score(dims, w, cfg["eligibility"]["min_dimensions"])
    out["score_peer_group"] = groups
    out["dims_available"] = dims.notna().sum(axis=1)
    out["rank_in_segment"] = out.groupby("cap_segment")["class_score"].rank(ascending=False, method="first")
    return out


# ---------------------------------------------------------------- how much do the weights matter?
def _top_set(scores: pd.Series, segment: pd.Series, k: int) -> set:
    return set(scores.dropna().sort_values(ascending=False).head(k).index)


def rank_stability(dims: pd.DataFrame, segment: pd.Series, cfg: dict | None = None,
                   min_dimensions: int = 4, top_n: int | None = None) -> pd.DataFrame:
    """Compare the base ranking with each alternative weight set.
    Rows: weight set x segment. spearman = rank correlation of total scores; overlap = share of the base top-n that
    stays in the alternative's top-n (n = top_n, or the segment size if smaller)."""
    cfg = cfg or portfolio_config()
    sets = cfg["weight_sets"]
    n = top_n or cfg["stability"]["top_n"]
    base = total_score(dims, sets["base"], min_dimensions)
    rows = []
    for name, w in sets.items():
        if name == "base":
            continue
        alt = total_score(dims, w, min_dimensions)
        for seg in SEGMENTS + ["all"]:
            idx = segment.index if seg == "all" else segment[segment == seg].index
            b, a = base.loc[idx].dropna(), alt.loc[idx].dropna()
            common = b.index.intersection(a.index)
            if len(common) < 5:
                continue
            k = min(n, len(common))
            rho = b.loc[common].corr(a.loc[common], method="spearman")
            ov = len(_top_set(b.loc[common], segment, k) & _top_set(a.loc[common], segment, k)) / k
            rows.append({"weight_set": name, "segment": seg, "n": len(common), "spearman": rho, "top_n": k,
                         "top_overlap": ov})
    return pd.DataFrame(rows)


def selection_frequency(dims: pd.DataFrame, segment: pd.Series, per_segment: dict, cfg: dict | None = None,
                        min_dimensions: int = 4) -> pd.Series:
    """Share of random weight vectors (Dirichlet around the base weights) in which each company is in its
    segment's top-k, k from `per_segment`. 1.0 = selected whatever the weights; ~0 = a weight artefact."""
    cfg = cfg or portfolio_config()
    st = cfg["stability"]
    names = list(dims.columns)
    base = np.array([cfg["weight_sets"]["base"].get(d, 0) for d in names], float)
    base = base / base.sum()
    rng = np.random.default_rng(st["seed"])
    draws = rng.dirichlet(np.maximum(base, 1e-3) * st["concentration"], size=st["draws"])
    hits = pd.Series(0.0, index=dims.index)
    for w in draws:
        s = total_score(dims, dict(zip(names, w)), min_dimensions)
        for seg, k in per_segment.items():
            idx = segment[segment == seg].index
            top = s.loc[idx].dropna().sort_values(ascending=False).head(k).index
            hits.loc[top] += 1
    return hits / len(draws)
