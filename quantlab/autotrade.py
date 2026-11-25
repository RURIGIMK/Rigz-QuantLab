"""Fully autonomous execution for broker-connected (API) accounts.

Runs inside the daily workflow after market data updates. For each enabled
account with mode "api":
  1. Connect to cTrader, authorise the configured account (demo or live).
  2. Record NAV (account balance) and broker positions; check the kill switch.
     If tripped: flatten every managed position and stop trading.
  3. If a month-end decision hasn't been executed: compute target weights,
     plan orders under hard leverage limits, execute, and record every fill.
  4. Report on Telegram.

Exits quietly (status 0) when the broker isn't configured yet, so the rest of
the daily job keeps working.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import sys

import pandas as pd

from quantlab import notify, store
from quantlab.brokers import planner, tokens
from quantlab.brokers.ctrader import BUY, SELL, CTraderClient
from quantlab.daily import build_strategy, latest_decision_date, load_config
from quantlab.ledger import Ledger

SIDE = {BUY: "BUY", SELL: "SELL"}


def resolve_symbols(wanted: list[str], available: dict[str, int], overrides: dict[str, str]) -> dict[str, int]:
    """Map our tickers (SPY, EURUSD) to the broker's symbol names (e.g. 'SPY.US')."""
    out = {}
    for sym in wanted:
        cands = [overrides[sym]] if sym in overrides else [sym, f"{sym}.US", f"{sym}.us", f"{sym}US"]
        hit = next((c for c in cands if c in available), None)
        if hit:
            out[sym] = available[hit]
    return out


def run_account(name: str, acct: dict, cfg: dict, messages: list[str]) -> None:
    cid, secret = tokens.env_credentials()
    access = tokens.valid_access_token(cid, secret)
    led = Ledger(name, acct.get("initial_cash", 0.0))

    signal_px = store.load_prices(acct.get("market", "us_etf"), "adj_close")
    close = store.load_prices(acct.get("market", "us_etf"), "close").iloc[-1]
    usd_px = {**close.to_dict()}
    if store.available("fx"):
        usd_px.update(store.load_prices("fx", "close").iloc[-1].to_dict())
    today = signal_px.index[-1].date().isoformat()

    with CTraderClient(acct.get("env", "demo"), cid, secret, access) as c:
        accounts = c.accounts()
        match = [a for a in accounts if str(a.get("traderLogin")) == str(acct["account_login"])]
        if not match:
            raise RuntimeError(f"account {acct['account_login']} not available to this token")
        if bool(match[0].get("isLive")) != (acct.get("env") == "live"):
            raise RuntimeError("refusing to trade: account live/demo flag does not match config env")
        c.authorize(int(match[0]["ctidTraderAccountId"]))

        trader = c.trader()
        balance = trader["balance_money"]
        nav_usd = balance * (usd_px.get("EURUSD", 1.0) if acct.get("currency") == "EUR" else 1.0)
        ids = resolve_symbols(list(signal_px.columns), c.symbols(), acct.get("symbol_map", {}))
        specs = c.symbol_specs(ids)
        missing = sorted(set(signal_px.columns) - set(specs))
        positions = c.positions()
        id_to_sym = {s.symbol_id: n for n, s in specs.items()}
        holdings = {}
        for p in positions:
            n = id_to_sym.get(p.symbol_id, str(p.symbol_id))
            holdings[n] = holdings.get(n, 0.0) + (p.volume if p.side == BUY else -p.volume) / 100
        led.add_nav(today, nav_usd, nav_usd, holdings)

        # kill switch: flatten and stop
        peak = max(n["nav"] for n in led.nav_history())
        dd = nav_usd / peak - 1
        if led.halted() or dd <= -cfg["risk"]["kill_drawdown"]:
            if not led.halted():
                led.add_event(today, "halt", f"drawdown {dd:.1%}")
            flat = planner.plan({}, nav_usd, usd_px, positions, specs, max_gross=0, max_pos=0)
            for a in flat:
                c.close_position(a.position_id, a.volume)
                led._append("executions", {"date": today, "kind": "close", "symbol": a.symbol,
                                           "volume": a.volume, "reason": "kill_switch"})
            messages.append(f"🛑 <b>{name}</b>: kill switch, drawdown {dd:.1%}. "
                            f"Closed {len(flat)} positions; trading halted.")
            return

        decision = latest_decision_date(signal_px.index)
        if decision is None or led.rebalanced_for(decision.date().isoformat()):
            return
        target = build_strategy(cfg)(signal_px.loc[:decision])
        actions = planner.plan(target.to_dict(), nav_usd, usd_px, positions, specs,
                               max_gross=acct.get("max_gross", 1.0), max_pos=acct.get("max_pos", 0.6))
        led.add_event(today, "rebalance", decision.date().isoformat())
        done, failed = [], []
        for a in actions:
            try:
                ev = (c.close_position(a.position_id, a.volume) if a.kind == "close"
                      else c.market_order(specs[a.symbol].symbol_id, a.side, a.volume,
                                          f"rigz {decision.date()}"))
                deal = ev.get("deal", {})
                led._append("executions", {
                    "date": today, "decision_date": decision.date().isoformat(), "kind": a.kind,
                    "symbol": a.symbol, "side": SIDE[a.side], "volume": a.volume,
                    "price": deal.get("executionPrice"), "deal_id": deal.get("dealId"),
                    "position_id": deal.get("positionId", a.position_id)})
                done.append(a)
            except Exception as e:  # noqa: BLE001 - record and continue with the rest
                led._append("executions", {"date": today, "kind": a.kind, "symbol": a.symbol,
                                           "volume": a.volume, "error": str(e)})
                failed.append(f"{a.symbol}: {e}")

    tgt = ", ".join(f"{k} {v:.0%}" for k, v in target.items()) or "flat"
    lines = [f"🤖 <b>{name}</b> rebalanced for {decision.date()} ({acct.get('env')})",
             f"Target: {html.escape(tgt)}", f"Balance ${nav_usd:,.2f}; {len(done)} orders executed."]
    lines += [f"• {a.kind} {SIDE[a.side]} {a.volume / 100:g} {a.symbol}" for a in done]
    if failed:
        lines.append("⚠️ Failed: " + html.escape("; ".join(failed)))
    if missing:
        lines.append("Not tradable at broker: " + ", ".join(missing))
    messages.append("\n".join(lines))


def main() -> int:
    cfg = load_config()
    api_accounts = {n: a for n, a in cfg["accounts"].items() if a.get("enabled") and a.get("mode") == "api"}
    if not api_accounts:
        print("[autotrade] no enabled API accounts")
        return 0
    try:
        _, secret = tokens.env_credentials()
    except RuntimeError as e:
        print(f"[autotrade] skipped: {e}")
        return 0
    if tokens.load(secret) is None and not os.environ.get("CTRADER_REFRESH_TOKEN"):
        # credentials exist but the one-time ctrader-connect step hasn't run yet:
        # that's "not connected", not a failure; stay quiet instead of alerting nightly
        print("[autotrade] skipped: broker not connected yet (run ctrader-connect)")
        return 0
    messages: list[str] = []
    status = 0
    for name, acct in api_accounts.items():
        try:
            run_account(name, acct, cfg, messages)
        except Exception as e:  # noqa: BLE001 - one account failing must be reported, not hidden
            status = 1
            messages.append(f"❌ <b>{name}</b> autotrade error: {html.escape(str(e))}")
            print(f"[autotrade] {name}: {e}", file=sys.stderr)
    if messages:
        notify.send("\n\n".join(messages))
    for m in messages:
        print(m)
    return status


if __name__ == "__main__":
    sys.exit(main())
