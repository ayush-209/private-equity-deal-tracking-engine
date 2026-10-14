import numpy as np
import plotly.express as px
import streamlit as st

from src.config import portfolio_config
from src.portfolio.scoring import SEGMENTS, dimension_scores, normalise, rank_stability, selection_frequency
from src.reporting.exports import to_csv_bytes
from src.ui import portfolio_state as ps

cfg = portfolio_config()
names = list(cfg["dimensions"])
st.title("Class-basis scoring")
st.caption("A second, independent ranking built from the class framework: size, profitability, valuation, growth, risk "
           "and technicals. It does not change the private-equity screen.")

d = ps.class_data()
for n in d.notes:
    st.warning(n, icon="⚠️")
if d.benchmark_symbol:
    st.caption(f"Beta measured against {d.benchmark_symbol}, monthly returns, up to {cfg['benchmark']['max_months']} months.")

# ---------------------------------------------------------------- weights
st.subheader("1 · Weights, and why")
w = ps.class_weights()
cols = st.columns(len(names))
for c, n in zip(cols, names):
    w[n] = c.slider(ps.DIM_LABELS[n], 0, 60, int(round(w[n])), 1, key=f"cw_{n}")
norm = normalise(w, names)
st.caption("Applied (normalised to 100): " + " · ".join(f"{ps.DIM_LABELS[n]} {norm[n]:.0f}" for n in names))
if st.button("Reset weights to the documented defaults"):
    st.session_state.class_weights = {n: float(cfg["dimensions"][n]["weight"]) for n in names}
    for n in names:
        st.session_state.pop(f"cw_{n}", None)
    st.rerun()
with st.expander("The basis for each default weight", expanded=False):
    for n in names:
        s = cfg["dimensions"][n]
        mets = ", ".join(f"{m['metric']} ({'lower' if m['direction'] == 'lower' else 'higher'} is better)"
                         for m in s["metrics"])
        st.markdown(f"**{ps.DIM_LABELS[n]} — default {s['weight']}**  \n{' '.join(s['why'].split())}  \n"
                    f"<span style='color:#5B6770'>Measured by: {mets}</span>", unsafe_allow_html=True)
    st.markdown("**Market capitalisation** is not a scored dimension. It defines the three segments (rank 1–100 large, "
                "101–250 mid, 251+ small, the AMFI convention) and every company is scored against others of its own size. "
                "That is how size shapes the result: it decides who competes with whom and how much of the portfolio each "
                "segment must hold (Portfolio builder).")

c1, c2 = st.columns(2)
inc_fin = c1.toggle("Include banks / NBFCs / insurers", value=cfg["eligibility"]["include_financials"],
                    help="Off by default: ROCE and sales growth do not mean the same thing for a lender.")
ex_loss = c2.toggle("Exclude loss-making companies", value=cfg["eligibility"]["exclude_loss_making"])

s = ps.scored(d, inc_fin, ex_loss)
elig = s[s.eligible & s.class_score.notna()]

# ---------------------------------------------------------------- ranking
st.subheader("2 · Ranking within each cap segment")
m = st.columns(4)
m[0].metric("Companies scored", f"{len(elig):,}", help="Eligible and with at least 4 of 5 dimensions computable.")
for c, seg in zip(m[1:], SEGMENTS):
    c.metric(ps.SEG_LABELS[seg], f"{int((elig.cap_segment == seg).sum()):,}")

seg_pick = st.radio("Segment", ["All"] + SEGMENTS, horizontal=True, format_func=lambda x: ps.SEG_LABELS.get(x, x))
view = elig if seg_pick == "All" else elig[elig.cap_segment == seg_pick]
view = view.sort_values("class_score", ascending=False)
show = view[["company_name", "ticker", "cap_segment", "sector", "market_cap", "class_score", "growth_pct", "profitability_pct",
             "valuation_pct", "risk_pct", "technical_pct", "sales_yoy", "eps_growth", "roe", "roce", "pe", "peg", "pb",
             "beta", "sd", "ret_6m"]].copy()
for c in ["growth_pct", "profitability_pct", "valuation_pct", "risk_pct", "technical_pct",
          "sales_yoy", "eps_growth", "roe", "roce", "sd", "ret_6m"]:
    show[c] = show[c] * 100            # displayed as percent / percentile points
show = show.replace([np.inf, -np.inf], np.nan)
one = st.column_config.NumberColumn(format="%.1f")
st.dataframe(show.rename(columns={
    "company_name": "Company", "ticker": "Ticker", "cap_segment": "Segment", "sector": "Sector", "market_cap": "Mkt cap (₹ cr)",
    "class_score": "Score", "growth_pct": "Growth", "profitability_pct": "Profit.", "valuation_pct": "Valuation",
    "risk_pct": "Risk", "technical_pct": "Technical", "sales_yoy": "Sales YoY %", "eps_growth": "EPS growth %",
    "roe": "ROE %", "roce": "ROCE %", "pe": "P/E", "peg": "PEG", "pb": "P/BV", "beta": "Beta", "sd": "SD % (ann.)",
    "ret_6m": "6m return %"}), hide_index=True, width="stretch", height=480,
    column_config={
        "Mkt cap (₹ cr)": st.column_config.NumberColumn(format="%d"),
        "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        **{k: st.column_config.NumberColumn(format="%.0f") for k in ["Growth", "Profit.", "Valuation", "Risk", "Technical"]},
        **{k: one for k in ["Sales YoY %", "EPS growth %", "ROE %", "ROCE %", "P/E", "SD % (ann.)", "6m return %"]},
        "PEG": st.column_config.NumberColumn(format="%.2f"), "P/BV": st.column_config.NumberColumn(format="%.2f"),
        "Beta": st.column_config.NumberColumn(format="%.2f"),
    })
st.caption("Growth … Technical are percentiles (0–100) among companies of the same size; 100 = best in segment.")
st.download_button("Download ranking (CSV)", to_csv_bytes(view), "class_basis_ranking.csv", "text/csv")

with st.expander("Companies left out, and why"):
    out = s[~s.eligible | s.class_score.isna()]
    out = out.assign(reason=np.where(out.ineligible_reason != "", out.ineligible_reason,
                                     "fewer than 4 of 5 dimensions computable"))
    st.dataframe(out[["company_name", "ticker", "cap_segment", "reason"]], hide_index=True, width="stretch")

# ---------------------------------------------------------------- stability
st.subheader("3 · Do the weights actually matter?")
st.write("The weights are a judgement call, so the real question is how much the answer depends on them. Below, the "
         "ranking is recomputed under other plausible weightings. High rank correlation and high top-15 overlap mean "
         "the debate over exact weights matters little.")
tmp = elig.reset_index(drop=True)
dims, _, _ = dimension_scores(tmp, cfg)
stab = rank_stability(dims, tmp.cap_segment, cfg, cfg["eligibility"]["min_dimensions"])
if stab.empty:
    st.info("Not enough scored companies to compare rankings.")
else:
    pv = stab.pivot(index="weight_set", columns="segment", values="spearman")
    pv = pv[[c for c in ["all", *SEGMENTS] if c in pv.columns]]
    ov = stab.pivot(index="weight_set", columns="segment", values="top_overlap")
    ov = ov[[c for c in ["all", *SEGMENTS] if c in ov.columns]]
    a, b = st.columns(2)
    a.markdown("**Rank correlation with the base ranking** (Spearman)")
    a.dataframe(pv.round(2), width="stretch")
    b.markdown("**Share of the base top 15 that stays in the top 15**")
    b.dataframe((ov * 100).round(0).astype(int).astype(str) + "%", width="stretch")
    alts = "; ".join(f"{k}: " + ", ".join(f"{d} {v}" for d, v in cfg["weight_sets"][k].items())
                     for k in cfg["weight_sets"] if k != "base")
    st.caption("Alternative weight sets — " + alts)

per_seg = cfg["portfolio"]["names_per_segment"]
freq = selection_frequency(dims, tmp.cap_segment, per_seg, cfg, cfg["eligibility"]["min_dimensions"])
tmp = tmp.assign(selection_frequency=freq.values)
top = tmp.sort_values(["cap_segment", "class_score"], ascending=[True, False]).groupby("cap_segment").head(8)
fig = px.bar(top.sort_values("selection_frequency"), x="selection_frequency", y="ticker", color="cap_segment",
             orientation="h", labels={"selection_frequency": "Share of random weightings in which it is picked",
                                      "ticker": ""}, height=max(320, 22 * len(top)))
fig.update_xaxes(tickformat=".0%", range=[0, 1])
st.markdown("**Robustness of the top names** — each bar is the share of "
            f"{cfg['stability']['draws']} random weightings (centred on the base) in which the company stays among the "
            "segment's top picks. A tall bar means the pick does not depend on the exact weights.")
st.plotly_chart(fig, width="stretch")

with st.expander("Metric definitions and limits"):
    st.markdown("""
- **Sales YoY**: latest fiscal year revenue / prior year − 1.
- **EPS growth**: 3-year EPS CAGR (YoY if history is short). If EPS growth disagrees with profit-after-tax growth by
  more than 50 points a year, PAT growth is used instead — free data sources do not always adjust EPS for splits and bonus issues.
- **ROE**: PAT / average equity. **ROCE**: EBIT / average (total assets − current liabilities); where current liabilities are
  missing, EBIT / (equity + debt), which understates ROCE.
- **P/E** = market cap / PAT. **P/BV** = market cap / equity. **PEG** = P/E / (EPS growth in %); not meaningful when growth ≤ 0,
  and scored as worst, since there is no growth to pay for.
- **Beta**: slope of monthly stock returns on monthly index returns, 36–60 months. **SD**: monthly SD × √12, same window.
  Prices are not adjusted for dividends. Beta from 36–60 points has a wide error band; treat differences of ±0.2 as noise.
- **Technicals** (not defined in the class notes): 6-month return; 12-month return skipping the latest month; price vs
  its 10-month average.
- **Limits**: fundamentals cover about five years and are the latest restated figures; the universe is today's Nifty 500
  so past-delisted companies are absent (survivorship bias); no liquidity data, so small-cap position sizes are not
  checked against trading volumes.""")
