"""Command-line entry point for scheduled and on-demand jobs.

Examples
  python -m scripts.run_pipeline demo                    # load synthetic demo data + screen
  python -m scripts.run_pipeline universe --index nifty500
  python -m scripts.run_pipeline market                  # daily: prices / market cap
  python -m scripts.run_pipeline fundamentals            # periodic: annual statements + ownership
  python -m scripts.run_pipeline screen                  # recalc metrics, valuation, screening
  python -m scripts.run_pipeline all --source yfinance
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import ROOT, screening_config  # noqa: E402
from src.data_ingestion.pipeline import ingest  # noqa: E402
from src.database import repository as repo  # noqa: E402
from src.screening.universe import build_universe, persist_screen  # noqa: E402

(ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs" / "pipeline.log")])
log = logging.getLogger("run_pipeline")


def get_adapter(source: str):
    if source == "yfinance":
        from src.data_ingestion.adapters import YFinanceAdapter
        return YFinanceAdapter()
    if source == "csv":
        from src.data_ingestion.adapters import CSVAdapter
        return CSVAdapter()
    if source == "demo":
        from src.data_ingestion.demo import DemoAdapter
        return DemoAdapter()
    raise SystemExit(f"Unknown source {source}")


def get_universe(adapter, index: str):
    if hasattr(adapter, "universe"):
        return adapter.universe()
    from src.data_ingestion.adapters import load_universe
    return load_universe(index)


def run_screen():
    uni = build_universe()
    rid = persist_screen(uni, screening_config()["weights"])
    log.info("Screen run %s: %s companies, %s pass", rid, len(uni.table), int(uni.table.passes_screen.sum()))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("job", choices=["init", "demo", "universe", "market", "fundamentals", "screen", "all"])
    p.add_argument("--source", default="yfinance", choices=["yfinance", "csv", "demo"])
    p.add_argument("--index", default="nifty500")
    p.add_argument("--limit", type=int, default=None, help="Only the first N tickers (for testing)")
    a = p.parse_args()
    repo.init_db()
    if a.job == "init":
        return
    if a.job == "demo":
        ad = get_adapter("demo")
        ingest(ad, ad.universe())
        run_screen()
        return
    ad = get_adapter(a.source)
    uni = get_universe(ad, a.index)
    if a.job == "universe":
        ingest(ad, uni, jobs=(), limit=a.limit)
    elif a.job == "market":
        ingest(ad, uni, jobs=("market",), limit=a.limit)
        run_screen()  # multiples and valuation scores move with prices
    elif a.job == "fundamentals":
        ingest(ad, uni, jobs=("financials", "ownership"), limit=a.limit)
        run_screen()
    elif a.job == "screen":
        run_screen()
    elif a.job == "all":
        ingest(ad, uni, limit=a.limit)
        run_screen()


if __name__ == "__main__":
    main()
