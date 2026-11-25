"""Append-only ledger stored as JSON lines in git: ledger/<account>/{orders,fills,nav}.jsonl.

Nothing is ever edited or deleted. Positions and cash are always recomputed
from fills, so the state can't drift from the history.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ledger"


class Ledger:
    def __init__(self, account: str, initial_cash: float, root: Path = ROOT):
        self.account = account
        self.initial_cash = float(initial_cash)
        self.dir = root / account
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---- storage -------------------------------------------------------
    def _read(self, kind: str) -> list[dict]:
        p = self.dir / f"{kind}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def _append(self, kind: str, rec: dict) -> dict:
        with (self.dir / f"{kind}.jsonl").open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def orders(self) -> list[dict]:
        return self._read("orders")

    def fills(self) -> list[dict]:
        return self._read("fills")

    def nav_history(self) -> list[dict]:
        return self._read("nav")

    def events(self) -> list[dict]:
        return self._read("events")

    # ---- writes --------------------------------------------------------
    def add_order(self, *, symbol: str, side: str, notional: float, signal_price: float,
                  decision_date: str, exec_date: str, strategy: str, reason: str) -> dict:
        oid = max((o["id"] for o in self.orders()), default=0) + 1
        return self._append("orders", {
            "id": oid, "symbol": symbol, "side": side, "notional": round(notional, 2),
            "signal_price": round(signal_price, 6), "decision_date": decision_date,
            "exec_date": exec_date, "strategy": strategy, "reason": reason,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        })

    def add_fill(self, *, order_id: int, quantity: float, price: float, fees: float,
                 filled_at: str, entered_by: str) -> dict:
        order = next((o for o in self.orders() if o["id"] == order_id), None)
        if order is None:
            raise ValueError(f"order {order_id} not found in {self.account}")
        if any(f["order_id"] == order_id for f in self.fills()):
            raise ValueError(f"order {order_id} already has a fill")
        if quantity <= 0 or price <= 0 or fees < 0:
            raise ValueError("quantity and price must be positive, fees non-negative")
        if order["side"] == "SELL" and quantity > self.positions().get(order["symbol"], 0) + 1e-9:
            raise ValueError(f"cannot sell {quantity} {order['symbol']}: only "
                             f"{self.positions().get(order['symbol'], 0):.8f} held")
        return self._append("fills", {
            "order_id": order_id, "symbol": order["symbol"], "side": order["side"],
            "quantity": round(quantity, 8), "price": round(price, 6), "fees": round(fees, 6),
            "filled_at": filled_at, "entered_by": entered_by,
        })

    def add_nav(self, date: str, nav: float, cash: float, holdings: dict) -> None:
        if any(n["date"] == date for n in self.nav_history()):
            return
        self._append("nav", {"date": date, "nav": round(nav, 6), "cash": round(cash, 6),
                             "holdings": {k: round(v, 8) for k, v in holdings.items()}})

    def add_dividend(self, date: str, symbol: str, quantity: float, per_share: float,
                     withholding: float) -> None:
        if any(c["date"] == date and c["symbol"] == symbol for c in self._read("cashflows")):
            return
        gross = quantity * per_share
        self._append("cashflows", {"date": date, "symbol": symbol, "kind": "dividend",
                                   "gross": round(gross, 6), "withholding": round(gross * withholding, 6),
                                   "amount": round(gross * (1 - withholding), 6)})

    def add_split(self, date: str, symbol: str, quantity: float, ratio: float) -> None:
        if any(a["date"] == date and a["symbol"] == symbol for a in self._read("adjustments")):
            return
        self._append("adjustments", {"date": date, "symbol": symbol, "kind": "split",
                                     "ratio": ratio, "qty_delta": round(quantity * (ratio - 1), 8)})

    def add_event(self, date: str, kind: str, detail: str) -> None:
        self._append("events", {"date": date, "kind": kind, "detail": detail})

    # ---- derived state -------------------------------------------------
    def positions(self) -> dict[str, float]:
        pos: dict[str, float] = {}
        for f in self.fills():
            sign = 1 if f["side"] == "BUY" else -1
            pos[f["symbol"]] = pos.get(f["symbol"], 0.0) + sign * f["quantity"]
        for a in self._read("adjustments"):
            pos[a["symbol"]] = pos.get(a["symbol"], 0.0) + a["qty_delta"]
        return {s: q for s, q in pos.items() if abs(q) > 1e-10}

    def cash(self) -> float:
        c = self.initial_cash
        for f in self.fills():
            gross = f["quantity"] * f["price"]
            c += (-gross if f["side"] == "BUY" else gross) - f["fees"]
        c += sum(cf["amount"] for cf in self._read("cashflows"))
        return c

    def open_orders(self) -> list[dict]:
        filled = {f["order_id"] for f in self.fills()}
        cancelled = {e["detail"] for e in self.events() if e["kind"] == "cancel"}
        return [o for o in self.orders() if o["id"] not in filled and str(o["id"]) not in cancelled]

    def halted(self) -> bool:
        state = [e["kind"] for e in self.events() if e["kind"] in ("halt", "resume")]
        return bool(state) and state[-1] == "halt"

    def rebalanced_for(self, decision_date: str) -> bool:
        """True if this decision was acted on. A later "rebalance_void" event for the same
        decision (append-only correction) re-opens it."""
        if any(o["decision_date"] == decision_date for o in self.orders()):
            return True
        state = [e["kind"] for e in self.events()
                 if e["detail"] == decision_date and e["kind"] in ("rebalance", "rebalance_void")]
        return bool(state) and state[-1] == "rebalance"
