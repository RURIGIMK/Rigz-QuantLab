import pandas as pd

from quantlab.validate import validate_bars


def test_real_data_passes(bars):
    r = validate_bars("SPY", bars)
    assert r.ok, r.errors


def test_corrupted_spike_is_rejected(bars):
    df = bars.copy()
    df.iloc[len(df) // 2, df.columns.get_loc("adj_close")] *= 2
    assert not validate_bars("SPY", df).ok


def test_close_outside_range_rejected(bars):
    df = bars.copy()
    i = df.columns.get_loc("close")
    df.iloc[10, i] = df["high"].iloc[10] * 1.2
    assert not validate_bars("SPY", df).ok


def test_duplicate_dates_rejected(bars):
    df = pd.concat([bars, bars.iloc[[5]]]).sort_index()
    assert not validate_bars("SPY", df).ok


def test_empty_rejected(bars):
    assert not validate_bars("SPY", bars.iloc[0:0]).ok
