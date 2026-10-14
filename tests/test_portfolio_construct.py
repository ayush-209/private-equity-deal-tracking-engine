import numpy as np
import pandas as pd
import pytest

from src.portfolio import construct as pc


def make_scored(seed=0):
    rng = np.random.default_rng(seed)
    rows, cid = [], 0
    sectors = ["IT", "Pharma", "Auto", "Metals", "FMCG", "Infra"]
    for seg, n in [("large", 12), ("mid", 12), ("small", 10)]:
        for i in range(n):
            rows.append(dict(company_id=cid, company_name=f"{seg}{i}", ticker=f"{seg[:1].upper()}{i}", cap_segment=seg,
                             sector=sectors[(cid * 5) % len(sectors)], class_score=90 - 2 * i - rng.uniform(0, 1),
                             market_cap=1e4, last_price=float(rng.uniform(50, 3000)), beta=rng.uniform(0.6, 1.4),
                             sd=rng.uniform(0.2, 0.5), pe=20.0, roe=0.15, eligible=True))
            cid += 1
    df = pd.DataFrame(rows)
    df["rank_in_segment"] = df.groupby("cap_segment").class_score.rank(ascending=False)
    return df


def test_select_takes_top_k_per_segment_and_respects_sector_limit():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 4, "mid": 4, "small": 3}, max_sector=0.30)
    sel, notes = pc.select_names(s, p)
    assert sel.groupby("cap_segment").size().to_dict() == {"large": 4, "mid": 4, "small": 3}
    assert sel.sector.value_counts().max() <= int(0.30 * 11)       # floor(0.3 x 11) = 3
    top_large = s[s.cap_segment == "large"].nlargest(1, "class_score").company_name.iloc[0]
    assert top_large in set(sel.company_name)
    assert not notes


def test_select_reports_shortfall_and_ignores_ineligible():
    s = make_scored()
    s.loc[s.cap_segment == "small", "eligible"] = False
    sel, notes = pc.select_names(s, pc.Params(names_per_segment={"large": 3, "mid": 3, "small": 3}))
    assert "small" not in set(sel.cap_segment)
    assert any("small" in n for n in notes)


def test_sizing_meets_every_constraint():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 5, "mid": 5, "small": 4},
                  min_segment_weight={"large": 0.30, "mid": 0.20, "small": 0.25}, max_position=0.12, max_sector=0.30,
                  weighting="score")
    sel, _ = pc.select_names(s, p)
    w, notes = pc.size_positions(sel, p)
    assert w.sum() == pytest.approx(1.0)
    assert (w <= 0.12 + 1e-6).all() and (w >= 0).all()
    seg = pd.Series(w).groupby(sel.cap_segment.values).sum()
    assert seg["small"] >= 0.25 - 1e-6 and seg["large"] >= 0.30 - 1e-6 and seg["mid"] >= 0.20 - 1e-6
    assert pd.Series(w).groupby(sel.sector.values).sum().max() <= 0.30 + 1e-6
    assert not notes


@pytest.mark.parametrize("weighting", pc.WEIGHTINGS)
def test_all_weightings_produce_valid_weights(weighting):
    s = make_scored(1)
    p = pc.Params(weighting=weighting)
    sel, _ = pc.select_names(s, p)
    w, _ = pc.size_positions(sel, p)
    assert w.sum() == pytest.approx(1.0) and (w <= p.max_position + 1e-6).all()


def test_equal_weighting_without_binding_constraints_is_equal():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 4, "mid": 4, "small": 4}, min_segment_weight={"large": 0, "mid": 0, "small": 0},
                  max_position=0.5, max_sector=1.0, weighting="equal")
    sel, _ = pc.select_names(s, p)
    w, _ = pc.size_positions(sel, p)
    assert np.allclose(w, 1 / 12, atol=1e-6)


def test_score_weighting_favours_higher_scores():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 5, "mid": 5, "small": 5}, min_segment_weight={"large": 0, "mid": 0, "small": 0},
                  max_position=0.5, max_sector=1.0, weighting="score")
    sel, _ = pc.select_names(s, p)
    w, _ = pc.size_positions(sel, p)
    assert np.corrcoef(w, sel.class_score)[0, 1] > 0.99


def test_impossible_position_cap_is_raised_and_reported():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 2, "mid": 2, "small": 1}, max_position=0.05, max_sector=1.0,
                  min_segment_weight={"large": 0, "mid": 0, "small": 0})
    sel, _ = pc.select_names(s, p)
    w, notes = pc.size_positions(sel, p)
    assert w.sum() == pytest.approx(1.0)
    assert any("below 1/5" in n for n in notes)


def test_contradictory_floors_fall_back_with_message():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 5, "mid": 5, "small": 4},
                  min_segment_weight={"large": 0.9, "mid": 0.9, "small": 0.0})
    sel, _ = pc.select_names(s, p)
    w, notes = pc.size_positions(sel, p)
    assert any("could not all be met" in n for n in notes)
    assert w.sum() == pytest.approx(1.0)


def test_round_to_shares_hand_checked():
    shares, cash = pc.round_to_shares(np.array([0.5, 0.5]), np.array([300.0, 700.0]), 10000.0)
    # floors: 5000/300 = 16, 5000/700 = 7 -> spent 4800 + 4900 = 9700; 300 left buys one more of the first
    assert shares.tolist() == [17, 7]
    assert cash == pytest.approx(10000 - 17 * 300 - 7 * 700)
    assert cash >= 0


def test_round_to_shares_never_overspends_and_handles_bad_prices():
    rng = np.random.default_rng(5)
    for _ in range(50):
        w = rng.dirichlet(np.ones(8))
        px = rng.uniform(20, 9000, 8)
        sh, cash = pc.round_to_shares(w, px, 1e7)
        assert cash >= -1e-6 and (sh >= 0).all()
        assert abs((sh * px).sum() + cash - 1e7) < 1e-4
        assert cash < px.max()                                     # nothing else fits... for the neediest
    sh, cash = pc.round_to_shares(np.array([0.5, 0.5]), np.array([np.nan, 100.0]), 1000.0)
    assert sh[0] == 0 and sh[1] >= 5


def _rets(n=60, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2021-01", periods=n, freq="M")
    return pd.DataFrame(rng.normal(0.01, 0.05, (n, 3)), index=idx, columns=[1, 2, 3])


def test_portfolio_risk_matches_covariance_formula():
    R = _rets()
    w = pd.Series([0.5, 0.3, 0.2], index=[1, 2, 3])
    out = pc.portfolio_risk(w, R)
    cov = R.cov().to_numpy() * 12
    assert out["sd"] == pytest.approx(float(np.sqrt(w.to_numpy() @ cov @ w.to_numpy())))
    assert out["diversification"] > 0 and out["sd"] < out["weighted_avg_sd"]


def test_perfectly_correlated_assets_give_no_diversification():
    base = _rets()[1]
    R = pd.DataFrame({1: base, 2: base * 2})
    out = pc.portfolio_risk(pd.Series([0.5, 0.5], index=[1, 2]), R)
    assert out["diversification"] == pytest.approx(0.0, abs=1e-9)


def test_portfolio_beta_is_weighted_beta_for_exact_betas():
    rng = np.random.default_rng(9)
    idx = pd.period_range("2021-01", periods=60, freq="M")
    m = pd.Series(rng.normal(0.01, 0.04, 60), index=idx)
    R = pd.DataFrame({1: 0.8 * m, 2: 1.4 * m})
    out = pc.portfolio_risk(pd.Series([0.25, 0.75], index=[1, 2]), R, m)
    assert out["beta"] == pytest.approx(0.25 * 0.8 + 0.75 * 1.4)


def test_risk_is_nan_with_too_little_history():
    out = pc.portfolio_risk(pd.Series([1.0, 0.0], index=[1, 2]), _rets(8)[[1, 2]])
    assert np.isnan(out["sd"])


def test_build_portfolio_end_to_end_with_prices():
    s = make_scored()
    rng = np.random.default_rng(3)
    dates = pd.date_range("2021-01-01", periods=61, freq="MS")
    rows = [dict(company_id=c, date=d.date(), share_price=float(100 * np.exp(np.cumsum(rng.normal(0.01, 0.06, 61)))[i]))
            for c in s.company_id for i, d in enumerate(dates)]
    prices = pd.DataFrame(rows)
    bench = pd.Series(rng.normal(0.01, 0.04, 60), index=pd.period_range("2021-02", periods=60, freq="M"))
    res = pc.build_portfolio(s, prices, bench, pc.Params(capital=1e7))
    h = res["holdings"]
    assert len(h) == 14
    assert res["invested"] + res["cash"] == pytest.approx(1e7)
    assert h.weight.sum() == pytest.approx(res["invested"] / 1e7)
    assert (h.shares * h.last_price - h.invested).abs().max() < 1e-6
    assert np.isfinite(res["risk"]["sd"]) and np.isfinite(res["risk"]["beta"])
    assert res["segment_weights"].sum() == pytest.approx(h.weight.sum())


def test_no_eligible_names_returns_empty_portfolio():
    s = make_scored()
    s["eligible"] = False
    res = pc.build_portfolio(s, pd.DataFrame(), pd.Series(dtype=float), pc.Params())
    assert res["holdings"].empty and res["notes"]
