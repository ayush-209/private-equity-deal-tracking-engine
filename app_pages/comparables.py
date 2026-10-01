import numpy as np
import plotly.express as px
import streamlit as st

from src.ui import state
from src.ui.charts import OCHRE, TEAL
from src.ui.formatting import mult, pct
from src.valuation.peers import explain_valuation, peer_members, peer_stats, pretty_peer_group

uni = state.universe()
cid = state.select_company(uni)
r = state.company_row(uni, cid)
t = uni.table
st.title(f"Comparable companies — {r.company_name}")
state.provenance(uni)

peers = peer_members(t, r.peer_group).copy()
ps = peer_stats(t, cid)
st.caption(f"Peer group: **{pretty_peer_group(r.peer_group)}** ({len(peers) - 1} peers). The group widens from industry to sector to the "
           "full universe when fewer than the configured minimum have a meaningful EV/EBITDA.")

c = st.columns(4)
names = {"ev_ebitda": "EV/EBITDA", "ev_sales": "EV/Sales", "pe": "P/E", "fcf_yield": "FCF yield"}
for col, (_, p) in zip(c, ps.iterrows()):
    f = pct if p.multiple == "fcf_yield" else mult
    col.metric(names[p.multiple], f(p.company),
               delta=None if np.isnan(p.premium_discount) else f"{p.premium_discount:+.0%} vs median",
               delta_color="inverse" if p.multiple != "fcf_yield" else "normal",
               help=f"Peer p25 {f(p.peer_p25)} · median {f(p.peer_median)} · p75 {f(p.peer_p75)} · n={p.n_peers}")

st.subheader("What may explain the valuation")
for n in explain_valuation(t, cid):
    st.markdown(f"- {n}")
st.caption("A low multiple is not treated as attractive by itself; it is read alongside growth, returns, cash conversion and leverage.")

peers["is_target"] = np.where(peers.company_id == cid, "Selected company", "Peer")
CAP = {"ev_ebitda": 60, "ev_sales": 20, "pe": 100, "net_debt_to_ebitda": 10}
LABELS = {**names, "revenue_cagr_3y": "3y revenue CAGR", "ebitda_margin": "EBITDA margin", "roic": "ROIC",
          "fcf_conversion": "FCF conversion", "net_debt_to_ebitda": "Net debt/EBITDA"}


def capped(df, col):
    """Drop extreme multiples (near-zero denominators) from charts only; medians above are unaffected."""
    if col not in CAP:
        return df, 0
    keep = df[col] <= CAP[col]
    return df[keep], int((~keep).sum())
cmap = {"Selected company": OCHRE, "Peer": TEAL}
left, right = st.columns(2)
with left:
    metric = st.selectbox("Compare on", ["ev_ebitda", "ev_sales", "pe", "fcf_yield", "revenue_cagr_3y", "ebitda_margin", "roic",
                                         "fcf_conversion", "net_debt_to_ebitda"], format_func=LABELS.get)
    d, hidden = capped(peers.dropna(subset=[metric]).sort_values(metric), metric)
    fig = px.bar(d, x=metric, y="company_name", color="is_target", orientation="h", color_discrete_map=cmap,
                 height=max(320, 22 * len(d)), labels={"company_name": "", metric: names.get(metric, metric)})
    fig.update_layout(showlegend=False, yaxis={"categoryorder": "total ascending"})
    if hidden:
        st.caption(f"{hidden} peer(s) above {CAP[metric]}x hidden from the chart (near-zero denominator).")
    if metric in {"fcf_yield", "revenue_cagr_3y", "ebitda_margin", "roic", "fcf_conversion"}:
        fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(fig, width="stretch")
with right:
    xm = st.selectbox("Valuation against", ["roic", "revenue_cagr_3y", "ebitda_margin", "fcf_conversion"], format_func=LABELS.get)
    d, hidden2 = capped(peers.dropna(subset=["ev_ebitda", xm]), "ev_ebitda")
    fig = px.scatter(d, x=xm, y="ev_ebitda", color="is_target", hover_name="company_name", color_discrete_map=cmap,
                     height=420,
                     labels={"ev_ebitda": "EV/EBITDA (x)", xm: LABELS[xm]})
    if len(d) >= 5:  # least-squares line across the peer group
        k, b = np.polyfit(d[xm], d.ev_ebitda, 1)
        xs = np.linspace(d[xm].min(), d[xm].max(), 20)
        fig.add_scatter(x=xs, y=k * xs + b, mode="lines", line=dict(color="#9AA6A6", dash="dot"), hoverinfo="skip")
    fig.update_xaxes(tickformat=".0%")
    fig.update_layout(showlegend=False, title="Is the multiple consistent with fundamentals?")
    st.plotly_chart(fig, width="stretch")
    st.caption("Points above the line trade richer than peers with similar fundamentals; below, cheaper.")

st.subheader("Peer table")
cols = ["company_name", "ticker", "market_cap", "revenue_growth", "ebitda_margin", "roic", "ev_ebitda", "ev_sales", "pe", "fcf_yield", "total_score"]
d = peers[cols].copy()
for c_ in ["revenue_growth", "ebitda_margin", "roic", "fcf_yield"]:
    d[c_] *= 100
st.dataframe(d.sort_values("ev_ebitda"), hide_index=True, width="stretch", column_config={
    "company_name": "Company", "market_cap": st.column_config.NumberColumn("Mkt cap (₹ cr)", format="%.0f"),
    "revenue_growth": st.column_config.NumberColumn("Rev growth", format="%.1f%%"),
    "ebitda_margin": st.column_config.NumberColumn("EBITDA margin", format="%.1f%%"),
    "roic": st.column_config.NumberColumn("ROIC", format="%.1f%%"),
    "ev_ebitda": st.column_config.NumberColumn("EV/EBITDA", format="%.1fx"),
    "ev_sales": st.column_config.NumberColumn("EV/Sales", format="%.2fx"),
    "pe": st.column_config.NumberColumn("P/E", format="%.1fx"),
    "fcf_yield": st.column_config.NumberColumn("FCF yield", format="%.1f%%"),
    "total_score": st.column_config.NumberColumn("Score", format="%.0f")})
