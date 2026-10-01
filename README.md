# PE Deal Intelligence

A screening and underwriting workstation that takes a broad universe of Indian listed companies down to a small,
explainable, research-ready deal pipeline:

**Universe → Screening → Company analysis → Valuation → Deal economics → Risk → Due diligence → Investment committee**

It is not a stock-picking or prediction tool. Every score, archetype and red flag can be traced back to
source data → calculation → rule → interpretation, and the AI layer is restricted to a cited fact pack.

## Quick start

### Docker (PostgreSQL included)

```bash
cp .env.example .env                                  # add ANTHROPIC_API_KEY if you want the AI page
docker compose up -d db app
docker compose run --rm pipeline demo                 # synthetic demo data, or:
docker compose run --rm pipeline all --source yfinance --limit 50
```

Open http://localhost:8501.

### Local

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # set DATABASE_URL to your PostgreSQL
python -m scripts.run_pipeline init
python -m scripts.run_pipeline all --source yfinance --limit 50   # first 50 Nifty 500 names
streamlit run app.py
```

Start with `--limit 50` to check the data source works on your machine, then drop the limit for the full
Nifty 500 (roughly half an hour, because requests are deliberately rate-limited).

### Demo data

`python -m scripts.run_pipeline demo` loads 174 **fictitious** companies (every name ends in "(Demo)", every row is
tagged `SYNTHETIC_DEMO`, and the app shows a banner). They are generated so that each archetype and red flag fires,
which makes the demo useful for testing rules, not for drawing conclusions. Use a separate database for real data,
or drop the demo rows first.

## Jobs

| Command | What it does | Suggested schedule |
|---|---|---|
| `run_pipeline market` | Prices and market cap, then re-screen | Weekdays after market close |
| `run_pipeline fundamentals` | Annual statements and ownership, then re-screen | Weekly |
| `run_pipeline screen` | Recalculate metrics, valuation and scores | On demand |
| `run_pipeline all` | Everything | First load |
| `run_pipeline demo` | Synthetic data + screen | Testing |

Add `--source yfinance|csv` and `--index nifty500|nifty_midsmallcap400|nifty_total_market`.
`.github/workflows/refresh.yml` runs these on a schedule against the database in the `DATABASE_URL` repository
secret. Every run is logged in the `pipeline_runs` table and `logs/pipeline.log`.

## Data sources

- **Universe**: Nifty index constituent files (cached to `data/universe.csv`; replace with your own list if the site blocks you).
- **yfinance**: free; about four years of annual statements; figures are restated, not as reported.
- **CSV adapter**: for CMIE Prowess, Capitaline or Ace Equity exports. Gives longer history and real filing dates,
  which make historical validation genuinely point-in-time. Format in `data/README.md`.

## Pages

| Section | Page | Purpose |
|---|---|---|
| Screening | Executive dashboard | Funnel, sector mix, score distribution, growth vs ROIC |
| | Deal universe | Filterable, sortable table; select a company; CSV/Excel/JSON export |
| Company | Company deep dive | Trends, key metrics, score breakdown with every underlying metric, archetype reasons, red flags, PDF |
| | Comparable companies | Automatic peer group, multiples vs p25/median/p75, what explains the valuation |
| | Deal economics | Control (illustrative) or minority/PIPE; sources & uses, debt paydown, value bridge, MOIC/IRR heatmaps |
| | Risk & due diligence | Flags by category, valuation and data risks, traceable DD questions |
| | AI research | Cited research note from a deterministic fact pack, with number verification |
| | Investment committee | One-page summary and PDF export |
| Workflow | Deal pipeline | Statuses, thesis, concerns, next steps; persists in PostgreSQL with history |
| | Historical validation | Re-screen at a past date, track later fundamentals |
| | Data quality | Grades, coverage, validation issues, run log |

Screening weights are in the sidebar on every page; scores update immediately.

## Project structure

```
app.py                      Streamlit entry point and navigation
app_pages/                  One file per page (UI only — no calculations)
src/
  config.py                 Environment + YAML methodology
  database/                 Schema (models.py) and all SQL access (repository.py)
  data_ingestion/           Source adapters, synthetic demo, ingestion pipeline
  data_validation/          Batch validation and data-quality grading
  financial_metrics/        Pure formula functions and per-company time series
  screening/                Scoring, archetypes, pass rules; universe assembly
  valuation/                Multiples, peer groups, valuation explanation
  lbo/                      Control and minority deal models, IRR, sensitivities
  risk/                     Red flags and due-diligence questions
  ai/                       Fact pack, prompt, generation, number verification
  backtest/                 Historical validation
  reporting/                IC summary, PDFs, CSV/Excel/JSON exports
  ui/                       Shared Streamlit state, formatting (₹ crore, lakh crore), charts
config/                     screening.yaml (weights, thresholds, rules), sectors.yaml
sql/                        schema.sql (generated), reference queries, seed notes
scripts/                    run_pipeline.py (CLI), export_schema.py
tests/                      Unit tests + PostgreSQL integration tests
docs/METHODOLOGY.md         Formulas, scoring, valuation, LBO, AI, limitations
```

Pages live in `app_pages/` rather than `pages/` because Streamlit treats a `pages/` folder as an automatic
multipage app, which overrides the grouped navigation defined in `app.py`.

## Tests

```bash
pytest -q                                              # unit tests
TEST_DATABASE_URL=postgresql+psycopg2://pe:pe@localhost:5432/pe_test pytest -q   # + integration (wipes that DB)
```

The suite covers CAGR, ROIC, FCF conversion, net debt, leverage classes, working-capital days, IRR/MOIC,
sources-and-uses balance, the value bridge, sensitivities, scoring, weight normalisation, archetypes, validation, red
flags, formatting and the AI number check, including edge cases (negative EBITDA, negative FCF, zero debt, zero
interest, negative equity, missing data, extreme leverage). Integration tests cover append-only versioning,
point-in-time loading and pipeline persistence.

## Design decisions worth knowing

- **Relative scoring.** Percentiles within industry peer groups, so the screen does not just rank sectors.
- **Financials excluded.** Banks, NBFCs and insurers need a different framework; they are listed with the reason.
- **Missing data cannot help a company.** Insufficient dimensions make a score partial and block a pass.
- **Ind AS 116** breaks in margin history are flagged for lease-heavy industries.
- **India deal structure.** The control LBO is labelled illustrative (delisting rules, acquisition-finance limits);
  a minority/PIPE mode reflects how most PE capital enters listed Indian companies.
- **AI is grounded and checked.** The model sees only a fact pack with IDs; numbers it produces are verified.

See `docs/METHODOLOGY.md` for every formula and threshold, and its Limitations section before relying on any output.

*Not investment advice.*
