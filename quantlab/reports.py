"""Human-readable trade and account messages (Telegram HTML; WhatsApp converts them)."""
from __future__ import annotations

import datetime as dt
import html


def ksh(usd: float, rate: float) -> str:
    return f"KSh {usd * rate:,.0f}"


def signed_ksh(usd: float, rate: float) -> str:
    return f"{'+' if usd >= 0 else '−'}KSh {abs(usd) * rate:,.0f}"


def _money(deal: dict, field: str, fx_to_usd: float, digits_key: str = "moneyDigits") -> float:
    detail = deal.get("closePositionDetail", {})
    digits = detail.get(digits_key, deal.get(digits_key, 2))
    return detail.get(field, 0) / 10 ** digits * fx_to_usd


def trade_opened(account: str, symbol: str, side: str, units: float, price: float, notional_usd: float,
                 nav_usd: float, rate: float, why: dict | None, target_mult: float | None,
                 stop_frac: float | None, start_nav: float) -> str:
    lev = notional_usd / nav_usd if nav_usd else 0
    pip_value_usd = notional_usd * 0.0001          # P&L per 0.01% move
    arrow = "🔴 SHORT" if side == "SELL" else "🟢 LONG"
    lines = [f"{arrow} <b>{html.escape(symbol)}</b> opened · {account}",
             f"Size: {units:,.0f} units ({units / 100_000:.2f} lot) @ <b>{price}</b>",
             f"Exposure: ${notional_usd:,.0f} = <b>{lev:.0f}×</b> the account ({ksh(nav_usd, rate)})",
             f"Each 0.01% move ≈ {ksh(pip_value_usd, rate)}"]
    if why:
        hz = ", ".join(f"{k} {v:+.1%}" for k, v in why["horizons"].items())
        lines += ["", f"<b>Why:</b> strongest trend of all pairs. {html.escape(symbol)} is "
                      f"{why['agreement']} across horizons ({hz}); 3-month risk-adjusted move "
                      f"{why['risk_adj_3m']:+.1f}σ.",
                  "Runners-up: " + ", ".join(f"{s} ({a})" for s, a in why["runners_up"])]
    if target_mult or stop_frac:
        lines += ["", "Stops: " + " · ".join(
            x for x in (f"🎯 target {ksh(start_nav * target_mult, rate)}" if target_mult else "",
                        f"💀 stop-out {ksh(start_nav * stop_frac, rate)}" if stop_frac else "") if x)]
    return "\n".join(lines)


def trade_closed(account: str, symbol: str, side_closed: str, units: float, exit_price: float,
                 deal: dict, fx_to_usd: float, rate: float, opened_ms: int | None, reason: str) -> str:
    gross = _money(deal, "grossProfit", fx_to_usd)
    swap = _money(deal, "swap", fx_to_usd)
    comm = _money(deal, "commission", fx_to_usd)
    net = gross + swap + comm
    entry = deal.get("closePositionDetail", {}).get("entryPrice")
    held = ""
    if opened_ms:
        days = (dt.datetime.now(dt.timezone.utc).timestamp() * 1000 - opened_ms) / 86_400_000
        held = f" after {days:.1f} days"
    icon = "✅" if net >= 0 else "❌"
    direction = "SHORT" if side_closed == "SELL" else "LONG"
    return "\n".join([
        f"{icon} {direction} <b>{html.escape(symbol)}</b> closed{held} · {account}",
        f"{units:,.0f} units: entry {entry if entry is not None else '?'} → exit <b>{exit_price}</b>",
        f"Result: <b>{signed_ksh(net, rate)}</b> (price {signed_ksh(gross, rate)}, "
        f"swap {signed_ksh(swap, rate)}, fees {signed_ksh(comm, rate)})",
        f"Why closed: {html.escape(reason)}"])


def daily_summary(account: str, nav_usd: float, prev_nav: float | None, start_nav: float, rate: float,
                  open_lines: list[str], target_mult: float | None, stop_frac: float | None) -> str:
    day = f" ({signed_ksh(nav_usd - prev_nav, rate)} today)" if prev_nav else ""
    total = (nav_usd / start_nav - 1) if start_nav else 0
    lines = [f"📊 <b>{account}</b> daily: <b>{ksh(nav_usd, rate)}</b>{day}",
             f"Since start: {total:+.1%} ({ksh(start_nav, rate)} → {ksh(nav_usd, rate)})"]
    if target_mult and stop_frac:
        lo, hi = start_nav * stop_frac, start_nav * target_mult
        prog = (nav_usd - lo) / (hi - lo)
        lines.append(f"Between stop-out and target: {prog:.0%} of the way to 🎯")
    lines += open_lines or ["No open positions."]
    return "\n".join(lines)
