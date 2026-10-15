# Methodology

This document explains how every number in the application is produced, so that any output can be traced
from source data to calculation to screening rule to interpretation.

## 1. Data sources and provenance

| Source | What it provides | Strengths | Limitations |
|---|---|---|---|
| Nifty index constituent files (niftyindices.com) | Universe: name, NSE symbol, ISIN, NSE industry | Official classification; no hard-coded list | Index membership only (Nifty 500 by default; Total Market for ~750) |
| yfinance (`.NS` tickers) | ~4 annual statements, monthly prices, share count, insider/institutional holding | Free, broad coverage | Restated (latest) figures, not as reported; short history; field gaps for some companies; historic market cap uses the *current* share count |
| CSV adapter | Any licensed export (CMIE Prowess, Capitaline, Ace Equity) | 10+ years, true filing dates | Requires a licence and an export step |
| Synthetic demo | Fictitious companies for testing | Runs offline; exercises every rule | Not real; every row tagged `SYNTHETIC_DEMO` and bannered in the UI |

Every row stores `source` and `ingested_at`. Raw responses are written to `data/raw/<run_id>/` before validation.
All monetary values are stored in **₹ crore**.

**Filing dates.** SEBI LODR Regulation 33 requires audited annual results within 60 days of the year end.
When a source gives no publication date, `filing_date = period_end + 60 days` and `filing_date_estimated = true`.

**Append-only history.** Statements are never overwritten. A re-ingested statement that differs from the stored
version is appended as a new row; `v_financials_latest` returns the most recent version, and the full restatement
trail stays queryable. Identical re-ingestions append nothing.

## 2. Validation

Applied to every batch before insertion (`src/data_validation/validator.py`):

| Check | Action |
|---|---|
| Non-numeric values | Coerced to null, logged as warning |
| Duplicate company/year in batch | Last kept, logged |
| Revenue > ₹20 lakh crore | Treated as rupees, converted to crore (EPS untouched), logged |
| Fiscal year inconsistent with period end | Row blocked (error) |
| Year end not in March | Warning (transition or non-standard year; peer comparisons misaligned) |
| Missing critical fields | Warning |
| EBIT > EBITDA, EBITDA margin > 90%, negative revenue, debt > 150% of assets | Outlier warning |
| Revenue moves >5x or <0.2x YoY | Warning (units, merger or restatement) |
| API failure / empty response | Logged per ticker; the run continues |

**Data-quality grade** (per company):

- **High**: ≥5 years of history, critical fields ≥95% complete, secondary (working-capital) fields ≥75% complete over the last 5 years *and* in the latest year, market data present, ≤3 validation warnings.
- **Medium**: ≥3 years, critical fields ≥80%, market data present.
- **Low**: anything else. Low-quality companies are scored for information but **cannot pass the screen**.

## 3. Financial formulas

All formulas live in `src/financial_metrics/metrics.py` and return *not meaningful* (NaN, shown as "n.m.") rather
than a misleading number when inputs make the metric undefined.

| Metric | Formula | Not meaningful when |
|---|---|---|
| YoY growth | Xₜ / Xₜ₋₁ − 1 | prior value ≤ 0 |
| CAGR (n years) | (Xₜ / Xₜ₋ₙ)^(1/n) − 1, base year must be exactly n years earlier | either endpoint ≤ 0, base year missing |
| EBITDA / EBIT / PAT margin | ÷ revenue | revenue ≤ 0 |
| Effective tax rate | tax / PBT, bounded to [0%, 35%] | PBT ≤ 0 → 25.17% (Sec. 115BAA) |
| NOPAT | EBIT × (1 − ETR) | |
| Invested capital | equity + total debt − cash | ≤ 0 |
| ROIC | NOPAT / average(opening, closing invested capital) | invested capital ≤ 0 |
| ROE | PAT / average equity | equity ≤ 0 |
| Free cash flow | operating cash flow − |capex| | |
| FCF margin | FCF / revenue | |
| FCF conversion | FCF / EBITDA | EBITDA ≤ 0 |
| OCF/EBITDA (3y) | Σ OCF / Σ EBITDA over the last 3 years | Σ EBITDA ≤ 0 |
| Net debt | total debt − cash | |
| Net debt / EBITDA | | EBITDA ≤ 0 (scored as worst if net debt > 0) |
| Interest coverage | EBIT / interest, capped at 99x | EBIT ≤ 0 with zero interest |
| Debt / equity | | equity ≤ 0 |
| FCF / debt | | zero debt |
| DSO | receivables / revenue × 365 | |
| DIO, DPO | inventory or payables / COGS × 365 (revenue if COGS unavailable — flagged) | |
| Cash conversion cycle | DSO + DIO − DPO | DSO unavailable |
| Stability | standard deviation over the last 5 years (growth, margin) | <3 observations |
| Direction | 3-year change; 5-year linear slope of margin | |

Year-end balances are used for working-capital days (average balances would need the opening balance sheet, which
the free source often lacks).

## 4. Screening methodology

### Exclusions
Banks, NBFCs, insurers and other financial-services companies are excluded from the operating-company screen.
EBITDA, net debt, FCF conversion and working-capital days do not describe a lender's economics; they need a framework
built on NIM, asset quality (GNPA/NNPA), capital adequacy and credit cost. Excluded companies are listed on the dashboard
with the reason.

### Peer groups
Scores are relative. Each company's peer group is its NSE industry; if fewer than 5 companies in that industry have a
usable value, it widens to the sector, then to the whole screenable universe. The level used is shown everywhere.

### Sub-metric percentiles
Each sub-metric (listed in `config/screening.yaml`) is converted to a mid-rank percentile inside the peer group:
`(rank − 0.5) / n`, inverted where lower is better. Values whose absence means *bad* rather than *unknown* are
filled with the worst value before ranking: net debt/EBITDA when EBITDA is negative and net debt positive;
EV/EBITDA and P/E for loss-making companies; FCF conversion for negative EBITDA.

### Dimensions and weights

| Dimension | Default weight | Sub-metrics |
|---|---:|---|
| Growth | 20 | revenue CAGR 3y and 5y, EBITDA CAGR 3y, EPS CAGR 3y, revenue-growth volatility (lower better) |
| Profitability | 20 | EBITDA margin, EBIT margin, ROIC, ROE, 3y margin change, margin volatility (lower better) |
| Cash flow | 20 | FCF margin, FCF conversion, 3y OCF/EBITDA, years of positive FCF in last 5 |
| Leverage | 15 | net debt/EBITDA, interest coverage, debt/equity, FCF/debt |
| Valuation | 15 | EV/EBITDA, EV/Sales, P/E (lower better), FCF yield (higher better) |
| Working capital | 10 | CCC level, 3y CCC change, 3y DSO change (all lower better) |

Dimension score = mean of available sub-metric percentiles × dimension weight. A dimension needs at least 50% of its
sub-metrics; otherwise it is marked *insufficient*. The total is re-based over the dimensions that could be scored,
but the company is flagged *partial* and **cannot pass the screen**, so missing data never silently inflates a score.

User weights are re-normalised to sum to 100.

### Pass rules
A company passes if: total score ≥ 60, data quality High or Medium, no critical red flags, and no insufficient dimension.

### Leverage classes
Net cash → Low. EBITDA ≤ 0 with net debt, or interest cover < 1.5x → Distressed. Otherwise by net debt/EBITDA:
< 1.0x Low, < 2.5x Moderate, < 4.0x High, else Distressed.

### Archetypes (evaluated in this order; first match wins)

| Archetype | Rule |
|---|---|
| Compounder | revenue CAGR 3y ≥ 12%, ROIC ≥ 15%, FCF conversion ≥ 50% |
| Cash Flow Compounder | revenue CAGR 3y ≥ 8%, FCF conversion ≥ 70%, FCF margin ≥ 10% |
| Turnaround Candidate | ROIC three years ago ≤ 8%, ROIC rising, latest revenue growth > 0, EBITDA margin +1pp over 3y |
| Margin Expansion | revenue CAGR 3y ≥ 3%, EBITDA margin +2pp over 3y, positive 5y margin slope |
| Value Opportunity | EV/EBITDA ≥ 20% below peer median *and* growth/profitability/cash-flow percentiles average ≥ 45% |
| Leveraged Opportunity | FCF conversion ≥ 60%, net cash or ND/EBITDA ≤ 1.5x, interest cover ≥ 5x, margin volatility ≤ 3pp |
| Further Investigation | none of the above |

Turnaround is tested before Margin Expansion because a recovering company also shows expanding margins; the weak
starting ROIC is the more specific signal. Each assignment carries the metric values that satisfied the rule.

## 5. Red flags

Each flag records metric, current value, previous value, threshold, explanation and severity
(critical / high / medium). Thresholds are in `config/screening.yaml`.

| Category | Flags |
|---|---|
| Revenue quality | revenue decline; unstable growth (σ > 15%); receivables growth exceeding revenue growth by >10pp |
| Profitability | EBITDA margin down >2pp YoY; ROIC down >3pp; PAT growth lagging EBITDA growth by >15pp; negative EBITDA (critical) |
| Cash flow | negative FCF (critical if 3 of 3 years); FCF conversion < 30%; 3y OCF/EBITDA < 60% |
| Balance sheet | ND/EBITDA up >0.75x YoY; interest cover < 2.5x (critical < 1.5x); gross debt up >30% |
| Working capital | DSO +10 days, DIO +15 days, CCC +15 days versus three years earlier |
| Accounting | **Ind AS 116 break**: for lease-heavy industries whose history spans FY2019→FY2020, margins and CAGRs across the transition overstate improvement because lease rentals moved below EBITDA |

Due-diligence questions are generated from templates attached to each flag, so every question names the data point
that triggered it. The LLM is not used for this list.

## 6. Valuation

EV = market capitalisation + latest net debt, recomputed so the multiple and its denominator refer to the same balance
sheet. EV/EBITDA, EV/Sales and P/E are not meaningful for non-positive denominators or negative EV.
FCF yield = FCF / market cap.

For each multiple the app shows the company's value, peer p25 / median / p75 (excluding the company itself) and the
premium or discount to the median.

A low multiple is never treated as attractive by itself. `explain_valuation` places the company's growth, ROIC,
margin, FCF conversion, leverage and growth volatility within the peer distribution and states whether each one
supports or contradicts the premium/discount. Where fundamentals sit mid-pack, the note points to factors outside the
data (governance, free float, liquidity, business mix).

## 7. Deal economics

### Control buyout (illustrative)
- Entry EV = entry multiple × LTM EBITDA. Fees = % of EV. Acquisition debt = Debt/EBITDA × LTM EBITDA.
  Sponsor equity = EV + fees − debt. Existing net debt is treated as refinanced inside the EV.
- Operating case: revenue grows at the chosen CAGR; EBITDA margin moves linearly from current to the exit margin
  (or EBITDA grows at an override CAGR in the sensitivity grids); D&A, capex as % of revenue; ΔNWC as % of
  incremental revenue.
- Each year: interest on opening debt; tax on (EBITDA − D&A − interest) if positive; levered FCF = EBITDA − interest −
  tax − capex − ΔNWC; a share of positive FCF (cash sweep) repays debt; the remainder builds cash.
- Exit EV = exit multiple × final-year EBITDA; exit equity = exit EV − (remaining debt − accumulated cash).
- MOIC = exit equity / sponsor equity. IRR solved by bisection on [−equity, 0, …, exit equity].
- Value bridge: EBITDA growth at the entry multiple + multiple change on exit EBITDA + deleveraging − fees. It
  reconciles exactly to the equity gain (tested).

**India caveat.** Taking a listed company private requires delisting under the SEBI (Delisting of Equity Shares)
Regulations, and onshore bank funding of share acquisitions has historically been restricted, so acquisition debt is
typically raised through NCDs or offshore structures. The control case is therefore a theoretical ceiling.

### Minority / PIPE stake
Stake % bought at market capitalisation × (1 + premium); no acquisition debt. The company's net debt evolves with its
own FCF less dividends. Cash flows to the investor: stake share of dividends each year, plus stake share of exit equity
(exit multiple × final EBITDA − net debt). MOIC includes dividends; IRR uses the dated flows.

Not modelled in either case: debt tranching, amortisation schedules, covenants, management incentive plans, tax
structuring, minimum public shareholding constraints on stake size, transaction timing.

## 8. AI methodology

1. A **fact pack** is built deterministically: company identity, five years of revenue/EBITDA/margin/FCF, key ratios,
   multiples versus peers, ownership and every red flag, each with an ID (`C`, `M`, `V`, `R`), source and date.
2. Optional filing text (PDF/TXT upload) is split into ~1,800-character excerpts `[F1]…`.
3. The system prompt restricts the model to these inputs, requires an ID citation on every factual sentence, requires
   the prefix "Interpretation:" for judgement, forbids outside knowledge and buy/sell recommendations, and requires a
   counter-thesis.
4. **Post-generation check**: every number in the output (excluding citations, fiscal-year labels and small counts) is
   compared with the numbers in the fact pack, allowing for rounding and percent/fraction conversion. Unmatched numbers
   are shown in the UI as unverified.
5. Each output is stored with its model name and the exact fact pack it was given.

The check catches invented numbers, not invented qualitative claims; the citation requirement and the visible fact
pack are what let a reader audit those.

## 9. Historical validation

For screening date D: statements with `filing_date ≤ D` and market data dated ≤ D are re-screened with the same rules.
For each company, the base year is the last fiscal year public at D and the end year is the latest available.
Outcomes measured: revenue and EBITDA CAGR, cumulative FCF versus base-year FCF, and changes in ROIC, EBITDA margin
and net debt/EBITDA. The app compares shortlist medians with the rest of the universe, shows outcome medians by score
quintile, and tests whether each archetype's thesis held (e.g. Compounder: revenue CAGR ≥ 8% and ROIC fell < 3pp).

This checks whether the screen picks companies whose later *fundamentals* behave as the thesis implies. It says
nothing about share-price returns and is not evidence of future investment success.

## 10. Limitations

- **Restated data.** Free sources return the latest version of each statement; historical validation is point-in-time on
  availability, not on values. Licensed vintages via the CSV adapter remove this limitation.
- **Short history from yfinance.** About four years, so 5-year CAGRs and volatility measures will often be insufficient;
  more companies receive Medium grades and partial scores.
- **Consolidated vs standalone.** The source decides; the two are not mixed deliberately but are not reconciled either.
- **Promoter holding.** yfinance "insiders" is a proxy for promoter holding; pledged shares are not available and are
  asked about in due diligence instead.
- **Historic market capitalisation** from yfinance uses the current share count; buybacks, bonus issues and dilution
  distort older EVs.
- **Point-in-time EV.** Historical EV uses the latest balance sheet public at the price date, not the balance sheet on
  that date.
- **Thresholds are judgement.** Archetype and red-flag thresholds are reasonable defaults, exposed in YAML so they can
  be reviewed and changed.
- **Sector scope.** Financial-services companies are excluded rather than scored with a bespoke framework.

## 11. Class basis: second ranking and portfolio

A separate scoring system, built from the class framework (size, profitability, valuation, growth, risk, technicals). It
runs beside the private-equity screen and shares only the data. All settings are in `config/portfolio.yaml`.

### Variables

| Class variable | Definition used |
|---|---|
| Market capitalisation | Latest price x shares. Ranked across all companies; rank 1-100 large, 101-250 mid, 251+ small (AMFI convention) |
| ROE | PAT / average equity |
| ROCE | EBIT / average (total assets - current liabilities). If current liabilities are missing: EBIT / (equity + debt), flagged approximate and biased low |
| P/E, P/BV | Market cap / PAT; market cap / total equity. Not meaningful for non-positive denominators |
| PEG | P/E / (EPS growth in percent). Not meaningful when growth <= 0 |
| Sales YoY | Revenue / prior-year revenue - 1 |
| EPS growth | 3-year EPS CAGR (YoY when history is short). Cross-checked against PAT growth; if they differ by more than 50 points a year the PAT figure is used and labelled, because unadjusted splits/bonuses corrupt per-share data |
| Beta | Slope of monthly stock returns on monthly index returns (Nifty 500, falling back to Nifty 50), latest 36-60 months |
| Standard deviation | Sample SD of monthly returns x sqrt(12), same window |
| Technicals | Not defined in the class notes. Used: 6-month return; 12-month return skipping the latest month; price vs 10-month average |

### Scoring

Each metric becomes a percentile **within the company's cap segment** (pooled across segments if one has fewer than 10
companies). Percentiles are averaged within a dimension, and dimensions are combined with the weights below. A
dimension needs at least half its metrics; a company needs at least 4 of the 5 dimensions or it is not ranked. Missing
dimensions re-scale the other weights (never counted as zero). Values where "missing" means "bad" (loss-making P/E, PEG
with no growth, P/BV on negative equity) are ranked last rather than dropped.

Why score inside the segment: ordinary levels of P/E, volatility and growth differ by size. Scoring across sizes would
let one segment crowd out the others; scoring within them gives each segment candidates and keeps the comparison fair.
This is also how market capitalisation influences the result - it decides who competes with whom and how much of the
portfolio each segment must hold - rather than being a score itself.

### Weights: the basis

The class notes leave "based on what?" open. These defaults are reasoned, not derived, and the app shows the reason
beside each (and lets you change them).

| Dimension | Weight | Basis |
|---|---|---|
| Growth (Sales YoY, EPS growth) | 30 | The notes flag YoY and EPS as very important. Objective is capital appreciation over several years, which follows earnings growth. Sales and EPS growth equal inside the dimension |
| Profitability (ROE, ROCE) | 20 | Separates growth that creates value from growth bought with capital |
| Valuation (P/E, PEG, P/BV) | 20 | Entry price drives realised return; PEG and P/BV guard against paying for growth already priced in |
| Risk (beta, SD; lower is better) | 20 | A fixed-horizon capital goal cannot ignore drawdown. Lower-is-better is a defensive tilt; the weight sets how defensive |
| Technical | 10 | Momentum carries information but is the noisiest signal and its definition is not given |

### Do the weights matter? (rank stability)

Because weights are ultimately a judgement, the app measures their influence instead of defending them:

1. **Alternative weight sets** (equal, growth-heavy, quality-heavy, defensive, no-technical): Spearman correlation with the
   base ranking and the share of the base top 15 that survives, per segment.
2. **Random weightings**: 300 weight vectors drawn (Dirichlet) around the base weights; each company's *selection
   frequency* is the share of draws in which it lands among its segment's top picks. High frequency = a pick that does not depend on the
   weights. The portfolio builder can restrict itself to such names.

### Portfolio construction

1. **Select** the top-k by score in each segment (default 5 large, 5 mid, 4 small), skipping a name when its sector would
   exceed floor(max sector x number of names).
2. **Size** with a small constrained least-squares problem (SLSQP): weights as close as possible to the target
   (equal, score-proportional or inverse-volatility) subject to: fully invested, no shorting, maximum position (12%),
   maximum sector (30%), minimum weight per segment (large 30%, mid 20%, small 10%). If the constraints cannot all
   hold, the target weights are shown with a message naming the problem.
3. **Whole shares** at the latest price: round down, then spend leftover cash one share at a time on the position furthest below
   target. Actual weights and cash left are reported.
4. **Risk**: portfolio SD = sqrt(w' S w) with S the annualised covariance of monthly returns over the holdings' common window (up to 60
   months); portfolio beta = slope of the weighted monthly return on the index; diversification = 1 - portfolio SD /
   weighted-average SD. The historical path holds today's weights constant.

Initial capital is an input (default Rs 1 crore). The app does not model or assume a return target.

### Limitations specific to this section

- Fundamentals cover about five years and are restated; beta from 36-60 monthly points has a wide error band (treat +/-0.2 as noise).
- The universe is today's Nifty 500: companies that left the index are absent (survivorship bias), and the historical
  path of today's picks is flattered by hindsight. It shows the ride, not the expected return.
- Prices are not adjusted for dividends; there is no volume data, so small-cap position sizes are not tested against liquidity.
- Banks, NBFCs and insurers are excluded by default; ROCE and sales growth are not comparable for lenders.
- The Yahoo label for current liabilities is "Current Liabilities"; where a company lacks it, ROCE is approximate and labelled.



### How the portfolio is derived (no fixed list)

Four user choices drive everything: capital, number of stocks, the split of money across size groups (large, mid, small), and a style (one of the documented weight sets). Names per size group are `number of stocks x split`, rounded by largest remainder with at least one name for any group with a share. A group's minimum weight is its share minus 5 points, so sizing can lean toward better ideas without abandoning the chosen split. Position and sector limits are inputs with defaults (12% and 30%); the sector limit becomes a maximum number of names per sector, `floor(limit x stocks)`. The Portfolio page lists the eligibility funnel and a decision for every candidate (picked, skipped with the reason, or first below the cut), so each result can be traced.

### Price tracking

A saved portfolio stores shares, buy price and buy date per holding. Current value uses the latest daily close from Yahoo Finance (cached 15 minutes); if that is unavailable the page falls back to the monthly closes in the database and says so. Profit/loss compares value with cost for holdings that have a price; the value path counts each holding from its buy date. The index comparison rebases the index to the amount invested at the start. Buy prices default to the latest stored price at save time and can be corrected to real trades. Prices are not dividend-adjusted, so total return is understated.
