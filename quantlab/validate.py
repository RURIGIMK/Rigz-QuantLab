"""Data-quality checks. Bad data is the #1 source of fake backtest profits,
so ingestion refuses to load anything that fails a hard check."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

MAX_DAILY_MOVE = 0.35      # >35% one-day move on an ETF is almost certainly bad data
MAX_GAP_BDAYS = 5          # missing more than 5 business days in a row -> suspicious


@dataclass
class Report:
    symbol: str
    errors: list[str] = field(default_factory=list)    # block the load
    warnings: list[str] = field(default_factory=list)  # load, but flag

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_bars(symbol: str, df: pd.DataFrame) -> Report:
    """df: index = DatetimeIndex, columns include open/high/low/close/adj_close/volume."""
    r = Report(symbol)
    if df.empty:
        r.errors.append("no rows")
        return r

    if not df.index.is_monotonic_increasing:
        r.errors.append("dates not sorted")
    if df.index.duplicated().any():
        r.errors.append(f"{int(df.index.duplicated().sum())} duplicate dates")

    if (df["close"] <= 0).any():
        r.errors.append("non-positive close")
    bad_hl = (df["high"] < df["low"]) | (df["close"] > df["high"] * 1.0001) | (df["close"] < df["low"] * 0.9999)
    if bad_hl.any():
        r.errors.append(f"{int(bad_hl.sum())} rows with close outside high/low")

    rets = df["adj_close"].pct_change().abs()
    spikes = rets[rets > MAX_DAILY_MOVE]
    if not spikes.empty:
        r.errors.append(f"implausible moves on {[d.date().isoformat() for d in spikes.index[:5]]}")

    gaps = pd.Series(df.index).diff().dt.days.fillna(0)
    if (gaps > MAX_GAP_BDAYS * 7 / 5 + 2).any():
        r.warnings.append(f"max calendar gap {int(gaps.max())} days")

    if df["adj_close"].isna().any():
        r.warnings.append(f"{int(df['adj_close'].isna().sum())} missing adj_close")
    if (df["volume"].fillna(0) == 0).mean() > 0.05:
        r.warnings.append("more than 5% zero-volume days")
    return r
