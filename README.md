# QuantLab

**Dashboard (private):** https://rigz-quantlab.streamlit.app — redeploys automatically on every push.

Signal-generating trading platform with human-in-the-loop execution.
The system decides; you place the orders; the ledger reconciles.

## Run Step 1

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d                 # Postgres + TimescaleDB, schema auto-applied
pytest -q                            # data-quality tests
python -m quantlab.ingest --to csv   # ETFs + forex
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

## How it runs (no local setup needed)

| Workflow | When | What |
|---|---|---|
| `daily` | Weekdays 01:17 Nairobi, or on demand | Downloads data → applies dividends/splits → month-end rebalance → NAV → kill switch → Telegram |
| `record-fill` | You trigger it after placing an order | Records the real fill, reports slippage on Telegram |
| `ci` | Every push | Full test suite on real data |

State lives in git: `data/` (market data), `ledger/<account>/` (orders, fills, cashflows, NAV, events).
Accounts and risk limits: `config/accounts.yaml`. To go live, fund the broker, set `live.enabled: true`
and `live.initial_cash`, and the next month-end sends you tickets.

## Status

| Step | Status |
|---|---|
| Data pipeline (Yahoo via Actions), validation | done |
| Backtester + dual-momentum baseline | done, see `docs/results/` |
| Risk engine, ledger, paper account, kill switch | done; paper replay matches backtest to 8 digits |
| Telegram tickets + fill recording | done |
| Go live (~KSh 700) | waiting on broker account |
| Dashboard (Streamlit, private) | done |
| Forex: trend and carry researched on 20y real data | both rejected, see `docs/results/` |
| Pepperstone cTrader adapter (autonomous) | waiting on demo account + Open API app |
| Turnover reduction, ML ranker | next |
