import streamlit as st

from src.lbo.model import inputs_from_company, run_control
from src.reporting.ic import ic_pdf, ic_summary
from src.ui import state
from src.ui.formatting import inr_cr, mult, pct

uni = state.universe()
cid = state.select_company(uni)
r = state.company_row(uni, cid)
lbo = st.session_state.get(f"lbo_{cid}")
if lbo is None and r.ebitda > 0:
    lbo = run_control(inputs_from_company(r))  # default case until the user sets assumptions on Deal economics
    lbo_note = "Default assumptions (entry = exit = current multiple, 3.0x debt). Adjust on the Deal economics page."
else:
    lbo_note = "Assumptions from the Deal economics page."
s = ic_summary(uni, cid, lbo)
state.provenance(uni)

st.markdown(f"## {s['company']}")
st.caption(f"{s['ticker']} · {s['sector']} · preliminary screening summary for discussion")
c = st.columns(4)
c[0].metric("Screening score", f"{s['score']:.0f}/100")
c[1].metric("Archetype", s["archetype"])
c[2].metric("Enterprise value", inr_cr(s["ev"]))
c[3].metric("Passes screen", "Yes" if s["passes"] else "No", help=r.fail_reasons or None)

a, b = st.columns(2)
with a:
    st.markdown("#### Financial profile")
    st.table({k: [v] for k, v in s["profile"].items()})
with b:
    st.markdown("#### Valuation")
    st.table({k: [v] for k, v in s["valuation"].items()})
for n in s["valuation_notes"]:
    st.markdown(f"- {n}")

if lbo and "error" not in lbo:
    st.markdown("#### Indicative deal economics")
    c = st.columns(5)
    for col, (k, v) in zip(c, [("Entry EV", inr_cr(lbo["entry_ev"])), ("Sponsor equity", inr_cr(lbo["sponsor_equity"])),
                               ("Exit equity", inr_cr(lbo["exit_equity"])), ("MOIC", mult(lbo["moic"], 2)), ("IRR", pct(lbo["irr"]))]):
        col.metric(k, v)
    st.caption(lbo_note + " Control case is illustrative for an Indian listed target.")

cols = st.columns(3)
for col, title, items in zip(cols, ["Potential value drivers", "Key risks", "Due-diligence priorities"],
                             [s["drivers"], s["risks"], s["dd"]]):
    with col:
        st.markdown(f"#### {title}")
        for i, x in enumerate(items, 1):
            st.markdown(f"{i}. {x}")

st.divider()
st.download_button("Export IC summary (PDF)", ic_pdf(s), f"{s['ticker']}_IC_summary.pdf", "application/pdf", type="primary")
