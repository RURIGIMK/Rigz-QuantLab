"""Record a real fill for a manual (live) order, then confirm on Telegram.

    python -m quantlab.record_fill --account live --order 3 --quantity 0.0123 --price 512.30 --fees 0
"""
from __future__ import annotations

import argparse
import datetime as dt

from quantlab import notify
from quantlab.daily import load_config
from quantlab.ledger import Ledger


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--account", default="live")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--quantity", type=float, required=True, help="shares received/sold (fractional ok)")
    p.add_argument("--price", type=float, required=True, help="average fill price per share, USD")
    p.add_argument("--fees", type=float, default=0.0)
    p.add_argument("--date", default=dt.date.today().isoformat())
    a = p.parse_args()

    cfg = load_config()["accounts"][a.account]
    led = Ledger(a.account, cfg["initial_cash"])
    order = next(o for o in led.orders() if o["id"] == a.order)
    led.add_fill(order_id=a.order, quantity=a.quantity, price=a.price, fees=a.fees,
                 filled_at=a.date, entered_by="manual")
    sign = 1 if order["side"] == "BUY" else -1
    slip_bps = sign * (a.price / order["signal_price"] - 1) * 1e4
    msg = (f"✅ Fill recorded: #{a.order} {order['side']} {a.quantity:g} {order['symbol']} @ ${a.price:.2f}\n"
           f"Slippage vs ticket: {slip_bps:+.0f} bps. Cash now ${led.cash():.2f}. "
           f"Open orders: {len(led.open_orders())}")
    print(msg)
    notify.send(msg)


if __name__ == "__main__":
    main()
