import numpy as np
import pandas as pd
import streamlit as st

from src.database import repository as repo
from src.reporting.exports import to_csv_bytes
from src.ui import embed
from src.ui import portfolio_state as ps
from src.ui import state

uni = state.universe()
d = ps.class_data()
scored = ps.scored(d, False, True)
st.title("Screen")
state.provenance(uni)

t = uni.table[["company_id", "total_score", "passes_screen", "archetype", "n_flags", "ev_ebitda", "data_quality"]]
x = scored[["company_id", "company_name", "ticker", "sector", "market_cap", "cap_segment", "class_score", "rank_in_segment",
            "roe", "pe", "ineligible_reason"]].merge(t, on="company_id", how="left")
x["class_score"] = x["class_score"].astype(float)

tab = st.radio("View", ["Companies", "More PE filters", "Market overview"], horizontal=True, label_visibility="collapsed")
if tab == "More PE filters":
    embed.run_page("deal_universe")
elif tab == "Market overview":
    embed.run_page("executive_dashboard")
else:
    c = st.columns([2, 2, 2, 1, 1])
    q = c[0].text_input("Search name or ticker")
    sectors = c[1].multiselect("Sector", sorted(x.sector.dropna().unique()))
    segs = c[2].multiselect("Size", ["large", "mid", "small"], format_func=ps.SEG_LABELS.get)
    min_deal = c[3].number_input("Min deal score", 0, 100, 0, 5)
    min_port = c[4].number_input("Min portfolio score", 0, 100, 0, 5)
    both = st.toggle("Only companies that pass the PE screen AND have a portfolio score")
    m = pd.Series(True, index=x.index)
    if q:
        m &= x.company_name.str.contains(q, case=False, na=False) | x.ticker.str.contains(q, case=False, na=False)
    if sectors: m &= x.sector.isin(sectors)
    if segs: m &= x.cap_segment.isin(segs)
    if min_deal: m &= x.total_score.fillna(-1) >= min_deal
    if min_port: m &= x.class_score.fillna(-1) >= min_port
    if both: m &= x.passes_screen.fillna(False).astype(bool) & x.class_score.notna()
    v = x[m].sort_values(["total_score", "class_score"], ascending=False)
    st.caption(f"{len(v):,} of {len(x):,} companies. Click a column header to sort. Select a row to open it in Company.")
    show = v[["company_name", "ticker", "sector", "cap_segment", "market_cap", "total_score", "passes_screen", "archetype",
              "class_score", "rank_in_segment", "roe", "pe", "n_flags"]].rename(columns={
        "company_name": "Company", "ticker": "Ticker", "sector": "Sector", "cap_segment": "Size", "market_cap": "Mkt cap (₹ cr)",
        "total_score": "Deal score", "passes_screen": "Passes PE screen", "archetype": "PE archetype",
        "class_score": "Portfolio score", "rank_in_segment": "Rank in size group", "roe": "ROE", "pe": "P/E", "n_flags": "Red flags"})
    show["ROE"] = show["ROE"] * 100
    ev = st.dataframe(show.replace([np.inf, -np.inf], np.nan), hide_index=True, width="stretch", height=520,
                      on_select="rerun", selection_mode="single-row", key="screen_table",
                      column_config={"Mkt cap (₹ cr)": st.column_config.NumberColumn(format="%d"),
                                     "Deal score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                                     "Portfolio score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                                     "ROE": st.column_config.NumberColumn(format="%.1f%%"),
                                     "P/E": st.column_config.NumberColumn(format="%.1f")})
    rows = ev.selection.rows if ev and ev.selection else []
    b1, b2 = st.columns([1, 1])
    if rows:
        cid = int(v.iloc[rows[0]].company_id)
        st.session_state.company_id = cid
        st.success(f"Selected: {v.iloc[rows[0]].company_name}. Open **Company & underwriting** in the left menu to see everything about it.")
        if b1.button("Add to deal pipeline "):
            repo.save_pipeline_entry(cid, "New", "", "", "", "")
            st.toast("Added to the pipeline")
    b2.download_button("Download this table (CSV)", to_csv_bytes(show), "screen.csv")
