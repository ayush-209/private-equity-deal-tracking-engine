"""Cached live price fetches for the Streamlit pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.portfolio import tracking as tk


@st.cache_data(ttl=900, show_spinner="Fetching latest prices…")
def daily_prices(tickers: tuple, start: str) -> pd.DataFrame:
    return tk.fetch_daily(list(tickers), start)
