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

from quantlab import notify, store, strategies
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


def run_account(name: str, acct: dict, cfg: dict, messages: list[str], dry_run: bool = False,
                monitor: bool = False) -> None:
    cid, secret = tokens.env_credentials()
    access = tokens.valid_access_token(cid, secret)
    led = Ledger(name, acct.get("initial_cash", 0.0))

    market = acct.get("market", "us_etf")
    signal_px = store.load_prices(market, "adj_close", acct.get("universe"))
    signal_px = signal_px[signal_px.index.dayofweek < 5]
    usd_px = store.load_prices("us_etf", "close").iloc[-1].to_dict()
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
        fx_rate = usd_px.get("EURUSD", 1.0) if acct.get("currency") == "EUR" else 1.0
        broker_nav = balance * fx_rate
        ids = resolve_symbols(list(signal_px.columns), c.symbols(), acct.get("symbol_map", {}))
        specs = c.symbol_specs(ids)
        missing = sorted(set(signal_px.columns) - set(specs))
        positions = c.positions()
        id_to_sym = {s.symbol_id: n for n, s in specs.items()}
        unrealized = sum(planner.unrealized_usd(id_to_sym.get(p.symbol_id), p, usd_px) for p in positions
                         if id_to_sym.get(p.symbol_id))
        nav_usd = broker_nav + unrealized
        if acct.get("virtual_capital_usd"):
            # Trade the big demo as if it held only `virtual_capital_usd`: NAV = virtual capital
            # + everything the strategy has made or lost since the baseline was recorded.
            # The baseline is kept in the ACCOUNT currency: converting the whole demo balance to
            # USD each day would turn EUR/USD moves into fake profit or loss.
            base = next((e for e in reversed(led.events()) if e["kind"] == "virtual_baseline_ccy"), None)
            if base is None and not dry_run:
                led.add_event(today, "virtual_baseline_ccy", f"{balance:.2f}")
            baseline = float(base["detail"]) if base else balance
            nav_usd = acct["virtual_capital_usd"] + (balance - baseline) * fx_rate + unrealized
        holdings = {}
        for p in positions:
            n = id_to_sym.get(p.symbol_id, str(p.symbol_id))
            holdings[n] = holdings.get(n, 0.0) + (p.volume if p.side == BUY else -p.volume) / 100
        print(f"[autotrade] {name}: broker balance {balance:.2f} {acct.get('currency', 'USD')}; "
              f"strategy NAV ${nav_usd:,.2f} (unrealized ${unrealized:,.2f}); "
              f"resolved {sorted(specs)}; positions {holdings}")
        if not dry_run:
            led.add_nav(today, nav_usd, nav_usd, holdings)

        # kill switch / profit target: flatten and stop
        hist_navs = [n["nav"] for n in led.nav_history()] or [nav_usd]
        peak, start_nav = max(hist_navs + [nav_usd]), hist_navs[0]
        dd = nav_usd / peak - 1
        kill = acct.get("kill_drawdown", cfg["risk"]["kill_drawdown"])
        target_mult = acct.get("take_profit_multiple")
        hit_target = bool(target_mult) and nav_usd >= start_nav * target_mult
        floor = acct.get("stop_out_fraction")
        hit_floor = bool(floor) and nav_usd <= start_nav * floor
        if led.halted() or (kill is not None and dd <= -kill) or hit_target or hit_floor:
            if not led.halted() and not dry_run:
                led.add_event(today, "halt", f"target reached: NAV {nav_usd:.2f} >= {target_mult}x start"
                              if hit_target else f"stop-out: NAV {nav_usd:.2f}" if hit_floor
                              else f"drawdown {dd:.1%}")
            if dry_run:
                print(f"[autotrade] {name}: would halt (dd {dd:.1%}, target_hit {hit_target})")
                return
            flat = planner.plan({}, nav_usd, usd_px, positions, specs, max_gross=0, max_pos=0)
            for a in flat:
                c.close_position(a.position_id, a.volume)
                led._append("executions", {"date": today, "kind": "close", "symbol": a.symbol,
                                           "volume": a.volume, "reason": "kill_switch"})
            messages.append((f"🎯 <b>{name}</b>: TARGET HIT. NAV ${nav_usd:,.2f} ({nav_usd / start_nav:.2f}x start). "
                             if hit_target else
                             f"💀 <b>{name}</b>: stop-out. NAV ${nav_usd:,.2f} ({nav_usd / start_nav:.0%} of start). "
                             if hit_floor else f"🛑 <b>{name}</b>: kill switch, drawdown {dd:.1%}. ")
                            + f"Closed {len(flat)} positions; trading halted.")
            return

        decision = (signal_px.index[-1] if acct.get("rebalance") == "daily"
                    else latest_decision_date(signal_px.index))
        if monitor or decision is None or led.rebalanced_for(decision.date().isoformat()):
            return   # monitor mode (night run): NAV + kill switch only; trading happens in market hours
        strat = (getattr(strategies, acct["strategy"])() if acct.get("strategy") else build_strategy(cfg))
        target = strat(signal_px.loc[:decision])
        cash_sym = cfg["strategy"]["cash_symbol"]
        trade_target = {k: v for k, v in target.items() if k != cash_sym}   # cash proxy = stay flat
        if acct.get("sizing") == "min_lot":
            # direction-only target -> exactly the broker's minimum volume per position
            trade_target = {k: (1 if v > 0 else -1) * specs[k].min_volume / 100
                            * planner.base_usd_price(k, usd_px) * 1.001 / nav_usd
                            for k, v in trade_target.items() if k in specs and v}
        unmapped = sorted(set(trade_target) - set(specs))
        actions = planner.plan(trade_target, nav_usd, usd_px, positions, specs,
                               max_gross=acct.get("max_gross", 1.0), max_pos=acct.get("max_pos", 0.6))
        print(f"[autotrade] {name}: decision {decision.date()} target {dict(target)}; unmapped {unmapped}")
        for a in actions:
            print(f"[autotrade]   plan {a.kind} {SIDE[a.side]} {a.volume / 100:g} {a.symbol}"
                  f" (~${a.volume / 100 * usd_px.get(a.symbol, 0):,.0f})")
        if dry_run:
            return
        if unmapped:
            messages.append(f"⚠️ <b>{name}</b>: target includes {', '.join(unmapped)}, which the broker "
                            "doesn't offer. Not trading; fix the account universe or symbol_map.")
            return
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
             f"Target: {html.escape(tgt)}",
             f"NAV ${nav_usd:,.2f} (KSh {nav_usd * usd_px.get('USDKES', 129):,.0f}); {len(done)} orders executed."]
    lines += [f"• {a.kind} {SIDE[a.side]} {a.volume / 100:g} {a.symbol}" for a in done]
    if failed:
        lines.append("⚠️ Failed: " + html.escape("; ".join(failed)) + " (will retry next run)")
    else:
        led.add_event(today, "rebalance", decision.date().isoformat())   # only mark done when complete
    if missing:
        lines.append("Not tradable at broker: " + ", ".join(missing))
    messages.append("\n".join(lines))


def main(dry_run: bool = False, monitor: bool = False) -> int:
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
            run_account(name, acct, cfg, messages, dry_run, monitor)
        except Exception as e:  # noqa: BLE001 - one account failing must be reported, not hidden
            status = 1
            messages.append(f"❌ <b>{name}</b> autotrade error: {html.escape(str(e))}")
            print(f"[autotrade] {name}: {e}", file=sys.stderr)
    if messages and not dry_run:
        notify.send("\n\n".join(messages))
    for m in messages:
        print(m)
    return status


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv, monitor="--monitor" in sys.argv))
