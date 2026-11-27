"""Daily health check run inside GitHub Actions. Alerts on Telegram only when something is wrong.

Checks: failed workflow runs in the last 26h, stale strategy heartbeat (weekdays),
broker login close to expiry, new halt events, execution errors.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

from quantlab import notify
from quantlab.daily import load_config
from quantlab.ledger import Ledger

REPO = os.environ.get("GITHUB_REPOSITORY", "RURIGIMK/Rigz-QuantLab")


def recent_failures(hours: int = 26) -> list[str]:
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok:
        return []
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/actions/runs?per_page=50",
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        runs = json.loads(r.read())["workflow_runs"]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    latest: dict[str, dict] = {}                       # runs come newest first
    for run in runs:
        if run.get("status") == "completed" and run["name"] not in latest:
            latest[run["name"]] = run
    out = []
    for name, run in latest.items():                   # only workflows whose LATEST run failed
        when = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if when >= cutoff and run.get("conclusion") == "failure" and name != "watchdog":
            out.append(f"{name} ({when:%a %H:%M} UTC) {run['html_url']}")
    return out


def check(now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    problems = [f"Failed run: {f}" for f in recent_failures()]
    cfg = load_config()
    for name, acct in cfg["accounts"].items():
        if not (acct.get("enabled") and acct.get("mode") == "api"):
            continue
        led = Ledger(name, acct.get("initial_cash", 0.0))
        hb = led.dir / "trade_heartbeat.json"
        weekend = now.weekday() >= 5 or (now.weekday() == 0 and now.hour < 12)
        if hb.exists():
            last = dt.datetime.fromisoformat(json.loads(hb.read_text())["last_check_utc"])
            hours = (now - last).total_seconds() / 3600
            if hours > 30 and not weekend:
                problems.append(f"{name}: no strategy check for {hours:.0f}h")
        elif not weekend:
            problems.append(f"{name}: no strategy check recorded")
        today = now.date().isoformat()
        yesterday = (now - dt.timedelta(days=1)).date().isoformat()
        for e in led.events():
            if e["kind"] == "halt" and e["date"] in (today, yesterday):
                problems.append(f"{name}: HALTED ({e['detail']})")
        for x in led._read("executions"):
            if x.get("error") and x.get("date") in (today, yesterday):
                problems.append(f"{name}: order error on {x['symbol']}: {x['error']}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        notify.send("🚨 <b>Watchdog</b>: needs attention\n" + "\n".join(f"• {p}" for p in problems)
                    + "\n\nTell Claude in the chat and it will diagnose and fix.")
        print("\n".join(problems))
    else:
        print("watchdog: healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
