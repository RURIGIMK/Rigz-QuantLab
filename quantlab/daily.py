"""The daily runner. Runs after each market-data update.

For each enabled account:
  1. Apply today's corporate actions to existing holdings (dividends, splits).
  2. If a month-end decision hasn't been acted on yet: compute target weights from
     data up to that month-end, plan trades under the risk rules, then
       - paper:  fill at today's close (same timing as the backtest), charge fees
       - manual: record orders and send a Telegram ticket for you to place
  3. Record end-of-day NAV, and trip the kill switch if drawdown exceeds the limit.

Idempotent: running it twice on the same data changes nothing.
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import pandas as pd
import yaml

from quantlab import notify, risk, store, strategies
from quantlab.ledger import ROOT as LEDGER_ROOT
from quantlab.ledger import Ledger

CONFIG = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def latest_decision_date(index: pd.DatetimeIndex) -> pd.Timestamp | None:
    """Most recent date that was the last trading day of its month (and is not today)."""
    idx = index[:-1]
    nxt = index[1:]
    month_end = idx[(idx.month != nxt.month)]
    return month_end[-1] if len(month_end) else None


def build_strategy(cfg: dict):
    s = cfg["strategy"]
    if s["name"] != "dual_momentum":
        raise ValueError(f"unknown strategy {s['name']}")
    return strategies.dual_momentum(top_n=s["top_n"], lookback=s["lookback"], cash=s["cash_symbol"])


def run_account(name: str, acct: dict, cfg: dict, signal_px: pd.DataFrame, trade_px: pd.DataFrame,
                actions: dict[str, pd.DataFrame], root: Path, messages: list[str]) -> None:
    led = Ledger(name, acct["initial_cash"], root)
    today = trade_px.index[-1]
    d = today.date().isoformat()
    close = trade_px.iloc[-1]

    # 1) corporate actions on positions held coming into today
    wh = cfg.get("tax", {}).get("us_dividend_withholding", 0.0)
    for sym, qty in led.positions().items():
        act = actions.get(sym)
        if act is None or today not in act.index:
            continue
        div, split = float(act.at[today, "dividends"]), float(act.at[today, "splits"])
        if split and split != 1.0:
            led.add_split(d, sym, qty, split)
        if div > 0:
            led.add_dividend(d, sym, led.positions()[sym], div, wh)

    # 2) rebalance if a month-end decision is pending
    decision = latest_decision_date(trade_px.index)
    if decision is not None and not led.rebalanced_for(decision.date().isoformat()):
        if led.halted():
            messages.append(f"⛔ <b>{name}</b>: kill switch active, skipping rebalance for {decision.date()}")
        else:
            rebalance(name, acct, cfg, led, signal_px.loc[:decision], close, decision, today, messages)

    # 3) mark to market + kill switch
    pos = led.positions()
    nav = led.cash() + sum(q * float(close[s]) for s, q in pos.items())
    led.add_nav(d, nav, led.cash(), pos)
    peak = max([n["nav"] for n in led.nav_history()] + [nav])
    dd = nav / peak - 1 if peak > 0 else 0.0
    if peak > 0 and dd <= -cfg["risk"]["kill_drawdown"] and not led.halted():
        led.add_event(d, "halt", f"drawdown {dd:.1%} from peak {peak:.2f}")
        messages.append(f"🛑 <b>{name}</b>: KILL SWITCH. NAV {nav:.2f} is {dd:.1%} below peak. "
                        "No new trades until you review and resume.")


def rebalance(name, acct, cfg, led: Ledger, hist: pd.DataFrame, close: pd.Series,
              decision: pd.Timestamp, today: pd.Timestamp, messages: list[str]) -> None:
    r, s = cfg["risk"], cfg["strategy"]
    dd_iso, today_iso = decision.date().isoformat(), today.date().isoformat()
    target = build_strategy(cfg)(hist)

    # a new decision supersedes any unfilled tickets from the last one
    for o in led.open_orders():
        led.add_event(today_iso, "cancel", str(o["id"]))

    plan = risk.plan_trades(target, led.positions(), led.cash(), close,
                            min_order=r["min_order"], max_weight=r["max_weight"],
                            cash_buffer=r["cash_buffer"], cash_symbol=s["cash_symbol"])
    led.add_event(today_iso, "rebalance", dd_iso)
    reason = "initial" if not led.fills() else "monthly"
    orders = [led.add_order(symbol=p.symbol, side=p.side, notional=p.notional,
                            signal_price=p.price, decision_date=dd_iso, exec_date=today_iso,
                            strategy=s["name"], reason=reason) for p in plan]

    target_txt = ", ".join(f"{k} {v:.0%}" for k, v in target.items()) or "all cash"
    if acct["mode"] == "paper":
        fee_rate = acct.get("fee_bps", 0) / 1e4
        held = led.positions()
        for o, p in zip(orders, plan):
            qty = held.get(p.symbol, 0.0) if p.full_exit else p.notional / p.price
            led.add_fill(order_id=o["id"], quantity=qty, price=p.price,
                         fees=qty * p.price * fee_rate, filled_at=today_iso, entered_by="paper")
        messages.append(f"📄 <b>{name}</b> rebalanced for {dd_iso}: target {html.escape(target_txt)}; "
                        f"{len(orders)} simulated fills.")
    else:
        if not orders:
            messages.append(f"✅ <b>{name}</b> ({dd_iso}): target {html.escape(target_txt)}. No trades needed.")
            return
        lines = [f"🧾 <b>Rebalance tickets: {name}</b>",
                 f"Decision {dd_iso}, target: {html.escape(target_txt)}", ""]
        for o in orders:
            lines.append(f"#{o['id']}  <b>{o['side']}</b> ${o['notional']:.2f} of <b>{o['symbol']}</b>"
                         f"  (ref ${o['signal_price']:.2f})")
        lines += ["", "Place SELLS first, then BUYS, as market orders.",
                  "Then record each fill: GitHub → Actions → record-fill."]
        messages.append("\n".join(lines))


def run(cfg: dict | None = None, *, signal_px: pd.DataFrame | None = None,
        trade_px: pd.DataFrame | None = None, actions: dict | None = None,
        root: Path = LEDGER_ROOT, send: bool = True) -> list[str]:
    cfg = cfg or load_config()
    if signal_px is None:
        signal_px = store.load_prices("us_etf", "adj_close")
        trade_px = store.load_prices("us_etf", "close").loc[signal_px.index]
        actions = {s: store.read_bars("us_etf", s)[store.ACTION_COLS] for s in signal_px.columns}
    messages: list[str] = []
    for name, acct in cfg["accounts"].items():
        if acct.get("enabled"):
            run_account(name, acct, cfg, signal_px, trade_px, actions or {}, root, messages)
    if send and messages:
        notify.send("\n\n".join(messages))
    return messages


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--no-send", action="store_true")
    a = p.parse_args()
    for m in run(send=not a.no_send):
        print(m, "\n")
