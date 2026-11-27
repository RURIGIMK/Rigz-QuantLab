"""Minimal cTrader Open API client over the JSON WebSocket interface.

Protocol facts (from Spotware's official ctrader-open-api SDK definitions):
  * Hosts: demo.ctraderapi.com / live.ctraderapi.com, JSON over WebSocket on port 5036.
  * Envelope: {"clientMsgId": str, "payloadType": int, "payload": {...}}.
  * Volumes are in 0.01 of a unit (100000 EUR = 10_000_000).
  * Enums are sent as numbers: orderType MARKET=1; tradeSide BUY=1, SELL=2.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from websockets.sync.client import connect

HOSTS = {"demo": "wss://demo.ctraderapi.com:5036", "live": "wss://live.ctraderapi.com:5036"}

PT = {  # payload types
    "HEARTBEAT": 51,
    "APP_AUTH_REQ": 2100, "APP_AUTH_RES": 2101,
    "ACCOUNT_AUTH_REQ": 2102, "ACCOUNT_AUTH_RES": 2103,
    "NEW_ORDER_REQ": 2106, "CLOSE_POSITION_REQ": 2111,
    "SYMBOLS_LIST_REQ": 2114, "SYMBOLS_LIST_RES": 2115,
    "SYMBOL_BY_ID_REQ": 2116, "SYMBOL_BY_ID_RES": 2117,
    "TRADER_REQ": 2121, "TRADER_RES": 2122,
    "RECONCILE_REQ": 2124, "RECONCILE_RES": 2125,
    "EXECUTION_EVENT": 2126, "ORDER_ERROR_EVENT": 2132, "ERROR_RES": 2142,
    "ACCOUNTS_BY_TOKEN_REQ": 2149, "ACCOUNTS_BY_TOKEN_RES": 2150,
}
BUY, SELL, MARKET = 1, 2, 1
EXEC_FILLED, EXEC_REJECTED, EXEC_PARTIAL = 3, 7, 11


class CTraderError(RuntimeError):
    pass


@dataclass
class SymbolSpec:
    symbol_id: int
    name: str
    digits: int
    lot_size: int        # 0.01-unit volume of one lot
    min_volume: int      # 0.01 units
    step_volume: int
    max_volume: int


@dataclass
class Position:
    position_id: int
    symbol_id: int
    side: int            # BUY | SELL
    volume: int          # 0.01 units
    price: float
    open_ts: int = 0     # ms since epoch


class CTraderClient:
    def __init__(self, env: str, client_id: str, client_secret: str, access_token: str,
                 timeout: float = 20.0):
        if env not in HOSTS:
            raise ValueError(env)
        self.env, self.timeout = env, timeout
        self._creds = (client_id, client_secret)
        self.access_token = access_token
        self.ws = None
        self.account_id: int | None = None
        self._last_send = 0.0

    # ---- transport -----------------------------------------------------
    def __enter__(self):
        cid, secret = self._creds
        last = None
        for attempt in range(4):                     # 0s, 5s, 15s, 45s back-off
            try:
                self.ws = connect(HOSTS[self.env], open_timeout=self.timeout, max_size=2**24)
                self.request(PT["APP_AUTH_REQ"], {"clientId": cid, "clientSecret": secret},
                             PT["APP_AUTH_RES"])
                return self
            except Exception as e:  # noqa: BLE001 - network blips, broker restarts
                last = e
                if self.ws:
                    self.ws.close()
                time.sleep(5 * (3 ** attempt) if attempt < 3 else 0)
        raise CTraderError(f"could not connect after retries: {last}")

    def __exit__(self, *exc):
        if self.ws:
            self.ws.close()

    def _send(self, payload_type: int, payload: dict) -> str:
        msg_id = uuid.uuid4().hex[:12]
        self.ws.send(json.dumps({"clientMsgId": msg_id, "payloadType": payload_type, "payload": payload}))
        self._last_send = time.monotonic()
        return msg_id

    def _recv(self) -> dict:
        if time.monotonic() - self._last_send > 10:
            self._send(PT["HEARTBEAT"], {})
        return json.loads(self.ws.recv(timeout=self.timeout))

    def request(self, payload_type: int, payload: dict, expect: int) -> dict:
        msg_id = self._send(payload_type, payload)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            msg = self._recv()
            pt, body = msg.get("payloadType"), msg.get("payload", {})
            if pt == PT["ERROR_RES"] and msg.get("clientMsgId") in (msg_id, None):
                raise CTraderError(f"{body.get('errorCode')}: {body.get('description')}")
            if pt == expect and msg.get("clientMsgId") in (msg_id, None):
                return body
        raise CTraderError(f"timeout waiting for payloadType {expect}")

    # ---- account -------------------------------------------------------
    def accounts(self) -> list[dict]:
        res = self.request(PT["ACCOUNTS_BY_TOKEN_REQ"], {"accessToken": self.access_token},
                           PT["ACCOUNTS_BY_TOKEN_RES"])
        return res.get("ctidTraderAccount", [])

    def authorize(self, account_id: int) -> None:
        self.request(PT["ACCOUNT_AUTH_REQ"], {"ctidTraderAccountId": account_id,
                                              "accessToken": self.access_token}, PT["ACCOUNT_AUTH_RES"])
        self.account_id = account_id

    def trader(self) -> dict:
        t = self.request(PT["TRADER_REQ"], {"ctidTraderAccountId": self.account_id}, PT["TRADER_RES"])["trader"]
        t["balance_money"] = t["balance"] / 10 ** t.get("moneyDigits", 2)
        return t

    def symbols(self) -> dict[str, int]:
        res = self.request(PT["SYMBOLS_LIST_REQ"], {"ctidTraderAccountId": self.account_id},
                           PT["SYMBOLS_LIST_RES"])
        return {s["symbolName"]: s["symbolId"] for s in res.get("symbol", []) if s.get("enabled", True)}

    def symbol_specs(self, ids: dict[str, int]) -> dict[str, SymbolSpec]:
        if not ids:
            return {}
        res = self.request(PT["SYMBOL_BY_ID_REQ"], {"ctidTraderAccountId": self.account_id,
                                                    "symbolId": list(ids.values())}, PT["SYMBOL_BY_ID_RES"])
        by_id = {v: k for k, v in ids.items()}
        return {by_id[s["symbolId"]]: SymbolSpec(s["symbolId"], by_id[s["symbolId"]], s.get("digits", 5),
                                                  s.get("lotSize", 10_000_000), s.get("minVolume", 100),
                                                  s.get("stepVolume", 100), s.get("maxVolume", 10**12))
                for s in res.get("symbol", [])}

    def positions(self) -> list[Position]:
        res = self.request(PT["RECONCILE_REQ"], {"ctidTraderAccountId": self.account_id}, PT["RECONCILE_RES"])
        return [Position(p["positionId"], p["tradeData"]["symbolId"], p["tradeData"]["tradeSide"],
                         p["tradeData"]["volume"], p.get("price", 0.0),
                         int(p["tradeData"].get("openTimestamp", 0))) for p in res.get("position", [])]

    # ---- trading -------------------------------------------------------
    def _await_execution(self, msg_id: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            msg = self._recv()
            pt, body = msg.get("payloadType"), msg.get("payload", {})
            if pt in (PT["ORDER_ERROR_EVENT"], PT["ERROR_RES"]):
                raise CTraderError(f"{body.get('errorCode')}: {body.get('description')}")
            if pt == PT["EXECUTION_EVENT"]:
                et = body.get("executionType")
                if et == EXEC_REJECTED:
                    raise CTraderError(f"order rejected: {body.get('errorCode')}")
                if et in (EXEC_FILLED, EXEC_PARTIAL):
                    return body
        raise CTraderError("timeout waiting for fill")

    def market_order(self, symbol_id: int, side: int, volume: int, label: str) -> dict:
        msg_id = self._send(PT["NEW_ORDER_REQ"], {
            "ctidTraderAccountId": self.account_id, "symbolId": symbol_id, "orderType": MARKET,
            "tradeSide": side, "volume": volume, "label": label[:100]})
        return self._await_execution(msg_id)

    def close_position(self, position_id: int, volume: int) -> dict:
        msg_id = self._send(PT["CLOSE_POSITION_REQ"], {"ctidTraderAccountId": self.account_id,
                                                       "positionId": position_id, "volume": volume})
        return self._await_execution(msg_id)
