"""PE Deal Intelligence — Streamlit entry point. Run: streamlit run app.py"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.ui.charts  # noqa: E402,F401  (registers the Plotly template)
from src.database.fetch import ensure_local_db  # noqa: E402
from src.ui import state  # noqa: E402


st.session_state["data_status"] = ensure_local_db()   # cheap when fresh; retries a failed download every 5 min

st.set_page_config(page_title="PE Deal Intelligence", page_icon="📊", layout="wide")

st.markdown("""
<style>
  [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-size: 1.45rem; }
  [data-testid="stMetricLabel"] p { font-size: 0.8rem; color: #5B6770; }
  .block-container { padding-top: 2.2rem; max-width: 1500px; }
  h1 { font-size: 1.6rem !important; letter-spacing: -0.01em; }
  h2, h3 { letter-spacing: -0.005em; }
  .flag-critical { border-left: 4px solid #B23A3A; padding-left: .6rem; }
  .flag-high { border-left: 4px solid #B7862B; padding-left: .6rem; }
  .flag-medium { border-left: 4px solid #9AA6A6; padding-left: .6rem; }
  div[data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }
</style>""", unsafe_allow_html=True)

P = "app_pages"
nav = st.navigation({
    "": [
        st.Page(f"{P}/start.py", title="Start here", icon="🏠", default=True),
        st.Page(f"{P}/screen.py", title="Screen", icon="🔎"),
        st.Page(f"{P}/company.py", title="Company", icon="🏢"),
        st.Page(f"{P}/deals.py", title="Deal shortlist (PE)", icon="🤝"),
        st.Page(f"{P}/portfolio.py", title="Portfolio", icon="🧺"),
        st.Page(f"{P}/track.py", title="Track", icon="📈"),
    ],
    "More": [
        st.Page(f"{P}/historical_validation.py", title="Historical validation", icon="⏪"),
        st.Page(f"{P}/data_quality.py", title="Data quality", icon="🧪"),
    ],
})
nav.run()
