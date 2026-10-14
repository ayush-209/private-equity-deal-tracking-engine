import math

import numpy as np
import pandas as pd
import pytest

from src.portfolio import metrics as pm
from tests.conftest import make_fin


def test_roce_hand_checked():
    assert pm.capital_employed(1000, 300) == 700
    assert math.isnan(pm.capital_employed(1000, 1200))           # negative capital employed
    assert math.isnan(pm.capital_employed(1000, None))
    assert pm.roce(140, 600, 700) == pytest.approx(140 / 650)    # average of opening and closing
    assert pm.roce(140, float("nan"), 700) == pytest.approx(0.2)  # closing only
    assert math.isnan(pm.roce(None, 600, 700))
    assert pm.roce_approx(100, 400, 100) == pytest.approx(0.2)
    assert math.isnan(pm.roce_approx(100, -400, 100))


def test_pb_and_peg():
    assert pm.price_to_book(5000, 1000) == 5.0
    assert math.isnan(pm.price_to_book(5000, -10))
    assert pm.peg_ratio(20, 0.25) == pytest.approx(0.8)           # P/E 20, growth 25% -> PEG 0.8
    assert math.isnan(pm.peg_ratio(20, 0.0))
    assert math.isnan(pm.peg_ratio(20, -0.1))
    assert math.isnan(pm.peg_ratio(-5, 0.2))


def test_eps_growth_uses_eps_when_consistent():
    eps = {2022: 10.0, 2023: 12.0, 2024: 14.4, 2025: 17.28}      # 20% a year
    pat = {2022: 100.0, 2023: 121.0, 2024: 143.0, 2025: 172.0}
    g, basis = pm.eps_growth_measure(eps, pat, 2025)
    assert g == pytest.approx(0.2, abs=1e-9)
    assert basis == "EPS 3y"


def test_eps_growth_falls_back_to_pat_when_eps_looks_split_distorted():
    eps = {2022: 100.0, 2025: 10.0}                                # unadjusted for a 1:10 split would look like -54%/yr
    eps = {2022: 5.0, 2023: 6.0, 2024: 7.0, 2025: 80.0}            # absurd jump from a distorted series
    pat = {2022: 100.0, 2023: 110.0, 2024: 121.0, 2025: 133.0}
    g, basis = pm.eps_growth_measure(eps, pat, 2025)
    assert g == pytest.approx((133 / 100) ** (1 / 3) - 1)
    assert basis.startswith("PAT")


def test_eps_growth_short_history_and_negative_base():
    g, basis = pm.eps_growth_measure({2024: 10.0, 2025: 12.0}, {2024: 100.0, 2025: 120.0}, 2025)
    assert g == pytest.approx(0.2) and basis == "EPS YoY"
    g, basis = pm.eps_growth_measure({2024: -2.0, 2025: 3.0}, {2024: -20.0, 2025: 30.0}, 2025)
    assert math.isnan(g)


def test_fundamentals_snapshot_with_and_without_current_liabilities():
    f = make_fin(n=5)
    f["current_liabilities"] = 500.0
    s = pm.fundamentals_snapshot(f).iloc[0]
    assert s.sales_yoy == pytest.approx(0.10)
    assert s.roce == pytest.approx(f.ebit.iloc[-1] / 1500.0)       # (2000 - 500)
    assert s.roce_basis.startswith("EBIT / avg")
    g = f.drop(columns="current_liabilities")
    s2 = pm.fundamentals_snapshot(g).iloc[0]
    assert s2.roce_basis.startswith("approx")
    assert s2.roce == pytest.approx(g.ebit.iloc[-1] / (g.total_equity.iloc[-1] + 500.0))


def test_cap_segments_by_rank():
    caps = pd.Series(np.arange(300, 0, -1) * 100.0)                # rank 1 = first element
    seg = pm.classify_cap(caps, {"large": {"rank_from": 1, "rank_to": 100}, "mid": {"rank_from": 101, "rank_to": 250},
                                 "small": {"rank_from": 251, "rank_to": 10**6}})
    assert seg.cap_segment.value_counts().to_dict() == {"mid": 150, "large": 100, "small": 50}
    assert seg.cap_segment.iloc[0] == "large" and seg.cap_segment.iloc[100] == "mid" and seg.cap_segment.iloc[250] == "small"
    caps.iloc[5] = np.nan
    assert pd.isna(pm.classify_cap(caps, {"large": {"rank_from": 1, "rank_to": 10}}).cap_segment.iloc[5])


def _series(returns, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(returns) + 1, freq="MS")
    return pd.Series(100 * np.cumprod(np.r_[1, 1 + np.asarray(returns)]), index=idx)


def test_beta_recovers_known_slope_and_sd():
    rng = np.random.default_rng(1)
    m = rng.normal(0.01, 0.04, 60)
    mkt, stock = pm.month_returns(_series(m)), pm.month_returns(_series(1.5 * m))
    out = pm.beta_and_sd(stock, mkt)
    assert out["beta"] == pytest.approx(1.5, abs=1e-9)
    assert out["sd"] == pytest.approx(stock.std(ddof=1) * math.sqrt(12))
    assert out["corr_mkt"] == pytest.approx(1.0)
    assert out["n_months"] == 60


def test_beta_needs_enough_months_and_variance():
    m = np.random.default_rng(2).normal(0, 0.04, 30)
    out = pm.beta_and_sd(pm.month_returns(_series(m)), pm.month_returns(_series(m)))
    assert math.isnan(out["beta"]) and math.isnan(out["sd"])
    flat = pm.month_returns(_series(np.zeros(60)))
    assert math.isnan(pm.beta_and_sd(pm.month_returns(_series(np.full(60, 0.01))), flat)["beta"])


def test_beta_uses_only_latest_window():
    rng = np.random.default_rng(3)
    m = rng.normal(0, 0.04, 100)
    stock = np.r_[3 * m[:40], 1.0 * m[40:]]
    out = pm.beta_and_sd(pm.month_returns(_series(stock)), pm.month_returns(_series(m)), max_months=60)
    assert out["n_months"] == 60 and out["beta"] == pytest.approx(1.0, abs=1e-9)


def test_technicals_hand_checked():
    px = pd.Series(np.arange(100.0, 100.0 + 14), index=pd.date_range("2024-01-01", periods=14, freq="MS"))
    t = pm.technicals(px)                                           # prices 100..113
    assert t["ret_6m"] == pytest.approx(113 / 107 - 1)
    assert t["mom_12_1"] == pytest.approx(112 / 101 - 1)            # P_{t-1} / P_{t-12}
    assert t["trend_10m"] == pytest.approx(113 / np.mean(range(104, 114)) - 1)
    short = pm.technicals(px.head(5))
    assert all(math.isnan(v) for v in short.values())


def test_month_returns_collapses_duplicate_months():
    s = pd.Series([100.0, 105.0, 110.0], index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-02-20"]))
    r = pm.month_returns(s)
    assert len(r) == 1 and r.iloc[0] == pytest.approx(0.10)
