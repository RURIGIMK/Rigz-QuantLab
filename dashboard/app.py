"""Rigz QuantLab dashboard. Reads the repo's data/ and ledger/ directly, so it
always shows what the daily workflow last committed.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantlab import backtest, metrics, store, strategies  # noqa: E402
from quantlab.ledger import Ledger  # noqa: E402

st.set_page_config(page_title="Rigz QuantLab", page_icon="📈", layout="wide")

CFG = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text())


# ---------------------------------------------------------------- data access
@st.cache_data(ttl=600)
def prices(market: str, field: str = "close") -> pd.DataFrame:
    df = store.load_prices(market, field)
    return df[df.index.dayofweek < 5]


def ledger(name: str) -> Ledger:
    return Ledger(name, CFG["accounts"][name].get("initial_cash", 0.0))


def nav_frame(led: Ledger) -> pd.DataFrame:
    rows = led.nav_history()
    if not rows:
        return pd.DataFrame(columns=["nav", "drawdown"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["drawdown"] = df["nav"] / df["nav"].cummax() - 1
    return df


def pct(x: float) -> str:
    return f"{x:+.2%}"


# ---------------------------------------------------------------- header
etf = prices("us_etf")
last_date = etf.index[-1].date()
enabled = [n for n, a in CFG["accounts"].items() if a.get("enabled")]
telegram_ok = (ROOT / "config" / "telegram.json").exists()

st.title("Rigz QuantLab")

auth_code = st.query_params.get("code")
if auth_code:
    st.success("cTrader authorisation received. Copy this code, then run GitHub → Actions → "
               "**ctrader-connect** → Run workflow, and paste it into the *code* box. It expires in a few minutes.")
    st.code(auth_code, language=None)
c = st.columns(4)
c[0].metric("Market data through", str(last_date))
c[1].metric("Active accounts", ", ".join(enabled) or "none")
c[2].metric("Kill switch", "TRIPPED" if any(ledger(n).halted() for n in enabled) else "armed")
c[3].metric("Telegram", "connected" if telegram_ok else "not connected")

tab_acct, tab_research, tab_data = st.tabs(["Accounts", "Research", "Data health"])

# ---------------------------------------------------------------- accounts
with tab_acct:
    for name in CFG["accounts"]:
        acct = CFG["accounts"][name]
        led = ledger(name)
        st.subheader(f"{name}  ·  {acct['mode']}  ·  {'enabled' if acct.get('enabled') else 'disabled'}")
        navs = nav_frame(led)
        if navs.empty:
            st.info("No activity yet.")
            continue

        nav, start_nav = navs["nav"].iloc[-1], acct.get("initial_cash") or navs["nav"].iloc[0]
        m = st.columns(5)
        m[0].metric("NAV (USD)", f"${nav:,.2f}", pct(nav / start_nav - 1) if start_nav else None)
        m[1].metric("Cash", f"${led.cash():,.2f}")
        m[2].metric("Drawdown", f"{navs['drawdown'].iloc[-1]:.1%}",
                    help=f"Kill switch at -{CFG['risk']['kill_drawdown']:.0%}")
        m[3].metric("Orders", len(led.orders()))
        m[4].metric("Open orders", len(led.open_orders()))

        left, right = st.columns([3, 2])
        with left:
            base = alt.Chart(navs.reset_index())
            st.altair_chart(base.mark_line().encode(x="date:T", y=alt.Y("nav:Q", title="NAV (USD)",
                            scale=alt.Scale(zero=False))).properties(height=220), use_container_width=True)
            st.altair_chart(base.mark_area(opacity=0.4, color="#c0392b").encode(
                x="date:T", y=alt.Y("drawdown:Q", axis=alt.Axis(format="%"), title="Drawdown")
            ).properties(height=120), use_container_width=True)
        with right:
            pos = led.positions() if acct["mode"] != "api" else navs.iloc[-1].get("holdings") or {}
            if pos and acct["mode"] == "api":
                st.caption("Broker positions (units, + long / − short)")
                st.dataframe(pd.DataFrame([{"symbol": k, "units": v} for k, v in pos.items()]),
                             hide_index=True, use_container_width=True)
            elif pos:
                last_px = etf.iloc[-1]
                rows = [{"symbol": s, "quantity": q, "price": float(last_px.get(s, float("nan"))),
                         "value": q * float(last_px.get(s, float("nan")))} for s, q in pos.items()]
                hold = pd.DataFrame(rows)
                hold["weight"] = hold["value"] / nav
                st.dataframe(hold.style.format({"quantity": "{:.6f}", "price": "${:.2f}",
                                                "value": "${:.2f}", "weight": "{:.1%}"}),
                             hide_index=True, use_container_width=True)
            else:
                st.write("No positions.")

        with st.expander("Orders, fills, dividends and events"):
            for kind in ("orders", "fills", "executions", "cashflows", "events"):
                rows = led._read(kind)
                if rows:
                    st.caption(kind)
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            fills = pd.DataFrame(led.fills())
            if not fills.empty and (fills["entered_by"] != "paper").any():
                orders = pd.DataFrame(led.orders()).rename(columns={"id": "order_id"})
                f = fills.merge(orders[["order_id", "signal_price"]], on="order_id")
                sign = f["side"].map({"BUY": 1, "SELL": -1})
                f["slippage_bps"] = sign * (f["price"] / f["signal_price"] - 1) * 1e4
                st.caption("slippage vs ticket price (positive = worse)")
                st.dataframe(f[["order_id", "symbol", "side", "signal_price", "price", "slippage_bps"]],
                             hide_index=True)
        st.divider()

# ---------------------------------------------------------------- research
with tab_research:
    st.caption("Recomputed from the committed data on every refresh. Costs included.")

    @st.cache_data(ttl=3600)
    def etf_research():
        px = prices("us_etf", "adj_close")
        runs = [backtest.run(px, strategies.dual_momentum(), 10, name="dual_momentum"),
                backtest.run(px, strategies.buy_and_hold("SPY"), 10, name="SPY"),
                backtest.run(px, strategies.equal_weight(), 10, name="equal_weight")]
        start = px.index[0] + pd.Timedelta(days=400)
        eq = pd.concat([r.equity.loc[start:] / r.equity.loc[start:].iloc[0] for r in runs], axis=1)
        return metrics.table(runs, start), eq

    table, eq = etf_research()
    st.subheader("ETF rotation vs benchmarks (since 2008)")
    st.dataframe(table, use_container_width=True)
    eq.index.name = "date"
    long = eq.reset_index().melt("date", var_name="strategy", value_name="growth of $1")
    st.altair_chart(alt.Chart(long).mark_line().encode(
        x=alt.X("date:T", title=None), y=alt.Y("growth of $1:Q", scale=alt.Scale(type="log")),
        color="strategy:N").properties(height=320), use_container_width=True)

    st.subheader("Research log")
    for md in sorted((ROOT / "docs" / "results").glob("*.md"), reverse=True):
        with st.expander(md.stem):
            st.markdown(md.read_text())

# ---------------------------------------------------------------- data health
with tab_data:
    rows = []
    for market in ("us_etf", "fx"):
        for sym in store.available(market):
            b = store.read_bars(market, sym)
            rows.append({"market": market, "symbol": sym, "first": b.index[0].date(),
                         "last": b.index[-1].date(), "rows": len(b)})
    health = pd.DataFrame(rows)
    stale = health["last"] < (pd.Timestamp(last_date) - pd.Timedelta(days=4)).date()
    if stale.any():
        st.warning(f"Stale symbols: {', '.join(health.loc[stale, 'symbol'])}")
    st.dataframe(health, hide_index=True, use_container_width=True)
    rates_dir = store.DATA / "rates"
    if rates_dir.exists():
        st.caption("Short-term interest rates (FRED, monthly)")
        r = {p.stem: pd.read_csv(p).iloc[-1] for p in sorted(rates_dir.glob("*.csv"))}
        st.dataframe(pd.DataFrame(r).T, use_container_width=True)
    tg = ROOT / "config" / "telegram.json"
    st.caption(f"Telegram chat configured: {json.loads(tg.read_text()) is not None if tg.exists() else False}")
