import math

import numpy as np
import pytest

from src.lbo.model import LBOInputs, irr, run_control, run_minority, sensitivity


def test_irr_matches_closed_form():
    assert irr([-100, 0, 0, 0, 0, 200]) == pytest.approx(2 ** 0.2 - 1, abs=1e-6)


def test_irr_with_interim_flows():
    # 10% coupon bond at par -> 10%
    assert irr([-100, 10, 10, 110]) == pytest.approx(0.10, abs=1e-6)


def test_irr_no_sign_change():
    assert math.isnan(irr([100, 10, 10]))


def base(**kw):
    p = dict(revenue=1000, ebitda=200, entry_multiple=10, debt_to_ebitda=4, interest_rate=0.10, fees_pct=0.0,
             revenue_cagr=0.0, capex_pct_revenue=0.0, nwc_pct_incremental_revenue=0.0, da_pct_revenue=0.0,
             tax_rate=0.0, holding_years=5, exit_multiple=10)
    p.update(kw)
    return LBOInputs(**p)


def test_sources_equal_uses():
    r = run_control(base(fees_pct=0.02))
    su = r["sources_uses"].set_index("item").amount
    assert su["Uses: total"] == pytest.approx(su["Sources: total"])
    assert r["entry_ev"] == 2000 and r["entry_debt"] == 800
    assert r["sponsor_equity"] == pytest.approx(2000 + 40 - 800)


def test_flat_case_hand_check():
    # No growth/tax/capex: FCF = 200 - interest on opening debt; 100% sweep.
    debt, cash = 800.0, 0.0
    for _ in range(5):
        fcf = 200 - debt * 0.10
        repay = min(fcf, debt)
        debt, cash = debt - repay, cash + fcf - repay
    r = run_control(base())
    assert r["exit_net_debt"] == pytest.approx(debt - cash)
    assert r["exit_equity"] == pytest.approx(2000 - (debt - cash))
    assert r["moic"] == pytest.approx(r["exit_equity"] / 1200)
    assert r["irr"] == pytest.approx(r["moic"] ** 0.2 - 1, abs=1e-6)


def test_value_bridge_reconciles():
    r = run_control(base(revenue_cagr=0.08, exit_multiple=12, fees_pct=0.02, tax_rate=0.25,
                         capex_pct_revenue=0.03, da_pct_revenue=0.02))
    assert r["value_bridge"].value.sum() == pytest.approx(r["exit_equity"] - r["sponsor_equity"])
    other = r["value_bridge"].set_index("driver").loc["Other", "value"]
    assert abs(other) < 1e-6


def test_zero_debt_is_unlevered():
    r = run_control(base(debt_to_ebitda=0))
    assert r["entry_debt"] == 0 and r["moic"] >= 1


def test_negative_ebitda_rejected():
    assert "error" in run_control(base(ebitda=-10))
    assert "error" in run_minority(base(ebitda=-10, market_cap=500))


def test_excess_leverage_rejected():
    assert "error" in run_control(base(debt_to_ebitda=11))


def test_more_leverage_raises_irr_when_returns_exceed_cost_of_debt():
    lo = run_control(base(revenue_cagr=0.10, debt_to_ebitda=2))["irr"]
    hi = run_control(base(revenue_cagr=0.10, debt_to_ebitda=5))["irr"]
    assert hi > lo


def test_ebitda_cagr_override():
    r = run_control(base(ebitda_cagr_override=0.10))
    assert r["exit_ebitda"] == pytest.approx(200 * 1.1 ** 5)


def test_minority_moic_includes_dividends():
    r = run_minority(base(market_cap=1500, stake_pct=0.2, entry_premium=0.0, payout_ratio=0.5,
                          existing_net_debt=500))
    assert r["investment"] == pytest.approx(300)
    assert sum(r["cashflows"][1:]) / 300 == pytest.approx(r["moic"])


def test_sensitivity_shape_and_monotonic():
    s = sensitivity(base(), "entry_multiple", [8, 10, 12], "exit_multiple", [8, 10, 12], "moic")
    assert s.shape == (3, 3)
    assert (np.diff(s.values, axis=0) < 0).all()   # higher entry -> lower MOIC
    assert (np.diff(s.values, axis=1) > 0).all()   # higher exit -> higher MOIC
