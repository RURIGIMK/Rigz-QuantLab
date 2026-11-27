"""cTrader OAuth tokens, stored encrypted in the repo so the daily job can
refresh them without anyone logging in again.

Encryption key = SHA-256 of CTRADER_CLIENT_SECRET (a GitHub secret), so the
committed file is useless without the secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet

TOKEN_FILE = Path(__file__).resolve().parents[2] / "ledger" / "secure" / "ctrader_tokens.enc"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
AUTH_URL = "https://openapi.ctrader.com/apps/auth"
REFRESH_MARGIN_S = 5 * 24 * 3600     # refresh when less than 5 days remain


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def save(tokens: dict, secret: str, path: Path = TOKEN_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet(secret).encrypt(json.dumps(tokens).encode()))


def load(secret: str, path: Path = TOKEN_FILE) -> dict | None:
    if not path.exists():
        return None
    return json.loads(_fernet(secret).decrypt(path.read_bytes()))


def _token_call(params: dict) -> dict:
    url = TOKEN_URL + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    body = {}
    for attempt in range(3):
        for method in ("GET", "POST"):   # docs show GET for exchange; refresh example uses POST
            req = urllib.request.Request(url, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = {"description": f"HTTP {e.code}"}
            except (urllib.error.URLError, TimeoutError) as e:
                body = {"description": f"network: {e}"}
            if body.get("accessToken") or body.get("access_token"):
                break
        if body.get("accessToken") or body.get("access_token") or "network" not in str(body.get("description")):
            break
        time.sleep(10 * (attempt + 1))
    if "accessToken" not in body and "access_token" not in body:
        raise RuntimeError(f"token endpoint error: {body.get('description') or body.get('errorCode') or body}")
    access = body.get("accessToken") or body.get("access_token")
    refresh = body.get("refreshToken") or body.get("refresh_token")
    expires = int(body.get("expiresIn") or body.get("expires_in") or 2_592_000)
    return {"access_token": access, "refresh_token": refresh, "expires_at": int(time.time()) + expires}


def auth_link(client_id: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode(
        {"client_id": client_id, "redirect_uri": redirect_uri, "scope": "trading"})


def exchange_code(code: str, client_id: str, secret: str, redirect_uri: str) -> dict:
    return _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                        "client_id": client_id, "client_secret": secret})


def valid_access_token(client_id: str, secret: str, path: Path = TOKEN_FILE) -> str:
    """Return a usable access token, refreshing (and re-saving) it if close to expiry."""
    tok = load(secret, path)
    if tok is None:
        tok = bootstrap_from_env(client_id, secret, path)
    if tok is None:
        raise RuntimeError("no cTrader tokens yet: run the ctrader-connect workflow first")
    if tok["expires_at"] - time.time() < REFRESH_MARGIN_S:
        tok = _token_call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                           "client_id": client_id, "client_secret": secret})
        save(tok, secret, path)
    return tok["access_token"]


def bootstrap_from_env(client_id: str, secret: str, path: Path = TOKEN_FILE) -> dict | None:
    """First run: turn the CTRADER_REFRESH_TOKEN secret (from the Sandbox page) into
    stored tokens. The refresh rotates the token, so afterwards the encrypted file
    is the source of truth and the secret is never needed again."""
    refresh = os.environ.get("CTRADER_REFRESH_TOKEN", "").strip()
    if not refresh:
        return None
    tok = _token_call({"grant_type": "refresh_token", "refresh_token": refresh,
                       "client_id": client_id, "client_secret": secret})
    save(tok, secret, path)
    return tok


def env_credentials() -> tuple[str, str]:
    cid, secret = os.environ.get("CTRADER_CLIENT_ID"), os.environ.get("CTRADER_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET not set")
    return cid, secret
