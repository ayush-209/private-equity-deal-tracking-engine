"""Indicative deal economics. Two structures:

1. Control buyout (illustrative). Acquisition debt, cash sweep, exit at a multiple. For an Indian listed
   target this requires a delisting under the SEBI Delisting Regulations and acquisition financing that
   Indian banks have historically been restricted from providing (NCDs/offshore funds are typical);
   treat the output as a theoretical ceiling on returns, not a deal that can be executed as modelled.
2. Minority / PIPE stake. Buy a stake at market value plus a premium, no acquisition debt, participate in
   dividends and the equity value at exit. This is how most PE capital enters Indian listed companies.

Neither is a full transaction model: no fees amortisation schedule, tranching, covenants, MIP or tax structuring.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd


@dataclass
class LBOInputs:
    revenue: float                 # LTM revenue, ₹ cr
    ebitda: float                  # LTM EBITDA, ₹ cr
    existing_net_debt: float = 0.0 # refinanced at close (control) / stays with company (minority)
    entry_multiple: float = 12.0
    debt_to_ebitda: float = 3.0
    interest_rate: float = 0.11
    fees_pct: float = 0.02         # transaction fees as % of entry EV
    revenue_cagr: float = 0.10
    exit_ebitda_margin: float | None = None   # margin reached linearly by exit; None = hold current margin
    ebitda_cagr_override: float | None = None # if set, EBITDA grows at this rate (used by sensitivities)
    capex_pct_revenue: float = 0.04
    nwc_pct_incremental_revenue: float = 0.15
    da_pct_revenue: float = 0.03
    tax_rate: float = 0.2517
    cash_sweep: float = 1.0        # share of post-interest FCF used to repay debt
    holding_years: int = 5
    exit_multiple: float = 12.0
    # minority
    stake_pct: float = 0.20
    entry_premium: float = 0.10    # premium to market value for a negotiated stake
    market_cap: float | None = None
    payout_ratio: float = 0.25


def irr(cashflows: list[float], lo: float = -0.99, hi: float = 10.0, tol: float = 1e-7) -> float:
    """Bisection IRR — robust for the single sign-change flows produced here. NaN if no sign change."""
    cf = np.asarray(cashflows, dtype=float)
    if not (cf < 0).any() or not (cf > 0).any():
        return float("nan")
    npv = lambda r: float(np.sum(cf / (1 + r) ** np.arange(len(cf))))  # noqa: E731
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return float("nan")
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _operating_case(p: LBOInputs) -> pd.DataFrame:
    m0 = p.ebitda / p.revenue
    m_exit = p.exit_ebitda_margin if p.exit_ebitda_margin is not None else m0
    rows, rev_prev, ebitda_prev = [], p.revenue, p.ebitda
    for y in range(1, p.holding_years + 1):
        rev = rev_prev * (1 + p.revenue_cagr)
        if p.ebitda_cagr_override is not None:
            ebitda = ebitda_prev * (1 + p.ebitda_cagr_override)
        else:
            ebitda = rev * (m0 + (m_exit - m0) * y / p.holding_years)
        rows.append({"year": y, "revenue": rev, "ebitda": ebitda, "ebitda_margin": ebitda / rev,
                     "da": rev * p.da_pct_revenue, "capex": rev * p.capex_pct_revenue,
                     "delta_nwc": (rev - rev_prev) * p.nwc_pct_incremental_revenue})
        rev_prev, ebitda_prev = rev, ebitda
    return pd.DataFrame(rows)


def run_control(p: LBOInputs) -> dict:
    if p.ebitda <= 0:
        return {"error": "EBITDA is not positive: a leveraged buyout cannot be underwritten on an EBITDA multiple."}
    entry_ev = p.entry_multiple * p.ebitda
    debt0 = p.debt_to_ebitda * p.ebitda
    fees = entry_ev * p.fees_pct
    equity = entry_ev + fees - debt0
    if equity <= 0:
        return {"error": "Debt exceeds purchase price plus fees; reduce leverage."}
    ops = _operating_case(p)
    debt, sched = debt0, []
    for _, r in ops.iterrows():
        interest = debt * p.interest_rate
        ebt = r.ebitda - r.da - interest
        tax = max(ebt, 0) * p.tax_rate
        fcf = r.ebitda - interest - tax - r.capex - r.delta_nwc
        repay = min(max(fcf * p.cash_sweep, 0), debt)
        cash_build = fcf - repay
        sched.append({"year": int(r.year), "opening_debt": debt, "interest": interest, "tax": tax,
                      "levered_fcf": fcf, "debt_repaid": repay, "cash_build": cash_build,
                      "closing_debt": debt - repay})
        debt -= repay
    sched = pd.DataFrame(sched)
    exit_ebitda = ops.ebitda.iloc[-1]
    exit_ev = exit_ebitda * p.exit_multiple
    accumulated_cash = sched.cash_build.clip(lower=None).sum()
    exit_net_debt = debt - accumulated_cash
    exit_equity = exit_ev - exit_net_debt
    moic = exit_equity / equity
    rate = irr([-equity] + [0] * (p.holding_years - 1) + [exit_equity])
    total_gain = exit_equity - equity
    bridge = _value_bridge(p, exit_ebitda, debt0, exit_net_debt, total_gain)
    return {
        "sources_uses": pd.DataFrame([
            {"item": "Uses: purchase enterprise value", "amount": entry_ev},
            {"item": "Uses: transaction fees", "amount": fees},
            {"item": "Uses: total", "amount": entry_ev + fees},
            {"item": "Sources: acquisition debt", "amount": debt0},
            {"item": "Sources: sponsor equity", "amount": equity},
            {"item": "Sources: total", "amount": debt0 + equity},
        ]),
        "operating_case": ops, "debt_schedule": sched,
        "entry_ev": entry_ev, "entry_debt": debt0, "sponsor_equity": equity,
        "equity_contribution_pct": equity / (entry_ev + fees),
        "purchase_price_equity": entry_ev - p.existing_net_debt,
        "exit_ebitda": exit_ebitda, "exit_ev": exit_ev, "debt_repaid": debt0 - debt,
        "exit_net_debt": exit_net_debt, "exit_equity": exit_equity, "moic": moic, "irr": rate,
        "value_bridge": bridge,
        "max_leverage_check": debt0 / p.ebitda, "min_icr_y1": p.ebitda / (debt0 * p.interest_rate) if debt0 else np.inf,
    }


def _value_bridge(p, exit_ebitda, debt0, exit_net_debt, total_gain) -> pd.DataFrame:
    """Split equity gain into EBITDA growth, multiple change, and deleveraging."""
    growth = (exit_ebitda - p.ebitda) * p.entry_multiple
    multiple = (p.exit_multiple - p.entry_multiple) * exit_ebitda
    delever = debt0 - exit_net_debt
    fees = -p.entry_multiple * p.ebitda * p.fees_pct
    other = total_gain - growth - multiple - delever - fees
    return pd.DataFrame([{"driver": "EBITDA growth", "value": growth},
                         {"driver": "Multiple expansion/(contraction)", "value": multiple},
                         {"driver": "Debt paydown / cash build", "value": delever},
                         {"driver": "Transaction fees", "value": fees},
                         {"driver": "Other", "value": other}])


def run_minority(p: LBOInputs) -> dict:
    if p.market_cap is None or p.market_cap <= 0:
        return {"error": "Market capitalisation required for a minority stake."}
    if p.ebitda <= 0:
        return {"error": "EBITDA is not positive: exit value cannot be set on an EBITDA multiple."}
    entry_equity_value = p.market_cap * (1 + p.entry_premium)
    invest = entry_equity_value * p.stake_pct
    ops = _operating_case(p)
    nd, flows, sched = p.existing_net_debt, [-invest], []
    for _, r in ops.iterrows():
        interest = max(nd, 0) * p.interest_rate
        tax = max(r.ebitda - r.da - interest, 0) * p.tax_rate
        pat = r.ebitda - r.da - interest - tax
        fcf = r.ebitda - interest - tax - r.capex - r.delta_nwc
        div = max(pat, 0) * p.payout_ratio
        nd = nd - (fcf - div)
        sched.append({"year": int(r.year), "pat": pat, "fcf": fcf, "dividends_total": div,
                      "dividends_to_stake": div * p.stake_pct, "closing_net_debt": nd})
        flows.append(div * p.stake_pct)
    exit_ev = ops.ebitda.iloc[-1] * p.exit_multiple
    exit_equity = exit_ev - nd
    stake_exit = exit_equity * p.stake_pct
    flows[-1] += stake_exit
    total_back = sum(flows[1:])
    return {"investment": invest, "entry_equity_value": entry_equity_value, "implied_entry_ev_ebitda":
            (entry_equity_value + p.existing_net_debt) / p.ebitda, "operating_case": ops,
            "schedule": pd.DataFrame(sched), "exit_ev": exit_ev, "exit_equity": exit_equity,
            "stake_exit_value": stake_exit, "moic": total_back / invest, "irr": irr(flows), "cashflows": flows}


def sensitivity(p: LBOInputs, row_param: str, row_values, col_param: str, col_values, metric: str = "irr",
                mode: str = "control") -> pd.DataFrame:
    """Grid of MOIC or IRR. Parameters are LBOInputs field names."""
    fn = run_control if mode == "control" else run_minority
    out = pd.DataFrame(index=list(row_values), columns=list(col_values), dtype=float)
    for rv in row_values:
        for cv in col_values:
            res = fn(replace(p, **{row_param: rv, col_param: cv}))
            out.loc[rv, cv] = res.get(metric, np.nan) if "error" not in res else np.nan
    out.index.name, out.columns.name = row_param, col_param
    return out


def inputs_from_company(row: pd.Series, **overrides) -> LBOInputs:
    """Seed assumptions from a company's latest data; the UI then lets the user change them."""
    cagr = row.get("revenue_cagr_3y")
    base = dict(
        revenue=float(row["revenue"]), ebitda=float(row["ebitda"]),
        existing_net_debt=float(row.get("net_debt") or 0.0),
        entry_multiple=round(float(row["ev_ebitda"]), 1) if pd.notna(row.get("ev_ebitda")) else 12.0,
        revenue_cagr=float(np.clip(cagr, 0.0, 0.25)) if pd.notna(cagr) else 0.08,
        market_cap=float(row["market_cap"]) if pd.notna(row.get("market_cap")) else None,
    )
    base["exit_multiple"] = base["entry_multiple"]
    base.update(overrides)
    return LBOInputs(**base)


def as_dict(p: LBOInputs) -> dict:
    return asdict(p)
