-- No seed data is inserted with SQL: company universes come from data sources, never hard-coded lists.
-- To populate a fresh database:
--   python -m scripts.run_pipeline demo                         (synthetic, clearly labelled)
--   python -m scripts.run_pipeline all --source yfinance        (Nifty 500 via yfinance)
--   python -m scripts.run_pipeline all --source csv             (licensed exports, see data/README.md)
SELECT 1;
