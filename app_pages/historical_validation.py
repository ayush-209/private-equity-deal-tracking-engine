import datetime as dt

import plotly.express as px
import streamlit as st

from src.backtest.historical import OUTCOMES, validate
from src.ui import state

uni = state.universe()
st.title("Historical validation")
state.provenance(uni)
st.markdown("Re-runs the screen as of a past date using only statements whose filing date had passed, then tracks what "
            "happened to each company's fundamentals afterwards. The question is whether the screen identifies "
            "companies whose later fundamentals match the thesis — not whether they made money.")
st.warning("Point-in-time on availability, not on values: free data sources return restated figures. With CMIE Prowess or "
           "Capitaline vintages loaded through the CSV adapter, this becomes a true as-reported test. "
           "Past results are not evidence of future investment success.", icon="⚠️")

fys = sorted(uni.financials.fiscal_year.unique())
dates = [dt.date(y, 6, 30) for y in fys[2:-1]]
if not dates:
    st.info("At least four fiscal years are needed.")
    st.stop()
as_of = st.select_slider("Screening date", dates, value=dates[len(dates) // 2], format_func=lambda d: d.strftime("%d %b %Y"))


@st.cache_data(show_spinner="Re-running the screen at that date…", ttl=900)
def run(d, w, _fin):
    return validate(d, dict(w), full_fin=_fin)


res = run(as_of, tuple(sorted(state.weights().items())), uni.financials)
if "error" in res:
    st.error(res["error"])
    st.stop()
c = st.columns(3)
c[0].metric("Screenable then", res["n_screenable"])
c[1].metric("Shortlisted then", res["n_shortlist"])
c[2].metric("Years tracked", int(res["detail"].years_after.median()))

st.subheader("Shortlist vs rest of universe (medians)")
comp = res["comparison"].copy()
for c_ in ["shortlist_median", "rest_median"]:
    comp[c_] = comp.apply(lambda r: (f"{r[c_]:.2f}x" if "(x)" in r.outcome else f"{r[c_]*100:.1f}" + ("pp" if "(pp)" in r.outcome else "%"))
                          if r[c_] == r[c_] else "n/a", axis=1)
st.dataframe(comp, hide_index=True, width="stretch")

left, right = st.columns(2)
with left:
    st.subheader("Did the archetype thesis hold?")
    ba = res["by_archetype"].copy()
    if len(ba):
        ba["hit_rate"] = ba.hit_rate * 100
        st.dataframe(ba, hide_index=True, column_config={"hit_rate": st.column_config.ProgressColumn("Thesis held", min_value=0, max_value=100, format="%.0f%%")})
    else:
        st.caption("No shortlisted company had a testable archetype.")
with right:
    st.subheader("Outcome by score quintile")
    out = st.selectbox("Outcome", list(OUTCOMES), format_func=OUTCOMES.get)
    fig = px.bar(res["by_quintile"], x="score_quintile", y=out, height=300, labels={"score_quintile": "", out: OUTCOMES[out]})
    fig.update_traces(marker_color="#0E5C63")
    st.plotly_chart(fig, width="stretch")
    st.caption("A useful screen shows outcomes that rise (or fall, for leverage) across quintiles.")

with st.expander("Company-level detail"):
    st.dataframe(res["detail"], hide_index=True, width="stretch")
