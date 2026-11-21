"""Download daily bars for the ETF universe, validate, and store.

    python -m quantlab.ingest_us_etf --to csv   # writes data/us_etf/*.csv (used by GitHub Actions)
    python -m quantlab.ingest_us_etf --to db    # loads into Postgres
    python -m quantlab.ingest_us_etf --to db --from-csv   # load the committed CSVs into Postgres
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

from quantlab import store
from quantlab.validate import validate_bars

log = logging.getLogger("ingest")
CONFIG = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"
MARKET = "us_etf"


def fetch(symbol: str, start: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.Ticker(symbol).history(start=start, auto_adjust=False, actions=False)
    if raw.empty:
        return raw
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low",
                             "Close": "close", "Adj Close": "adj_close",
                             "Volume": "volume"})
    return df[store.COLS]


def run(to: str, from_csv: bool) -> int:
    cfg = yaml.safe_load(CONFIG.read_text())
    failed = []
    frames: dict[str, pd.DataFrame] = {}
    for item in cfg["us_etf"]:
        sym = item["symbol"]
        df = store.read_bars(MARKET, sym) if from_csv else fetch(sym, cfg["history_start"])
        report = validate_bars(sym, df)
        for w in report.warnings:
            log.warning("%s: %s", sym, w)
        if not report.ok:
            log.error("REJECT %s: %s", sym, report.errors)
            failed.append(sym)
            continue
        frames[sym] = df
        log.info("%s: %d rows %s -> %s", sym, len(df), df.index[0].date(), df.index[-1].date())

    if to == "csv":
        for sym, df in frames.items():
            store.write_bars(MARKET, sym, df)
    else:
        from quantlab import db
        with db.connect() as conn:
            for item in cfg["us_etf"]:
                sym = item["symbol"]
                if sym in frames:
                    iid = db.upsert_instrument(conn, sym, "US", "ETF", "USD", item.get("name"))
                    db.upsert_bars(conn, iid, frames[sym], source="yfinance")
            conn.commit()
    log.info("stored %d symbols, rejected %s", len(frames), failed or "none")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--to", choices=["csv", "db"], default="csv")
    p.add_argument("--from-csv", action="store_true")
    a = p.parse_args()
    sys.exit(run(a.to, a.from_csv))
