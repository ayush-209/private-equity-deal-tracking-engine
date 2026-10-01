"""SYNTHETIC demo dataset — for testing the pipeline and UI without network access.

Every company is fictitious, every name carries "(Demo)", and every row is tagged
source='SYNTHETIC_DEMO'. The UI shows a persistent banner whenever this source is present.
Profiles are seeded so each archetype and red flag fires on at least a few companies.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from src.data_ingestion.adapters import Adapter, estimated_filing_date, map_sector

SOURCE = "SYNTHETIC_DEMO"
FY_START, FY_END = 2017, 2026  # FY2026 = year ended 31-Mar-2026, filed ~May 2026

INDUSTRIES = {  # industry: (margin, capital intensity, base EV/EBITDA, DSO, DIO, DPO)
    "Fast Moving Consumer Goods": (0.18, 0.04, 32, 15, 45, 55),
    "Information Technology": (0.22, 0.03, 22, 75, 0, 30),
    "Chemicals": (0.17, 0.08, 20, 70, 80, 60),
    "Capital Goods": (0.12, 0.04, 28, 95, 90, 80),
    "Automobile and Auto Components": (0.13, 0.06, 16, 50, 45, 60),
    "Healthcare": (0.20, 0.06, 24, 80, 110, 70),
    "Consumer Durables": (0.11, 0.04, 30, 40, 70, 75),
    "Construction": (0.12, 0.03, 12, 110, 60, 120),
    "Metals & Mining": (0.16, 0.10, 7, 25, 70, 50),
    "Textiles": (0.12, 0.05, 10, 60, 100, 50),
    "Consumer Services": (0.16, 0.08, 26, 10, 20, 50),
    "Construction Materials": (0.18, 0.09, 14, 25, 50, 60),
}
PROFILES = ["compounder", "cash_cow", "margin_expander", "turnaround", "value", "steady", "leveraged_ok",
            "distressed", "receivables_inflator", "cyclical", "steady", "compounder"]
SYL = ["Ara", "Vel", "Kri", "Sur", "Nav", "Ind", "Shi", "Pra", "Tam", "Utk", "Dha", "Rav", "Ved", "Kal", "Ama",
       "Sat", "Har", "Nir", "Bha", "Dev"]
SUF = ["ra", "tek", "van", "mira", "lok", "jit", "sha", "dra", "gir", "pur", "sen", "nova"]
IND_WORD = {"Fast Moving Consumer Goods": "Consumer", "Information Technology": "Infotech", "Chemicals": "Chemicals",
            "Capital Goods": "Engineering", "Automobile and Auto Components": "Autoparts", "Healthcare": "Pharma",
            "Consumer Durables": "Appliances", "Construction": "Infra", "Metals & Mining": "Metals",
            "Textiles": "Textiles", "Consumer Services": "Retail", "Construction Materials": "Cement",
            "Financial Services": "Finance"}


def _profile_paths(profile: str, n: int, rng: np.random.Generator):
    """Revenue growth path, margin offset path, receivables drift, debt multiplier."""
    t = np.arange(n)
    if profile == "compounder":
        g = rng.normal(0.17, 0.03, n); m = np.full(n, 0.04); rec = 0; debt = 0.2
    elif profile == "cash_cow":
        g = rng.normal(0.10, 0.02, n); m = np.full(n, 0.06); rec = -2; debt = 0.3
    elif profile == "margin_expander":
        g = rng.normal(0.09, 0.02, n); m = np.linspace(-0.05, 0.03, n); rec = 0; debt = 0.8
    elif profile == "turnaround":
        g = np.where(t < n - 3, rng.normal(-0.04, 0.04, n), rng.normal(0.12, 0.03, n))
        m = np.where(t < n - 3, -0.08, np.linspace(-0.06, 0.0, n)); rec = 0; debt = 2.5
    elif profile == "value":
        g = rng.normal(0.08, 0.03, n); m = np.full(n, 0.0); rec = 0; debt = 0.8
    elif profile == "leveraged_ok":
        g = rng.normal(0.07, 0.015, n); m = np.full(n, 0.03); rec = 0; debt = 1.2
    elif profile == "distressed":
        g = rng.normal(-0.02, 0.08, n); m = np.linspace(0.0, -0.12, n); rec = 4; debt = 4.0
    elif profile == "receivables_inflator":
        g = rng.normal(0.14, 0.03, n); m = np.full(n, 0.02); rec = 12; debt = 1.5
    elif profile == "cyclical":
        g = 0.08 + 0.18 * np.sin(t * 1.3) + rng.normal(0, 0.04, n); m = 0.05 * np.sin(t * 1.3); rec = 0; debt = 1.8
    else:  # steady
        g = rng.normal(0.09, 0.03, n); m = rng.normal(0, 0.01, n); rec = 1; debt = 1.0
    return g, m, rec, debt


class DemoAdapter(Adapter):
    source = SOURCE

    def __init__(self, n_per_industry: int = 14, seed: int = 7, as_of: dt.date | None = None):
        self.rng = np.random.default_rng(seed)
        self.as_of = as_of or dt.date(2026, 9, 30)
        self._universe, self._fin, self._mkt, self._own = self._build(n_per_industry)

    def universe(self) -> pd.DataFrame:
        return self._universe

    def financials(self, ticker: str) -> pd.DataFrame:
        return self._fin.get(ticker, pd.DataFrame())

    def market(self, ticker: str) -> pd.DataFrame:
        return self._mkt.get(ticker, pd.DataFrame())

    def ownership(self, ticker: str) -> pd.DataFrame:
        return self._own.get(ticker, pd.DataFrame())

    # ------------------------------------------------------------
    def _build(self, n_per):
        rng = self.rng
        uni, fins, mkts, owns = [], {}, {}, {}
        used = set()
        industries = list(INDUSTRIES) + ["Financial Services"]
        for ind in industries:
            count = 6 if ind == "Financial Services" else n_per
            for k in range(count):
                while True:
                    nm = rng.choice(SYL) + rng.choice(SUF)
                    if nm not in used:
                        used.add(nm)
                        break
                ticker = (nm[:6] + IND_WORD[ind][:3]).upper()
                name = f"{nm} {IND_WORD[ind]} (Demo)"
                uni.append({"company_name": name, "ticker": ticker, "isin": None, "industry": ind,
                            "sector": map_sector(ind), "exchange": "NSE", "source": SOURCE})
                profile = PROFILES[(k + len(ind)) % len(PROFILES)]
                fins[ticker], mkts[ticker], owns[ticker] = self._company(ind, profile, k)
        return pd.DataFrame(uni), fins, mkts, owns

    def _company(self, ind, profile, k):
        rng = self.rng
        if ind == "Financial Services":
            margin, capint, mult, dso, dio, dpo = 0.45, 0.01, 12, 0, 0, 0
            profile = "steady"
        else:
            margin, capint, mult, dso, dio, dpo = INDUSTRIES[ind]
        years = list(range(FY_START, FY_END + 1))
        # data-quality variety: short histories and gaps on a few names
        if k % 11 == 5:
            years = years[-3:]
        n = len(years)
        g, m_off, rec_drift, debt_mult = _profile_paths(profile, n, rng)
        rev0 = float(np.exp(rng.uniform(np.log(300), np.log(40000))))
        revs = [rev0]
        for i in range(1, n):
            revs.append(revs[-1] * (1 + g[i]))
        rows, debt = [], rev0 * margin * debt_mult
        equity = rev0 * rng.uniform(0.4, 0.9)
        for i, fy in enumerate(years):
            rev = revs[i]
            em = max(margin + m_off[i] + rng.normal(0, 0.008), -0.15)
            ebitda = rev * em
            dep = rev * capint * 0.7
            ebit = ebitda - dep
            interest = debt * rng.uniform(0.085, 0.11)
            pbt = ebit - interest
            tax = max(pbt, 0) * 0.2517
            pat = pbt - tax
            d_dso = dso + rec_drift * i * (1 if profile == "receivables_inflator" and i >= n - 3 else 0.3)
            receivables = rev * max(d_dso, 0) / 365
            cogs = rev * (1 - em) * 0.75
            inventory = cogs * dio / 365 * (1 + (0.25 if profile == "distressed" and i >= n - 2 else 0))
            payables = cogs * dpo / 365
            nwc = receivables + inventory - payables
            prev_nwc = rows[-1]["_nwc"] if rows else nwc
            light = 0.55 if profile in {"cash_cow", "compounder"} else 1.0
            capex = rev * capint * light * (1.6 if profile in {"turnaround", "distressed"} and i >= n - 3 else 1.0) \
                * rng.uniform(0.85, 1.15)
            ocf = ebitda - tax - (nwc - prev_nwc)
            fcf = ocf - capex
            divs = max(pat, 0) * (0.35 if profile in {"cash_cow", "steady"} else 0.15)
            debt = max(debt - (fcf - divs) * 0.6, 0) if profile not in {"distressed"} else debt * 1.18 + max(-fcf, 0)
            if profile == "leveraged_ok":
                debt = max(debt, ebitda * 0.6)
            equity = equity + pat - divs
            cash = max(rev * 0.05 + (fcf - divs) * 0.3, rev * 0.01)
            pe_ = dt.date(fy, 3, 31)
            r = {"fiscal_year": fy, "period_end": pe_, "filing_date": estimated_filing_date(pe_),
                 "filing_date_estimated": True, "revenue": rev, "cost_of_revenue": cogs, "ebitda": ebitda,
                 "ebit": ebit, "depreciation": dep, "pretax_income": pbt, "tax_expense": tax, "pat": pat,
                 "eps": pat / 10, "operating_cash_flow": ocf, "capex": capex, "free_cash_flow": fcf,
                 "total_assets": equity + debt + payables * 1.5, "total_equity": equity, "total_debt": debt,
                 "cash": cash, "receivables": receivables, "inventory": inventory, "payables": payables,
                 "interest_expense": interest, "dividends_paid": divs, "source": SOURCE, "_nwc": nwc}
            if k % 13 == 7 and i == n - 1:      # missing working-capital fields on a few names
                r["receivables"] = r["inventory"] = None
            rows.append(r)
        fin = pd.DataFrame(rows).drop(columns="_nwc")

        # monthly market data: EV = industry multiple × trailing EBITDA × profile-specific rating
        rating = {"value": 0.6, "distressed": 0.5, "compounder": 1.25, "turnaround": 0.8,
                  "receivables_inflator": 1.1, "cyclical": 0.85}.get(profile, 1.0)
        months = pd.date_range(dt.date(FY_START, 6, 30), self.as_of, freq="ME")
        mrows = []
        for d in months:
            avail = fin[pd.to_datetime(fin.filing_date) <= d]
            if avail.empty:
                continue
            last = avail.iloc[-1]
            ev = mult * rating * max(last.ebitda, last.revenue * 0.02) * float(np.exp(rng.normal(0, 0.08)))
            mcap = max(ev - (last.total_debt - last.cash), last.revenue * 0.15)
            mrows.append({"date": d.date(), "share_price": round(mcap / 10, 2), "shares_outstanding_cr": 10.0,
                          "market_cap": mcap, "enterprise_value": None, "source": SOURCE})
        mkt = pd.DataFrame(mrows)
        prom = float(rng.uniform(0.35, 0.75))
        inst = float(rng.uniform(0.1, 0.9 - prom))
        own = pd.DataFrame([{"date": dt.date(2026, 6, 30), "promoter_holding": prom, "institutional_holding": inst,
                             "public_holding": 1 - prom - inst, "source": SOURCE}])
        return fin, mkt, own
