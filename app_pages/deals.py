import pandas as pd
import streamlit as st

from src.config import portfolio_config
from src.database import repository as repo
from src.portfolio.construct import derive_params, select_with_reasons
from src.portfolio.explain import funnel
from src.reporting.exports import to_csv_bytes
from src.ui import portfolio_state as ps
from src.ui import shared, state
from src.ui.formatting import inr_cr

uni = state.universe()
d = ps.class_data()
st.title("Deal shortlist")
st.caption("The private-equity side of the same flow. Same size groups, same sector limit, same decision log as the "
           "Portfolio page, but companies are ranked by **deal score**: would this be a good business to buy and improve?")
state.provenance(uni)

st.subheader("1 · Your choices")
n_targets, mix = shared.count_and_mix("Number of targets", "How many companies to shortlist for diligence.", "dl")
if sum(mix.values()) <= 0:
    st.error("Give at least one size group some weight.")
    st.stop()
o1, o2, o3, o4 = st.columns(4)
max_sec = shared.sector_limit("dl", o1)
only_pass = o2.toggle("Only companies that pass the PE screen", value=True,
                      help="The screen applies hard tests on cash flow, leverage and data quality.")
no_crit = o3.toggle("Skip companies with critical red flags", value=True)
high_q = o4.toggle("Skip low data quality", value=True)
st.caption("Deal-score weights (growth, profitability, cash flow, leverage, valuation, working capital) are in the left "
           "sidebar under *Screening weights*. The size groups are the same ones the Portfolio page uses.")
state.weight_controls()

t = uni.table
seg = ps.scored(d, True, False)[["company_id", "cap_segment", "ineligible_reason"]]
f = t.merge(seg, on="company_id", how="left")
ok = f.cap_segment.notna() & f.total_score.notna()
if only_pass: ok &= f.passes_screen.fillna(False).astype(bool)
if no_crit: ok &= f.critical_flags.fillna(0) == 0
if high_q: ok &= f.data_quality.isin(["High", "Medium"])
f["eligible"] = ok
f["class_score"] = f.total_score.where(ok)
f["rank_in_segment"] = f.groupby("cap_segment").class_score.rank(ascending=False, method="first")

try:
    p = derive_params(1.0, int(n_targets), mix, 1.0, max_sec, "equal")
except ValueError as e:
    st.error(str(e))
    st.stop()
sel, notes, dec = select_with_reasons(f, p)
for n in notes:
    st.warning(n, icon="⚠️")
if sel.empty:
    st.info("No company meets these conditions. Loosen the filters above.")
    st.stop()

st.subheader("2 · How the targets were chosen")
st.markdown(
    "1. **Filter.** Keep companies with a deal score that satisfy your switches above.\n"
    "2. **Group.** Place each in its size group (large, mid, small) by market value.\n"
    f"3. **Pick.** Take the highest deal scores in each group: {', '.join(f'{n} {ps.SEG_LABELS[s].lower()}' for s, n in p.names_per_segment.items())}, "
    f"skipping a company if its sector would hold more than {int(max_sec * 100)}% of the names.")
steps = pd.DataFrame([("All companies with a deal score", int(f.total_score.notna().sum())),
                      ("Have a size group", int((f.cap_segment.notna() & f.total_score.notna()).sum())),
                      ("Meet your switches", int(ok.sum())),
                      ("Shortlisted", len(sel))], columns=["Step", "Companies left"])
st.dataframe(steps, hide_index=True, width="stretch")
with st.expander("Every decision, including near misses"):
    dd = dec.copy(); dd["score"] = dd["score"].round(0)
    st.dataframe(dd[["cap_segment", "company_name", "ticker", "sector", "rank", "of", "score", "decision", "reason"]].rename(
        columns={"cap_segment": "Size", "company_name": "Company", "ticker": "Ticker", "sector": "Sector", "rank": "Rank",
                 "of": "Out of", "score": "Deal score", "decision": "Decision", "reason": "Reason"}), hide_index=True, width="stretch")


def why(r) -> str:
    bits = [f"Rank {int(r.rank_in_segment)} of {int(dec[dec.company_id == r.company_id].iloc[0]['of'])} {r.cap_segment}-cap targets (deal score {r.total_score:.0f}/100)."]
    bits.append(f"Archetype: {r.archetype}.")
    m = []
    if pd.notna(r.ev_ebitda): m.append(f"EV/EBITDA {r.ev_ebitda:.1f}x")
    if pd.notna(r.net_debt_to_ebitda): m.append("net cash" if r.net_debt <= 0 else f"net debt/EBITDA {r.net_debt_to_ebitda:.1f}x")
    if pd.notna(r.fcf_conversion): m.append(f"FCF conversion {r.fcf_conversion:.0%}")
    if m: bits.append(", ".join(m) + ".")
    bits.append(f"{int(r.n_flags)} red flag(s)." if r.n_flags else "No red flags.")
    return " ".join(bits)


st.subheader("3 · The shortlist")
sel = sel.copy()
sel["why"] = [why(r) for _, r in sel.iterrows()]
k = st.columns(4)
k[0].metric("Targets", len(sel))
k[1].metric("Median EV/EBITDA", f"{sel.ev_ebitda.median():.1f}x" if sel.ev_ebitda.notna().any() else "n/a")
k[2].metric("Median deal score", f"{sel.total_score.median():.0f}")
k[3].metric("Combined market cap", inr_cr(sel.market_cap.sum()))
out = sel[["cap_segment", "company_name", "ticker", "sector", "market_cap", "total_score", "archetype", "ev_ebitda",
           "net_debt_to_ebitda", "roic", "n_flags", "why"]].copy()
out["roic"] = out["roic"] * 100
ev = st.dataframe(out.rename(columns={"cap_segment": "Size", "company_name": "Company", "ticker": "Ticker", "sector": "Sector",
                                      "market_cap": "Mkt cap (₹ cr)", "total_score": "Deal score", "archetype": "Archetype",
                                      "ev_ebitda": "EV/EBITDA", "net_debt_to_ebitda": "Net debt/EBITDA", "roic": "ROIC %",
                                      "n_flags": "Flags", "why": "Why it is here"}),
                  hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="deal_table",
                  column_config={"Mkt cap (₹ cr)": st.column_config.NumberColumn(format="%d"),
                                 "Deal score": st.column_config.NumberColumn(format="%.0f"),
                                 "EV/EBITDA": st.column_config.NumberColumn(format="%.1f"),
                                 "Net debt/EBITDA": st.column_config.NumberColumn(format="%.1f"),
                                 "ROIC %": st.column_config.NumberColumn(format="%.1f")})
rows = ev.selection.rows if ev and ev.selection else []
if rows:
    r = sel.iloc[rows[0]]
    st.session_state.company_id = int(r.company_id)
    st.success(f"Selected {r.company_name}. Open **Company** → *Deal economics* or *Memo* to underwrite it.")
a, b = st.columns(2)
if a.button("Add all to the deal pipeline", type="primary"):
    have = set(repo.load_pipeline().company_id)
    new = [int(c) for c in sel.company_id if int(c) not in have]
    for c in new:
        repo.save_pipeline_entry(c, "New", "", "", "", "")
    st.success(f"Added {len(new)} compan{'y' if len(new) == 1 else 'ies'} (the rest were already there). See Track → Deal pipeline.")
b.download_button("Download shortlist (CSV)", to_csv_bytes(out), "deal_shortlist.csv")
