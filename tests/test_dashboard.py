from pathlib import Path
import pytest

from quantlab import store


def test_dashboard_renders_without_errors():
    pytest.importorskip("streamlit")
    if not store.available("us_etf"):
        pytest.skip("no market data snapshot yet")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py"), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "Rigz QuantLab"
    labels = [m.label for m in at.metric]
    assert "NAV (USD)" in labels and "Market data through" in labels
