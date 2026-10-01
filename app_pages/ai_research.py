import datetime as dt

import pandas as pd
import streamlit as st

from src.ai.research import build_fact_pack, chunk_filing, fact_pack_json, generate
from src.config import anthropic_api_key, anthropic_model
from src.database import repository as repo
from src.reporting.exports import to_json_bytes
from src.ui import state
from src.valuation.peers import peer_stats

uni = state.universe()
cid = state.select_company(uni)
r = state.company_row(uni, cid)
h = uni.history[uni.history.company_id == cid]
st.title(f"AI research — {r.company_name}")
state.provenance(uni)
st.markdown("The model receives only the fact pack below (and any filing text you upload). Each fact has an ID it must "
            "cite. After generation, every number in the note is checked against the fact pack; anything that does not "
            "match is listed as unverified.")

src = ", ".join(uni.sources)
facts = build_fact_pack(r, h, uni.flags.get(cid, []), peer_stats(uni.table, cid), src, dt.date.today().isoformat())
up = st.file_uploader("Optional: annual report or filing (PDF or TXT) to add as cited excerpts", type=["pdf", "txt"])
filings = []
if up is not None:
    if up.type == "application/pdf":
        from pypdf import PdfReader
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(up).pages[:40])
    else:
        text = up.read().decode("utf-8", errors="ignore")
    filings = chunk_filing(text)
    st.caption(f"{len(filings)} excerpts from {up.name} will be passed as [F1]…[F{len(filings)}] (first ~22k characters).")

with st.expander(f"Fact pack ({len(facts)} facts)"):
    st.dataframe(pd.DataFrame(facts)[["id", "label", "value", "source", "as_of"]], hide_index=True, width="stretch")

if not anthropic_api_key():
    st.warning("Set ANTHROPIC_API_KEY in .env to enable generation. Everything else on this page works without it.")
go = st.button("Generate research note", type="primary", disabled=not anthropic_api_key())
if go:
    with st.spinner("Drafting from the fact pack…"):
        out = generate(facts, filings)
    if "error" in out:
        st.error(out["error"])
    else:
        repo.save_ai_output(cid, out["model"], fact_pack_json(facts), out["text"], out["unverified"])
        st.toast("Note saved")

hist = repo.load_ai_outputs(cid)
if len(hist):
    latest = hist.iloc[0]
    st.divider()
    st.caption(f"AI-generated draft · model {latest.model} · {pd.to_datetime(latest.created_at):%d %b %Y %H:%M}. "
               "Interpretation is the model's; figures should be checked against the cited fact IDs.")
    unv = latest.unverified_numbers or []
    if unv:
        st.warning("Numbers that do not match the fact pack: " + ", ".join(unv))
    else:
        st.success("Every number in the note matches a figure in the fact pack.")
    st.markdown(latest.output)
    st.download_button("Download note (JSON with fact pack)", to_json_bytes(
        {"company": r.company_name, "model": latest.model, "note": latest.output, "unverified_numbers": unv,
         "fact_pack": latest.fact_pack}), f"{r.ticker}_ai_note.json")
    if len(hist) > 1:
        with st.expander(f"{len(hist) - 1} earlier drafts"):
            for _, row in hist.iloc[1:].iterrows():
                st.markdown(f"**{pd.to_datetime(row.created_at):%d %b %Y %H:%M}** · {row.model}")
                st.markdown(row.output)
                st.divider()
else:
    st.caption(f"No notes generated yet for this company. Model: {anthropic_model()}.")
