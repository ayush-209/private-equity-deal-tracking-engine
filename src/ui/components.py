"""Reusable display blocks."""
import pandas as pd
import streamlit as st

from src.ui.formatting import days, inr_cr, mult, pct


def company_header(r: pd.Series):
    st.title(r.company_name)
    st.caption(f"{r.ticker} · {r.exchange} · {r.sector} / {r.industry} · Archetype: **{r.archetype}**")
    c = st.columns(6)
    c[0].metric("Market cap", inr_cr(r.market_cap))
    c[1].metric("Enterprise value", inr_cr(r.enterprise_value))
    c[2].metric("Screening score", f"{r.total_score:.0f}/100" if pd.notna(r.total_score) else "n/a")
    c[3].metric("Passes screen", "Yes" if r.passes_screen else "No", help=r.fail_reasons or None)
    c[4].metric("Leverage", r.leverage_class)
    c[5].metric("Data quality", r.data_quality, help=r.quality_reasons)


def flag_list(flags: list[dict]):
    if not flags:
        st.success("No automated red flags triggered.")
        return
    for f in flags:
        st.markdown(f"<div class='flag-{f['severity']}'><b>{f['flag']}</b> · {f['category']} · "
                    f"<span style='color:#5B6770'>{f['severity']}</span><br>{f['explanation']}<br>"
                    f"<span style='color:#5B6770;font-size:.85em'>Metric <code>{f['metric']}</code> · current "
                    f"{f['current']} · previous {f['previous']} · threshold {f['threshold']}</span></div>",
                    unsafe_allow_html=True)
        st.write("")


FMT = {"pct": pct, "x": mult, "cr": inr_cr, "days": days}
