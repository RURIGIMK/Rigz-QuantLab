"""Download daily bars for every market in config/universe.yaml, validate, and store.

    python -m quantlab.ingest --to csv            # writes data/<market>/*.csv (used by GitHub Actions)
    python -m quantlab.ingest --to db             # loads US ETFs into Postgres
    python -m quantlab.ingest --to db --from-csv  # load the committed CSVs into Postgres
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
MARKETS = ("us_etf", "fx")
YAHOO_SUFFIX = {"us_etf": "", "fx": "=X"}


def clean_fx(df: pd.DataFrame) -> pd.DataFrame:
    """Yahoo FX bars sometimes have a close outside the day's high/low and no volume.
    FX trades 24h, so widen high/low to include open/close; adjusted close = close."""
    df = df.copy()
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    df["adj_close"] = df["close"]
    df["volume"] = 0
    return df[(df["close"] > 0) & (df.index.dayofweek < 5)]   # drop partial weekend bars


def fetch(symbol: str, start: str, market: str = "us_etf") -> pd.DataFrame:
    """Download with retries; on persistent failure fall back to the last committed data."""
    import time
    for attempt in range(3):
        try:
            df = _fetch(symbol, start, market)
            if not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            log.warning("%s: download attempt %d failed: %s", symbol, attempt + 1, e)
        time.sleep(5 * (attempt + 1))
    log.warning("%s: using last committed data", symbol)
    try:
        return store.read_bars(market, symbol)
    except FileNotFoundError:
        return pd.DataFrame()


def _fetch(symbol: str, start: str, market: str = "us_etf") -> pd.DataFrame:
    import yfinance as yf

    raw = yf.Ticker(symbol + YAHOO_SUFFIX[market]).history(start=start, auto_adjust=False, actions=True)
    if raw.empty:
        return raw
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low",
                             "Close": "close", "Adj Close": "adj_close",
                             "Volume": "volume", "Dividends": "dividends",
                             "Stock Splits": "splits"})
    for c in store.ACTION_COLS:
        if c not in df.columns:
            df[c] = 0.0
    df = df[store.COLS + store.ACTION_COLS]
    df = df[~df.index.duplicated(keep="last")]
    return clean_fx(df) if market == "fx" else df


def run(to: str, from_csv: bool) -> int:
    cfg = yaml.safe_load(CONFIG.read_text())
    failed = []
    for market in MARKETS:
        frames = ingest_market(cfg, market, from_csv, failed)
        if to == "csv":
            for sym, df in frames.items():
                store.write_bars(market, sym, df)
        elif market == "us_etf":
            load_db(cfg, frames)
    if to == "csv" and not from_csv:
        ingest_rates(cfg, failed)
    log.info("rejected: %s", failed or "none")
    return 1 if failed else 0


def ingest_market(cfg: dict, market: str, from_csv: bool, failed: list) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for item in cfg.get(market, []):
        sym = item["symbol"]
        df = store.read_bars(market, sym) if from_csv else fetch(sym, cfg["history_start"], market)
        report = validate_bars(sym, df)
        for w in report.warnings:
            log.warning("%s: %s", sym, w)
        if not report.ok:
            log.error("REJECT %s: %s", sym, report.errors)
            failed.append(f"{market}/{sym}")
            continue
        frames[sym] = df
        log.info("%s/%s: %d rows %s -> %s", market, sym, len(df), df.index[0].date(), df.index[-1].date())
    return frames


def ingest_rates(cfg: dict, failed: list) -> None:
    """Monthly short rates from FRED's public CSV endpoint -> data/rates/<CCY>.csv."""
    import io
    import urllib.request
    out = store.DATA / "rates"
    out.mkdir(parents=True, exist_ok=True)
    for ccy, series in cfg.get("rates", {}).items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                df = pd.read_csv(io.BytesIO(r.read()))
            df.columns = ["date", "rate"]
            df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
            df = df.dropna()
            if df.empty:
                raise ValueError("no rows")
            df.to_csv(out / f"{ccy}.csv", index=False)
            log.info("rates/%s (%s): %d months %s -> %s", ccy, series, len(df), df.date.iloc[0], df.date.iloc[-1])
        except Exception as e:  # noqa: BLE001 - one bad series shouldn't stop the rest
            log.error("REJECT rates/%s: %s", ccy, e)
            failed.append(f"rates/{ccy}")


def load_db(cfg: dict, frames: dict[str, pd.DataFrame]) -> None:
    from quantlab import db
    with db.connect() as conn:
        for item in cfg["us_etf"]:
            sym = item["symbol"]
            if sym in frames:
                iid = db.upsert_instrument(conn, sym, "US", "ETF", "USD", item.get("name"))
                db.upsert_bars(conn, iid, frames[sym], source="yfinance")
        conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--to", choices=["csv", "db"], default="csv")
    p.add_argument("--from-csv", action="store_true")
    a = p.parse_args()
    sys.exit(run(a.to, a.from_csv))
