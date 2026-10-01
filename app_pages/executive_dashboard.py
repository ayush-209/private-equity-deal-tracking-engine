import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.config import screening_config
from src.database import repository as repo
from src.ui import state
from src.ui.charts import ARCHETYPE_COLORS, TEAL
from src.ui.formatting import mult, pct

uni = state.universe()
t = uni.table
st.title("Executive dashboard")
state.provenance(uni)

total = len(t) + len(uni.excluded)
passing = t[t.passes_screen]
c = st.columns(6)
c[0].metric("Universe", f"{total:,}", help="All companies with stored statements, incl. excluded financials")
c[1].metric("Passing", f"{len(passing):,}", help="Score ≥ threshold, data quality High/Medium, no critical flags, all dimensions scored")
c[2].metric("Median score", f"{t.total_score.median():.0f}/100")
c[3].metric("Median ROIC", pct(t.roic.median()))
c[4].metric("Median EBITDA gr.", pct(t.ebitda_growth.median()))
c[5].metric("Median ND/EBITDA", mult(t.net_debt_to_ebitda.median()))

left, right = st.columns([1, 1.25])
with left:
    stages = [("Universe", total), ("Operating companies", len(t)),
              ("Data quality High/Medium", int(t.data_quality.isin(["High", "Medium"]).sum())),
              ("No critical red flags", int(((t.critical_flags == 0) & t.data_quality.isin(["High", "Medium"])).sum())),
              ("Pass screen", len(passing)), ("Pipeline", len(repo.load_pipeline()))]
    fig = go.Figure(go.Funnel(y=[s for s, _ in stages], x=[n for _, n in stages], marker_color=TEAL,
                              textinfo="value+percent initial"))
    fig.update_layout(title="Screening funnel", height=380)
    st.plotly_chart(fig, width="stretch")
with right:
    sec = t.groupby("sector").agg(companies=("company_id", "size"), passing=("passes_screen", "sum")).reset_index()
    sec = sec.melt("sector", var_name="group", value_name="n")
    fig = px.bar(sec, y="sector", x="n", color="group", barmode="group", orientation="h", height=380,
                 title="Sector distribution", labels={"n": "Companies", "sector": ""})
    fig.update_layout(legend_title_text="", yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")

left, right = st.columns([1, 1.25])
with left:
    fig = px.histogram(t, x="total_score", nbins=25, color="passes_screen", height=380, title="Score distribution",
                       labels={"total_score": "Screening score", "passes_screen": "Passes"},
                       color_discrete_map={True: TEAL, False: "#C9D2D2"})
    fig.add_vline(x=screening_config()["pass_rules"]["min_total_score"], line_dash="dot")
    st.plotly_chart(fig, width="stretch")
with right:
    d = t.dropna(subset=["revenue_cagr_3y", "roic"]).copy()
    d["size"] = d.market_cap.fillna(d.market_cap.median()).clip(lower=1) ** 0.5
    fig = px.scatter(d, x="revenue_cagr_3y", y="roic", color="archetype", size="size", size_max=26, height=380,
                     hover_name="company_name", color_discrete_map=ARCHETYPE_COLORS,
                     hover_data={"size": False, "total_score": ":.0f", "revenue_cagr_3y": ":.1%", "roic": ":.1%"},
                     labels={"revenue_cagr_3y": "3y revenue CAGR", "roic": "ROIC"}, title="Growth vs ROIC")
    fig.update_xaxes(tickformat=".0%"); fig.update_yaxes(tickformat=".0%")
    fig.update_layout(legend_title_text="")
    st.plotly_chart(fig, width="stretch")

st.subheader("Archetypes among companies passing the screen")
a = passing.archetype.value_counts().rename_axis("Archetype").reset_index(name="Companies")
st.dataframe(a, hide_index=True, width="content")
if len(uni.excluded):
    with st.expander(f"{len(uni.excluded)} financial-sector companies excluded from the operating screen"):
        st.dataframe(uni.excluded[["company_name", "ticker", "industry", "exclusion_reason"]], hide_index=True,
                     width="stretch")
