"""Shared Streamlit state: cached universe, weight controls, company selection, provenance banner.
Pages call these helpers; no financial logic lives in the UI layer."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import screening_config
from src.database import repository as repo
from src.screening.engine import DIM_LABELS, DIMENSIONS, normalise_weights
from src.screening.universe import Universe, build_universe


def _refresh_key() -> str:
    try:
        t = repo.last_refresh()
    except Exception:  # noqa: BLE001
        return "none"
    return str(t)


@st.cache_resource(show_spinner="Building screening universe…", ttl=900)
def _base(refresh_key: str) -> Universe:
    return build_universe()


@st.cache_resource(show_spinner="Re-scoring…", max_entries=16)
def _rescored(refresh_key: str, weights: tuple) -> Universe:
    return _base(refresh_key).rescore(dict(weights))


def weights() -> dict:
    if "weights" not in st.session_state:
        st.session_state.weights = dict(screening_config()["weights"])
    return st.session_state.weights


def universe() -> Universe:
    try:
        key = _refresh_key()
        base = _base(key)
    except Exception as exc:  # noqa: BLE001
        status = st.session_state.get("data_status", "")
        st.error("No data available yet.\n\nThe app downloads its database from the `data` branch of the GitHub repository, "
                 "which the data-refresh workflow creates. Run Actions → data-refresh → Run workflow (job `all`) and wait "
                 f"for it to finish, then reload this page.\n\nDownload status: {status or 'not attempted'}\n\nDetail: {exc}")
        st.stop()
    if base.table.empty:
        st.info("The database has no financial statements yet. Run `python -m scripts.run_pipeline demo` to load the "
                "synthetic demo set, or `python -m scripts.run_pipeline all --source yfinance --limit 50` for real data.")
        st.stop()
    w = normalise_weights(weights())
    if w == normalise_weights(screening_config()["weights"]):
        return base
    return _rescored(key, tuple(sorted(w.items())))


def clear_cache():
    _base.clear()
    _rescored.clear()


def weight_controls():
    """Sidebar sliders. Weights are re-normalised to 100, so the user can move one slider freely."""
    w = weights()
    with st.sidebar.expander("Screening weights", expanded=False):
        for d in DIMENSIONS:
            w[d] = st.slider(DIM_LABELS[d], 0, 40, int(round(w[d])), 1, key=f"w_{d}")
        norm = normalise_weights(w)
        st.caption("Applied (normalised to 100): " + ", ".join(f"{DIM_LABELS[d]} {norm[d]:.0f}" for d in DIMENSIONS))
        if st.button("Reset to defaults", width="stretch"):
            st.session_state.weights = dict(screening_config()["weights"])
            for d in DIMENSIONS:
                st.session_state.pop(f"w_{d}", None)
            st.rerun()


def _embedded() -> bool:
    return bool(st.session_state.get("_embed"))


def provenance(uni: Universe):
    if _embedded():
        return
    if uni.is_synthetic:
        st.warning("Synthetic demo data. Every company here is fictitious and generated for testing. Load real data "
                   "with `python -m scripts.run_pipeline all --source yfinance`.", icon="⚠️")
    t = repo.last_refresh()
    st.caption(f"Data sources: {', '.join(uni.sources)} · Last refresh: {t:%d %b %Y %H:%M}" if t
               else f"Data sources: {', '.join(uni.sources)}")


def select_company(uni: Universe, label: str = "Company", container=None) -> int:
    """Company picker shared across company pages (sidebar by default). Passing companies are listed first.
    When a page is embedded in the Company page, the host's choice is used and no picker is drawn."""
    t = uni.table.sort_values(["passes_screen", "total_score"], ascending=[False, False])
    ids = t.company_id.tolist()
    names = dict(zip(t.company_id, t.company_name + " · " + t.ticker))
    current = st.session_state.get("company_id")
    if current not in ids:
        current = ids[0]
    if _embedded():
        return current
    cid = (container or st.sidebar).selectbox(label, ids, index=ids.index(current), format_func=lambda c: names[c],
                               key="company_select")
    st.session_state.company_id = cid
    return cid


def company_row(uni: Universe, cid: int) -> pd.Series:
    r = uni.table.set_index("company_id").loc[cid].copy()
    r["company_id"] = cid
    return r


def kpi(col, label: str, value: str, help: str | None = None):
    col.metric(label, value, help=help)
