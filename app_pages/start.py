import streamlit as st

from src.database import repository as repo
from src.ui import portfolio_state as ps
from src.ui import state

uni = state.universe()
d = ps.class_data()
scored = ps.scored(d, False, True)
st.title("Indian listed companies: screen, choose, track")
st.markdown("One app, one flow. Pick a company for a **private-equity style deal**, or build a **diversified stock "
            "portfolio**. Both start from the same data and the same list of companies.")
state.provenance(uni)

t = uni.table
c = st.columns(4)
c[0].metric("Companies loaded", f"{len(t):,}")
c[1].metric("Pass the PE screen", f"{int(t.passes_screen.sum()):,}", help="Deal score and hard tests on cash flow, debt and quality.")
c[2].metric("Scored for a portfolio", f"{int(scored.class_score.notna().sum()):,}",
            help="Eligible (not a lender, profitable, priced) and with enough data.")
n_pf = len(repo.load_portfolios())
c[3].metric("Portfolios you track", f"{n_pf}")

st.subheader("How to use it")
st.markdown("The app is a private-equity deal screener first, with a portfolio builder that works the same way. Steps 1 and 2 "
            "are shared. At step 3 you pick a path, and both paths use the same size groups, sector limit and decision log.")
a, b = st.columns(2)
a.markdown("**1 · Screen**\n\nEvery company with its deal score and portfolio score side by side.")
b.markdown("**2 · Company**\n\nFinancials, price chart, peers, risks and diligence, deal economics, memo, AI note.")
p1, p2 = st.columns(2)
p1.markdown("**3a · Deal shortlist (PE)**\n\nChoose how many targets and the size mix. Get a ranked shortlist with the reason for each, "
            "then underwrite it in Company (sources and uses, MOIC, IRR).")
p2.markdown("**3b · Portfolio**\n\nChoose capital, number of stocks, size mix and style. Get whole-share holdings with the reason for each.")
st.markdown("**4 · Track.** Deal pipeline stages and notes for the PE path; value, profit/loss and performance against the index for the portfolio path.")

st.subheader("The two scores")
l, r = st.columns(2)
l.markdown("**Deal score (private equity).** Asks: *would this be a good company to buy and improve?* Looks at growth, "
           "profitability, cash flow, leverage, valuation and working capital, and flags red flags.")
r.markdown("**Portfolio score (investing).** Asks: *is this a good stock to own?* Compares each company with others of "
           "**its own size** (large, mid or small cap) on growth, profitability, valuation, risk and price trend.")
st.info("A company can score high on one and low on the other. That is the point: a cash-rich, cheap, slow-growing "
        "business can be a good deal and a poor stock, and the reverse.")
st.caption("Limits: fundamentals cover about five years and are restated; the universe only has companies that exist today "
           "(survivorship bias); beta is noisy; prices are not dividend-adjusted. Research tool, not investment advice.")
