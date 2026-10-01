"""Data source adapters. Each adapter returns raw frames in ₹ crore with a `source` label.

Available
- YFinanceAdapter: free, ~4 annual periods for NSE (.NS) tickers. Figures are the latest restated
  version, not as-originally-reported. Good for a working prototype; weak for historical validation.
- CSVAdapter: long-format files exported from CMIE Prowess, Capitaline, Ace Equity or Screener.in
  exports (where your licence allows). Use this for 10+ years of history and real filing dates.
- NSE universe: Nifty index constituent files (industry classification + ISIN).

The universe is never hard-coded: it comes from the index file, or a user-supplied data/universe.csv.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd
import requests

from src.config import CRORE, ROOT, sector_config

log = logging.getLogger(__name__)

NSE_INDEX_URLS = {
    "nifty500": "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "nifty_midsmallcap400": "https://niftyindices.com/IndexConstituent/ind_niftymidsmallcap400list.csv",
    "nifty_total_market": "https://niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
}
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                   "Accept": "text/csv,*/*"}
FILING_LAG_DAYS = 60  # SEBI LODR Reg. 33: audited annual results within 60 days of year end


def estimated_filing_date(period_end: dt.date) -> dt.date:
    return period_end + dt.timedelta(days=FILING_LAG_DAYS)


def map_sector(industry: str | None) -> str:
    return sector_config()["industry_to_sector"].get(industry or "", "Other")


def with_retry(fn, *args, attempts: int = 3, base_delay: float = 2.0, **kwargs):
    """Exponential backoff for flaky/rate-limited APIs."""
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if i == attempts - 1:
                raise
            wait = base_delay * 2 ** i
            log.warning("%s failed (%s); retrying in %.0fs", getattr(fn, "__name__", fn), exc, wait)
            time.sleep(wait)


def load_universe(index: str = "nifty500", local_path: Path | None = None) -> pd.DataFrame:
    """Columns: company_name, ticker, isin, industry, sector, exchange."""
    local_path = local_path or ROOT / "data" / "universe.csv"
    if local_path.exists():
        raw = pd.read_csv(local_path)
        src = f"local:{local_path.name}"
    else:
        resp = with_retry(requests.get, NSE_INDEX_URLS[index], headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        raw = pd.read_csv(io.StringIO(resp.text))
        src = f"niftyindices:{index}"
        raw.to_csv(local_path, index=False)  # cache so later runs do not depend on the site
    cols = {c.lower().strip(): c for c in raw.columns}
    df = pd.DataFrame({
        "company_name": raw[cols.get("company name", "company_name")],
        "ticker": raw[cols.get("symbol", "ticker")].str.strip(),
        "isin": raw[cols["isin code"]] if "isin code" in cols else raw.get("isin"),
        "industry": raw[cols["industry"]] if "industry" in cols else None,
    })
    if "series" in cols:
        df = df[raw[cols["series"]].isin(["EQ", "BE"])]
    df["sector"] = df["industry"].map(map_sector)
    df["exchange"] = "NSE"
    df["source"] = src
    return df.drop_duplicates("ticker").reset_index(drop=True)


class Adapter(ABC):
    source: str

    @abstractmethod
    def financials(self, ticker: str) -> pd.DataFrame: ...

    @abstractmethod
    def market(self, ticker: str) -> pd.DataFrame: ...

    def ownership(self, ticker: str) -> pd.DataFrame:
        return pd.DataFrame()


# ---------------------------------------------------------------- yfinance
YF_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "cost_of_revenue": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT", "Operating Income"],
    "depreciation": ["Reconciled Depreciation", "Depreciation And Amortization In Income Statement"],
    "pretax_income": ["Pretax Income"],
    "tax_expense": ["Tax Provision"],
    "pat": ["Net Income Common Stockholders", "Net Income"],
    "eps": ["Diluted EPS", "Basic EPS"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "total_assets": ["Total Assets"],
    "total_equity": ["Stockholders Equity", "Common Stock Equity"],
    "total_debt": ["Total Debt"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
    "payables": ["Accounts Payable", "Payables"],
    "operating_cash_flow": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],
    "free_cash_flow": ["Free Cash Flow"],
    "dividends_paid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
}
PER_SHARE = {"eps"}


class YFinanceAdapter(Adapter):
    source = "yfinance"

    def __init__(self, suffix: str = ".NS", pause: float = 0.6):
        import yfinance  # noqa: F401  (import check)
        self.suffix, self.pause = suffix, pause

    def _ticker(self, t):
        import yfinance as yf
        return yf.Ticker(f"{t}{self.suffix}")

    def financials(self, ticker: str) -> pd.DataFrame:
        tk = self._ticker(ticker)
        frames = [with_retry(lambda: tk.income_stmt), with_retry(lambda: tk.balance_sheet),
                  with_retry(lambda: tk.cashflow)]
        time.sleep(self.pause)
        stacked = pd.concat([f for f in frames if f is not None and not f.empty])
        if stacked.empty:
            return pd.DataFrame()
        stacked = stacked[~stacked.index.duplicated()]
        rows = []
        for period_end in stacked.columns:
            pe = pd.Timestamp(period_end).date()
            r = {"period_end": pe, "fiscal_year": pe.year if pe.month <= 3 else pe.year + 1,
                 "filing_date": estimated_filing_date(pe), "filing_date_estimated": True, "source": self.source}
            for field, labels in YF_MAP.items():
                val = next((stacked.at[lbl, period_end] for lbl in labels
                            if lbl in stacked.index and pd.notna(stacked.at[lbl, period_end])), None)
                if val is not None and field not in PER_SHARE:
                    val = float(val) / CRORE
                r[field] = val
            if r.get("capex") is not None:
                r["capex"] = abs(r["capex"])
            if r.get("dividends_paid") is not None:
                r["dividends_paid"] = abs(r["dividends_paid"])
            rows.append(r)
        return pd.DataFrame(rows)

    def market(self, ticker: str) -> pd.DataFrame:
        tk = self._ticker(ticker)
        hist = with_retry(lambda: tk.history(period="10y", interval="1mo", auto_adjust=False))
        shares = None
        try:
            shares = tk.fast_info.get("shares") or tk.info.get("sharesOutstanding")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(self.pause)
        if hist is None or hist.empty or not shares:
            return pd.DataFrame()
        sh_cr = shares / CRORE
        df = pd.DataFrame({"date": [d.date() for d in hist.index], "share_price": hist["Close"].values})
        df["shares_outstanding_cr"] = sh_cr
        df["market_cap"] = df.share_price * sh_cr        # current share count applied to history: approximate
        df["enterprise_value"] = None                     # computed against the matching balance sheet downstream
        df["source"] = "yfinance (historic mcap uses current share count)"
        return df

    def ownership(self, ticker: str) -> pd.DataFrame:
        tk = self._ticker(ticker)
        try:
            mh = tk.major_holders
        except Exception:  # noqa: BLE001
            return pd.DataFrame()
        if mh is None or mh.empty:
            return pd.DataFrame()
        s = mh.iloc[:, 0] if mh.shape[1] == 1 else mh.set_index(mh.columns[1]).iloc[:, 0]
        ins, inst = s.get("insidersPercentHeld"), s.get("institutionsPercentHeld")
        if ins is None:
            return pd.DataFrame()
        return pd.DataFrame([{"date": dt.date.today(), "promoter_holding": float(ins),
                              "institutional_holding": float(inst) if inst is not None else None,
                              "public_holding": max(0.0, 1 - float(ins) - float(inst or 0)),
                              "source": "yfinance major_holders (insiders ≈ promoters)"}])


# ---------------------------------------------------------------- CSV (Prowess / Capitaline / exports)
class CSVAdapter(Adapter):
    """Reads data/financials_long.csv with columns:
        ticker, fiscal_year, period_end, field, value, unit[, filing_date]
    unit ∈ {cr, lakh, mn, rupees}. Optional data/market_long.csv: ticker, date, share_price, market_cap_cr.
    """
    UNIT = {"cr": 1, "crore": 1, "lakh": 0.01, "mn": 0.1, "million": 0.1, "rupees": 1 / CRORE, "inr": 1 / CRORE}

    def __init__(self, fin_path: Path | None = None, mkt_path: Path | None = None, source: str = "csv_import"):
        self.fin_path = fin_path or ROOT / "data" / "financials_long.csv"
        self.mkt_path = mkt_path or ROOT / "data" / "market_long.csv"
        self.source = source
        self._fin = pd.read_csv(self.fin_path) if self.fin_path.exists() else pd.DataFrame()
        self._mkt = pd.read_csv(self.mkt_path) if self.mkt_path.exists() else pd.DataFrame()

    def financials(self, ticker: str) -> pd.DataFrame:
        if self._fin.empty:
            return pd.DataFrame()
        d = self._fin[self._fin.ticker == ticker].copy()
        if d.empty:
            return d
        d["value"] = [v * (1 if f == "eps" else self.UNIT[str(u).lower()]) for v, u, f in zip(d.value, d.unit, d.field)]
        wide = d.pivot_table(index=["fiscal_year", "period_end"], columns="field", values="value", aggfunc="last").reset_index()
        if "filing_date" in d:
            fd = d.groupby("fiscal_year")["filing_date"].first()
            wide["filing_date"] = wide.fiscal_year.map(fd)
        wide["period_end"] = pd.to_datetime(wide.period_end).dt.date
        est = wide.get("filing_date") is None or wide["filing_date"].isna()
        wide["filing_date_estimated"] = est if isinstance(est, bool) else est.values
        wide["filing_date"] = [pd.to_datetime(f).date() if pd.notna(f) else estimated_filing_date(p)
                               for f, p in zip(wide.get("filing_date", [None] * len(wide)), wide.period_end)]
        wide["source"] = self.source
        return wide

    def market(self, ticker: str) -> pd.DataFrame:
        if self._mkt.empty:
            return pd.DataFrame()
        d = self._mkt[self._mkt.ticker == ticker]
        return pd.DataFrame({"date": pd.to_datetime(d.date).dt.date, "share_price": d.share_price,
                             "market_cap": d.market_cap_cr, "shares_outstanding_cr": None,
                             "enterprise_value": None, "source": self.source})
