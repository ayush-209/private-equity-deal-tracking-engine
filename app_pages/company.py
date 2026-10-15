import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.database import repository as repo
from src.portfolio import tracking as tk
from src.portfolio.explain import why_text
from src.ui import embed
from src.ui import portfolio_state as ps
from src.ui import state
from src.ui.formatting import inr_cr
from src.ui.live import daily_prices

uni = state.universe()
d = ps.class_data()
scored = ps.scored(d, False, True)
cid = state.select_company(uni, "Company", container=st)
r = state.company_row(uni, cid)
sc = scored[scored.company_id == cid]
st.title(r.company_name)
st.caption(f"{r.ticker} · {r.sector} / {r.industry}")
state.provenance(uni)

c = st.columns(5)
c[0].metric("Market cap", inr_cr(r.market_cap))
c[1].metric("Deal score", f"{r.total_score:.0f}/100" if pd.notna(r.total_score) else "n/a",
            help="Private-equity view. " + (r.fail_reasons or "Passes the screen."))
cs = sc.iloc[0] if len(sc) else None
if cs is not None and pd.notna(cs.class_score):
    c[2].metric("Portfolio score", f"{cs.class_score:.0f}/100", help=why_text(cs))
    c[3].metric("Size group", ps.SEG_LABELS.get(cs.cap_segment, "n/a"), help="Compared only with companies of this size.")
else:
    c[2].metric("Portfolio score", "n/a", help=(cs.ineligible_reason if cs is not None else "") or "Not enough data")
    c[3].metric("Size group", ps.SEG_LABELS.get(cs.cap_segment, "n/a") if cs is not None else "n/a")
c[4].metric("PE screen", "Passes" if r.passes_screen else "Does not pass")

VIEWS = ["Overview", "Price", "Peers", "Risks & diligence", "Deal economics", "Memo", "AI note", "Portfolio fit"]
view = st.radio("Section", VIEWS, horizontal=True, label_visibility="collapsed", key="company_view")
if view == "Overview":
    embed.run_page("company_deep_dive")
elif view == "Peers":
    embed.run_page("comparables")
elif view == "Risks & diligence":
    embed.run_page("risk_due_diligence")
elif view == "Deal economics":
    embed.run_page("deal_economics", "Deal assumptions")
elif view == "Memo":
    embed.run_page("investment_committee")
elif view == "AI note":
    embed.run_page("ai_research")
elif view == "Portfolio fit":
    st.subheader("How this company scores as a stock")
    if cs is None or pd.isna(cs.class_score):
        st.info(f"Not scored for portfolios: {(cs.ineligible_reason if cs is not None else '') or 'not enough data'}.")
    else:
        st.write(why_text(cs))
        rows = {n: cs.get(f"{k}_pct") for n, k in [("Growth", "growth"), ("Profitability", "profitability"),
                                                   ("Valuation", "valuation"), ("Risk", "risk"), ("Technical", "technical")]}
        fig = go.Figure(go.Bar(x=list(rows.values()), y=list(rows.keys()), orientation="h", marker_color="#0E5C63"))
        fig.update_xaxes(range=[0, 1], tickformat=".0%", title="Percentile within its size group (100% = best)")
        fig.update_layout(height=300)
        st.plotly_chart(fig, width="stretch")
        k = st.columns(6)
        for col, (lab, key, mult) in zip(k, [("Sales YoY", "sales_yoy", 100), ("EPS growth", "eps_growth", 100), ("ROE", "roe", 100),
                                              ("ROCE", "roce", 100), ("P/E", "pe", 1), ("Beta", "beta", 1)]):
            v = cs.get(key)
            col.metric(lab, "n/a" if pd.isna(v) else (f"{v * mult:.1f}%" if mult == 100 else f"{v:.2f}"))
else:   # Price
    st.subheader("Share price")
    hist = repo.load_market_history(cid)
    live = daily_prices((r.ticker,), (pd.Timestamp.today() - pd.DateOffset(years=5)).date().isoformat())
    if r.ticker in live.columns and live[r.ticker].notna().sum() > 20:
        s, note = live[r.ticker].dropna(), "Daily closes from Yahoo Finance (cached 15 minutes)."
    else:
        s = hist.assign(date=pd.to_datetime(hist.date)).set_index("date").share_price.dropna()
        note = "Live prices unavailable, showing the monthly closes stored in the database."
    if s.empty:
        st.info("No price history for this company.")
    else:
        rng = st.radio("Range", ["6M", "1Y", "3Y", "5Y", "Max"], index=1, horizontal=True)
        days = {"6M": 182, "1Y": 365, "3Y": 1095, "5Y": 1826, "Max": 99999}[rng]
        w = s[s.index >= s.index[-1] - pd.Timedelta(days=days)]
        stt = tk.stock_stats(s)
        ma = s.rolling(210 if len(s) > 400 else 10).mean()
        fig = go.Figure()
        fig.add_scatter(x=w.index, y=w, name="Close", line_color="#0E5C63")
        fig.add_scatter(x=w.index, y=ma.reindex(w.index), name="Long-run average (≈10 months)", line=dict(dash="dot", color="#B7862B"))
        fig.update_layout(height=380, yaxis_title="₹", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, width="stretch")
        m = st.columns(6)
        f = lambda v: "n/a" if pd.isna(v) else f"{v:+.1%}"  # noqa: E731
        m[0].metric("Last price", f"₹{stt['last']:,.2f}", help=f"{stt['last_date']:%d %b %Y}")
        m[1].metric("1 month", f(stt["ret_1M"])); m[2].metric("6 months", f(stt["ret_6M"]))
        m[3].metric("1 year", f(stt["ret_1Y"])); m[4].metric("From 52-week high", f(stt["from_hi"]))
        m[5].metric("Volatility (ann.)", "n/a" if pd.isna(stt["vol"]) else f"{stt['vol']:.0%}")
        st.caption(note + f" 52-week range ₹{stt['lo_52w']:,.0f} – ₹{stt['hi_52w']:,.0f}. Worst fall from a peak: "
                          f"{stt['max_drawdown']:.0%}.")
