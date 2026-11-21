"""File-based price store: data/<market>/<SYMBOL>.csv, one file per instrument.

The CSV snapshot in git is the research source of truth (versioned, diffable,
usable without a database). Postgres holds the same bars plus the live ledger.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLS = ["open", "high", "low", "close", "adj_close", "volume"]
ACTION_COLS = ["dividends", "splits"]   # per-share cash dividend on ex-date; split ratio (0 = none)


def path(market: str, symbol: str) -> Path:
    return DATA / market.lower() / f"{symbol}.csv"


def write_bars(market: str, symbol: str, df: pd.DataFrame) -> Path:
    p = path(market, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = COLS + [c for c in ACTION_COLS if c in df.columns]
    out = df[cols].copy()
    out.index.name = "date"
    out.round(6).to_csv(p, date_format="%Y-%m-%d")
    return p


def read_bars(market: str, symbol: str) -> pd.DataFrame:
    df = pd.read_csv(path(market, symbol), index_col="date", parse_dates=True)
    for c in ACTION_COLS:
        if c not in df.columns:
            df[c] = 0.0
    return df[COLS + ACTION_COLS]


def available(market: str) -> list[str]:
    d = DATA / market.lower()
    return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []


def load_prices(market: str = "us_etf", field: str = "adj_close",
                symbols: list[str] | None = None) -> pd.DataFrame:
    """Wide frame (dates x symbols), starting when every symbol has data."""
    symbols = symbols or available(market)
    if not symbols:
        raise FileNotFoundError(f"no data in {DATA / market}; run the data workflow")
    wide = pd.concat({s: read_bars(market, s)[field] for s in symbols}, axis=1, sort=True)
    return wide.dropna(how="any").sort_index()
