"""Download the published database file.

The scheduled refresh publishes data/pe.db to the `data` branch of the GitHub repository (one commit, history
discarded, so the repository does not grow). The app fetches it here at start-up and again when it is older than
MAX_AGE_HOURS. If the download fails the local copy, if any, keeps being used.
"""
from __future__ import annotations

import logging
import os
import time

import requests

from src.config import DATA_BRANCH, DATA_DIR, GITHUB_REPO, is_sqlite

log = logging.getLogger(__name__)
MAX_AGE_HOURS = 6
DB_PATH = DATA_DIR / "pe.db"


def db_url() -> str:
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{DATA_BRANCH}/pe.db"


def local_age_hours() -> float | None:
    return (time.time() - DB_PATH.stat().st_mtime) / 3600 if DB_PATH.exists() else None


def ensure_local_db(force: bool = False) -> str:
    """Returns a short status string. Never raises: the app should still start if GitHub is unreachable."""
    if not is_sqlite() or os.getenv("PE_NO_DOWNLOAD"):
        return "not used"
    age = local_age_hours()
    if not force and age is not None and age < MAX_AGE_HOURS:
        return f"local copy, {age:.1f} h old"
    try:
        r = requests.get(db_url(), timeout=120)
        r.raise_for_status()
        if not r.content.startswith(b"SQLite format 3"):
            raise ValueError("downloaded file is not a SQLite database")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DB_PATH.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        os.replace(tmp, DB_PATH)
        from src.database import repository as repo
        repo.get_engine.cache_clear()           # connections to the replaced file are stale
        repo.get_workspace_engine.cache_clear()
        return f"downloaded {len(r.content) / 1e6:.1f} MB"
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not download the database: %s", exc)
        return f"download failed ({exc}); " + ("using local copy" if DB_PATH.exists() else "no local copy")
