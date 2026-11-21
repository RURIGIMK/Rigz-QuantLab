# QuantLab

Signal-generating trading platform with human-in-the-loop execution.
The system decides; you place the orders; the ledger reconciles.

## Run Step 1

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d                 # Postgres + TimescaleDB, schema auto-applied
pytest -q                            # data-quality tests
python -m quantlab.ingest_us_etf     # ~20 years of ETF history
```

Check it worked:

```bash
docker compose exec db psql -U quant -d quantlab -c \
 "SELECT i.symbol, count(*), min(trade_date), max(trade_date)
    FROM daily_bars b JOIN instruments i ON i.id=b.instrument_id GROUP BY 1 ORDER BY 1;"
docker compose exec db psql -U quant -d quantlab -c "SELECT * FROM ingestion_runs;"
```

Done when: 13 ETFs loaded, `ingestion_runs.status = ok`, and the
incremental run (`--days 10`) leaves the row count unchanged except for new days.

## Run Steps 2–3 (backtest)

```bash
python -m quantlab.run_backtest --cost-bps 10
python -m quantlab.run_backtest --cost-bps 30   # stress: does the edge survive higher costs?
```

Compares dual momentum against SPY buy-and-hold and an equal-weight portfolio.
Decisions use month-end closes; trades fill on the next close; costs are charged on turnover.

## Roadmap

| Step | Build | Done when |
|---|---|---|
| 1 | DB schema, ETF ingestion, data validation | 13 ETFs loaded, tests green |
| 2 | Backtester: monthly rebalance, cost model, walk-forward | Reproduces buy-and-hold SPY exactly; baseline rule results match a hand check |
| 3 | Baseline strategy: dual-momentum rotation (top 2 + BIL trend filter) | Backtest report with Sharpe, max drawdown, turnover, after costs |
| 4 | Risk engine: target weights to trades, min order size, caps, kill switch | Unit tests for every rule |
| 5 | PaperAdapter + ledger | Paper account runs a full month automatically |
| 6 | ManualAdapter: Telegram order tickets, fill entry, reconciliation, slippage view | Dry run with a fake fill reconciles to zero difference |
| 7 | **Go live, KSh ~700, baseline rule only** | 3 clean monthly cycles |
| 8 | Feature store + LightGBM ranker, walk-forward | Beats baseline out of sample after costs, or is discarded |
| 9 | NSE ingestion + research track | Liquidity-filtered universe, stale-close handling |
| 10 | Dashboard (React): live vs paper vs backtest, drawdown, kill switch | Used every month |
