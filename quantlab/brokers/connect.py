"""One-time cTrader connection.

    python -m quantlab.brokers.connect            # print the authorisation link
    python -m quantlab.brokers.connect CODE       # exchange the code, store encrypted tokens

The redirect URI must match the one registered on the Open API app.
"""
from __future__ import annotations

import os
import sys

from quantlab.brokers import tokens

REDIRECT_URI = "https://rigz-quantlab.streamlit.app"


def main() -> int:
    cid, secret = tokens.env_credentials()
    code = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CTRADER_AUTH_CODE", "")).strip()
    if not code:
        print("Open this link, approve access, then copy the 'code' shown on the dashboard:")
        print(tokens.auth_link(cid, REDIRECT_URI))
        return 0
    tok = tokens.exchange_code(code, cid, secret, REDIRECT_URI)
    tokens.save(tok, secret)
    print("cTrader tokens stored (encrypted). Expires in "
          f"{(tok['expires_at'] - __import__('time').time()) / 86400:.0f} days; refreshed automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
