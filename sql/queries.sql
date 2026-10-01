-- Reference queries. The application issues equivalent SQL from src/database/repository.py.

-- Latest statements for every company
SELECT * FROM v_financials_latest ORDER BY company_id, fiscal_year;

-- Point-in-time: what was public on a given date (used by historical validation)
SELECT * FROM v_financials_latest WHERE filing_date <= DATE '2023-06-30';

-- Every stored version of one company's statements (restatement audit trail)
SELECT fiscal_year, revenue, ebitda, source, ingested_at
FROM financials f JOIN companies c USING (company_id)
WHERE c.ticker = 'TICKER' ORDER BY fiscal_year, ingested_at;

-- Latest screening run: passing companies by score
SELECT c.company_name, s.total_score, s.archetype, s.data_quality
FROM screening_results s JOIN companies c USING (company_id)
WHERE s.calc_run_id = (SELECT MAX(calc_run_id) FROM screening_results) AND s.passes_screen
ORDER BY s.total_score DESC;

-- How a company's score has moved across screening runs
SELECT screening_date, total_score, archetype FROM screening_results
WHERE company_id = 1 ORDER BY calc_run_id;

-- Validation issues by type for the last ingestion
SELECT check_name, severity, COUNT(*) FROM data_issues
WHERE run_id = (SELECT MAX(run_id) FROM pipeline_runs WHERE job LIKE 'ingest%')
GROUP BY 1, 2 ORDER BY 3 DESC;

-- Pipeline with latest score
SELECT c.company_name, p.status, p.updated_at, s.total_score
FROM deal_pipeline p JOIN companies c USING (company_id)
LEFT JOIN screening_results s ON s.company_id = p.company_id
 AND s.calc_run_id = (SELECT MAX(calc_run_id) FROM screening_results);
