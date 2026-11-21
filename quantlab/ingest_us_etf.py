"""Step 1 job: download daily bars for the ETF universe and load them.

Usage:
    python -m quantlab.ingest_us_etf            # full history from config
    python -m quantlab.ingest_us_etf --days 10  # incremental daily run
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd
import yaml

from quantlab import db
from quantlab.validate import validate_bars

log = logging.getLogger("ingest")
CONFIG = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"


def fetch(symbol: str, start: str) -> pd.DataFrame:
    import yfinance as yf  # imported here so tests don't need network libs

    raw = yf.Ticker(symbol).history(start=start, auto_adjust=False, actions=False)
    if raw.empty:
        return raw
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low",
                             "Close": "close", "Adj Close": "adj_close",
                             "Volume": "volume"})
    return df[["open", "high", "low", "close", "adj_close", "volume"]]


def run(days: int | None) -> None:
    cfg = yaml.safe_load(CONFIG.read_text())
    start = (cfg["history_start"] if days is None
             else (dt.date.today() - dt.timedelta(days=days)).isoformat())

    with db.connect() as conn:
        run_id = conn.execute(
            "INSERT INTO ingestion_runs (job) VALUES ('us_etf') RETURNING id"
        ).fetchone()[0]
        conn.commit()

        total, problems = 0, []
        for item in cfg["us_etf"]:
            sym = item["symbol"]
            df = fetch(sym, start)
            report = validate_bars(sym, df)
            if not report.ok:
                problems.append(f"{sym}: {report.errors}")
                log.error("SKIP %s %s", sym, report.errors)
                continue
            for w in report.warnings:
                log.warning("%s: %s", sym, w)
            iid = db.upsert_instrument(conn, sym, "US", "ETF", "USD", item.get("name"))
            total += db.upsert_bars(conn, iid, df, source="yfinance")
            conn.commit()
            log.info("%s: %d rows (%s -> %s)", sym, len(df),
                     df.index[0].date(), df.index[-1].date())

        status = "ok" if not problems else "warn"
        conn.execute(
            """UPDATE ingestion_runs SET finished_at=now(), rows_upserted=%s,
               status=%s, notes=%s WHERE id=%s""",
            (total, status, "; ".join(problems) or None, run_id),
        )
        conn.commit()
    log.info("done: %d rows, status=%s", total, status)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=None)
    run(p.parse_args().days)
