"""Turn target weights into concrete orders, under hard risk rules.

Rules (in order):
  1. Risky weights capped at max_weight; cash proxy is exempt. Excess stays as cash.
  2. A cash buffer is held back so a price move before you fill can't overdraw you.
  3. Full exits always go through, even below the minimum order size.
  4. Any other trade smaller than min_order is skipped (it would be rejected or
     cost more than it's worth).
  5. Sells come first; buys are scaled down to the cash actually available.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class PlannedOrder:
    symbol: str
    side: str          # BUY | SELL
    notional: float    # USD
    price: float       # reference price the plan used
    full_exit: bool = False


def plan_trades(target: pd.Series, holdings: dict[str, float], cash: float,
                prices: pd.Series, *, min_order: float, max_weight: float,
                cash_buffer: float, cash_symbol: str = "BIL") -> list[PlannedOrder]:
    values = {s: q * float(prices[s]) for s, q in holdings.items() if q}
    nav = cash + sum(values.values())
    if nav <= 0:
        return []

    tgt = target[target > 0].astype(float).copy()
    risky = tgt.index != cash_symbol
    tgt[risky] = tgt[risky].clip(upper=max_weight)
    if tgt.sum() > 1:
        raise ValueError(f"target weights sum to {tgt.sum():.4f} > 1")
    desired = (tgt * nav * (1 - cash_buffer)).to_dict()

    sells, buys = [], []
    for sym in sorted(set(desired) | set(values)):
        delta = desired.get(sym, 0.0) - values.get(sym, 0.0)
        px = float(prices[sym])
        if delta < 0:
            full = desired.get(sym, 0.0) == 0.0
            if full or -delta >= min_order:
                sells.append(PlannedOrder(sym, "SELL", round(min(-delta, values[sym]), 2), px, full))
        elif delta >= min_order:
            buys.append(PlannedOrder(sym, "BUY", delta, px))

    available = cash + sum(o.notional for o in sells)
    need = sum(o.notional for o in buys)
    scale = min(1.0, available / need) if need > 0 else 1.0
    final_buys = []
    for o in buys:
        o.notional = round(o.notional * scale - 0.005, 2)   # round down, never overdraw
        if o.notional >= min_order:
            final_buys.append(o)
    return sells + final_buys
