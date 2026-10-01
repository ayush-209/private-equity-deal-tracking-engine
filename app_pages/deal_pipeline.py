import plotly.express as px
import streamlit as st

from src.database import repository as repo
from src.reporting.exports import to_csv_bytes
from src.ui import state

uni = state.universe()
st.title("Deal pipeline")
pipe = repo.load_pipeline()
scores = uni.table[["company_id", "total_score", "archetype", "n_flags"]]
pipe = pipe.merge(scores, on="company_id", how="left")

if len(pipe):
    counts = pipe.status.value_counts().reindex(repo.PIPELINE_STATUSES, fill_value=0).reset_index()
    fig = px.bar(counts, x="status", y="count", height=240, labels={"status": "", "count": "Companies"})
    fig.update_traces(marker_color="#0E5C63")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(pipe[["company_name", "ticker", "status", "total_score", "archetype", "n_flags", "thesis", "next_steps", "updated_at"]],
                 hide_index=True, width="stretch",
                 column_config={"total_score": st.column_config.NumberColumn("Score", format="%.0f"),
                                "n_flags": "Flags", "updated_at": st.column_config.DatetimeColumn("Updated", format="DD MMM YYYY HH:mm")})
    st.download_button("Export pipeline (CSV)", to_csv_bytes(pipe), "deal_pipeline.csv")
else:
    st.info("The pipeline is empty. Add companies from the Deal universe page or below.")

st.subheader("Add or update a company")
t = uni.table.sort_values("company_name")
names = dict(zip(t.company_id, t.company_name + " · " + t.ticker))
default = st.session_state.get("company_id", t.company_id.iloc[0])
cid = st.selectbox("Company", t.company_id.tolist(), index=t.company_id.tolist().index(default) if default in names else 0,
                   format_func=lambda c: names[c])
cur = pipe[pipe.company_id == cid]
get = lambda k, d="": (cur[k].iloc[0] or d) if len(cur) else d  # noqa: E731
with st.form("pipeline_form"):
    status = st.selectbox("Status", repo.PIPELINE_STATUSES, index=repo.PIPELINE_STATUSES.index(get("status", "New")))
    thesis = st.text_area("Thesis", get("thesis"))
    concerns = st.text_area("Concerns", get("concerns"))
    next_steps = st.text_area("Next steps", get("next_steps"))
    notes = st.text_area("Notes", get("notes"))
    a, b = st.columns(2)
    save = a.form_submit_button("Save", type="primary", width="stretch")
    remove = b.form_submit_button("Remove from pipeline", width="stretch", disabled=not len(cur))
if save:
    repo.save_pipeline_entry(int(cid), status, thesis, concerns, next_steps, notes)
    st.toast("Saved")
    st.rerun()
if remove:
    repo.remove_pipeline_entry(int(cid))
    st.rerun()

hist = repo.read_df("SELECT h.changed_at, c.company_name, h.status, h.note FROM pipeline_history h JOIN companies c "
                    "USING (company_id) ORDER BY h.changed_at DESC LIMIT 100")
if len(hist):
    with st.expander("Status history"):
        st.dataframe(hist, hide_index=True, width="stretch")
