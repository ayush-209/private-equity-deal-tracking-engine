import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.config import portfolio_config
from src.portfolio.construct import WEIGHTINGS, Params, build_portfolio
from src.portfolio.scoring import SEGMENTS, dimension_scores, normalise, selection_frequency
from src.reporting.exports import to_csv_bytes, to_excel_bytes
from src.ui import portfolio_state as ps
from src.ui.formatting import indian_group, inr_cr

cfg = portfolio_config()
pc = cfg["portfolio"]
st.title("Portfolio builder")
st.caption("Turns the class-basis ranking into holdings: best names per cap segment, sized under explicit limits, "
           "in whole shares at the latest price. Set the scoring weights on the Class-basis scoring page.")

d = ps.class_data()
for n in d.notes:
    st.warning(n, icon="⚠️")

with st.sidebar.expander("Portfolio settings", expanded=True):
    capital = st.number_input("Capital (₹)", min_value=100000, max_value=10_000_000_000, step=500000,
                              value=int(pc["capital"]), help="Initial capital to allocate.")
    st.caption(f"= {inr_cr(capital / 1e7, 2)}")
    st.markdown("**Names per segment**")
    k = {seg: st.number_input(ps.SEG_LABELS[seg], 0, 15, int(pc["names_per_segment"][seg]), key=f"k_{seg}")
         for seg in SEGMENTS}
    st.markdown("**Minimum weight per segment (%)**")
    floors = {seg: st.slider(ps.SEG_LABELS[seg], 0, 70, int(pc["min_segment_weight"][seg] * 100), 5, key=f"fl_{seg}") / 100
              for seg in SEGMENTS}
    max_pos = st.slider("Maximum single position (%)", 5, 40, int(pc["max_position"] * 100), 1) / 100
    max_sec = st.slider("Maximum single sector (%)", 15, 100, int(pc["max_sector"] * 100), 5) / 100
    weighting = st.selectbox("Weighting", WEIGHTINGS, index=WEIGHTINGS.index(pc["weighting"]),
                             format_func={"equal": "Equal", "score": "Proportional to score",
                                          "inverse_volatility": "Inverse volatility"}.get)
    robust = st.slider("Only names picked in at least this share of random weightings (%)", 0, 90, 0, 10,
                       help="Drops picks that depend on the exact scoring weights.") / 100
    inc_fin = st.toggle("Include banks / NBFCs / insurers", value=cfg["eligibility"]["include_financials"])
    ex_loss = st.toggle("Exclude loss-making companies", value=cfg["eligibility"]["exclude_loss_making"])

if sum(floors.values()) > 1.0:
    st.error("The segment minimums add up to more than 100%.")
    st.stop()

w = ps.class_weights()
norm = normalise(w, list(cfg["dimensions"]))
st.caption("Scoring weights in use: " + " · ".join(f"{ps.DIM_LABELS[n]} {norm[n]:.0f}" for n in norm))

scored = ps.scored(d, inc_fin, ex_loss)
elig = scored[scored.eligible & scored.class_score.notna()].reset_index(drop=True)
dims, _, _ = dimension_scores(elig, cfg)
freq_s = selection_frequency(dims, elig.cap_segment, {s: n for s, n in k.items() if n > 0}, cfg,
                             cfg["eligibility"]["min_dimensions"])
freq = pd.Series(freq_s.values, index=elig.company_id)

p = Params(capital=float(capital), names_per_segment={s: n for s, n in k.items() if n > 0},
           min_segment_weight=floors, max_position=max_pos, max_sector=max_sec, weighting=weighting, min_freq=robust)
res = build_portfolio(scored, d.prices, d.bench_returns, p, freq)
h, risk = res["holdings"], res["risk"]
for n in res["notes"]:
    st.warning(n, icon="⚠️")
if h.empty:
    st.stop()

# ---------------------------------------------------------------- headline
c = st.columns(6)
c[0].metric("Invested", f"₹{indian_group(res['invested'])}", help="Whole shares at the latest price.")
c[1].metric("Cash left", f"₹{indian_group(res['cash'])}", help="Not enough to buy another share of the neediest position.")
c[2].metric("Holdings", f"{len(h)}")
c[3].metric("Portfolio SD (ann.)", f"{risk['sd']:.1%}" if pd.notna(risk.get('sd')) else "n/a",
            help="From the covariance of monthly returns of the holdings over their common history.")
c[4].metric("Portfolio beta", f"{risk['beta']:.2f}" if pd.notna(risk.get('beta')) else "n/a",
            help="Slope of the portfolio's monthly return on the index. The weighted average of individual betas "
                 f"is {risk['weighted_avg_beta']:.2f}." if pd.notna(risk.get('weighted_avg_beta')) else None)
c[5].metric("Diversification", f"{risk['diversification']:.0%}" if pd.notna(risk.get('diversification')) else "n/a",
            help="How much lower the portfolio SD is than the weighted average of the holdings' SDs.")

# ---------------------------------------------------------------- holdings
st.subheader("Holdings")
tbl = h.copy()
tbl["weight"] *= 100
tbl["target_weight"] *= 100
if "selection_frequency" in tbl:
    tbl["selection_frequency"] *= 100
st.dataframe(tbl[["cap_segment", "company_name", "ticker", "sector", "class_score", "last_price", "shares", "invested",
                  "weight", "target_weight", "beta", "sd", "selection_frequency"]].rename(columns={
    "cap_segment": "Segment", "company_name": "Company", "ticker": "Ticker", "sector": "Sector", "class_score": "Score",
    "last_price": "Price (₹)", "shares": "Shares", "invested": "Invested (₹)", "weight": "Weight %",
    "target_weight": "Target %", "beta": "Beta", "sd": "SD (ann.)", "selection_frequency": "Robustness %"}),
    hide_index=True, width="stretch",
    column_config={"Score": st.column_config.NumberColumn(format="%.0f"),
                   "Price (₹)": st.column_config.NumberColumn(format="%.2f"),
                   "Invested (₹)": st.column_config.NumberColumn(format="%d"),
                   "Weight %": st.column_config.NumberColumn(format="%.1f"),
                   "Target %": st.column_config.NumberColumn(format="%.1f"),
                   "Beta": st.column_config.NumberColumn(format="%.2f"),
                   "SD (ann.)": st.column_config.NumberColumn(format="%.2f"),
                   "Robustness %": st.column_config.NumberColumn(format="%.0f")})
st.caption("Target % is the constrained ideal; Weight % is what whole shares actually give. Robustness % is the share of "
           "random scoring weightings in which the company is picked.")

a, b = st.columns(2)
seg = res["segment_weights"].rename(index=ps.SEG_LABELS).reset_index()
seg.columns = ["Segment", "Weight"]
fa = px.pie(seg, names="Segment", values="Weight", hole=0.55, title="By market-cap segment")
fa.update_traces(texttemplate="%{label}<br>%{percent}")
a.plotly_chart(fa, width="stretch")
sec = res["sector_weights"].reset_index()
sec.columns = ["Sector", "Weight"]
fb = px.bar(sec.sort_values("Weight"), x="Weight", y="Sector", orientation="h", title="By sector")
fb.update_xaxes(tickformat=".0%")
b.plotly_chart(fb, width="stretch")

# ---------------------------------------------------------------- history
st.subheader("How these holdings behaved over the past")
path = risk.get("path")
if path is not None and len(path):
    bench = d.bench_returns.reindex(path.index).dropna()
    fig = go.Figure()
    base_path = path / path.iloc[0]
    fig.add_scatter(x=path.index.to_timestamp(), y=capital * base_path.values,
                    name="This portfolio (today's weights, held constant)")
    if len(bench) > 1:
        bpath = (1 + bench).cumprod()
        bpath = bpath / bpath.iloc[0]
        fig.add_scatter(x=bpath.index.to_timestamp(), y=capital * bpath.values, name=f"{d.benchmark_symbol or 'Index'} (same start)")
    fig.update_layout(height=360, yaxis_title="Value (₹)", legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, width="stretch")
    r = st.columns(3)
    r[0].metric("Annualised return (historical)", f"{risk['ann_return']:.1%}")
    r[1].metric("Worst drawdown (monthly closes)", f"{risk['max_drawdown']:.1%}")
    r[2].metric("Months of history used", f"{risk['months']}")
    st.caption("This is a look back, not a forecast. The names were picked using today's data, so the past performance "
               "of the chosen stocks is flattered by hindsight (selection bias), and the universe excludes companies that "
               "dropped out of the index (survivorship bias). Use it to see how rough the ride was, not to estimate returns.")
else:
    st.info("Not enough common price history across the holdings to show a historical path.")

# ---------------------------------------------------------------- export
st.download_button("Download holdings (Excel)", to_excel_bytes({
    "Holdings": h.drop(columns=["company_id"]),
    "Settings": pd.DataFrame({"setting": ["capital", "names per segment", "segment floors", "max position", "max sector",
                                          "weighting", "scoring weights"],
                              "value": [capital, str(p.names_per_segment), str(floors), max_pos, max_sec, weighting,
                                        str({k_: round(v, 1) for k_, v in norm.items()})]})}),
    "portfolio.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
