"""Strategies: functions from price history (up to decision date) to target weights."""
from __future__ import annotations

import pandas as pd


def buy_and_hold(symbol: str):
    def strat(hist: pd.DataFrame) -> pd.Series:
        return pd.Series({symbol: 1.0})
    strat.__name__ = f"buy_hold_{symbol}"
    return strat


def dual_momentum(top_n: int = 2, lookback: int = 252, cash: str = "BIL"):
    """Rank risky assets by trailing return. Hold the top_n equally, but any
    pick whose return doesn't beat the cash asset is replaced by cash.
    Before `lookback` days of history exist, stay fully in cash."""
    def strat(hist: pd.DataFrame) -> pd.Series:
        if len(hist) <= lookback:
            return pd.Series({cash: 1.0}) if cash in hist else pd.Series(dtype=float)
        window = hist.iloc[[-lookback - 1, -1]]
        mom = (window.iloc[1] / window.iloc[0] - 1).dropna()
        cash_ret = mom.get(cash, 0.0)
        risky = mom.drop(labels=[cash], errors="ignore")
        picks = risky.nlargest(top_n)
        w = pd.Series(0.0, index=hist.columns)
        slot = 1.0 / top_n
        for sym, m in picks.items():
            if m > cash_ret:
                w[sym] += slot
            elif cash in w.index:
                w[cash] += slot
        return w[w > 0]
    strat.__name__ = f"dual_momentum_top{top_n}_lb{lookback}"
    return strat


def equal_weight(exclude: tuple[str, ...] = ("BIL",)):
    def strat(hist: pd.DataFrame) -> pd.Series:
        avail = [c for c in hist.columns if c not in exclude and pd.notna(hist[c].iloc[-1])]
        return pd.Series(1.0 / len(avail), index=avail)
    strat.__name__ = "equal_weight"
    return strat
