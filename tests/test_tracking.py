import numpy as np
import pandas as pd
import pytest

from src.database import repository as repo
from src.portfolio import tracking as tk


def _prices():
    idx = pd.bdate_range("2025-01-01", periods=60)
    return pd.DataFrame({"AAA": np.linspace(100, 130, 60), "BBB": np.linspace(50, 40, 60),
                         "^NSEI": np.linspace(1000, 1100, 60)}, index=idx)


def _holdings(date="2025-01-01"):
    return pd.DataFrame({"ticker": ["AAA", "BBB"], "shares": [10, 20], "buy_price": [100.0, 50.0],
                         "buy_date": [pd.Timestamp(date)] * 2, "company_name": ["A", "B"]})


def test_value_and_pnl():
    px = _prices()
    v = tk.value_holdings(_holdings(), tk.latest_prices(px))
    s = tk.summary(v)
    assert s["cost"] == pytest.approx(2000)
    assert s["value"] == pytest.approx(10 * 130 + 20 * 40)
    assert s["pnl"] == pytest.approx(100)
    assert v.weight.sum() == pytest.approx(1)


def test_missing_price_excluded_not_zero():
    h = _holdings()
    h.loc[1, "ticker"] = "ZZZ"
    s = tk.summary(tk.value_holdings(h, tk.latest_prices(_prices())))
    assert s["n_missing"] == 1
    assert s["pnl"] == pytest.approx(300)       # only AAA counted on both sides
    assert s["pnl_pct"] == pytest.approx(0.3)


def test_value_path_starts_at_cost_and_respects_buy_date():
    px = _prices()
    h = _holdings()
    h.loc[1, "buy_date"] = px.index[30]
    path = tk.value_path(h, px)
    assert path.invested.iloc[0] == pytest.approx(1000)       # only AAA at the start
    assert path.invested.iloc[-1] == pytest.approx(2000)
    assert path.value.iloc[-1] == pytest.approx(10 * 130 + 20 * 40)


def test_index_return_and_rebase():
    px = _prices()
    assert tk.pick_index(px) == "^NSEI"
    assert tk.index_return_since(px, "^NSEI", px.index[0]) == pytest.approx(0.1)
    assert tk.rebase(px["^NSEI"], 100).iloc[0] == 100


def test_parse_download_both_layouts():
    idx = pd.date_range("2025-01-01", periods=3, tz="Asia/Kolkata")
    a = pd.DataFrame(np.arange(6.0).reshape(3, 2), index=idx,
                     columns=pd.MultiIndex.from_product([["Close"], ["AAA.NS", "BBB.NS"]]))
    b = pd.DataFrame(np.arange(6.0).reshape(3, 2), index=idx,
                     columns=pd.MultiIndex.from_product([["AAA.NS", "BBB.NS"], ["Close"]]))
    for raw in (a, b):
        out = tk.parse_download(raw, ["AAA.NS", "BBB.NS"], suffix=".NS")
        assert list(out.columns) == ["AAA", "BBB"] and out.index.tz is None and len(out) == 3
    single = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx[:2])
    assert list(tk.parse_download(single, ["AAA"], ".NS").columns) == ["AAA"]
    assert tk.parse_download(pd.DataFrame(), ["AAA"]).empty


def test_stock_stats():
    idx = pd.bdate_range(end="2026-01-30", periods=400)
    s = pd.Series(np.linspace(100, 200, 400), index=idx)
    st = tk.stock_stats(s)
    assert st["last"] == 200 and st["from_hi"] == pytest.approx(0)
    assert st["ret_1Y"] > 0 and np.isnan(st["ret_5Y"]) and st["max_drawdown"] == pytest.approx(0)
    assert tk.stock_stats(pd.Series(dtype=float))["last"] != tk.stock_stats(pd.Series(dtype=float))["last"]


def test_monthly_fallback():
    panel = pd.DataFrame({"company_id": [1, 1, 2], "date": ["2025-01-31", "2025-02-28", "2025-01-31"], "share_price": [10, 11, 5]})
    bench = pd.DataFrame({"symbol": ["^NSEI"] * 2, "date": ["2025-01-31", "2025-02-28"], "close": [100, 101]})
    w = tk.monthly_fallback(panel, {1: "AAA", 2: "BBB"}, bench)
    assert set(w.columns) == {"AAA", "BBB", "^NSEI"} and len(w) == 2


def test_portfolio_storage_roundtrip(tmp_path):
    from sqlalchemy import create_engine
    e = create_engine(f"sqlite:///{tmp_path}/w.db")
    repo.init_db(e)
    h = _holdings()
    pid = repo.save_portfolio("Test", 2000.0, h, engine=e)
    got = repo.load_holdings(pid, engine=e)
    assert len(got) == 2 and pd.to_datetime(got.buy_date).iloc[0] == pd.Timestamp("2025-01-01")
    h2 = h.copy(); h2.loc[0, "shares"] = 99
    repo.replace_holdings(pid, h2, engine=e)
    assert repo.load_holdings(pid, engine=e).shares.iloc[0] == 99
    assert len(repo.load_portfolios(engine=e)) == 1
    repo.delete_portfolio(pid, engine=e)
    assert repo.load_portfolios(engine=e).empty and repo.load_holdings(pid, engine=e).empty
