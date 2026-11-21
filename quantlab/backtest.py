"""Daily-resolution, monthly-rebalance portfolio backtester.

Honesty rules baked in:
  * A strategy only ever sees prices up to and including the decision date.
  * Trades execute `lag` trading days after the decision (default 1: decide on
    the month-end close, trade on the next close) - no trading at a price you
    used to make the decision.
  * Every rebalance pays cost_bps on traded notional (|target - current| summed).
  * Weights drift with prices between rebalances; unallocated weight is cash at 0%.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

Strategy = Callable[[pd.DataFrame], pd.Series]


@dataclass
class Result:
    equity: pd.Series          # portfolio value, starts at 1.0
    weights: pd.DataFrame      # weights held at each close (after any trade)
    trades: pd.DataFrame       # date, turnover, cost
    name: str = "strategy"


def month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).max().values)


def run(prices: pd.DataFrame, strategy: Strategy, cost_bps: float = 10.0,
        lag: int = 1, name: str = "strategy") -> Result:
    prices = prices.sort_index()
    rets = prices.pct_change().fillna(0.0).to_numpy()
    dates = prices.index
    cols = list(prices.columns)
    decision_days = set(month_end_dates(dates))

    w = np.zeros(len(cols))
    equity = 1.0
    pending: list[tuple[int, np.ndarray]] = []   # (execute_at_index, target)
    eq_out = np.empty(len(dates))
    w_out = np.empty((len(dates), len(cols)))
    trades = []

    for i, d in enumerate(dates):
        # 1) mark to market with today's returns, let weights drift
        r = float(w @ rets[i])
        equity *= 1.0 + r
        if 1.0 + r != 0:
            w = w * (1.0 + rets[i]) / (1.0 + r)

        # 2) decide (using data up to and including today)
        if d in decision_days:
            target = strategy(prices.iloc[: i + 1])
            tgt = target.reindex(cols).fillna(0.0).to_numpy(dtype=float)
            if tgt.sum() > 1.0 + 1e-9 or (tgt < -1e-12).any():
                raise ValueError(f"{d.date()}: weights must be long-only and sum<=1, got {target.to_dict()}")
            pending.append((i + lag, tgt))

        # 3) execute any trade scheduled for today at today's close
        while pending and pending[0][0] <= i:
            _, tgt = pending.pop(0)
            turnover = float(np.abs(tgt - w).sum())
            cost = turnover * cost_bps / 1e4
            equity *= 1.0 - cost
            w = tgt.copy()
            trades.append({"date": d, "turnover": turnover, "cost": cost})

        eq_out[i] = equity
        w_out[i] = w

    return Result(
        equity=pd.Series(eq_out, index=dates, name=name),
        weights=pd.DataFrame(w_out, index=dates, columns=cols),
        trades=pd.DataFrame(trades, columns=["date", "turnover", "cost"]),
        name=name,
    )
