"""Run an existing page inside a tab of another page, so every view of a company lives in one place.

While a page is embedded: its title is dropped (the host page has one), its sidebar widgets move into an expander,
the company picker and data banner are skipped (the host already shows them), and st.stop() only ends that tab."""
from __future__ import annotations

from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import StopException

PAGES = Path(__file__).resolve().parents[2] / "app_pages"


def embedded() -> bool:
    return bool(st.session_state.get("_embed"))


def run_page(name: str, controls_label: str = "Settings") -> None:
    src = (PAGES / f"{name}.py").read_text()
    real_title, real_sidebar = st.title, st.sidebar
    st.session_state["_embed"] = True
    st.title = lambda *a, **k: None                      # type: ignore[assignment]
    st.sidebar = st.expander(controls_label, expanded=True)   # type: ignore[assignment]
    try:
        exec(compile(src, str(PAGES / f"{name}.py"), "exec"), {"__name__": f"embedded_{name}"})   # noqa: S102
    except StopException:
        pass
    finally:
        st.title, st.sidebar = real_title, real_sidebar    # type: ignore[assignment]
        st.session_state["_embed"] = False
