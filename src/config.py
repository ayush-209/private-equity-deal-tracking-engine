"""Central configuration: environment settings plus the YAML screening methodology."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

try:  # optional .env support for local runs
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


DATA_DIR = ROOT / "data"
GITHUB_REPO = os.getenv("PE_GITHUB_REPO", "ayush-209/private-equity-deal-tracking-engine")
DATA_BRANCH = "data"          # the refresh job publishes pe.db to this branch; the app downloads it


def database_url() -> str:
    """Default: one SQLite file (data/pe.db) written by the scheduled refresh and read by the app.
    Set PE_DATABASE_URL to use PostgreSQL instead (Docker / a hosted server)."""
    return os.getenv("PE_DATABASE_URL") or f"sqlite:///{DATA_DIR / 'pe.db'}"


def is_sqlite(url: str | None = None) -> bool:
    return (url or database_url()).startswith("sqlite")


def workspace_url() -> str:
    """Where the app's own notes live (deal pipeline, AI outputs). Kept apart from the refreshed market data so a
    new data file never overwrites them. With PostgreSQL everything shares one database."""
    return f"sqlite:///{DATA_DIR / 'workspace.db'}" if is_sqlite() else database_url()


def gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or None


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


CRORE = 1e7  # 1 crore = 10,000,000 rupees. All monetary values in the database are stored in ₹ crore.
DEFAULT_TAX_RATE = 0.2517  # Section 115BAA effective rate incl. surcharge and cess


@lru_cache
def screening_config() -> dict:
    with open(CONFIG_DIR / "screening.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache
def sector_config() -> dict:
    with open(CONFIG_DIR / "sectors.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache
def portfolio_config() -> dict:
    with open(CONFIG_DIR / "portfolio.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)
