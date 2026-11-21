import numpy as np
import pandas as pd

from quantlab.validate import validate_bars


def make_bars(n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "adj_close": close, "volume": 1_000_000,
    }, index=idx)


def test_clean_data_passes():
    assert validate_bars("X", make_bars()).ok


def test_spike_is_rejected():
    df = make_bars()
    df.iloc[150, df.columns.get_loc("adj_close")] *= 2
    assert not validate_bars("X", df).ok


def test_close_outside_range_rejected():
    df = make_bars()
    df.iloc[10, df.columns.get_loc("close")] = df["high"].iloc[10] * 1.2
    assert not validate_bars("X", df).ok


def test_duplicates_rejected():
    df = make_bars()
    df = pd.concat([df, df.iloc[[5]]]).sort_index()
    assert not validate_bars("X", df).ok


def test_empty_rejected():
    assert not validate_bars("X", make_bars().iloc[0:0]).ok
