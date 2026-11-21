import pandas as pd
import pytest

from quantlab import backtest, metrics, strategies


def test_buy_and_hold_matches_price_exactly(prices):
    res = backtest.run(prices, strategies.buy_and_hold("SPY"), cost_bps=0, lag=1)
    first = res.trades["date"].iloc[0]
    got = res.equity.iloc[-1] / res.equity.loc[first]
    want = prices["SPY"].iloc[-1] / prices["SPY"].loc[first]
    assert got == pytest.approx(want, rel=1e-10)


def test_one_time_entry_cost(prices):
    free = backtest.run(prices, strategies.buy_and_hold("SPY"), cost_bps=0)
    paid = backtest.run(prices, strategies.buy_and_hold("SPY"), cost_bps=50)
    assert paid.equity.iloc[-1] == pytest.approx(free.equity.iloc[-1] * (1 - 0.005), rel=1e-9)


def test_no_lookahead(prices):
    """Tripling one asset's future prices must not change any earlier decision."""
    strat = strategies.dual_momentum()
    base = backtest.run(prices, strat)
    cut = prices.index[len(prices) * 2 // 3]
    shocked = prices.copy()
    shocked.loc[cut:, "GLD"] *= 3.0
    res = backtest.run(shocked, strat)
    before = prices.index[prices.index < cut]
    pd.testing.assert_frame_equal(base.weights.loc[before], res.weights.loc[before])
    pd.testing.assert_series_equal(base.equity.loc[before], res.equity.loc[before])


def test_trades_execute_after_decision(prices):
    res = backtest.run(prices, strategies.buy_and_hold("SPY"), lag=1)
    first_decision = backtest.month_end_dates(prices.index)[0]
    assert res.weights.loc[first_decision, "SPY"] == 0.0
    assert res.trades["date"].iloc[0] > first_decision


@pytest.mark.parametrize("frac", [0.3, 0.5, 0.7, 0.9])
def test_dual_momentum_matches_independent_calculation(prices, frac):
    hist = prices.iloc[: int(len(prices) * frac)]
    got = strategies.dual_momentum(top_n=2, lookback=252)(hist)

    ret = hist.iloc[-1] / hist.iloc[-253] - 1
    top2 = ret.drop("BIL").sort_values(ascending=False).head(2)
    want = {}
    for sym, r in top2.items():
        key = sym if r > ret["BIL"] else "BIL"
        want[key] = want.get(key, 0) + 0.5
    assert got.to_dict() == pytest.approx(want)


def test_rejects_leverage(prices):
    with pytest.raises(ValueError):
        backtest.run(prices, lambda h: pd.Series({"SPY": 1.5}))


def test_metrics_table(prices):
    res = backtest.run(prices, strategies.dual_momentum(), name="dm")
    assert "dm" in metrics.table([res]).index
