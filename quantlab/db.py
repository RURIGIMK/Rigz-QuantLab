from __future__ import annotations

import os
from contextlib import contextmanager

import pandas as pd
import psycopg


def dsn() -> str:
    return os.environ.get(
        "QUANTLAB_DSN", "postgresql://quant:quant@localhost:5432/quantlab"
    )


@contextmanager
def connect():
    with psycopg.connect(dsn()) as conn:
        yield conn


def upsert_instrument(conn, symbol: str, market: str, asset_class: str,
                      currency: str, name: str | None) -> int:
    row = conn.execute(
        """INSERT INTO instruments (symbol, market, asset_class, currency, name)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (symbol, market) DO UPDATE SET name = EXCLUDED.name
           RETURNING id""",
        (symbol, market, asset_class, currency, name),
    ).fetchone()
    return row[0]


def upsert_bars(conn, instrument_id: int, df: pd.DataFrame, source: str) -> int:
    rows = [
        (instrument_id, idx.date(), r.open, r.high, r.low, r.close,
         r.adj_close, None if pd.isna(r.volume) else int(r.volume), source)
        for idx, r in df.iterrows()
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO daily_bars
                 (instrument_id, trade_date, open, high, low, close, adj_close, volume, source)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (instrument_id, trade_date) DO UPDATE SET
                 open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                 close=EXCLUDED.close, adj_close=EXCLUDED.adj_close,
                 volume=EXCLUDED.volume, source=EXCLUDED.source, ingested_at=now()""",
            rows,
        )
    return len(rows)
