"""FX carry: interest differentials from short-rate data, applied point-in-time."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantlab import store

PUBLICATION_LAG_MONTHS = 2   # a monthly OECD rate for month m is only safely known ~2 months later


def short_rates(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Daily frame of annual rates (decimal), as they were *known* on each date."""
    out = {}
    for p in sorted((store.DATA / "rates").glob("*.csv")):
        s = pd.read_csv(p, parse_dates=["date"]).set_index("date")["rate"] / 100
        s.index = s.index + pd.DateOffset(months=PUBLICATION_LAG_MONTHS)
        out[p.stem] = s
    rates = pd.DataFrame(out).sort_index()
    return rates.reindex(rates.index.union(index)).ffill().reindex(index)


def pair_carry(pairs: list[str], rates: pd.DataFrame) -> pd.DataFrame:
    """Annual carry of holding each pair long = base rate - quote rate."""
    return pd.DataFrame({p: rates[p[:3]] - rates[p[3:]] for p in pairs}, index=rates.index)


def total_return_index(px: pd.DataFrame, carry: pd.DataFrame) -> pd.DataFrame:
    """Price index that also accrues the daily interest differential (what a swap pays a long)."""
    days = px.index.to_series().diff().dt.days.fillna(0).to_numpy()[:, None]
    accrual = (1 + carry.fillna(0).to_numpy() * days / 365)
    return px.pct_change().fillna(0).add(1).mul(accrual).cumprod() * px.iloc[0]


def carry_strategy(carry: pd.DataFrame, target_vol: float = 0.08, cov_window: int = 63,
                   max_gross: float = 3.0, n_side: int = 3):
    """Long the n_side highest-carry pairs, short the n_side lowest, risk-scaled to target_vol."""
    def strat(hist: pd.DataFrame) -> pd.Series:
        c = carry.loc[:hist.index[-1]].iloc[-1].dropna()
        if len(c) < 2 * n_side or len(hist) < cov_window + 1:
            return pd.Series(dtype=float)
        ranked = c.sort_values()
        raw = pd.Series(0.0, index=hist.columns)
        raw[ranked.index[-n_side:]] = 1.0
        raw[ranked.index[:n_side]] = -1.0
        rets = hist.pct_change().iloc[-cov_window:].fillna(0.0)
        vol = float(np.sqrt(raw.to_numpy() @ (rets.cov().to_numpy() * 252) @ raw.to_numpy()))
        w = raw * (target_vol / vol) if vol > 0 else raw * 0
        if w.abs().sum() > max_gross:
            w *= max_gross / w.abs().sum()
        return w[w != 0]
    return strat
