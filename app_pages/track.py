import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.database import repository as repo
from src.portfolio import tracking as tk
from src.ui import state
from src.ui.formatting import indian_group
from src.ui.live import daily_prices

uni = state.universe()
st.title("Track my portfolio")
st.caption("Saved portfolios valued at the latest prices.")

# ------------------------------------------------------------------ portfolios
pf = repo.load_portfolios()
with st.expander("Import a portfolio from CSV" + ("" if len(pf) else " (or build one on the Portfolio page)"), expanded=not len(pf)):
    st.caption("Columns: ticker, shares, buy_price, buy_date (YYYY-MM-DD). This is also how to restore a portfolio you "
               "downloaded earlier, since saved portfolios live in the app's workspace and are cleared when the app restarts.")
    up = st.file_uploader("CSV file", type="csv")
    nm = st.text_input("Name for the imported portfolio", "Imported portfolio")
    if up is not None and st.button("Import"):
        df = pd.read_csv(up)
        need = {"ticker", "shares", "buy_price", "buy_date"}
        if not need <= set(df.columns):
            st.error(f"Missing columns: {', '.join(sorted(need - set(df.columns)))}")
        else:
            pid = repo.save_portfolio(nm, float((df.shares * df.buy_price).sum()), df)
            st.session_state["track_pid"] = pid
            st.rerun()
if pf.empty:
    st.info("No saved portfolios yet. Build one on the **Portfolio** page and press *Save to Track*.")
    st.stop()

ids = pf.portfolio_id.tolist()
cur = st.session_state.get("track_pid")
pid = st.selectbox("Portfolio", ids, index=ids.index(cur) if cur in ids else 0,
                   format_func=lambda i: pf.set_index("portfolio_id").loc[i, "name"])
st.session_state["track_pid"] = pid
h = repo.load_holdings(pid)
h["buy_date"] = pd.to_datetime(h.buy_date)

start = (h.buy_date.min() - pd.Timedelta(days=10)).date().isoformat()
live = daily_prices(tuple(h.ticker.unique()), start)
is_live = not live.empty and any(t in live.columns for t in h.ticker)
if is_live:
    prices, src = live, "Daily closes from Yahoo Finance, refreshed every 15 minutes."
else:
    panel = repo.load_price_panel()
    comp = repo.load_companies()
    m_ = dict(zip(comp.company_id, comp.ticker))
    prices = tk.monthly_fallback(panel, m_, repo.load_benchmark())
    src = "Live prices are unavailable right now, so this uses the monthly closes stored in the database (can be weeks old)."
last = tk.latest_prices(prices)
val = tk.value_holdings(h, last)
sm = tk.summary(val)
idx = tk.pick_index(prices)

k = st.columns(5)
k[0].metric("Current value", f"₹{indian_group(sm['value'])}" if pd.notna(sm["value"]) else "n/a")
k[1].metric("Invested", f"₹{indian_group(sm['cost'])}")
k[2].metric("Profit / loss", f"₹{indian_group(sm['pnl'])}" if pd.notna(sm["pnl"]) else "n/a",
            f"{sm['pnl_pct']:+.1%}" if pd.notna(sm["pnl_pct"]) else None)
if idx:
    ir = tk.index_return_since(prices, idx, h.buy_date.min())
    k[3].metric(f"Index since start", f"{ir:+.1%}", help=f"{idx}, from the first buy date.")
    k[4].metric("vs index", f"{(sm['pnl_pct'] - ir) * 100:+.1f} pts" if pd.notna(sm["pnl_pct"]) and pd.notna(ir) else "n/a")
else:
    k[3].metric("Index since start", "n/a")
st.caption(src + (f" {sm['n_missing']} holding(s) have no price and are left out of profit/loss." if sm["n_missing"] else ""))

path = tk.value_path(h, prices)
if len(path) > 1:
    fig = go.Figure()
    fig.add_scatter(x=path.index, y=path.value, name="Portfolio value", line_color="#0E5C63")
    fig.add_scatter(x=path.index, y=path.invested, name="Amount invested", line=dict(color="#9AA6A6", dash="dot"))
    if idx:
        ix = prices[idx].dropna()
        ix = ix[ix.index >= path.index[0]]
        if len(ix):
            fig.add_scatter(x=ix.index, y=tk.rebase(ix, float(path.invested.iloc[0])), name=f"{idx} (same start)",
                            line_color="#B7862B")
    fig.update_layout(height=380, yaxis_title="₹", legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, width="stretch")
else:
    st.info("Not enough price history since the buy date to draw a chart yet.")

show = val.assign(weight=val.weight * 100, pnl_pct=val.pnl_pct * 100)[
    ["company_name", "ticker", "segment", "sector", "shares", "buy_price", "price", "cost", "value", "pnl", "pnl_pct", "weight"]]
st.dataframe(show.rename(columns={"company_name": "Company", "ticker": "Ticker", "segment": "Size", "sector": "Sector",
                                  "shares": "Shares", "buy_price": "Buy price", "price": "Latest price", "cost": "Cost (₹)",
                                  "value": "Value (₹)", "pnl": "P&L (₹)", "pnl_pct": "P&L %", "weight": "Weight %"}),
             hide_index=True, width="stretch",
             column_config={"Cost (₹)": st.column_config.NumberColumn(format="%d"),
                            "Value (₹)": st.column_config.NumberColumn(format="%d"),
                            "P&L (₹)": st.column_config.NumberColumn(format="%d"),
                            "P&L %": st.column_config.NumberColumn(format="%+.1f%%"),
                            "Weight %": st.column_config.NumberColumn(format="%.1f"),
                            "Buy price": st.column_config.NumberColumn(format="%.2f"),
                            "Latest price": st.column_config.NumberColumn(format="%.2f")})

with st.expander("Correct shares or buy prices to match your real trades"):
    ed = st.data_editor(h[["ticker", "company_name", "shares", "buy_price", "buy_date", "segment", "sector"]],
                        num_rows="dynamic", hide_index=True, key=f"ed_{pid}")
    if st.button("Save changes"):
        repo.replace_holdings(pid, ed)
        st.rerun()
c1, c2 = st.columns(2)
c1.download_button("Download this portfolio (CSV)", h[["ticker", "company_name", "shares", "buy_price", "buy_date", "segment", "sector"]]
                   .to_csv(index=False).encode(), "my_portfolio.csv")
if c2.button("Delete this portfolio"):
    repo.delete_portfolio(pid)
    st.session_state.pop("track_pid", None)
    st.rerun()
