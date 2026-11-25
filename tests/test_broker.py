import time

import pytest

from quantlab import store
from quantlab.autotrade import resolve_symbols
from quantlab.brokers import planner, tokens
from quantlab.brokers.ctrader import BUY, SELL, Position, SymbolSpec

# Broker-style contract specs: FX min 0.01 lot (1,000 units), share CFDs whole shares.
FX = dict(digits=5, lot_size=10_000_000, min_volume=100_000, step_volume=100_000, max_volume=10**12)
SH = dict(digits=2, lot_size=100, min_volume=100, step_volume=100, max_volume=10**9)


@pytest.fixture(scope="module")
def px():
    if not store.available("fx") or not store.available("us_etf"):
        pytest.skip("no market data snapshot yet")
    out = store.load_prices("us_etf", "close").iloc[-1].to_dict()
    out.update(store.load_prices("fx", "close").iloc[-1].to_dict())
    return out


def specs():
    return {"SPY": SymbolSpec(1, "SPY", **SH), "QQQ": SymbolSpec(2, "QQQ", **SH),
            "EURUSD": SymbolSpec(10, "EURUSD", **FX), "USDJPY": SymbolSpec(11, "USDJPY", **FX),
            "GBPJPY": SymbolSpec(12, "GBPJPY", **FX)}


def test_open_from_flat_sizes_by_usd_value(px):
    acts = planner.plan({"SPY": 0.5, "EURUSD": -0.5}, 100_000, px, [], specs(), max_gross=2, max_pos=1)
    by = {a.symbol: a for a in acts}
    assert by["SPY"].side == BUY and by["SPY"].volume == int(50_000 / px["SPY"]) * 100
    eur_units = 50_000 / px["EURUSD"]
    assert by["EURUSD"].side == SELL
    assert by["EURUSD"].volume == int(eur_units * 100) // 100_000 * 100_000


def test_cross_pair_converts_base_currency(px):
    acts = planner.plan({"GBPJPY": 0.3}, 100_000, px, [], specs(), max_gross=2, max_pos=1)
    gbp_units = 30_000 / px["GBPUSD"]
    assert acts[0].volume == int(gbp_units * 100) // 100_000 * 100_000


def test_leverage_caps_are_enforced(px):
    acts = planner.plan({"SPY": 5.0, "QQQ": 5.0}, 10_000, px, [], specs(), max_gross=1.0, max_pos=0.6)
    notional = sum(a.volume / 100 * px[a.symbol] for a in acts)
    assert notional <= 10_000 * 1.0 + 1e-6


def test_flip_closes_wrong_side_then_opens(px):
    pos = [Position(7, 10, BUY, 300_000, 1.1)]
    acts = planner.plan({"EURUSD": -0.5}, 100_000, px, pos, specs(), max_gross=2, max_pos=1)
    assert acts[0].kind == "close" and acts[0].position_id == 7 and acts[0].volume == 300_000
    assert acts[1].kind == "open" and acts[1].side == SELL


def test_reduce_uses_partial_close_and_no_new_order(px):
    target_units = 0.2 * 100_000 / px["SPY"]
    held = int(target_units * 100) // 100 * 100 + 1000                 # 10 shares too many
    acts = planner.plan({"SPY": 0.2}, 100_000, px, [Position(3, 1, BUY, held, 500)], specs(),
                        max_gross=1, max_pos=1)
    assert [a.kind for a in acts] == ["close"] and acts[0].volume == 1000


def test_flatten_closes_everything_and_ignores_unmanaged(px):
    pos = [Position(1, 1, BUY, 500, 1), Position(2, 10, SELL, 100_000, 1), Position(9, 999, BUY, 100, 1)]
    acts = planner.plan({}, 50_000, px, pos, specs(), max_gross=0, max_pos=0)
    assert {a.position_id for a in acts} == {1, 2}


def test_below_minimum_is_skipped(px):
    assert planner.plan({"EURUSD": 0.001}, 1_000, px, [], specs(), max_gross=1, max_pos=1) == []


def test_tokens_roundtrip_and_wrong_key_fails(tmp_path):
    p = tmp_path / "t.enc"
    tok = {"access_token": "a", "refresh_token": "r", "expires_at": int(time.time()) + 30 * 86400}
    tokens.save(tok, "secret-1", p)
    assert tokens.load("secret-1", p) == tok
    assert b"refresh" not in p.read_bytes()
    with pytest.raises(Exception):
        tokens.load("secret-2", p)
    assert tokens.valid_access_token("cid", "secret-1", p) == "a"     # far from expiry: no refresh


def test_symbol_resolution():
    avail = {"SPY.US": 1, "EURUSD": 2, "QQQ.US": 3, "GLD.US": 4}
    got = resolve_symbols(["SPY", "EURUSD", "QQQ", "XYZ", "GLD"], avail, {"GLD": "GLD.US"})
    assert got == {"SPY": 1, "EURUSD": 2, "QQQ": 3, "GLD": 4}


def test_fx_bold_picks_one_direction_and_min_lot_sizing(px):
    from quantlab import strategies
    fx = store.load_prices("fx", "close")
    fx = fx[fx.index.dayofweek < 5]
    for cut in (0.4, 0.7, 1.0):
        tgt = strategies.fx_bold()(fx.iloc[: int(len(fx) * cut)])
        assert len(tgt) == 1 and abs(tgt.iloc[0]) == 1
    sym = tgt.index[0]
    nav = 5000 / 129
    w = {sym: tgt.iloc[0] * 1000 * planner.base_usd_price(sym, px) * 1.001 / nav}
    acts = planner.plan(w, nav, px, [], specs() | {sym: SymbolSpec(99, sym, **FX)}, max_gross=60, max_pos=60)
    assert len(acts) == 1 and acts[0].volume == 100_000          # exactly 0.01 lot


def test_unrealized_pnl_in_usd(px):
    # long 1,000 EUR bought 1% below the latest close -> profit ~1% of notional, in USD
    p = Position(1, 1, BUY, 100_000, px["EURUSD"] * 0.99)
    assert planner.unrealized_usd("EURUSD", p, px) == pytest.approx(1000 * px["EURUSD"] * 0.01, rel=1e-6)
    # short 1,000 USD vs JPY entered 1% above close -> profit in JPY converted to USD
    s = Position(2, 4, SELL, 100_000, px["USDJPY"] * 1.01)
    assert planner.unrealized_usd("USDJPY", s, px) == pytest.approx(1000 * 0.01, rel=1e-2)
