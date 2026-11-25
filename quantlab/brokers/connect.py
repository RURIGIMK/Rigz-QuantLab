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
    cid, secret = cid.strip(), secret.strip()
    code = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CTRADER_AUTH_CODE", "")).strip()
    # Diagnostics that reveal nothing secret: shape of the credentials, and whether the
    # API server accepts them (application auth needs no user code).
    print(f"client id: prefix {cid.split('_')[0]}_..., length {len(cid)}; secret length {len(secret)}; "
          f"whitespace in values: {cid != cid.strip() or secret != secret.strip()}")
    try:
        from quantlab.brokers.ctrader import CTraderClient
        with CTraderClient("demo", cid.strip(), secret.strip(), ""):
            print("application credentials: ACCEPTED by cTrader API")
    except Exception as e:  # noqa: BLE001
        print(f"application credentials: REJECTED ({e})")
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
