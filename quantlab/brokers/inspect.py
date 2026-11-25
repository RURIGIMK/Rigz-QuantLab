"""Read-only broker inspection: symbol names, contract specs, positions. Places no orders.

    python -m quantlab.brokers.inspect
"""
from __future__ import annotations

import json

from quantlab import store
from quantlab.autotrade import resolve_symbols
from quantlab.brokers import tokens
from quantlab.brokers.ctrader import CTraderClient
from quantlab.daily import load_config


def main() -> None:
    cfg = load_config()["accounts"]["pepperstone_demo"]
    cid, secret = tokens.env_credentials()
    with CTraderClient(cfg["env"], cid, secret, tokens.valid_access_token(cid, secret)) as c:
        acct = next(a for a in c.accounts() if str(a.get("traderLogin")) == str(cfg["account_login"]))
        c.authorize(int(acct["ctidTraderAccountId"]))
        names = c.symbols()
        print(f"{len(names)} symbols available")
        wanted = store.available("us_etf")
        for sym in wanted:
            close = sorted(n for n in names if sym.upper() in n.upper().replace(".", "").replace("_", ""))[:8]
            print(f"  {sym}: candidates {close}")
        ids = resolve_symbols(wanted + store.available("fx"), names, cfg.get("symbol_map", {}))
        for name, spec in c.symbol_specs(ids).items():
            print("  spec", name, json.dumps(spec.__dict__))
        print("positions", [p.__dict__ for p in c.positions()])


if __name__ == "__main__":
    main()
