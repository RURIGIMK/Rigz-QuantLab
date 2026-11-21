import copy

import pandas as pd
import pytest

from quantlab import backtest, daily, risk, strategies
from quantlab.ledger import Ledger

BASE_CFG = {
    "strategy": {"name": "dual_momentum", "top_n": 2, "lookback": 252, "cash_symbol": "BIL"},
    "risk": {"min_order": 1.0, "max_weight": 0.6, "cash_buffer": 0.01, "kill_drawdown": 0.30},
    "tax": {"us_dividend_withholding": 0.30},
    "accounts": {"paper": {"enabled": True, "mode": "paper", "initial_cash": 5.40, "fee_bps": 10}},
}


# ---------- risk engine on real prices ----------

def test_plan_respects_cash_min_order_and_caps(prices):
    px = prices.iloc[-1]
    target = pd.Series({"QQQ": 0.9, "GLD": 0.1})
    plan = risk.plan_trades(target, {}, 5.40, px, min_order=1.0, max_weight=0.6,
                            cash_buffer=0.01, cash_symbol="BIL")
    by = {o.symbol: o for o in plan}
    assert by["QQQ"].notional <= 0.6 * 5.40 + 1e-9          # capped
    assert "GLD" not in by                                   # 0.54 < $1 minimum
    assert sum(o.notional for o in plan if o.side == "BUY") <= 5.40


def test_full_exit_goes_through_below_minimum(prices):
    px = prices.iloc[-1]
    tiny = 0.5 / px["TLT"]                                   # $0.50 of TLT
    plan = risk.plan_trades(pd.Series({"BIL": 1.0}), {"TLT": tiny}, 5.0, px,
                            min_order=1.0, max_weight=0.6, cash_buffer=0.0)
    sells = [o for o in plan if o.side == "SELL"]
    assert len(sells) == 1 and sells[0].full_exit


def test_buys_never_exceed_available_cash(prices):
    px = prices.iloc[-1]
    holdings = {"SPY": 3.0 / px["SPY"]}
    plan = risk.plan_trades(pd.Series({"QQQ": 0.5, "GLD": 0.5}), holdings, 2.0, px,
                            min_order=1.0, max_weight=0.6, cash_buffer=0.01)
    proceeds = sum(o.notional for o in plan if o.side == "SELL")
    assert sum(o.notional for o in plan if o.side == "BUY") <= 2.0 + proceeds + 1e-9


# ---------- ledger ----------

def test_ledger_rejects_bad_fills(tmp_path):
    led = Ledger("t", 10.0, tmp_path)
    o = led.add_order(symbol="SPY", side="SELL", notional=1, signal_price=500,
                      decision_date="2026-10-31", exec_date="2026-11-03", strategy="x", reason="t")
    with pytest.raises(ValueError):                         # can't sell what you don't hold
        led.add_fill(order_id=o["id"], quantity=0.01, price=500, fees=0, filled_at="d", entered_by="t")
    b = led.add_order(symbol="SPY", side="BUY", notional=5, signal_price=500,
                      decision_date="2026-10-31", exec_date="2026-11-03", strategy="x", reason="t")
    led.add_fill(order_id=b["id"], quantity=0.01, price=500, fees=0.01, filled_at="d", entered_by="t")
    with pytest.raises(ValueError):                         # no double fills
        led.add_fill(order_id=b["id"], quantity=0.01, price=500, fees=0, filled_at="d", entered_by="t")
    assert led.cash() == pytest.approx(10 - 5 - 0.01)
    led.add_dividend("2026-11-10", "SPY", 0.01, 2.0, 0.30)
    assert led.cash() == pytest.approx(10 - 5 - 0.01 + 0.014)
    led.add_split("2026-11-12", "SPY", 0.01, 2.0)
    assert led.positions()["SPY"] == pytest.approx(0.02)


# ---------- the live engine must reproduce the backtest ----------

def test_paper_replay_matches_backtest(prices, tmp_path):
    cfg = copy.deepcopy(BASE_CFG)
    cfg["risk"].update(min_order=0.0, max_weight=1.0, cash_buffer=0.0, kill_drawdown=0.99)
    cfg["accounts"]["paper"].update(initial_cash=1_000_000.0, fee_bps=0)

    px = prices.loc["2019-01-01":"2021-12-31"]
    full = prices.loc[:"2021-12-31"]
    start = px.index[px.index.month != pd.Series(px.index).shift().dt.month.values][1]  # first day of a month

    for d in full.loc[start:].index:
        hist = full.loc[:d]
        daily.run(cfg, signal_px=hist, trade_px=hist, actions={}, root=tmp_path, send=False)

    nav = pd.Series({pd.Timestamp(n["date"]): n["nav"] for n in
                     Ledger("paper", 1e6, tmp_path).nav_history()})
    bt = backtest.run(full, strategies.dual_momentum(), cost_bps=0, lag=1).equity.loc[start:]
    paper_ret = nav.iloc[-1] / nav.iloc[0]
    bt_ret = bt.iloc[-1] / bt.loc[nav.index[0]]
    assert paper_ret == pytest.approx(bt_ret, rel=1e-4)


def test_daily_is_idempotent(prices, tmp_path):
    hist = prices.loc[:"2024-03-04"]
    for _ in range(3):
        daily.run(copy.deepcopy(BASE_CFG), signal_px=hist, trade_px=hist, actions={},
                  root=tmp_path, send=False)
    led = Ledger("paper", 5.40, tmp_path)
    assert len(led.nav_history()) == 1
    assert len({o["decision_date"] for o in led.orders()}) == 1


def test_kill_switch_halts_trading(prices, tmp_path):
    cfg = copy.deepcopy(BASE_CFG)
    cfg["risk"]["kill_drawdown"] = 0.0001                     # trips on the first down day
    for d in prices.loc["2022-01-01":"2022-04-30"].index:
        hist = prices.loc[:d]
        daily.run(cfg, signal_px=hist, trade_px=hist, actions={}, root=tmp_path, send=False)
    led = Ledger("paper", 5.40, tmp_path)
    assert led.halted()
    halt_day = next(e["date"] for e in led.events() if e["kind"] == "halt")
    assert all(o["exec_date"] <= halt_day for o in led.orders())
