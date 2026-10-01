"""Normalised schema. This module is the single source of truth; sql/schema.sql is generated from it.

Design rules
- All monetary values are ₹ crore.
- Nothing is overwritten. Every financial/market/ownership row carries `source` and `ingested_at`;
  "latest" views pick the most recent ingestion per (company, period).
- `filing_date` records when a figure became public, so historical screens can avoid look-ahead bias.
"""
from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    UniqueConstraint, func,
)

metadata = MetaData()

companies = Table(
    "companies", metadata,
    Column("company_id", Integer, primary_key=True, autoincrement=True),
    Column("company_name", String(255), nullable=False),
    Column("ticker", String(32), nullable=False, unique=True),
    Column("isin", String(16)),
    Column("exchange", String(8), nullable=False, server_default="NSE"),
    Column("sector", String(64)),
    Column("industry", String(128)),
    Column("market_cap", Float),            # latest, ₹ cr (denormalised convenience; history in market_data)
    Column("listing_status", String(16), server_default="Listed"),
    Column("fiscal_year_end_month", Integer, server_default="3"),
    Column("source", String(64), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now()),
)

financials = Table(
    "financials", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False, index=True),
    Column("fiscal_year", Integer, nullable=False),     # FY2025 = year ending 31-Mar-2025
    Column("period_end", Date, nullable=False),
    Column("filing_date", Date, nullable=False),
    Column("filing_date_estimated", Boolean, nullable=False, server_default="true"),
    Column("revenue", Float), Column("cost_of_revenue", Float),
    Column("ebitda", Float), Column("ebit", Float), Column("depreciation", Float),
    Column("pretax_income", Float), Column("tax_expense", Float), Column("pat", Float),
    Column("eps", Float),
    Column("operating_cash_flow", Float), Column("capex", Float), Column("free_cash_flow", Float),
    Column("total_assets", Float), Column("total_equity", Float), Column("total_debt", Float),
    Column("cash", Float), Column("receivables", Float), Column("inventory", Float),
    Column("payables", Float), Column("interest_expense", Float),
    Column("dividends_paid", Float),
    Column("source", String(64), nullable=False),
    Column("ingested_at", DateTime, nullable=False, server_default=func.now()),
    UniqueConstraint("company_id", "fiscal_year", "source", "ingested_at", name="uq_fin_version"),
)

market_data = Table(
    "market_data", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False, index=True),
    Column("date", Date, nullable=False),
    Column("share_price", Float),
    Column("shares_outstanding_cr", Float),
    Column("market_cap", Float),
    Column("enterprise_value", Float),
    Column("source", String(64), nullable=False),
    Column("ingested_at", DateTime, nullable=False, server_default=func.now()),
    UniqueConstraint("company_id", "date", "source", name="uq_market_obs"),
)

ownership = Table(
    "ownership", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False, index=True),
    Column("date", Date, nullable=False),
    Column("promoter_holding", Float),
    Column("institutional_holding", Float),
    Column("public_holding", Float),
    Column("source", String(64), nullable=False),
    Column("ingested_at", DateTime, nullable=False, server_default=func.now()),
    UniqueConstraint("company_id", "date", "source", name="uq_own_obs"),
)

calculated_metrics = Table(
    "calculated_metrics", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False, index=True),
    Column("fiscal_year", Integer, nullable=False),
    Column("calc_run_id", Integer, nullable=False),
    *[Column(c, Float) for c in [
        "revenue_growth", "revenue_cagr_3y", "revenue_cagr_5y", "ebitda_growth", "ebitda_cagr_3y",
        "eps_cagr_3y", "ebitda_margin", "ebit_margin", "pat_margin", "roic", "roe", "fcf_margin",
        "fcf_conversion", "ocf_to_ebitda", "net_debt", "net_debt_to_ebitda", "interest_coverage",
        "debt_to_equity", "fcf_to_debt", "dso", "dio", "dpo", "cash_conversion_cycle",
    ]],
    UniqueConstraint("company_id", "fiscal_year", "calc_run_id", name="uq_metric_run"),
)

valuation = Table(
    "valuation", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False, index=True),
    Column("date", Date, nullable=False),
    Column("ev_ebitda", Float), Column("ev_sales", Float), Column("pe", Float), Column("fcf_yield", Float),
    Column("peer_group", String(128)), Column("peer_median_ev_ebitda", Float),
    Column("calc_run_id", Integer, nullable=False),
)

screening_results = Table(
    "screening_results", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False, index=True),
    Column("screening_date", Date, nullable=False),
    Column("growth_score", Float), Column("profitability_score", Float), Column("cash_flow_score", Float),
    Column("leverage_score", Float), Column("valuation_score", Float), Column("working_capital_score", Float),
    Column("total_score", Float),
    Column("archetype", String(64)),
    Column("data_quality", String(8)),
    Column("passes_screen", Boolean),
    Column("weights", JSON),
    Column("calc_run_id", Integer, nullable=False),
)

deal_pipeline = Table(
    "deal_pipeline", metadata,
    Column("company_id", Integer, ForeignKey("companies.company_id"), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("thesis", Text), Column("concerns", Text), Column("next_steps", Text), Column("notes", Text),
    Column("added_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now()),
)

pipeline_history = Table(  # append-only audit trail of status changes
    "pipeline_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False),
    Column("status", String(32), nullable=False),
    Column("changed_at", DateTime, server_default=func.now()),
    Column("note", Text),
)

ai_outputs = Table(
    "ai_outputs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.company_id"), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
    Column("model", String(64)),
    Column("fact_pack", JSON),
    Column("output", Text),
    Column("unverified_numbers", JSON),
)

pipeline_runs = Table(
    "pipeline_runs", metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("job", String(64), nullable=False),
    Column("source", String(64)),
    Column("started_at", DateTime, server_default=func.now()),
    Column("finished_at", DateTime),
    Column("status", String(16)),
    Column("n_ok", Integer), Column("n_failed", Integer),
    Column("message", Text),
)

data_issues = Table(
    "data_issues", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("pipeline_runs.run_id")),
    Column("company_id", Integer, ForeignKey("companies.company_id")),
    Column("fiscal_year", Integer),
    Column("check_name", String(64), nullable=False),
    Column("severity", String(8), nullable=False),   # info | warn | error
    Column("detail", Text),
    Column("created_at", DateTime, server_default=func.now()),
)

FINANCIAL_FIELDS = [
    "revenue", "cost_of_revenue", "ebitda", "ebit", "depreciation", "pretax_income", "tax_expense", "pat",
    "eps", "operating_cash_flow", "capex", "free_cash_flow", "total_assets", "total_equity", "total_debt",
    "cash", "receivables", "inventory", "payables", "interest_expense", "dividends_paid",
]
