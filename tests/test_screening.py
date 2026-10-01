import numpy as np
import pandas as pd
import pytest

from src.financial_metrics.metrics import compute_all
from src.screening.engine import assign_archetype, leverage_class, normalise_weights, score_universe, screen
from src.valuation.peers import assign_peer_groups, compute_multiples, peer_stats
from tests.conftest import make_fin


def test_weights_normalise():
    w = normalise_weights({"growth": 40, "profitability": 40, "cash_flow": 40, "leverage": 30, "valuation": 30,
                           "working_capital": 20})
    assert sum(w.values()) == pytest.approx(100)
    assert w["growth"] == pytest.approx(20)


def test_zero_weights_fall_back_to_defaults():
    assert sum(normalise_weights({}).values()) == pytest.approx(100)


@pytest.mark.parametrize("nd,icr,ebitda,net,expected", [
    (-0.5, 50, 100, -50, "Low"), (0.5, 20, 100, 50, "Low"), (2.0, 6, 100, 200, "Moderate"),
    (3.0, 3, 100, 300, "High"), (5.0, 2, 100, 500, "Distressed"), (np.nan, np.nan, -10, 100, "Distressed"),
    (2.0, 1.2, 100, 200, "Distressed")])
def test_leverage_class(nd, icr, ebitda, net, expected):
    assert leverage_class(nd, icr, ebitda, net) == expected


def universe(n=8):
    """Eight companies, same industry, quality increasing with company_id."""
    fins = pd.concat([make_fin(cid=i, g=0.02 * i, margin=0.08 + 0.02 * i, debt=800 - 80 * i) for i in range(1, n + 1)])
    _, snap = compute_all(fins)
    mkt = pd.DataFrame({"company_id": range(1, n + 1), "market_cap": [5000.0] * n, "date": pd.Timestamp("2026-06-30")})
    u = snap.merge(compute_multiples(snap, mkt), on="company_id")
    u["company_name"] = [f"Co{i}" for i in u.company_id]
    u["sector"], u["industry"] = "Industrials", "Capital Goods"
    u["peer_group"] = assign_peer_groups(u, "ev_ebitda")
    u["scoring_peer_group"] = assign_peer_groups(u, "revenue")
    u["ev_ebitda_premium"] = 0.0
    u["data_quality"], u["critical_flags"] = "High", 0
    return u


def test_scores_bounded_and_ordered():
    s, detail = score_universe(universe())
    assert s.total_score.between(0, 100).all()
    ts = s.set_index("company_id").total_score
    assert ts[8] > ts[4] > ts[1]
    dims = [c for c in s.columns if c.endswith("_score") and c != "total_score"]
    for d in dims:
        assert (s[d].dropna() <= s[d.replace("_score", "_max")]).all()
    assert {"company_id", "metric", "percentile"} <= set(detail.columns)


def test_weight_change_changes_scores():
    u = universe()
    a, _ = score_universe(u, {"growth": 100, "profitability": 0, "cash_flow": 0, "leverage": 0, "valuation": 0, "working_capital": 0})
    b, _ = score_universe(u, {"growth": 0, "profitability": 0, "cash_flow": 0, "leverage": 0, "valuation": 100, "working_capital": 0})
    assert not np.allclose(a.total_score, b.total_score)


def test_missing_dimension_marks_partial_and_fails_screen():
    u = universe()
    for c in ["ev_ebitda", "ev_sales", "pe", "fcf_yield"]:
        u.loc[u.company_id == 8, c] = np.nan
    u.loc[u.company_id == 8, "market_cap"] = np.nan
    t, _ = screen(u)
    r = t.set_index("company_id").loc[8]
    assert r.score_is_partial and "Valuation" in r.dimensions_missing
    assert not r.passes_screen


def test_low_quality_cannot_pass():
    u = universe()
    u["data_quality"] = "Low"
    t, _ = screen(u)
    assert not t.passes_screen.any()


def test_archetype_compounder_and_reasons():
    r = pd.Series({"revenue_cagr_3y": 0.2, "roic": 0.25, "fcf_conversion": 0.6})
    a, reasons = assign_archetype(r, None)
    assert a == "Compounder" and len(reasons) == 3 and "ROIC" in reasons[1]


def test_archetype_default():
    a, reasons = assign_archetype(pd.Series({"revenue_cagr_3y": -0.1}), None)
    assert a == "Further Investigation" and reasons


def test_peer_stats_excludes_company_itself():
    u = universe()
    ps = peer_stats(u, 1).set_index("multiple")
    assert ps.loc["ev_ebitda", "n_peers"] == 7


def test_negative_ebitda_scores_worst_on_valuation():
    u = universe()
    u.loc[u.company_id == 8, ["ebitda", "ev_ebitda"]] = [-50, np.nan]
    s, detail = score_universe(u)
    d = detail[(detail.company_id == 8) & (detail.metric == "ev_ebitda")]
    assert d.percentile.iloc[0] < 0.1
