import math

import pytest

from src.financial_metrics import metrics as m
from tests.conftest import make_fin

nan = math.isnan


def test_cagr_basic():
    assert m.cagr(100, 133.1, 3) == pytest.approx(0.10)


@pytest.mark.parametrize("start,end", [(0, 100), (-50, 100), (100, -10), (None, 100), (float("nan"), 1)])
def test_cagr_undefined(start, end):
    assert nan(m.cagr(start, end, 3))


def test_growth_negative_base():
    assert nan(m.growth(-10, 20))
    assert m.growth(100, 90) == pytest.approx(-0.10)


def test_roic():
    nopat = m.nopat(100, 25, 100)  # 25% tax
    assert nopat == pytest.approx(75)
    ic = m.invested_capital(400, 200, 100)
    assert ic == 500
    assert m.roic(nopat, 500, 500) == pytest.approx(0.15)
    assert m.roic(nopat, float("nan"), 500) == pytest.approx(0.15)  # closing only


def test_roic_negative_invested_capital():
    assert nan(m.invested_capital(-500, 100, 50))


def test_tax_rate_bounds_and_fallback():
    assert m.effective_tax_rate(80, 100) == 0.35          # capped
    assert m.effective_tax_rate(-5, 100) == 0.0
    assert m.effective_tax_rate(10, -100) == pytest.approx(0.2517)  # loss year -> statutory


def test_fcf_and_conversion():
    assert m.free_cash_flow(120, -20) == 100          # capex sign-agnostic
    assert m.fcf_conversion(100, 200) == 0.5
    assert nan(m.fcf_conversion(100, -50))            # negative EBITDA
    assert m.fcf_conversion(-30, 100) == -0.3         # negative FCF is meaningful


def test_net_debt_and_leverage():
    assert m.net_debt(500, 100) == 400
    assert m.net_debt(0, 100) == -100                 # zero debt -> net cash
    assert m.net_debt_to_ebitda(400, 200) == 2.0
    assert nan(m.net_debt_to_ebitda(400, -10))
    assert m.net_debt_to_ebitda(-100, 200) == -0.5    # net cash stays negative


def test_interest_coverage_edges():
    assert m.interest_coverage(100, 25) == 4
    assert m.interest_coverage(100, 0) == m.ICR_CAP   # zero interest
    assert nan(m.interest_coverage(-10, 0))
    assert m.interest_coverage(1e6, 1) == m.ICR_CAP   # capped


def test_negative_equity():
    assert nan(m.roe(50, -100, -80))
    assert nan(m.debt_to_equity(100, -10))


def test_extreme_leverage_fcf_to_debt():
    assert m.fcf_to_debt(10, 10_000) == pytest.approx(0.001)
    assert nan(m.fcf_to_debt(10, 0))


def test_working_capital_days():
    assert m.days(100, 365) == pytest.approx(100)
    assert nan(m.days(100, 0))
    assert m.ccc(60, 45, 30) == 75
    assert m.ccc(60, float("nan"), float("nan")) == 60   # services company without inventory


def test_company_series(fin):
    h = m.compute_company_metrics(fin)
    last = h.iloc[-1]
    assert last.revenue_growth == pytest.approx(0.10)
    assert last.revenue_cagr_3y == pytest.approx(0.10)
    assert last.revenue_cagr_5y == pytest.approx(0.10)
    assert last.ebitda_margin == pytest.approx(0.20)
    assert last.dso == pytest.approx(60)
    assert last.dio == pytest.approx(45)
    assert last.cash_conversion_cycle == pytest.approx(75)
    assert last.fcf_conversion == pytest.approx((0.8 * 0.2 - 0.05) / 0.2)
    assert nan(h.iloc[0].revenue_growth)               # no prior year


def test_cagr_requires_exact_year_gap():
    f = make_fin(n=6).drop(index=2)                    # missing FY2022
    h = m.compute_company_metrics(f).set_index("fiscal_year")
    assert nan(h.loc[2025, "revenue_cagr_3y"])         # FY2022 base missing -> no CAGR
    assert not nan(h.loc[2025, "revenue_cagr_5y"])


def test_negative_ebitda_company():
    f = make_fin(margin=-0.05)
    h = m.compute_company_metrics(f)
    assert nan(h.iloc[-1].net_debt_to_ebitda)
    assert nan(h.iloc[-1].fcf_conversion)
    assert h.iloc[-1].fcf_margin < 0


def test_trailing_stats(fin):
    _, snap = m.compute_all(fin)
    s = snap.iloc[0]
    assert s.ebitda_margin_change_3y == pytest.approx(0, abs=1e-12)
    assert s.positive_fcf_years_5y == 5
    assert s.ocf_to_ebitda_3y == pytest.approx(0.8)
    assert s.years_of_history == 6
