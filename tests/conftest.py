import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_fin(n=6, rev0=1000.0, g=0.10, margin=0.20, debt=500.0, cash=100.0, cid=1, start=2020, **over):
    """Simple, hand-checkable statements: constant growth and margin."""
    rows = []
    for i in range(n):
        rev = rev0 * (1 + g) ** i
        ebitda = rev * margin
        r = dict(company_id=cid, fiscal_year=start + i, revenue=rev, cost_of_revenue=rev * 0.6, ebitda=ebitda,
                 ebit=ebitda - rev * 0.03, pretax_income=ebitda - rev * 0.03 - 40, tax_expense=(ebitda - rev * 0.03 - 40) * 0.25,
                 pat=(ebitda - rev * 0.03 - 40) * 0.75, eps=10 * (1 + g) ** i, operating_cash_flow=ebitda * 0.8,
                 capex=rev * 0.05, free_cash_flow=None, total_assets=2000, total_equity=1000 + 50 * i, total_debt=debt,
                 cash=cash, receivables=rev * 60 / 365, inventory=rev * 0.6 * 45 / 365, payables=rev * 0.6 * 30 / 365,
                 interest_expense=40.0)
        rows.append(r)
    df = pd.DataFrame(rows)
    for k, v in over.items():
        df[k] = v
    return df


@pytest.fixture
def fin():
    return make_fin()
