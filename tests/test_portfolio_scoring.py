import numpy as np
import pandas as pd
import pytest

from src.config import portfolio_config
from src.portfolio import scoring as sc

CFG = portfolio_config()


def make_frame(n=40, seed=0):
    rng = np.random.default_rng(seed)
    f = pd.DataFrame({
        "company_id": range(n), "cap_segment": ["large"] * (n // 2) + ["mid"] * (n - n // 2),
        "sales_yoy": rng.normal(0.1, 0.05, n), "eps_growth": rng.normal(0.12, 0.08, n),
        "roe": rng.normal(0.15, 0.05, n), "roce": rng.normal(0.18, 0.05, n),
        "pe": rng.uniform(10, 50, n), "peg": rng.uniform(0.5, 3, n), "pb": rng.uniform(1, 8, n),
        "beta": rng.uniform(0.5, 1.5, n), "sd": rng.uniform(0.2, 0.5, n),
        "ret_6m": rng.normal(0.05, 0.1, n), "mom_12_1": rng.normal(0.1, 0.2, n), "trend_10m": rng.normal(0.02, 0.05, n),
        "pat": rng.uniform(50, 500, n), "market_cap": rng.uniform(1e3, 1e5, n), "total_equity": rng.uniform(100, 1e4, n)})
    return f


def test_best_company_in_segment_scores_best():
    f = make_frame()
    star = 3                                                       # large-cap, best on every metric
    for c, hi in [("sales_yoy", 1), ("eps_growth", 1), ("roe", 1), ("roce", 1), ("ret_6m", 1), ("mom_12_1", 1),
                  ("trend_10m", 1), ("pe", 0), ("peg", 0), ("pb", 0), ("beta", 0), ("sd", 0)]:
        f.loc[star, c] = f[c].max() + 1 if hi else f[c].min() - 1
    s = sc.score_frame(f)
    assert s.loc[star, "class_score"] == pytest.approx(s[s.cap_segment == "large"].class_score.max())
    assert s.loc[star, "rank_in_segment"] == 1
    assert s.loc[star, "class_score"] > 95


def test_percentiles_are_within_segment():
    f = make_frame()
    f.loc[f.cap_segment == "mid", "pe"] += 500                      # mid caps all expensive; large caps unaffected
    _, detail, _ = sc.dimension_scores(f)
    # Within each segment the P/E percentiles span the full range: size-relative, not absolute
    d = detail[detail.metric == "pe"].merge(f[["company_id", "cap_segment"]], on="company_id")
    v = d.groupby("cap_segment").percentile.agg(["min", "max"])
    assert (v["min"] < 0.05).all() and (v["max"] > 0.95).all()


def test_loss_makers_and_no_growth_are_scored_worst_not_dropped():
    f = make_frame()
    f.loc[0, "pat"] = -5.0
    f.loc[1, "eps_growth"] = -0.2
    f.loc[1, "peg"] = np.nan
    f.loc[2, "total_equity"] = -50.0
    f.loc[2, "pb"] = np.nan
    x = sc.prepare_inputs(f)
    assert np.isinf(x.loc[0, "pe"]) and np.isinf(x.loc[1, "peg"]) and np.isinf(x.loc[2, "pb"])
    dims, detail, _ = sc.dimension_scores(f)
    d = detail[detail.company_id == 1].set_index("metric").percentile
    assert d["peg"] < 0.05                                         # worst in its segment on PEG


def test_unknown_peg_is_not_penalised():
    f = make_frame()
    f.loc[5, ["eps_growth", "peg"]] = np.nan                       # growth unknown (not <= 0)
    x = sc.prepare_inputs(f)
    assert np.isnan(x.loc[5, "peg"])


def test_total_score_requires_enough_dimensions():
    dims = pd.DataFrame({"growth": [0.8, 0.8, 0.8], "profitability": [0.6, 0.6, np.nan], "valuation": [0.4, np.nan, np.nan],
                         "risk": [0.2, 0.2, np.nan], "technical": [1.0, np.nan, np.nan]})
    w = {"growth": 30, "profitability": 20, "valuation": 20, "risk": 20, "technical": 10}
    s = sc.total_score(dims, w, min_dimensions=4)
    assert s.iloc[0] == pytest.approx((0.8 * 30 + 0.6 * 20 + 0.4 * 20 + 0.2 * 20 + 1.0 * 10) / 100 * 100)
    assert np.isnan(s.iloc[1]) and np.isnan(s.iloc[2])
    # a missing dimension re-scales the weights of the others rather than counting as zero
    dims2 = dims.iloc[[0]].copy()
    dims2["technical"] = np.nan
    s2 = sc.total_score(dims2, w, min_dimensions=4).iloc[0]
    assert s2 == pytest.approx((0.8 * 30 + 0.6 * 20 + 0.4 * 20 + 0.2 * 20) / 90 * 100)


def test_weights_normalise_and_reject_all_zero():
    n = sc.normalise({"growth": 1, "risk": 3}, ["growth", "risk", "valuation"])
    assert n == {"growth": 25.0, "risk": 75.0, "valuation": 0.0}
    with pytest.raises(ValueError):
        sc.normalise({"growth": 0}, ["growth"])


def test_small_segment_is_pooled():
    f = make_frame()
    f.loc[:2, "cap_segment"] = "small"                              # only 3 small caps
    g = sc.peer_groups(f, 10)
    assert (g[:3] == "ALL").all() and (g[3:20] == "large").all()


def test_default_weights_sum_to_100_and_have_a_reason():
    ws = [s["weight"] for s in CFG["dimensions"].values()]
    assert sum(ws) == 100
    assert all(len(" ".join(s["why"].split())) > 40 for s in CFG["dimensions"].values())
    for name, w in CFG["weight_sets"].items():
        assert set(w) == set(CFG["dimensions"]), name


def test_rank_stability_identical_and_different_weights():
    f = make_frame(80)
    dims, _, _ = sc.dimension_scores(f)
    cfg = {**CFG, "weight_sets": {"base": CFG["weight_sets"]["base"], "same": CFG["weight_sets"]["base"],
                                  "flip": {"growth": 0, "profitability": 0, "valuation": 0, "risk": 100, "technical": 0}}}
    st = sc.rank_stability(dims, f.cap_segment, cfg)
    same = st[st.weight_set == "same"]
    assert np.allclose(same.spearman, 1.0) and (same.top_overlap == 1.0).all()
    flip = st[(st.weight_set == "flip") & (st.segment == "all")].iloc[0]
    assert flip.spearman < 0.8 and flip.top_overlap < 1.0


def test_selection_frequency_dominant_company_always_picked():
    f = make_frame(80)
    star = 7
    for c, hi in [("sales_yoy", 1), ("eps_growth", 1), ("roe", 1), ("roce", 1), ("ret_6m", 1), ("mom_12_1", 1),
                  ("trend_10m", 1), ("pe", 0), ("peg", 0), ("pb", 0), ("beta", 0), ("sd", 0)]:
        f.loc[star, c] = f[c].max() + 1 if hi else f[c].min() - 1
    dims, _, _ = sc.dimension_scores(f)
    freq = sc.selection_frequency(dims, f.cap_segment, {"large": 3, "mid": 3}, CFG)
    assert freq.loc[star] == 1.0
    assert freq.between(0, 1).all()
    assert freq[f.cap_segment == "large"].sum() == pytest.approx(3.0)   # exactly 3 picks per draw
