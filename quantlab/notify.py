"""Telegram notifications. Token comes from the TELEGRAM_BOT_TOKEN env var
(a GitHub Actions secret). The chat id is discovered once from the bot's
updates (after you press Start) and saved to config/telegram.json."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "telegram.json"


def _token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def _call(method: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{_token()}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"telegram {method} failed: {body.get('description')}")
    return body["result"]


def chat_id() -> int | None:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())["chat_id"]
    if not _token():
        return None
    for upd in reversed(_call("getUpdates")):
        chat = (upd.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            CONFIG.write_text(json.dumps({"chat_id": chat["id"]}, indent=2) + "\n")
            _call("sendMessage", {"chat_id": chat["id"], "text":
                  "🔗 Rigz QuantLab connected. You'll get rebalance tickets, fill confirmations "
                  "and kill-switch alerts here."})
            return chat["id"]
    print(f"[notify] token set but no private chat found in {len(_call('getUpdates'))} updates; "
          "message the bot, then rerun")
    return None


def send(text: str) -> bool:
    """Send a message; prints instead when no token/chat is configured."""
    cid = chat_id() if _token() else None
    if not cid:
        print("[notify:dry-run]\n" + text)
        return False
    _call("sendMessage", {"chat_id": cid, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True})
    return True
