import numpy as np
import plotly.graph_objects as go
import streamlit as st

from src.lbo.model import inputs_from_company, run_control, run_minority, sensitivity
from src.ui import state
from src.ui.formatting import inr_cr, mult, pct

uni = state.universe()
cid = state.select_company(uni)
r = state.company_row(uni, cid)
st.title(f"Deal economics — {r.company_name}")
state.provenance(uni)

if not (r.ebitda > 0):
    st.error("EBITDA is not positive, so neither structure can be underwritten on an EBITDA multiple. "
             "Use an EV/Sales or asset-based approach outside this tool.")
    st.stop()

seed = inputs_from_company(r)
sb = st.sidebar
sb.markdown("**Deal assumptions**")
mode = sb.radio("Structure", ["Control buyout (illustrative)", "Minority / PIPE stake"], key="lbo_mode")
control = mode.startswith("Control")
entry = sb.slider("Entry EV/EBITDA (x)", 3.0, 60.0, float(np.clip(seed.entry_multiple, 3, 60)), 0.5)
if control:
    lev = sb.slider("Debt/EBITDA (x)", 0.0, 7.0, 3.0, 0.25)
    rate = sb.slider("Interest rate", 0.06, 0.18, 0.11, 0.005, format="%.3f")
    fees = sb.slider("Transaction fees (% of EV)", 0.0, 0.05, 0.02, 0.005, format="%.3f")
    sweep = sb.slider("Cash sweep", 0.0, 1.0, 1.0, 0.05)
else:
    stake = sb.slider("Stake acquired", 0.05, 0.49, 0.20, 0.01, format="%.2f")
    prem = sb.slider("Premium to market value", -0.10, 0.40, 0.10, 0.01, format="%.2f")
    payout = sb.slider("Dividend payout ratio", 0.0, 0.8, 0.25, 0.05)
years = sb.slider("Holding period (years)", 3, 8, 5)
cagr = sb.slider("Revenue CAGR", -0.05, 0.35, float(seed.revenue_cagr), 0.01, format="%.2f")
m0 = float(r.ebitda / r.revenue)
exit_margin = sb.slider("EBITDA margin at exit", -0.05, 0.6, float(round(m0, 3)), 0.005, format="%.3f")
capex = sb.slider("Capex (% of revenue)", 0.0, 0.2, 0.04, 0.005, format="%.3f")
nwc = sb.slider("NWC (% of incremental revenue)", -0.1, 0.5, 0.15, 0.01, format="%.2f")
exit_mult = sb.slider("Exit EV/EBITDA (x)", 3.0, 60.0, float(np.clip(entry, 3, 60)), 0.5)

if control:
    p = inputs_from_company(r, entry_multiple=entry, debt_to_ebitda=lev, interest_rate=rate, fees_pct=fees,
                            cash_sweep=sweep, holding_years=years, revenue_cagr=cagr, exit_ebitda_margin=exit_margin,
                            capex_pct_revenue=capex, nwc_pct_incremental_revenue=nwc, exit_multiple=exit_mult)
    st.info("Illustrative only. Taking an Indian listed company private requires a delisting under the SEBI Delisting "
            "Regulations, and onshore bank funding of share acquisitions has historically been restricted; acquisition "
            "debt is usually raised through NCDs or offshore structures. Read these returns as a theoretical ceiling.",
            icon="ℹ️")
    res = run_control(p)
else:
    p = inputs_from_company(r, entry_multiple=entry, holding_years=years, revenue_cagr=cagr, exit_ebitda_margin=exit_margin,
                            capex_pct_revenue=capex, nwc_pct_incremental_revenue=nwc, exit_multiple=exit_mult,
                            stake_pct=stake, entry_premium=prem, payout_ratio=payout)
    res = run_minority(p)
if "error" in res:
    st.error(res["error"])
    st.stop()

c = st.columns(6)
if control:
    st.session_state[f"lbo_{cid}"] = res
    vals = [("Entry EV", inr_cr(res["entry_ev"])), ("Sponsor equity", inr_cr(res["sponsor_equity"])),
            ("Exit EV", inr_cr(res["exit_ev"])), ("Exit equity", inr_cr(res["exit_equity"])),
            ("MOIC", mult(res["moic"], 2)), ("IRR", pct(res["irr"]))]
else:
    vals = [("Investment", inr_cr(res["investment"])), ("Implied entry EV/EBITDA", mult(res["implied_entry_ev_ebitda"])),
            ("Exit EV", inr_cr(res["exit_ev"])), ("Stake value at exit", inr_cr(res["stake_exit_value"])),
            ("MOIC (incl. dividends)", mult(res["moic"], 2)), ("IRR", pct(res["irr"]))]
for col, (k, v) in zip(c, vals):
    col.metric(k, v)

ops = res["operating_case"].copy()
if control:
    t1, t2, t3, t4 = st.tabs(["Sources & uses", "Operating case", "Debt paydown", "Exit & value bridge"])
    with t1:
        su = res["sources_uses"].copy()
        su["amount"] = su.amount.map(inr_cr)
        st.dataframe(su.rename(columns={"item": "Sources and uses", "amount": "₹"}), hide_index=True, width="content")
        st.caption(f"Equity contribution {pct(res['equity_contribution_pct'])} of total uses. Year-1 EBITDA/interest "
                   f"{mult(res['min_icr_y1'])}. "
                   + (f"Existing net debt of {inr_cr(p.existing_net_debt)} is refinanced out of the purchase EV."
                      if p.existing_net_debt > 0 else
                      f"The target holds net cash of {inr_cr(-p.existing_net_debt)}; the equity purchase price is EV plus that cash."))
    with t2:
        st.dataframe(ops.style.format({"revenue": "{:,.0f}", "ebitda": "{:,.0f}", "ebitda_margin": "{:.1%}", "da": "{:,.0f}",
                                       "capex": "{:,.0f}", "delta_nwc": "{:,.0f}"}), hide_index=True, width="stretch")
    with t3:
        ds = res["debt_schedule"]
        fig = go.Figure()
        fig.add_bar(x=ds.year, y=ds.closing_debt, name="Closing debt", marker_color="#5B6770")
        fig.add_bar(x=ds.year, y=ds.debt_repaid, name="Repaid in year", marker_color="#0E5C63")
        fig.update_layout(barmode="group", height=340, xaxis_title="Year", yaxis_title="₹ cr", legend_orientation="h")
        st.plotly_chart(fig, width="stretch")
        st.dataframe(ds.style.format("{:,.0f}", subset=[c for c in ds.columns if c != "year"]), hide_index=True,
                     width="stretch")
    with t4:
        b = res["value_bridge"]
        b = b[b.value.abs() > 1e-6]
        fig = go.Figure(go.Waterfall(x=["Sponsor equity", *b.driver, "Exit equity"],
                                     y=[res["sponsor_equity"], *b.value, 0],
                                     measure=["absolute", *["relative"] * len(b), "total"],
                                     connector={"line": {"color": "#C9D2D2"}},
                                     increasing={"marker": {"color": "#2E7D4F"}}, decreasing={"marker": {"color": "#B23A3A"}},
                                     totals={"marker": {"color": "#0E5C63"}}))
        fig.update_layout(height=380, title="Where the equity value comes from (₹ cr)")
        st.plotly_chart(fig, width="stretch")
        st.markdown(f"Exit EBITDA {inr_cr(res['exit_ebitda'])} × {mult(p.exit_multiple)} = exit EV {inr_cr(res['exit_ev'])}; "
                    f"less exit net debt {inr_cr(res['exit_net_debt'])} = exit equity {inr_cr(res['exit_equity'])}. "
                    f"Debt repaid over the hold: {inr_cr(res['debt_repaid'])}.")
else:
    t1, t2 = st.tabs(["Operating case", "Cash flows to the stake"])
    with t1:
        st.dataframe(ops.style.format({"revenue": "{:,.0f}", "ebitda": "{:,.0f}", "ebitda_margin": "{:.1%}", "da": "{:,.0f}",
                                       "capex": "{:,.0f}", "delta_nwc": "{:,.0f}"}), hide_index=True, width="stretch")
    with t2:
        st.dataframe(res["schedule"].style.format("{:,.0f}", subset=["pat", "fcf", "dividends_total", "dividends_to_stake",
                                                                     "closing_net_debt"]), hide_index=True, width="stretch")
        st.caption("No acquisition debt. Returns come from the stake's share of dividends plus its share of exit equity value.")

st.subheader("Sensitivity analysis")
metric = st.radio("Show", ["IRR", "MOIC"], horizontal=True)
key = metric.lower()
mode_key = "control" if control else "minority"
entry_grid = np.round(np.arange(entry - 4, entry + 4.01, 2), 1)
exit_grid = np.round(np.arange(exit_mult - 4, exit_mult + 4.01, 2), 1)
entry_grid, exit_grid = entry_grid[entry_grid > 0], exit_grid[exit_grid > 0]


def heat(df, title, xfmt, yfmt):
    z = df.values.astype(float)
    text = [[("n/a" if np.isnan(v) else (f"{v:.1%}" if key == "irr" else f"{v:.2f}x")) for v in row] for row in z]
    mid = 0.20 if key == "irr" else 2.0
    fig = go.Figure(go.Heatmap(z=z, x=[xfmt(c) for c in df.columns], y=[yfmt(i) for i in df.index], text=text,
                               texttemplate="%{text}", colorscale=[[0, "#B23A3A"], [0.5, "#F5F1E6"], [1, "#2E7D4F"]],
                               zmid=mid, showscale=False))
    fig.update_layout(title=title, height=330, xaxis_title=df.columns.name.replace("_", " "),
                      yaxis_title=df.index.name.replace("_", " "))
    return fig


a, b = st.columns(2)
with a:
    s1 = sensitivity(p, "entry_multiple", entry_grid, "exit_multiple", exit_grid, key, mode_key)
    st.plotly_chart(heat(s1, f"{metric}: entry × exit multiple", lambda v: f"{v:.1f}x", lambda v: f"{v:.1f}x"),
                    width="stretch")
with b:
    g_grid = np.round(np.arange(-0.05, 0.251, 0.05), 2)
    if control:
        l_grid = np.arange(max(0, lev - 2), lev + 2.01, 1.0)
        s2 = sensitivity(p, "debt_to_ebitda", l_grid, "ebitda_cagr_override", g_grid, key, mode_key)
        st.plotly_chart(heat(s2, f"{metric}: leverage × EBITDA CAGR", lambda v: f"{v:.0%}", lambda v: f"{v:.1f}x"),
                        width="stretch")
    else:
        s2 = sensitivity(p, "exit_multiple", exit_grid, "ebitda_cagr_override", g_grid, key, mode_key)
        st.plotly_chart(heat(s2, f"{metric}: exit multiple × EBITDA CAGR", lambda v: f"{v:.0%}", lambda v: f"{v:.1f}x"),
                        width="stretch")
st.caption("Heat-map colour is centred on 20% IRR / 2.0x MOIC. In the EBITDA-CAGR grids, EBITDA grows at the stated rate "
           "regardless of the margin path above.")
