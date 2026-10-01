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


def database_url() -> str:
    """PostgreSQL is the intended backend. SQLite is accepted only for quick local tests."""
    return os.getenv("DATABASE_URL", "postgresql+psycopg2://pe:pe@localhost:5432/pe_deals")


def anthropic_api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or None


def anthropic_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")


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
