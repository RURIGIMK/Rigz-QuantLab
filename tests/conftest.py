"""All tests run on the real market-data snapshot committed in data/.
If the snapshot isn't there yet (fresh clone before the data workflow ran),
tests skip instead of falling back to invented prices."""
import pytest

from quantlab import store


@pytest.fixture(scope="session")
def bars():
    if "SPY" not in store.available("us_etf"):
        pytest.skip("no market data snapshot yet - run the market-data workflow")
    return store.read_bars("us_etf", "SPY")


@pytest.fixture(scope="session")
def prices():
    if len(store.available("us_etf")) < 4:
        pytest.skip("no market data snapshot yet - run the market-data workflow")
    return store.load_prices("us_etf")
