"""Turn target weights into concrete broker actions for a margin (CFD) account.

Pure function, no network: given NAV, prices, current broker positions and
symbol specs, decide which positions to close (fully or partly) and which
market orders to open so that each symbol's net exposure matches its target.

Hard limits enforced here, independent of the strategy:
  * gross notional <= max_gross x NAV
  * per-symbol notional <= max_pos x NAV
  * volumes rounded DOWN to the broker's step; anything below min volume skipped
"""
from __future__ import annotations

from dataclasses import dataclass

from quantlab.brokers.ctrader import BUY, SELL, Position, SymbolSpec


@dataclass
class Action:
    kind: str              # "close" | "open"
    symbol: str
    side: int              # BUY | SELL (for close: side of the position being closed)
    volume: int            # 0.01 units
    position_id: int | None = None


def base_usd_price(symbol: str, usd_px: dict[str, float]) -> float:
    """USD value of one unit of the instrument's base (FX pair base currency, or one CFD share)."""
    if len(symbol) == 6 and symbol.isalpha():          # FX pair, e.g. EURJPY
        base = symbol[:3]
        if base == "USD":
            return 1.0
        if base + "USD" in usd_px:
            return usd_px[base + "USD"]
        if "USD" + base in usd_px:
            return 1.0 / usd_px["USD" + base]
        raise KeyError(f"no USD conversion for {base}")
    return usd_px[symbol]                              # share / ETF CFD priced in USD


def plan(targets: dict[str, float], nav_usd: float, usd_px: dict[str, float],
         positions: list[Position], specs: dict[str, SymbolSpec], *,
         max_gross: float, max_pos: float) -> list[Action]:
    id_to_sym = {s.symbol_id: name for name, s in specs.items()}
    if nav_usd <= 0:
        raise ValueError("NAV must be positive")

    # hard caps, applied to the strategy's targets
    t = {s: max(-max_pos, min(max_pos, w)) for s, w in targets.items() if s in specs}
    gross = sum(abs(w) for w in t.values())
    if gross > max_gross:
        t = {s: w * max_gross / gross for s, w in t.items()}

    # current signed volume per symbol
    held: dict[str, list[Position]] = {}
    for p in positions:
        name = id_to_sym.get(p.symbol_id)
        if name is None:
            continue   # positions in symbols we don't manage are left alone
        held.setdefault(name, []).append(p)

    actions: list[Action] = []
    for sym in sorted(set(t) | set(held)):
        spec = specs[sym]
        units = t.get(sym, 0.0) * nav_usd / base_usd_price(sym, usd_px)
        target_vol = int(abs(units) * 100) // spec.step_volume * spec.step_volume
        target_vol = min(target_vol, spec.max_volume)
        if target_vol < spec.min_volume:
            target_vol = 0
        target_side = BUY if units > 0 else SELL
        pos = held.get(sym, [])
        net = sum(p.volume if p.side == BUY else -p.volume for p in pos)
        want = target_vol if target_side == BUY else -target_vol
        if want == net:
            continue

        # positions on the wrong side, or all of them when flattening: close fully
        same_side = [p for p in pos if (p.side == BUY) == (want > 0)] if want != 0 else []
        for p in pos:
            if p not in same_side:
                actions.append(Action("close", sym, p.side, p.volume, p.position_id))
        current = sum(p.volume for p in same_side)
        need = abs(want) - current
        if need > 0:
            vol = need // spec.step_volume * spec.step_volume
            if vol >= spec.min_volume:
                actions.append(Action("open", sym, target_side, vol))
        elif need < 0:                               # reduce: partial closes, oldest first
            to_cut = -need // spec.step_volume * spec.step_volume
            for p in sorted(same_side, key=lambda x: x.position_id):
                if to_cut <= 0:
                    break
                cut = min(p.volume, to_cut)
                if cut >= spec.min_volume or cut == p.volume:
                    actions.append(Action("close", sym, p.side, cut, p.position_id))
                    to_cut -= cut
    # closes before opens frees margin first
    return [a for a in actions if a.kind == "close"] + [a for a in actions if a.kind == "open"]
