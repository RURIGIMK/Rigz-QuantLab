from __future__ import annotations

import numpy as np
import pandas as pd

from quantlab.backtest import Result

TRADING_DAYS = 252


def summarize(res: Result, start: pd.Timestamp | None = None) -> dict:
    eq = res.equity if start is None else res.equity.loc[start:]
    eq = eq / eq.iloc[0]
    daily = eq.pct_change().dropna()
    years = len(daily) / TRADING_DAYS
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = daily.std() * np.sqrt(TRADING_DAYS)
    sharpe = (daily.mean() * TRADING_DAYS) / vol if vol > 0 else np.nan  # rf = 0
    dd = eq / eq.cummax() - 1
    trades = res.trades if start is None else res.trades[res.trades["date"] >= start]
    return {
        "name": res.name,
        "cagr": cagr,
        "vol": vol,
        "sharpe_rf0": sharpe,
        "max_drawdown": dd.min(),
        "worst_month": eq.resample("ME").last().pct_change().min(),
        "turnover_per_year": trades["turnover"].sum() / years if years > 0 else np.nan,
        "cost_drag_per_year": trades["cost"].sum() / years if years > 0 else np.nan,
        "years": years,
    }


def table(results: list[Result], start: pd.Timestamp | None = None) -> pd.DataFrame:
    df = pd.DataFrame([summarize(r, start) for r in results]).set_index("name")
    pct = ["cagr", "vol", "max_drawdown", "worst_month", "cost_drag_per_year"]
    out = df.copy()
    for c in pct:
        out[c] = (df[c] * 100).map("{:.2f}%".format)
    out["sharpe_rf0"] = df["sharpe_rf0"].map("{:.2f}".format)
    out["turnover_per_year"] = df["turnover_per_year"].map("{:.1f}x".format)
    out["years"] = df["years"].map("{:.1f}".format)
    return out
