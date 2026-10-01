# Data folder

Nothing here is committed except templates. The pipeline writes:

- `universe.csv` — cached index constituents (or put your own list here: `Company Name, Symbol, ISIN Code, Industry`).
- `raw/<run_id>/` — the raw response for every ticker in every ingestion run, kept for audit.

## Loading licensed data (CMIE Prowess, Capitaline, Ace Equity)

Export annual statements to `financials_long.csv` in long format — see `financials_long.example.csv`:

| column | meaning |
|---|---|
| ticker | NSE symbol |
| fiscal_year | 2025 = year ending 31-Mar-2025 |
| period_end | statement date |
| field | one of the `FINANCIAL_FIELDS` in `src/database/models.py` |
| value | number |
| unit | `cr`, `lakh`, `mn` or `rupees` (EPS is never scaled) |
| filing_date | optional: date the results were published. Supplying it makes historical validation genuinely point-in-time. |

The example rows use a dummy ticker and placeholder values to show the format.
Optional `market_long.csv`: `ticker, date, share_price, market_cap_cr`. Then run
`python -m scripts.run_pipeline all --source csv`.
