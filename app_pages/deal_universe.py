import numpy as np
import pandas as pd
import streamlit as st

from src.database import repository as repo
from src.reporting.exports import to_csv_bytes, to_excel_bytes, to_json_bytes
from src.ui import state

uni = state.universe()
t = uni.table.copy()
st.title("Deal universe")
state.provenance(uni)


def rng(col, label, scale=1.0, fmt="%.0f", step=None):
    s = t[col].replace([np.inf, -np.inf], np.nan).dropna() * scale
    if s.empty:
        return None
    lo, hi = float(np.floor(s.quantile(0.0))), float(np.ceil(s.quantile(1.0)))
    if lo == hi:
        hi = lo + 1
    return st.slider(label, lo, hi, (lo, hi), step=step, format=fmt, key=f"f_{col}")


with st.expander("Filters", expanded=False):
    c1, c2, c3 = st.columns(3)
    with c1:
        sectors = st.multiselect("Sector", sorted(t.sector.dropna().unique()))
        inds = st.multiselect("Industry", sorted(t[t.sector.isin(sectors) if sectors else t.index == t.index].industry.unique()))
        only_pass = st.toggle("Only companies passing the screen", value=False)
        quality = st.multiselect("Data quality", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
        search = st.text_input("Search name or ticker")
    with c2:
        f_mcap = rng("market_cap", "Market cap (₹ cr)")
        f_rev = rng("revenue", "Revenue (₹ cr)")
        f_cagr = rng("revenue_cagr_3y", "3y revenue CAGR (%)", 100, "%.0f%%")
        f_marg = rng("ebitda_margin", "EBITDA margin (%)", 100, "%.0f%%")
    with c3:
        f_roic = rng("roic", "ROIC (%)", 100, "%.0f%%")
        f_fcfc = st.slider("Minimum FCF conversion (%)", -100, 100, -100, 5)
        f_nd = st.slider("Maximum net debt/EBITDA (x)", 0.0, 10.0, 10.0, 0.25, help="Net-cash companies always pass this filter")
        f_ev = st.slider("Maximum EV/EBITDA (x)", 0, 100, 100)
        f_score = st.slider("Minimum score", 0, 100, 0)

m = pd.Series(True, index=t.index)
if sectors: m &= t.sector.isin(sectors)
if inds: m &= t.industry.isin(inds)
if only_pass: m &= t.passes_screen
m &= t.data_quality.isin(quality)
if search: m &= t.company_name.str.contains(search, case=False) | t.ticker.str.contains(search, case=False)
for f, col, sc in [(f_mcap, "market_cap", 1), (f_rev, "revenue", 1), (f_cagr, "revenue_cagr_3y", 100),
                   (f_marg, "ebitda_margin", 100), (f_roic, "roic", 100)]:
    if f and (f[0] > float(np.floor((t[col] * sc).min())) or f[1] < float(np.ceil((t[col] * sc).max()))):
        m &= (t[col] * sc).between(*f)
if f_fcfc > -100: m &= t.fcf_conversion * 100 >= f_fcfc
if f_nd < 10: m &= (t.net_debt <= 0) | (t.net_debt_to_ebitda <= f_nd)
if f_ev < 100: m &= t.ev_ebitda <= f_ev
if f_score > 0: m &= t.total_score >= f_score
f = t[m].sort_values("total_score", ascending=False)

cols = {"company_name": "Company", "ticker": "Ticker", "sector": "Sector", "industry": "Industry",
        "market_cap": "Mkt cap (₹ cr)", "revenue": "Revenue (₹ cr)", "revenue_cagr_3y": "Rev CAGR 3y",
        "ebitda_margin": "EBITDA margin", "roic": "ROIC", "fcf_conversion": "FCF conv.",
        "net_debt_to_ebitda": "ND/EBITDA", "ev_ebitda": "EV/EBITDA", "total_score": "Score",
        "archetype": "Archetype", "leverage_class": "Leverage", "n_flags": "Flags", "data_quality": "Data",
        "passes_screen": "Passes"}
d = f[["company_id", *cols]].copy()
for c in ["revenue_cagr_3y", "ebitda_margin", "roic", "fcf_conversion"]:
    d[c] = d[c] * 100
num = [c for c in cols if c not in {"company_name", "ticker", "sector", "industry", "archetype", "leverage_class",
                                    "data_quality", "passes_screen"}]
d[num] = d[num].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
st.markdown(f"**{len(f):,}** of {len(t):,} companies match. Click a row to select it for the company pages.")
cfg = {
    "company_id": None,
    "market_cap": st.column_config.NumberColumn(cols["market_cap"], format="%.0f"),
    "revenue": st.column_config.NumberColumn(cols["revenue"], format="%.0f"),
    **{c: st.column_config.NumberColumn(cols[c], format="%.1f%%") for c in ["revenue_cagr_3y", "ebitda_margin", "roic", "fcf_conversion"]},
    "net_debt_to_ebitda": st.column_config.NumberColumn(cols["net_debt_to_ebitda"], format="%.1fx", help="Negative = net cash"),
    "ev_ebitda": st.column_config.NumberColumn(cols["ev_ebitda"], format="%.1fx"),
    "total_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
    **{c: st.column_config.Column(cols[c]) for c in ["company_name", "ticker", "sector", "industry", "archetype",
                                                      "leverage_class", "n_flags", "data_quality", "passes_screen"]},
}
ev = st.dataframe(d, column_config=cfg, hide_index=True, width="stretch", height=520,
                  on_select="rerun", selection_mode="single-row", key="universe_table")
if ev.selection.rows:
    sel = d.iloc[ev.selection.rows[0]]
    st.session_state.company_id = int(sel.company_id)
    a, b, c = st.columns([2, 1, 1])
    a.success(f"Selected **{sel.company_name}** — open any page under Company.")
    if b.button("Open deep dive", width="stretch"):
        st.switch_page("app_pages/company_deep_dive.py")
    if c.button("Add to pipeline", width="stretch"):
        if int(sel.company_id) in set(repo.load_pipeline().company_id):
            st.info("Already in the pipeline.")
        else:
            repo.save_pipeline_entry(int(sel.company_id), "Screened")
            st.toast(f"Added {sel.company_name} to the pipeline")

st.divider()
e1, e2, e3, e4 = st.columns(4)
e1.download_button("Filtered companies (CSV)", to_csv_bytes(f.drop(columns=["archetype_reasons"])), "filtered_companies.csv",
                   width="stretch")
e2.download_button("Screening universe (CSV)", to_csv_bytes(t.drop(columns=["archetype_reasons"])), "screening_universe.csv",
                   width="stretch")
e3.download_button("Financial metrics (Excel)", to_excel_bytes({"screen": t, "metric_history": uni.history,
                                                                 "score_detail": uni.detail}),
                   "pe_screen.xlsx", width="stretch")
e4.download_button("Screening output (JSON)", to_json_bytes({"weights": state.weights(), "companies": f}),
                   "screening_output.json", width="stretch")
