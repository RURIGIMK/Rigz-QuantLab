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


def _send_telegram(text: str) -> bool:
    cid = chat_id() if _token() else None
    if not cid:
        return False
    _call("sendMessage", {"chat_id": cid, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True})
    return True


def to_whatsapp(text: str) -> str:
    """Telegram HTML -> WhatsApp formatting (*bold*, _italic_), entities unescaped."""
    import html as _html
    import re
    t = re.sub(r"</?b>", "*", text)
    t = re.sub(r"</?i>", "_", t)
    t = re.sub(r"<[^>]+>", "", t)
    return _html.unescape(t)


def _send_whatsapp(text: str) -> bool:
    """WhatsApp via CallMeBot (free, personal use). Needs WHATSAPP_PHONE + WHATSAPP_APIKEY."""
    import urllib.parse
    phone, key = os.environ.get("WHATSAPP_PHONE"), os.environ.get("WHATSAPP_APIKEY")
    if not phone or not key:
        return False
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": phone, "text": to_whatsapp(text)[:3500], "apikey": key})
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read().decode(errors="replace")
    if r.status != 200 or "error" in body.lower()[:200]:
        raise RuntimeError(f"callmebot: HTTP {r.status}")
    return True


def send(text: str) -> bool:
    """Send to every configured channel; a failing channel never blocks the others.
    Prints instead when nothing is configured."""
    sent = False
    for name, fn in (("telegram", _send_telegram), ("whatsapp", _send_whatsapp)):
        try:
            sent = fn(text) or sent
        except Exception as e:  # noqa: BLE001
            print(f"[notify] {name} failed: {e}")
    if not sent:
        print("[notify:dry-run]\n" + text)
    return sent


if __name__ == "__main__":
    # Connection check: discovers and saves the chat id (sending a one-time
    # confirmation) if it isn't configured yet.
    if not _token():
        print("[notify] TELEGRAM_BOT_TOKEN not set")
    else:
        print(f"[notify] chat configured: {chat_id() is not None}")
