import datetime as dt

import pandas as pd

from src.data_validation.validator import grade_data_quality, validate_financials
from src.financial_metrics.metrics import compute_all
from src.risk.red_flags import dd_questions, red_flags
from tests.conftest import make_fin


def with_dates(f):
    f = f.copy()
    f["period_end"] = [dt.date(y, 3, 31) for y in f.fiscal_year]
    return f


def test_duplicates_removed():
    f = with_dates(make_fin(n=3))
    clean, issues = validate_financials(pd.concat([f, f.tail(1)]))
    assert len(clean) == 3 and any(i["check_name"] == "duplicate" for i in issues)


def test_rupee_values_normalised_to_crore():
    f = with_dates(make_fin(n=2))
    f.loc[1, ["revenue", "ebitda"]] = f.loc[1, ["revenue", "ebitda"]] * 1e7
    f.loc[1, [c for c in f.columns if c not in {"company_id", "fiscal_year", "period_end", "eps", "revenue", "ebitda"}]] *= 1e7
    clean, issues = validate_financials(f)
    assert clean.revenue.max() < 2000 and any(i["check_name"] == "unit_normalisation" for i in issues)


def test_fiscal_year_mismatch_is_blocked():
    f = with_dates(make_fin(n=2))
    f.loc[0, "period_end"] = dt.date(2019, 3, 31)   # says FY2020
    clean, issues = validate_financials(f)
    assert len(clean) == 1 and any(i["severity"] == "error" for i in issues)


def test_non_march_year_end_warns():
    f = with_dates(make_fin(n=1))
    f.loc[0, "period_end"] = dt.date(2019, 12, 31)
    f.loc[0, "fiscal_year"] = 2020
    _, issues = validate_financials(f)
    assert any("month 12" in i["detail"] for i in issues)


def test_non_numeric_coerced():
    f = with_dates(make_fin(n=2)).astype({"revenue": object})
    f.loc[0, "revenue"] = "n/a"
    clean, issues = validate_financials(f)
    assert clean.revenue.isna().sum() == 1 and any(i["check_name"] == "data_type" for i in issues)


def test_quality_grades():
    good = make_fin(n=6, cid=1)
    short = make_fin(n=2, cid=2)
    q = grade_data_quality(pd.concat([good, short]), pd.DataFrame(columns=["company_id", "severity"]), {1, 2}).set_index("company_id")
    assert q.loc[1, "data_quality"] == "High" and q.loc[2, "data_quality"] == "Low"
    q2 = grade_data_quality(good, pd.DataFrame(columns=["company_id", "severity"]), set())
    assert q2.data_quality.iloc[0] != "High"   # no market data


def flags_for(f, industry="Capital Goods"):
    h, snap = compute_all(f)
    return red_flags(h, snap.iloc[0], industry)


def test_receivables_flag_fires_with_explanation():
    f = make_fin(n=4)
    f.loc[3, "receivables"] = f.loc[2, "receivables"] * 1.27
    fl = {x["flag"]: x for x in flags_for(f)}
    assert "Receivables risk" in fl
    assert "27%" in fl["Receivables risk"]["explanation"] and "10%" in fl["Receivables risk"]["explanation"]
    assert {"metric", "current", "previous", "threshold", "explanation"} <= set(fl["Receivables risk"])


def test_clean_company_has_no_flags():
    assert flags_for(make_fin(n=6)) == []


def test_negative_fcf_three_years_is_critical():
    f = make_fin(n=4)
    f["capex"] = f.revenue * 0.5
    fl = {x["flag"]: x for x in flags_for(f)}
    assert fl["Negative FCF"]["severity"] == "critical"


def test_ind_as_116_flag():
    f = make_fin(n=4, start=2018)
    assert any(x["flag"] == "Ind AS 116 break in series" for x in flags_for(f, "Retail"))
    assert not any(x["flag"] == "Ind AS 116 break in series" for x in flags_for(f, "Chemicals"))


def test_dd_questions_trace_to_flags():
    f = make_fin(n=4)
    f.loc[3, "receivables"] = f.loc[2, "receivables"] * 1.5
    h, snap = compute_all(f)
    qs = dd_questions(red_flags(h, snap.iloc[0]), -0.25, "High", snap.iloc[0])
    assert any("Receivables risk" in q["triggered_by"] for q in qs)
    assert any("discount" in q["question"] for q in qs)
