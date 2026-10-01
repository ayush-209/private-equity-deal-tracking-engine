import plotly.express as px
import streamlit as st

from src.database import repository as repo
from src.ui import state

uni = state.universe()
t = uni.table
st.title("Data quality")
state.provenance(uni)

runs = repo.load_runs()
issues = repo.load_issues()
fails = runs[runs.job.str.startswith("ingest")].n_failed.fillna(0).sum() if len(runs) else 0
c = st.columns(6)
c[0].metric("Last refresh", f"{repo.last_refresh():%d %b %Y}" if repo.last_refresh() else "never")
c[1].metric("Companies", len(t) + len(uni.excluded))
c[2].metric("Complete 5y history", int((t.years_of_history >= 5).sum()))
c[3].metric("API failures (all runs)", int(fails))
c[4].metric("Duplicates caught", int((issues.check_name == "duplicate").sum()) if len(issues) else 0)
c[5].metric("Missing-field warnings", int((issues.check_name == "missing_critical").sum()) if len(issues) else 0)

left, right = st.columns(2)
with left:
    q = t.data_quality.value_counts().reindex(["High", "Medium", "Low"], fill_value=0).reset_index()
    fig = px.bar(q, x="data_quality", y="count", height=300, title="Data-quality grade",
                 color="data_quality", color_discrete_map={"High": "#2E7D4F", "Medium": "#B7862B", "Low": "#B23A3A"})
    fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="Companies")
    st.plotly_chart(fig, width="stretch")
with right:
    cov = uni.financials.groupby("fiscal_year").company_id.nunique().reset_index(name="companies")
    fig = px.bar(cov, x="fiscal_year", y="companies", height=300, title="Historical coverage")
    fig.update_traces(marker_color="#0E5C63")
    st.plotly_chart(fig, width="stretch")

st.subheader("Per-company grade")
st.caption("High: ≥5 years, ≥95% critical fields, ≥75% working-capital fields, market data, ≤3 warnings. "
           "Medium: ≥3 years, ≥80% critical fields, market data. Low companies cannot pass the screen.")
st.dataframe(t[["company_name", "ticker", "data_quality", "years_of_history", "critical_completeness",
                "secondary_completeness", "validation_warnings", "quality_reasons"]].sort_values("data_quality"),
             hide_index=True, width="stretch",
             column_config={"critical_completeness": st.column_config.ProgressColumn("Critical fields", format="percent", min_value=0, max_value=1),
                            "secondary_completeness": st.column_config.ProgressColumn("Secondary fields", format="percent", min_value=0, max_value=1)})

tabs = st.tabs(["Validation issues", "Pipeline runs", "Sources"])
with tabs[0]:
    if len(issues):
        st.dataframe(issues.groupby(["check_name", "severity"]).size().reset_index(name="count"), hide_index=True)
        st.dataframe(issues[["ticker", "fiscal_year", "check_name", "severity", "detail", "created_at"]], hide_index=True,
                     width="stretch")
    else:
        st.caption("No issues logged.")
with tabs[1]:
    st.dataframe(runs, hide_index=True, width="stretch")
with tabs[2]:
    st.dataframe(repo.data_sources(), hide_index=True, width="stretch")
if st.button("Clear app cache and reload data"):
    state.clear_cache()
    st.rerun()
