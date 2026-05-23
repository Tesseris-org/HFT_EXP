-- Polymarket Agent - SQLite Schema

-- Markets: canonical metadata, refreshed periodically from Gamma API
CREATE TABLE IF NOT EXISTS markets (
    market_id       TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    slug            TEXT,
    outcome_yes_id  TEXT NOT NULL,
    outcome_no_id   TEXT NOT NULL,
    end_date        TEXT,
    category        TEXT,
    neg_risk        INTEGER DEFAULT 0,
    min_tick        REAL DEFAULT 0.01,
    active          INTEGER DEFAULT 1,
    volume          REAL DEFAULT 0,
    liquidity       REAL DEFAULT 0,
    raw_metadata    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Price snapshots: periodic market state captures
CREATE TABLE IF NOT EXISTS market_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    yes_price       REAL,
    no_price        REAL,
    spread          REAL,
    best_bid        REAL,
    best_ask        REAL,
    bid_depth_10    REAL,
    ask_depth_10    REAL,
    volume_24h      REAL,
    source          TEXT DEFAULT 'polymarket',
    captured_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_snapshots_market ON market_snapshots(market_id, captured_at);

-- Signals: every probability estimate from every source
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    source          TEXT NOT NULL,
    probability     REAL NOT NULL,
    confidence      REAL,
    reasoning       TEXT,
    context_json    TEXT,
    model           TEXT,
    latency_ms      INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(market_id, created_at);
CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source, created_at);

-- Decisions: every trade decision (including HOLD)
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    action          TEXT NOT NULL,
    ensemble_prob   REAL NOT NULL,
    market_prob     REAL NOT NULL,
    edge            REAL NOT NULL,
    size_usd        REAL,
    kelly_fraction  REAL,
    risk_checks     TEXT,
    signals_used    TEXT,
    reason          TEXT,
    executed        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_decisions_market ON decisions(market_id, created_at);

-- Orders: full order lifecycle
CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    token_id        TEXT NOT NULL,
    side            TEXT NOT NULL,
    size            REAL NOT NULL,
    price           REAL NOT NULL,
    order_type      TEXT DEFAULT 'GTC',
    status          TEXT DEFAULT 'pending',
    filled_size     REAL DEFAULT 0,
    avg_fill_price  REAL,
    decision_id     INTEGER REFERENCES decisions(id),
    submitted_at    TEXT,
    filled_at       TEXT,
    cancelled_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market_id);

-- Positions: current holdings
CREATE TABLE IF NOT EXISTS positions (
    market_id       TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    side            TEXT NOT NULL,
    size            REAL DEFAULT 0,
    avg_cost        REAL DEFAULT 0,
    unrealized_pnl  REAL DEFAULT 0,
    realized_pnl    REAL DEFAULT 0,
    updated_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (market_id, token_id)
);

-- Calibration: track signal accuracy over time
CREATE TABLE IF NOT EXISTS calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    source          TEXT NOT NULL,
    predicted_prob  REAL NOT NULL,
    market_prob     REAL NOT NULL,
    actual_outcome  INTEGER,
    resolved_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_calibration_source ON calibration(source);

-- News events
CREATE TABLE IF NOT EXISTS news_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT,
    content_summary TEXT,
    relevant_markets TEXT,
    relevance_score REAL,
    processed       INTEGER DEFAULT 0,
    published_at    TEXT,
    ingested_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_news_processed ON news_events(processed, ingested_at);

-- Daily P&L tracking
CREATE TABLE IF NOT EXISTS daily_pnl (
    date            TEXT PRIMARY KEY,
    starting_balance REAL,
    ending_balance  REAL,
    realized_pnl    REAL,
    unrealized_pnl  REAL,
    fees_paid       REAL,
    num_trades      INTEGER,
    num_markets     INTEGER,
    win_rate        REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- System state: key-value store
CREATE TABLE IF NOT EXISTS system_state (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);
