import pandas as pd
import streamlit as st

from src.database import repository as repo
from src.reporting.exports import to_csv_bytes
from src.risk.red_flags import SEVERITY_ORDER, dd_questions
from src.ui import state
from src.ui.components import flag_list
from src.ui.formatting import mult, pct

uni = state.universe()
cid = state.select_company(uni)
r = state.company_row(uni, cid)
flags = sorted(uni.flags.get(cid, []), key=lambda f: SEVERITY_ORDER.get(f["severity"], 3))
st.title(f"Risk & due diligence — {r.company_name}")
state.provenance(uni)

fin_flags = [f for f in flags if f["category"] in {"Revenue quality", "Profitability", "Cash flow", "Accounting", "Data"}]
lev_flags = [f for f in flags if f["category"] == "Balance sheet"]
wc_flags = [f for f in flags if f["category"] == "Working capital"]

val_risks = []
if pd.isna(r.ev_ebitda):
    val_risks.append("EV/EBITDA is not meaningful (negative EBITDA, negative EV or missing market data).")
elif pd.notna(r.ev_ebitda_premium) and r.ev_ebitda_premium > 0.5:
    val_risks.append(f"Trades at a {r.ev_ebitda_premium:.0%} premium to peers ({mult(r.ev_ebitda)} vs {mult(r.peer_median_ev_ebitda)}): "
                     "entry price leaves little room for multiple contraction.")
elif pd.notna(r.ev_ebitda_premium) and r.ev_ebitda_premium < -0.3:
    val_risks.append(f"Trades at a {abs(r.ev_ebitda_premium):.0%} discount to peers: test for a value trap before treating it as an opportunity.")
if str(r.peer_group) == "universe":
    val_risks.append("Too few industry or sector peers; compared against the whole universe, so the peer median is a weak anchor.")
if pd.notna(r.promoter_holding if "promoter_holding" in r else None) and r.promoter_holding > 0.7:
    val_risks.append(f"Promoter holding {pct(r.promoter_holding)} — low free float; minimum public shareholding rules (25%) "
                     "constrain how a stake can be bought.")

c = st.columns(4)
c[0].metric("Red flags", len(flags))
c[1].metric("Critical", sum(f["severity"] == "critical" for f in flags))
c[2].metric("Leverage class", r.leverage_class)
c[3].metric("Data quality", r.data_quality)

tabs = st.tabs([f"Financial ({len(fin_flags)})", f"Valuation ({len(val_risks)})", f"Leverage ({len(lev_flags)})",
                f"Working capital ({len(wc_flags)})", "Data quality"])
with tabs[0]:
    flag_list(fin_flags)
with tabs[1]:
    if not val_risks:
        st.success("No valuation-specific risks triggered.")
    for v in val_risks:
        st.markdown(f"- {v}")
with tabs[2]:
    st.markdown(f"Net debt {('net cash' if r.net_debt <= 0 else mult(r.net_debt_to_ebitda) + ' EBITDA')}, interest cover "
                f"{mult(r.interest_coverage)}, debt/equity {mult(r.debt_to_equity)}.")
    flag_list(lev_flags)
with tabs[3]:
    flag_list(wc_flags)
with tabs[4]:
    st.markdown(f"**{r.data_quality}** — {r.quality_reasons}. Filing dates "
                f"{'estimated (period end + 60 days)' if r.filing_dates_estimated else 'from source'}.")
    iss = repo.load_issues()
    iss = iss[iss.company_id == cid][["fiscal_year", "check_name", "severity", "detail", "created_at"]]
    st.dataframe(iss, hide_index=True, width="stretch") if len(iss) else st.caption("No validation issues logged.")

st.subheader("Due-diligence questions")
qs = pd.DataFrame(dd_questions(flags, r.ev_ebitda_premium, r.data_quality, r))
for i, q in qs.iterrows():
    st.markdown(f"{i + 1}. {q.question}  \n<span style='color:#5B6770;font-size:.85em'>Triggered by {q.triggered_by} · "
                f"{q.priority} priority</span>", unsafe_allow_html=True)
a, b = st.columns(2)
a.download_button("Download questions (CSV)", to_csv_bytes(qs), f"{r.ticker}_dd_questions.csv", width="stretch")
if b.button("Copy questions into pipeline next steps", width="stretch"):
    pipe = repo.load_pipeline()
    cur = pipe[pipe.company_id == cid]
    status = cur.status.iloc[0] if len(cur) else "Under Review"
    prev = cur.next_steps.iloc[0] if len(cur) and cur.next_steps.iloc[0] else ""
    steps = (prev + "\n" if prev else "") + "\n".join(f"- {q}" for q in qs.question)
    keep = {k: (cur[k].iloc[0] or "") if len(cur) else "" for k in ["thesis", "concerns", "notes"]}
    repo.save_pipeline_entry(cid, status, next_steps=steps, **keep)
    st.toast("Saved to pipeline")
