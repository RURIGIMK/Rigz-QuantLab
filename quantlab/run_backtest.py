"""Run the baseline strategy vs benchmarks on data in the database.

    python -m quantlab.run_backtest --cost-bps 10
Writes reports/equity.csv and prints a metrics table.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from quantlab import backtest, metrics, store, strategies

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def load_prices_db(market: str = "US") -> pd.DataFrame:
    from quantlab import db
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT b.trade_date, i.symbol, b.adj_close
               FROM daily_bars b JOIN instruments i ON i.id = b.instrument_id
               WHERE i.market = %s ORDER BY b.trade_date""", (market,)
        ).fetchall()
    df = pd.DataFrame(rows, columns=["date", "symbol", "adj_close"])
    df["adj_close"] = df["adj_close"].astype(float)
    wide = df.pivot(index="date", columns="symbol", values="adj_close")
    wide.index = pd.to_datetime(wide.index)
    # start when every ETF has data; forward-fill only isolated holes
    return wide.dropna(how="any", subset=wide.columns).ffill(limit=3)


def main(cost_bps: float, source: str) -> None:
    prices = store.load_prices("us_etf") if source == "csv" else load_prices_db()
    runs = [
        backtest.run(prices, strategies.dual_momentum(), cost_bps, name="dual_momentum"),
        backtest.run(prices, strategies.buy_and_hold("SPY"), cost_bps, name="SPY"),
        backtest.run(prices, strategies.equal_weight(), cost_bps, name="equal_weight"),
    ]
    start = prices.index[0] + pd.Timedelta(days=400)   # skip momentum warm-up
    print(f"Data {prices.index[0].date()} -> {prices.index[-1].date()}, cost {cost_bps}bps\n")
    print(metrics.table(runs, start).to_string())
    REPORTS.mkdir(exist_ok=True)
    pd.concat([r.equity for r in runs], axis=1).to_csv(REPORTS / "equity.csv")
    runs[0].weights.resample("ME").last().round(3).to_csv(REPORTS / "dual_momentum_weights.csv")
    print(f"\nwrote {REPORTS}/equity.csv and dual_momentum_weights.csv")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--source", choices=["csv", "db"], default="csv")
    a = p.parse_args()
    main(a.cost_bps, a.source)
