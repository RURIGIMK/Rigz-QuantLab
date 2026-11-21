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


def fx_trend(lookbacks=(21, 63, 126, 252), target_vol: float = 0.10, cov_window: int = 63,
             max_gross: float = 3.0, max_pos: float = 1.0):
    """Time-series momentum across FX pairs, the most robustly documented FX premium.

    Each pair's signal is the average sign of its return over several horizons
    (so it's in [-1, 1]). Positions are scaled so the *portfolio's* expected
    volatility, from the recent covariance matrix, equals target_vol. That uses
    the correlation between pairs (many share the USD leg) instead of pretending
    they're independent. Per-pair and gross exposure are hard-capped.
    """
    import numpy as np

    longest = max(lookbacks)

    def strat(hist: pd.DataFrame) -> pd.Series:
        if len(hist) <= longest + 1:
            return pd.Series(dtype=float)
        last = hist.iloc[-1]
        sig = sum(np.sign(last / hist.iloc[-1 - lb] - 1) for lb in lookbacks) / len(lookbacks)
        sig = sig.fillna(0.0)
        if (sig == 0).all():
            return pd.Series(dtype=float)
        rets = hist.pct_change().iloc[-cov_window:].dropna(how="all").fillna(0.0)
        cov = rets.cov().to_numpy() * 252
        raw = sig.to_numpy()
        port_vol = float(np.sqrt(raw @ cov @ raw))
        w = raw * (target_vol / port_vol) if port_vol > 0 else raw * 0
        w = np.clip(w, -max_pos, max_pos)
        gross = np.abs(w).sum()
        if gross > max_gross:
            w *= max_gross / gross
        out = pd.Series(w, index=hist.columns)
        return out[out.abs() > 1e-6]

    strat.__name__ = f"fx_trend_vol{int(target_vol * 100)}"
    return strat
