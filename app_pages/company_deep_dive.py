import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src.reporting.exports import to_json_bytes
from src.reporting.ic import deep_dive_pdf, ic_summary
from src.screening.engine import DIM_LABELS, DIMENSIONS
from src.ui import state
from src.ui.charts import OCHRE, RED, SLATE, TEAL
from src.ui.components import company_header, flag_list
from src.ui.formatting import days, mult, pct

uni = state.universe()
cid = state.select_company(uni)
r = state.company_row(uni, cid)
h = uni.history[uni.history.company_id == cid].sort_values("fiscal_year")
flags = uni.flags.get(cid, [])
company_header(r)
state.provenance(uni)
fy = "FY" + h.fiscal_year.astype(str)

st.subheader("Financial trends")
tabs = st.tabs(["Revenue & EBITDA", "Cash flow", "Debt", "Returns", "Working capital"])
with tabs[0]:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=fy, y=h.revenue, name="Revenue", marker_color="#C9D8D9")
    fig.add_bar(x=fy, y=h.ebitda, name="EBITDA", marker_color=TEAL)
    fig.add_scatter(x=fy, y=h.ebitda_margin, name="EBITDA margin", mode="lines+markers", line_color=OCHRE, secondary_y=True)
    fig.update_yaxes(title_text="₹ cr", secondary_y=False); fig.update_yaxes(tickformat=".1%", secondary_y=True, showgrid=False)
    fig.update_layout(barmode="group", height=380, legend_orientation="h")
    st.plotly_chart(fig, width="stretch")
with tabs[1]:
    fig = go.Figure()
    fig.add_bar(x=fy, y=h.ebitda, name="EBITDA", marker_color="#C9D8D9")
    fig.add_bar(x=fy, y=h.operating_cash_flow, name="Operating cash flow", marker_color=SLATE)
    fig.add_bar(x=fy, y=h.free_cash_flow, name="Free cash flow", marker_color=TEAL)
    fig.update_layout(barmode="group", height=380, yaxis_title="₹ cr", legend_orientation="h",
                      title="Does EBITDA turn into cash?")
    st.plotly_chart(fig, width="stretch")
with tabs[2]:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=fy, y=h.total_debt, name="Gross debt", marker_color=SLATE)
    fig.add_bar(x=fy, y=h.net_debt, name="Net debt", marker_color=TEAL)
    fig.add_scatter(x=fy, y=h.net_debt_to_ebitda, name="Net debt/EBITDA", line_color=RED, secondary_y=True)
    fig.update_yaxes(title_text="₹ cr", secondary_y=False); fig.update_yaxes(ticksuffix="x", secondary_y=True, showgrid=False)
    fig.update_layout(barmode="group", height=380, legend_orientation="h")
    st.plotly_chart(fig, width="stretch")
with tabs[3]:
    fig = go.Figure()
    for col, nm, c in [("roic", "ROIC", TEAL), ("roe", "ROE", OCHRE), ("ebit_margin", "EBIT margin", SLATE)]:
        fig.add_scatter(x=fy, y=h[col], name=nm, mode="lines+markers", line_color=c)
    fig.update_layout(height=380, yaxis_tickformat=".0%", legend_orientation="h")
    st.plotly_chart(fig, width="stretch")
with tabs[4]:
    fig = go.Figure()
    for col, nm, c in [("dso", "DSO", TEAL), ("dio", "DIO", OCHRE), ("dpo", "DPO", SLATE),
                       ("cash_conversion_cycle", "Cash conversion cycle", RED)]:
        fig.add_scatter(x=fy, y=h[col], name=nm, mode="lines+markers", line_color=c)
    fig.update_layout(height=380, yaxis_title="Days", legend_orientation="h")
    st.plotly_chart(fig, width="stretch")
    st.caption(f"DIO/DPO basis: {h.dio_dpo_basis.iloc[-1]}.")

st.subheader("Key metrics")
c = st.columns(8)
for col, (lab, val) in zip(c, [("Revenue CAGR 3y", pct(r.revenue_cagr_3y)), ("EBITDA CAGR 3y", pct(r.ebitda_cagr_3y)),
                               ("ROIC", pct(r.roic)), ("ROE", pct(r.roe)), ("FCF conversion", pct(r.fcf_conversion)),
                               ("Net debt/EBITDA", "Net cash" if r.net_debt <= 0 else mult(r.net_debt_to_ebitda)),
                               ("Interest cover", "No interest" if r.interest_coverage >= 99 else mult(r.interest_coverage)), ("Cash conv. cycle", days(r.cash_conversion_cycle))]):
    col.metric(lab, val)

st.subheader("Screening result")
left, right = st.columns([1.1, 1])
with left:
    sc = pd.DataFrame([{"dimension": DIM_LABELS[d], "points": r[f"{d}_score"], "max": r[f"{d}_max"]} for d in DIMENSIONS])
    fig = go.Figure()
    fig.add_bar(y=sc.dimension, x=sc["max"], orientation="h", marker_color="#E7ECEC", name="Weight", hoverinfo="skip")
    fig.add_bar(y=sc.dimension, x=sc.points, orientation="h", marker_color=TEAL, name="Points",
                text=[f"{p:.1f}/{m:.0f}" if pd.notna(p) else "insufficient data" for p, m in zip(sc.points, sc["max"])],
                textposition="outside")
    fig.update_layout(barmode="overlay", height=300, showlegend=False, yaxis_autorange="reversed",
                      title=f"Overall score {r.total_score:.0f}/100" + (" (partial)" if r.score_is_partial else ""))
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Percentiles computed within peer group: {r.scoring_peer_group}. "
               + (f"Fails screen: {r.fail_reasons}." if not r.passes_screen else "Passes the screen."))
with right:
    st.markdown(f"**Archetype: {r.archetype}**")
    for reason in r.archetype_reasons:
        st.markdown(f"- {reason}")
    st.markdown(f"**Data quality: {r.data_quality}** — {r.quality_reasons}")
with st.expander("Show every metric behind the score"):
    det = uni.detail[uni.detail.company_id == cid].copy()
    det["dimension"] = det.dimension.map(DIM_LABELS)
    det["percentile"] = det.percentile * 100
    st.dataframe(det[["dimension", "metric", "value", "direction", "percentile"]], hide_index=True, width="stretch",
                 column_config={"percentile": st.column_config.ProgressColumn("Peer percentile", min_value=0, max_value=100, format="%.0f"),
                                "value": st.column_config.NumberColumn("Value", format="%.4f")})

st.subheader(f"Red flags ({len(flags)})")
flag_list(flags)

st.divider()
s = ic_summary(uni, cid)
a, b = st.columns(2)
a.download_button("Export deep dive (PDF)", deep_dive_pdf(s, h, flags), f"{r.ticker}_deep_dive.pdf", "application/pdf",
                  width="stretch")
b.download_button("Export company analysis (JSON)", to_json_bytes({"company": r.drop("archetype_reasons").to_dict(),
                  "archetype_reasons": r.archetype_reasons, "history": h, "flags": flags}),
                  f"{r.ticker}_analysis.json", width="stretch")
