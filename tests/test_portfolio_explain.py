import numpy as np
import pandas as pd
import pytest

from src.portfolio import construct as pc
from src.portfolio import explain as ex
from tests.test_portfolio_construct import make_scored


def test_derive_params_from_n_and_mix():
    p = pc.derive_params(1e7, 15, {"large": 40, "mid": 35, "small": 25})
    assert sum(p.names_per_segment.values()) == 15
    assert p.names_per_segment == {"large": 6, "mid": 5, "small": 4}
    assert p.min_segment_weight["large"] == pytest.approx(0.35)       # share minus 5 points
    assert p.min_segment_weight["small"] == pytest.approx(0.20)


def test_derive_params_edge_cases():
    p = pc.derive_params(1e6, 2, {"large": 50, "mid": 30, "small": 20})
    assert all(n >= 1 for n in p.names_per_segment.values())          # every segment with a share gets a name
    p2 = pc.derive_params(1e6, 10, {"large": 100, "mid": 0, "small": 0})
    assert p2.names_per_segment == {"large": 10}
    with pytest.raises(ValueError):
        pc.derive_params(1e6, 10, {"large": 0, "mid": 0})
    with pytest.raises(ValueError):
        pc.derive_params(1e6, 0, {"large": 1})


def test_decisions_explain_every_pick_and_skip():
    s = make_scored()
    p = pc.Params(names_per_segment={"large": 4, "mid": 3, "small": 3}, max_sector=0.20)   # floor(0.2 x 10) = 2 per sector
    sel, notes, dec = pc.select_with_reasons(s, p)
    assert (dec.decision == "Picked").sum() == len(sel)
    assert set(dec[dec.decision == "Picked"].company_id) == set(sel.company_id)
    skipped = dec[dec.decision == "Skipped"]
    assert len(skipped) > 0 and skipped.reason.str.startswith("Sector limit").all()
    assert (dec.decision == "Not picked").sum() <= 3 * 3                   # a few just-missed names per segment
    top = s[s.cap_segment == "large"].nlargest(1, "class_score").company_id.iloc[0]
    assert dec[dec.company_id == top].decision.iloc[0] == "Picked"


def test_user_exclusions_and_inclusions():
    s = make_scored()
    p0 = pc.Params(names_per_segment={"large": 3, "mid": 3, "small": 3}, max_sector=1.0)
    sel0, _, _ = pc.select_with_reasons(s, p0)
    best = sel0[sel0.cap_segment == "large"].company_id.iloc[0]
    p1 = pc.Params(names_per_segment={"large": 3, "mid": 3, "small": 3}, max_sector=1.0, exclude_ids=frozenset({best}))
    sel1, _, dec1 = pc.select_with_reasons(s, p1)
    assert best not in set(sel1.company_id) and len(sel1[sel1.cap_segment == "large"]) == 3
    assert dec1[dec1.company_id == best].reason.iloc[0] == "Removed by you"
    weak = s[s.cap_segment == "mid"].nsmallest(1, "class_score").company_id.iloc[0]
    p2 = pc.Params(names_per_segment={"large": 3, "mid": 3, "small": 3}, max_sector=1.0, include_ids=frozenset({weak}))
    sel2, _, dec2 = pc.select_with_reasons(s, p2)
    assert weak in set(sel2.company_id) and len(sel2[sel2.cap_segment == "mid"]) == 3
    assert dec2[dec2.company_id == weak].reason.iloc[0] == "Added by you"


def test_robustness_filter_is_reported():
    s = make_scored()
    freq = pd.Series(0.9, index=s.company_id)
    target = s[s.cap_segment == "large"].nlargest(1, "class_score").company_id.iloc[0]
    freq.loc[target] = 0.1
    p = pc.Params(names_per_segment={"large": 3, "mid": 2, "small": 2}, max_sector=1.0, min_freq=0.5)
    sel, _, dec = pc.select_with_reasons(s, p, freq)
    assert target not in set(sel.company_id)
    assert "10%" in dec[dec.company_id == target].reason.iloc[0]


def test_why_text_and_funnel():
    r = pd.Series({"cap_segment": "large", "rank_in_segment": 2, "class_score": 78.4, "growth_pct": 0.97,
                   "profitability_pct": 0.9, "valuation_pct": 0.2, "risk_pct": 0.5, "technical_pct": 0.6})
    t = ex.why_text(r)
    assert "Rank 2 among large-cap" in t and "growth" in t and "top 3%" in t and "weakest on valuation" in t
    df = pd.DataFrame({"cap_segment": ["large", "mid", None, "mid", "small"],
                       "ineligible_reason": ["", "financial business (x)", "no market capitalisation", "loss-making or earnings missing", ""],
                       "class_score": [70.0, np.nan, np.nan, np.nan, np.nan]})
    f = ex.funnel(df)
    assert f.companies.tolist() == [5, 4, 3, 2, 2, 1]
    assert f.companies.is_monotonic_decreasing
