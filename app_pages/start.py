import streamlit as st

from src.database import repository as repo
from src.ui import portfolio_state as ps
from src.ui import state

uni = state.universe()
d = ps.class_data()
scored = ps.scored(d, False, True)
st.title("Deal & Portfolio Intelligence for Indian listed companies")
st.markdown("One dataset, two ways to use it. Underwrite a company as a **private-equity deal**, or pick stocks for a "
            "**diversified portfolio**. Both start from the same screen, the same size groups and the same explanations.")
state.provenance(uni)

t = uni.table
c = st.columns(4)
c[0].metric("Companies loaded", f"{len(t):,}")
c[1].metric("Pass the PE screen", f"{int(t.passes_screen.sum()):,}", help="Deal score plus hard tests on cash flow, debt and quality.")
c[2].metric("Scored for a portfolio", f"{int(scored.class_score.notna().sum()):,}",
            help="Eligible (not a lender, profitable, priced) and with enough data.")
c[3].metric("Deals in your pipeline", f"{len(repo.load_pipeline())}")

st.subheader("Where things are")
a, b, c2 = st.columns(3)
a.markdown("**Screening**\n\nExecutive dashboard, Deal universe, the combined score table, and the Deal shortlist.")
b.markdown("**Company**\n\nDeep dive, comparables, deal economics (MOIC and IRR), risk and diligence, AI research, investment-committee memo. "
           "The Company hub puts them in one place along with the price chart and portfolio fit.")
c2.markdown("**Portfolio and workflow**\n\nPortfolio builder and tracking, Deal pipeline, Historical validation, Data quality.")

st.subheader("The two scores")
l, r = st.columns(2)
l.markdown("**Deal score (private equity).** Would this be a good business to buy and improve? Growth, profitability, cash flow, "
           "leverage, valuation, working capital, plus red flags.")
r.markdown("**Portfolio score (investing).** Is this a good stock to own? Each company is compared with others of **its own size** "
           "(large, mid or small cap) on growth, profitability, valuation, risk and price trend.")
st.info("A company can score high on one and low on the other. A cash-rich, cheap, slow-growing business can be a good deal "
        "and a poor stock, and the reverse.")
st.caption("Limits: about five years of restated fundamentals; only companies that exist today (survivorship bias); beta is "
           "noisy; prices are not dividend-adjusted. Research tool, not investment advice.")
