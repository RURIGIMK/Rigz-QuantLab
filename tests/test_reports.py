import pytest

from quantlab import notify, reports, store, strategies


@pytest.fixture(scope="module")
def fx():
    if not store.available("fx"):
        pytest.skip("no market data snapshot yet")
    px = store.load_prices("fx", "close")
    return px[px.index.dayofweek < 5]


def test_explanation_matches_strategy_pick(fx):
    why = strategies.fx_bold_explain(fx)
    pick = strategies.fx_bold()(fx)
    assert why["symbol"] == pick.index[0]
    assert why["direction"] == ("LONG" if pick.iloc[0] > 0 else "SHORT")
    assert set(why["horizons"]) == {"1m", "3m", "6m", "12m"} and len(why["runners_up"]) == 3


def test_open_message_has_size_price_leverage_reason(fx):
    why = strategies.fx_bold_explain(fx)
    msg = reports.trade_opened("fx_bold_5k", why["symbol"], "SELL", 1000, 1.09445, 1094.45, 38.76, 129,
                               why, 2.0, 0.15, 38.76)
    for s in ("1,000 units", "0.01 lot", "1.09445", "28×", "Why:", "Runners-up", "KSh 10,000", "KSh 750"):
        assert s in msg, s


def test_close_message_uses_broker_deal_numbers():
    deal = {"executionPrice": 1.0920, "closePositionDetail": {
        "entryPrice": 1.09445, "grossProfit": 225, "swap": -12, "commission": -7, "moneyDigits": 2}}
    msg = reports.trade_closed("fx_bold_5k", "EURUSD", "SELL", 1000, 1.0920, deal, 1.0, 129, None,
                               "signal moved to LONG GBPUSD")
    assert "✅" in msg and "+KSh 266" in msg and "1.09445" in msg and "GBPUSD" in msg   # (2.25-.12-.07)*129 = 265.7


def test_whatsapp_formatting():
    assert notify.to_whatsapp("<b>EURUSD</b> &amp; <i>x</i>") == "*EURUSD* & _x_"


def test_end_of_day_report_lists_trades_and_realised_pnl():
    trades = [{"kind": "close", "side": "SELL", "symbol": "EURUSD", "volume": 100000, "entry": 1.09445,
               "price": 1.0920, "pnl_usd": 2.06},
              {"kind": "open", "side": "BUY", "symbol": "GBPUSD", "volume": 100000, "price": 1.2701},
              {"kind": "open", "symbol": "X", "volume": 1, "error": "MARKET_CLOSED"}]
    msg = reports.daily_summary("fx_bold_5k", 40.0, 38.0, 38.76, 129, ["LONG GBPUSD 1,000 ..."], 2.0, 0.15, trades)
    assert "Trades today (2)" in msg and "Closed SHORT EURUSD" in msg and "+KSh 266" in msg
    assert "Opened LONG GBPUSD" in msg and "Realised today: <b>+KSh 266</b>" in msg
    assert "MARKET_CLOSED" not in msg and "Progress:" in msg and "Open positions" in msg
    quiet = reports.daily_summary("fx_bold_5k", 40.0, 38.0, 38.76, 129, [], 2.0, 0.15, [])
    assert "none (signal unchanged)" in quiet


def test_slow_variant_explanation_consistent(fx):
    pick = strategies.fx_bold_slow()(fx)
    why = strategies.fx_bold_explain(fx, lookbacks=(252,))
    assert why["symbol"] == pick.index[0] and set(why["horizons"]) == {"12m"}
