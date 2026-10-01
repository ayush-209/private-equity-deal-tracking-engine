"""PE Deal Intelligence — Streamlit entry point. Run: streamlit run app.py"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.ui.charts  # noqa: E402,F401  (registers the Plotly template)
from src.ui import state  # noqa: E402

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
    "Screening": [
        st.Page(f"{P}/executive_dashboard.py", title="Executive dashboard", icon="📈", default=True),
        st.Page(f"{P}/deal_universe.py", title="Deal universe", icon="🔎"),
    ],
    "Company": [
        st.Page(f"{P}/company_deep_dive.py", title="Company deep dive", icon="🏢"),
        st.Page(f"{P}/comparables.py", title="Comparable companies", icon="⚖️"),
        st.Page(f"{P}/deal_economics.py", title="Deal economics", icon="💰"),
        st.Page(f"{P}/risk_due_diligence.py", title="Risk & due diligence", icon="🚩"),
        st.Page(f"{P}/ai_research.py", title="AI research", icon="🧠"),
        st.Page(f"{P}/investment_committee.py", title="Investment committee", icon="📄"),
    ],
    "Workflow": [
        st.Page(f"{P}/deal_pipeline.py", title="Deal pipeline", icon="🗂️"),
        st.Page(f"{P}/historical_validation.py", title="Historical validation", icon="⏪"),
        st.Page(f"{P}/data_quality.py", title="Data quality", icon="🧪"),
    ],
})
state.weight_controls()
nav.run()
