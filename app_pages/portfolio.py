import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.config import portfolio_config
from src.database import repository as repo
from src.portfolio.construct import WEIGHTINGS, build_portfolio, derive_params
from src.portfolio.explain import STYLES, funnel, why_text
from src.portfolio.scoring import SEGMENTS, dimension_scores, normalise, selection_frequency
from src.reporting.exports import to_excel_bytes
from src.ui import portfolio_state as ps
from src.ui import shared
from src.ui import state
from src.ui.formatting import indian_group, inr_cr

cfg = portfolio_config()
pc = cfg["portfolio"]
uni = state.universe()
d = ps.class_data()
st.title("Build a portfolio")
st.caption("Four choices, then the app shows what it picked and why. Nothing is fixed in advance: change any choice and "
           "the whole result updates.")
for n in d.notes:
    st.warning(n, icon="⚠️")

# ------------------------------------------------------------------ 1. choices
st.subheader("1 · Your choices")
a, c = st.columns([1, 2])
capital = a.number_input("Capital (₹)", 100000, 10_000_000_000, int(pc["capital"]), 500000,
                         help="Money to invest. Only used to count whole shares and cash left over.")
a.caption(f"= {inr_cr(capital / 1e7, 2)}")
style_key = c.selectbox("Style", list(STYLES) + ["custom"], index=0,
                        format_func=lambda k: STYLES[k][0] if k in STYLES else "Custom weights")
if style_key in STYLES:
    c.caption(STYLES[style_key][1])
    ps.set_style(style_key)
else:
    w = ps.class_weights()
    cols = st.columns(5)
    for col, name in zip(cols, cfg["dimensions"]):
        w[name] = col.slider(ps.DIM_LABELS[name], 0, 60, int(round(w[name])), 1, key=f"cw_{name}")

st.markdown("**How many stocks, and how to split the money across company sizes** (shared with the Deal shortlist page)")
n_stocks, mix = shared.count_and_mix("Number of stocks", "More stocks spread risk further; fewer concentrate it.", "pf")
if sum(mix.values()) <= 0:
    st.error("Give at least one size group some weight.")
    st.stop()
tot = sum(mix.values())
st.caption("Large = top 100 companies by market value, mid = next 150, small = the rest (AMFI convention). "
           f"Split used: {', '.join(f'{ps.SEG_LABELS[s].split()[0]} {v / tot:.0%}' for s, v in mix.items())}.")

with st.expander("Advanced limits and overrides"):
    x1, x2, x3 = st.columns(3)
    max_pos = x1.slider("Maximum in one stock (%)", 5, 40, int(pc["max_position"] * 100), 1) / 100
    max_sec = shared.sector_limit("pf", x2)
    weighting = x3.selectbox("How to size positions", WEIGHTINGS, index=WEIGHTINGS.index(pc["weighting"]),
                             format_func={"equal": "Equal", "score": "Proportional to score",
                                          "inverse_volatility": "Inverse volatility"}.get)
    y1, y2, y3 = st.columns(3)
    robust = y1.slider("Only stocks picked in at least this % of random weightings", 0, 90, 0, 10,
                       help="Drops picks that only win under one exact set of weights.") / 100
    inc_fin = y2.toggle("Include banks / NBFCs / insurers", value=cfg["eligibility"]["include_financials"])
    ex_loss = y3.toggle("Exclude loss-making companies", value=cfg["eligibility"]["exclude_loss_making"])

scored = ps.scored(d, inc_fin, ex_loss)
pool = scored[scored.eligible & scored.class_score.notna()]
label = dict(zip(pool.company_id, pool.company_name + " · " + pool.ticker))
with st.expander("Force a company in, or keep one out"):
    z1, z2 = st.columns(2)
    inc_ids = z1.multiselect("Always include", pool.company_id.tolist(), format_func=label.get)
    exc_ids = z2.multiselect("Never include", pool.company_id.tolist(), format_func=label.get)

try:
    p = derive_params(float(capital), int(n_stocks), mix, max_pos, max_sec, weighting, min_freq=robust,
                      include_ids=frozenset(inc_ids), exclude_ids=frozenset(exc_ids))
except ValueError as e:
    st.error(str(e))
    st.stop()
elig = pool.reset_index(drop=True)
dims, _, _ = dimension_scores(elig, cfg)
freq = pd.Series(selection_frequency(dims, elig.cap_segment, p.names_per_segment, cfg,
                                     cfg["eligibility"]["min_dimensions"]).values, index=elig.company_id)
res = build_portfolio(scored, d.prices, d.bench_returns, p, freq)
h, risk = res["holdings"], res["risk"]
for n in res["notes"]:
    st.warning(n, icon="⚠️")
if h.empty:
    st.stop()

# ------------------------------------------------------------------ 2. how it chose
st.subheader("2 · How the stocks were chosen")
st.markdown(
    "1. **Filter.** Drop companies that cannot be compared fairly (see the table).\n"
    "2. **Score.** Each remaining company gets 0–100, ranked *only against companies of its own size*, on "
    f"{', '.join(ps.DIM_LABELS[n].lower() for n in cfg['dimensions'])}, using the style's weights.\n"
    f"3. **Pick.** Take the best scorers in each size group: {', '.join(f'{n} {ps.SEG_LABELS[s].lower()}' for s, n in p.names_per_segment.items())}, "
    f"skipping a stock if its sector would hold more than {int(max_sec * 100)}% of the names.\n"
    "4. **Size.** Spread the money by your sizing rule within your stock and sector limits, then round to whole shares.")
f1, f2 = st.columns([1, 1])
f1.dataframe(funnel(scored).rename(columns={"step": "Step", "companies": "Companies left"}), hide_index=True, width="stretch")
w_now = normalise(ps.class_weights(), list(cfg["dimensions"]))
f2.dataframe(pd.DataFrame({"Criterion": [ps.DIM_LABELS[n] for n in w_now], "Weight %": [round(v) for v in w_now.values()],
                           "Why it matters": [" ".join(cfg["dimensions"][n]["why"].split())[:110] + "…" for n in w_now]}),
             hide_index=True, width="stretch")
with st.expander("Every decision, including near misses"):
    dec = res["decisions"].copy()
    dec["score"] = dec["score"].round(0)
    st.dataframe(dec[["cap_segment", "company_name", "ticker", "sector", "rank", "of", "score", "decision", "reason"]].rename(
        columns={"cap_segment": "Size", "company_name": "Company", "ticker": "Ticker", "sector": "Sector", "rank": "Rank",
                 "of": "Out of", "score": "Score", "decision": "Decision", "reason": "Reason"}), hide_index=True, width="stretch")

# ------------------------------------------------------------------ 3. result
st.subheader("3 · The portfolio")
k = st.columns(6)
k[0].metric("Invested", f"₹{indian_group(res['invested'])}")
k[1].metric("Cash left", f"₹{indian_group(res['cash'])}")
k[2].metric("Holdings", f"{len(h)}")
k[3].metric("Risk (SD, ann.)", f"{risk['sd']:.1%}" if pd.notna(risk.get("sd")) else "n/a",
            help="Volatility of the whole portfolio from monthly returns over the common history.")
k[4].metric("Beta vs index", f"{risk['beta']:.2f}" if pd.notna(risk.get("beta")) else "n/a", help="1.0 moves with the index.")
k[5].metric("Diversification", f"{risk['diversification']:.0%}" if pd.notna(risk.get("diversification")) else "n/a",
            help="How much lower portfolio risk is than the average of the stocks' risks.")
sc_idx = scored.set_index("company_id")
h["why"] = [why_text(sc_idx.loc[c]) for c in h.company_id]
tbl = h.assign(weight=h.weight * 100)[["cap_segment", "company_name", "ticker", "sector", "class_score", "last_price", "shares",
                                       "invested", "weight", "why"]]
st.dataframe(tbl.rename(columns={"cap_segment": "Size", "company_name": "Company", "ticker": "Ticker", "sector": "Sector",
                                 "class_score": "Score", "last_price": "Price (₹)", "shares": "Shares", "invested": "Invested (₹)",
                                 "weight": "Weight %", "why": "Why it is here"}),
             hide_index=True, width="stretch",
             column_config={"Score": st.column_config.NumberColumn(format="%.0f"),
                            "Price (₹)": st.column_config.NumberColumn(format="%.2f"),
                            "Invested (₹)": st.column_config.NumberColumn(format="%d"),
                            "Weight %": st.column_config.NumberColumn(format="%.1f")})
g1, g2 = st.columns(2)
seg = res["segment_weights"].rename(index=ps.SEG_LABELS).reset_index()
seg.columns = ["Size", "Weight"]
g1.plotly_chart(px.pie(seg, names="Size", values="Weight", hole=0.55, title="By company size"), width="stretch")
sec = res["sector_weights"].reset_index()
sec.columns = ["Sector", "Weight"]
fb = px.bar(sec.sort_values("Weight"), x="Weight", y="Sector", orientation="h", title="By sector")
fb.update_xaxes(tickformat=".0%")
g2.plotly_chart(fb, width="stretch")

path = risk.get("path")
if path is not None and len(path):
    bench = d.bench_returns.reindex(path.index).dropna()
    fig = go.Figure()
    fig.add_scatter(x=path.index.to_timestamp(), y=capital * (path / path.iloc[0]).values, name="This portfolio (today's weights)")
    if len(bench) > 1:
        bp = (1 + bench).cumprod()
        fig.add_scatter(x=bp.index.to_timestamp(), y=capital * (bp / bp.iloc[0]).values, name=d.benchmark_symbol or "Index")
    fig.update_layout(height=340, yaxis_title="₹", legend=dict(orientation="h", y=-0.2), title="Looking back (not a forecast)")
    st.plotly_chart(fig, width="stretch")
    st.caption("Names were picked with today's data, so their past looks better than it would have in real time "
               "(selection bias), and delisted companies are missing (survivorship bias).")

# ------------------------------------------------------------------ 4. save
st.subheader("4 · Save it and track it")
s1, s2, s3 = st.columns([2, 1, 1])
pname = s1.text_input("Name", f"Portfolio {pd.Timestamp.today():%d %b %Y}")
bdate = s2.date_input("Bought on", pd.Timestamp.today().date(), help="Used as the start date for profit/loss and the chart.")
if s3.button("Save to Track", type="primary", width="stretch"):
    hh = h[h.shares > 0].rename(columns={"last_price": "buy_price"}).assign(buy_date=bdate)
    hh = hh.rename(columns={"cap_segment": "segment"})
    pid = repo.save_portfolio(pname, float(capital), hh[["ticker", "company_name", "shares", "buy_price", "buy_date", "segment", "sector"]])
    st.session_state["track_pid"] = pid
    st.success("Saved. Open **Track** in the left menu. Buy prices are the latest stored prices; correct them there if "
               "you trade at different prices.")
st.download_button("Download holdings (Excel)", to_excel_bytes({"Holdings": h.drop(columns=["company_id"])}), "portfolio.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
