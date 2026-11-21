-- QuantLab core schema (Step 1).
-- Design rules:
--   * Prices are stored raw AND adjusted; never overwrite history silently.
--   * Every row knows when we learned it (ingested_at) -> point-in-time research.
--   * Orders/fills form an append-only ledger; positions are derived, never edited.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS instruments (
    id          SERIAL PRIMARY KEY,
    symbol      TEXT NOT NULL,
    market      TEXT NOT NULL CHECK (market IN ('US', 'NSE')),
    asset_class TEXT NOT NULL CHECK (asset_class IN ('ETF', 'STOCK')),
    currency    TEXT NOT NULL,
    name        TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    listed_on   DATE,
    delisted_on DATE,                    -- keep delisted names: avoids survivorship bias
    UNIQUE (symbol, market)
);

CREATE TABLE IF NOT EXISTS daily_bars (
    instrument_id INT  NOT NULL REFERENCES instruments(id),
    trade_date    DATE NOT NULL,
    open          NUMERIC(18,6),
    high          NUMERIC(18,6),
    low           NUMERIC(18,6),
    close         NUMERIC(18,6) NOT NULL,   -- raw close as traded
    adj_close     NUMERIC(18,6),            -- split/dividend adjusted
    volume        BIGINT,
    source        TEXT NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, trade_date)
);
SELECT create_hypertable('daily_bars', 'trade_date', if_not_exists => TRUE, migrate_data => TRUE);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id          BIGSERIAL PRIMARY KEY,
    job         TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    rows_upserted INT,
    status      TEXT NOT NULL DEFAULT 'running',  -- running | ok | warn | failed
    notes       TEXT
);

-- Signals: what the model wanted, frozen at generation time.
CREATE TABLE IF NOT EXISTS signals (
    id            BIGSERIAL PRIMARY KEY,
    strategy      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    as_of_date    DATE NOT NULL,
    instrument_id INT  NOT NULL REFERENCES instruments(id),
    score         DOUBLE PRECISION,
    rank          INT,
    target_weight DOUBLE PRECISION NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (strategy, model_version, as_of_date, instrument_id)
);

-- Append-only execution ledger. account = 'paper' | 'live-manual' | 'live-api'.
CREATE TABLE IF NOT EXISTS orders (
    id             BIGSERIAL PRIMARY KEY,
    account        TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    instrument_id  INT  NOT NULL REFERENCES instruments(id),
    side           TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    notional       NUMERIC(18,6) NOT NULL,      -- money amount (fractional-share friendly)
    limit_price    NUMERIC(18,6),
    signal_price   NUMERIC(18,6) NOT NULL,      -- price when ticket issued -> slippage baseline
    status         TEXT NOT NULL DEFAULT 'issued', -- issued | filled | cancelled | expired
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fills (
    id         BIGSERIAL PRIMARY KEY,
    order_id   BIGINT NOT NULL REFERENCES orders(id),
    quantity   NUMERIC(18,8) NOT NULL,
    price      NUMERIC(18,6) NOT NULL,
    fees       NUMERIC(18,6) NOT NULL DEFAULT 0,
    filled_at  TIMESTAMPTZ NOT NULL,
    entered_by TEXT NOT NULL DEFAULT 'manual'
);

-- Positions are derived from fills, never stored by hand.
CREATE OR REPLACE VIEW positions AS
SELECT o.account, o.instrument_id,
       SUM(CASE WHEN o.side = 'BUY' THEN f.quantity ELSE -f.quantity END) AS quantity,
       SUM(f.fees) AS total_fees
FROM fills f JOIN orders o ON o.id = f.order_id
GROUP BY o.account, o.instrument_id
HAVING SUM(CASE WHEN o.side = 'BUY' THEN f.quantity ELSE -f.quantity END) <> 0;

-- Slippage per filled order, in basis points (positive = worse than signal).
CREATE OR REPLACE VIEW slippage AS
SELECT o.id AS order_id, o.account, o.side,
       o.signal_price, AVG(f.price) AS avg_fill,
       CASE WHEN o.side = 'BUY'
            THEN (AVG(f.price) / o.signal_price - 1) * 10000
            ELSE (1 - AVG(f.price) / o.signal_price) * 10000 END AS slippage_bps
FROM orders o JOIN fills f ON f.order_id = o.id
GROUP BY o.id;
