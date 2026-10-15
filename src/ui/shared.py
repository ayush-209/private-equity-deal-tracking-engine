"""Inputs shared by the Portfolio and Deal shortlist pages, so both flows use the same parameters.
Values live in one session dict and survive switching pages."""
from __future__ import annotations

import streamlit as st

DEFAULTS = {"n": 14, "large": 45, "mid": 35, "small": 20, "max_sector": 30}


def _sh() -> dict:
    return st.session_state.setdefault("_shared", dict(DEFAULTS))


def count_and_mix(n_label: str, n_help: str, key: str):
    """Number of names and the large/mid/small split. Returns (n, mix dict summing to the user's shares)."""
    sh = _sh()
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    sh["n"] = c1.number_input(n_label, 3, 40, int(sh["n"]), 1, help=n_help, key=f"{key}_n")
    sh["large"] = c2.slider("Large cap (%)", 0, 100, int(sh["large"]), 5, key=f"{key}_l")
    sh["mid"] = c3.slider("Mid cap (%)", 0, 100, int(sh["mid"]), 5, key=f"{key}_m")
    sh["small"] = c4.slider("Small cap (%)", 0, 100, int(sh["small"]), 5, key=f"{key}_s")
    return int(sh["n"]), {s: sh[s] / 100 for s in ("large", "mid", "small")}


def sector_limit(key: str, container=None) -> float:
    sh = _sh()
    sh["max_sector"] = (container or st).slider("Maximum in one sector (%)", 15, 100, int(sh["max_sector"]), 5, key=f"{key}_ms")
    return sh["max_sector"] / 100
