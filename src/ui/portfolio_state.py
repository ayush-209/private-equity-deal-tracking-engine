"""Streamlit state for the class-basis pages: cached data frame and the shared weight sliders."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import portfolio_config
from src.database import repository as repo
from src.portfolio.frame import ClassData, build_class_frame, class_scores
from src.portfolio.scoring import dim_names, normalise

DIM_LABELS = {"growth": "Growth", "profitability": "Profitability", "valuation": "Valuation", "risk": "Risk",
              "technical": "Technical"}
SEG_LABELS = {"large": "Large cap", "mid": "Mid cap", "small": "Small cap"}


def _refresh_key() -> str:
    try:
        return str(repo.last_refresh())
    except Exception:  # noqa: BLE001
        return "none"


@st.cache_resource(show_spinner="Computing class-basis metrics…", ttl=900)
def _data(refresh_key: str) -> ClassData:
    return build_class_frame()


def class_data() -> ClassData:
    try:
        d = _data(_refresh_key())
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not build the class-basis data: {exc}")
        st.stop()
    if d.frame.empty:
        st.info("No data loaded yet. " + " ".join(d.notes))
        st.stop()
    return d


def clear_cache():
    _data.clear()


def class_weights() -> dict:
    cfg = portfolio_config()
    if "class_weights" not in st.session_state:
        st.session_state.class_weights = {d: float(s["weight"]) for d, s in cfg["dimensions"].items()}
    return st.session_state.class_weights


def scored(d: ClassData, include_financials: bool, exclude_loss: bool) -> pd.DataFrame:
    return class_scores(d, normalise(class_weights(), dim_names()), include_financials, exclude_loss)


def set_style(key: str) -> dict:
    """Load one of the documented weight sets (e.g. 'base', 'defensive') into the session."""
    w = {d: float(v) for d, v in portfolio_config()["weight_sets"][key].items()}
    st.session_state.class_weights = w
    return w
