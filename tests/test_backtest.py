import numpy as np
import pandas as pd
import pytest

from quantlab import backtest, metrics, strategies


def prices(n=800, seed=1, drifts=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n)
    drifts = drifts or {"SPY": 0.0004, "TLT": 0.0001, "GLD": 0.0002, "BIL": 0.00005}
    data = {}
    for s, mu in drifts.items():
        vol = 0.0005 if s == "BIL" else 0.01
        data[s] = 100 * np.exp(np.cumsum(rng.normal(mu, vol, n)))
    return pd.DataFrame(data, index=idx)


def test_buy_and_hold_matches_price_exactly():
    p = prices()
    res = backtest.run(p, strategies.buy_and_hold("SPY"), cost_bps=0, lag=1)
    first_trade = res.trades["date"].iloc[0]
    got = res.equity.iloc[-1] / res.equity.loc[first_trade]
    want = p["SPY"].iloc[-1] / p["SPY"].loc[first_trade]
    assert got == pytest.approx(want, rel=1e-12)


def test_costs_reduce_returns_by_expected_amount():
    p = prices()
    free = backtest.run(p, strategies.buy_and_hold("SPY"), cost_bps=0)
    paid = backtest.run(p, strategies.buy_and_hold("SPY"), cost_bps=50)
    # only the first trade has turnover (1.0); later rebalances are no-ops
    assert paid.equity.iloc[-1] == pytest.approx(free.equity.iloc[-1] * (1 - 0.005), rel=1e-9)


def test_no_lookahead_future_shock_does_not_change_past():
    p = prices()
    strat = strategies.dual_momentum(top_n=2, lookback=126)
    base = backtest.run(p, strat)
    shocked = p.copy()
    cut = p.index[600]
    shocked.loc[cut:, "GLD"] *= 3.0     # huge future move in one asset
    res = backtest.run(shocked, strat)
    before = p.index[p.index < cut]
    pd.testing.assert_frame_equal(base.weights.loc[before], res.weights.loc[before])
    pd.testing.assert_series_equal(base.equity.loc[before], res.equity.loc[before])


def test_trades_execute_after_decision():
    p = prices()
    res = backtest.run(p, strategies.buy_and_hold("SPY"), lag=1)
    first_decision = backtest.month_end_dates(p.index)[0]
    assert res.weights.loc[first_decision, "SPY"] == 0.0
    assert res.trades["date"].iloc[0] > first_decision


def test_dual_momentum_goes_to_cash_when_everything_falls():
    p = prices(drifts={"SPY": -0.002, "TLT": -0.002, "GLD": -0.002, "BIL": 0.00005})
    w = strategies.dual_momentum(top_n=2, lookback=252)(p)
    assert w.to_dict() == {"BIL": pytest.approx(1.0)}


def test_dual_momentum_picks_strongest():
    p = prices(drifts={"SPY": 0.002, "TLT": -0.001, "GLD": 0.001, "BIL": 0.00005})
    w = strategies.dual_momentum(top_n=2, lookback=252)(p)
    assert set(w.index) == {"SPY", "GLD"}
    assert w.sum() == pytest.approx(1.0)


def test_rejects_leverage():
    p = prices()
    with pytest.raises(ValueError):
        backtest.run(p, lambda h: pd.Series({"SPY": 1.5}))


def test_metrics_run():
    p = prices()
    res = backtest.run(p, strategies.dual_momentum(lookback=126), name="dm")
    t = metrics.table([res])
    assert "dm" in t.index
