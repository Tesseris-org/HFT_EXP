# Polymarket Agent — Production Design Document

## 1. Design Philosophy

**Core thesis:** In prediction markets, alpha comes from *information edge* — estimating
event probabilities better than the market consensus. Infrastructure is secondary.
The system is designed signal-first, infrastructure-second.

**Principles:**

1. **Signal is king.** Every architectural decision serves one goal: produce better
   probability estimates faster than the market.
2. **Simple until proven insufficient.** Single process, SQLite, async Python.
   No microservices, no message queues, no orchestration frameworks.
3. **Same codepath, paper or live.** Only the execution layer differs.
   Everything upstream is identical in both modes.
4. **Every decision is auditable.** Full trail: what signals fired, what edge was
   calculated, why the trade was or wasn't taken.
5. **Fail safe, not fail fast.** Risk limits are hard vetoes. The system degrades
   gracefully — if news API is down, trade on cross-platform signals. If LLM is
   slow, use cached estimates. Never trade on stale data.
6. **No LangChain, no frameworks.** Direct API calls. Explicit async control flow.
   Easier to debug, profile, and reason about.

---

## 2. System Architecture

```
                         ┌─────────────────────────────┐
                         │        MAIN LOOP             │
                         │     (async orchestrator)     │
                         └──────────┬──────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │  DATA INGESTION  │  │  SIGNAL ENGINE   │  │   OPERATIONS     │
   │                  │  │                  │  │                  │
   │ ┌──────────────┐ │  │ ┌──────────────┐ │  │ ┌──────────────┐ │
   │ │ Polymarket   │ │  │ │ LLM          │ │  │ │ Health       │ │
   │ │ REST + WS    │ │  │ │ Forecaster   │ │  │ │ Monitor      │ │
   │ ├──────────────┤ │  │ ├──────────────┤ │  │ ├──────────────┤ │
   │ │ News         │ │  │ │ News         │ │  │ │ Position     │ │
   │ │ Ingestor     │ │  │ │ Analyzer     │ │  │ │ Reconciler   │ │
   │ ├──────────────┤ │  │ ├──────────────┤ │  │ ├──────────────┤ │
   │ │ Cross-Plat   │ │  │ │ Cross-Plat   │ │  │ │ Alerter      │ │
   │ │ Fetcher      │ │  │ │ Aggregator   │ │  │ │ (Telegram)   │ │
   │ └──────────────┘ │  │ ├──────────────┤ │  │ └──────────────┘ │
   └────────┬─────────┘  │ │ Ensemble     │ │  └────────┬─────────┘
            │            │ │ Combiner     │ │           │
            │            │ └──────────────┘ │           │
            │            └────────┬─────────┘           │
            │                     │                     │
            ▼                     ▼                     │
   ┌─────────────────────────────────────────────┐      │
   │              DECISION ENGINE                │      │
   │                                             │      │
   │  Edge Calc ──▶ Kelly Sizing ──▶ Risk Check  │      │
   └──────────────────────┬──────────────────────┘      │
                          │                             │
                          ▼                             │
   ┌─────────────────────────────────────────────┐      │
   │            EXECUTION ENGINE                 │      │
   │                                             │      │
   │  ┌─────────────┐    ┌─────────────────┐     │      │
   │  │ PaperExec   │ OR │ LiveExec        │     │      │
   │  │ (simulator) │    │ (py-clob-client)│     │      │
   │  └─────────────┘    └─────────────────┘     │      │
   └──────────────────────┬──────────────────────┘      │
                          │                             │
                          ▼                             ▼
   ┌─────────────────────────────────────────────────────────┐
   │                   PERSISTENCE LAYER                     │
   │                                                         │
   │   SQLite (primary)              S3 (archival)           │
   │   - markets, signals            - nightly DB backup     │
   │   - decisions, orders           - raw event logs        │
   │   - positions, P&L              - signal snapshots      │
   │   - calibration data                                    │
   └─────────────────────────────────────────────────────────┘
```

**Single-process rationale:** Prediction market trading requires shared state (open
positions, order book context, running P&L, active signals). A single async Python
process with in-memory hot state + SQLite persistence is simpler, faster, and more
reliable than any distributed alternative at this scale.

---

## 3. Data Model

### 3.1 Core Entities

```
Market
├── market_id: str              # Polymarket condition_id
├── question: str               # "Will X happen by Y?"
├── slug: str                   # URL slug
├── outcomes: list[str]         # ["Yes", "No"]
├── token_ids: dict[str, str]   # {"Yes": "0x...", "No": "0x..."}
├── end_date: datetime
├── category: str               # "politics", "crypto", "sports"
├── neg_risk: bool              # multi-outcome market?
├── min_tick: float             # tick size (usually 0.01)
├── active: bool
├── volume: float               # total volume traded
└── liquidity: float            # current book depth

Signal
├── id: int (auto)
├── market_id: str
├── source: str                 # "llm_forecaster", "cross_platform", "news", "ensemble"
├── probability: float          # estimated probability [0, 1]
├── confidence: float           # model confidence [0, 1]
├── reasoning: str              # why this estimate (for audit)
├── context: json               # input data used (news, prices, etc.)
├── model: str                  # "claude-sonnet-4-6", etc.
├── latency_ms: int             # how long the signal took to generate
└── created_at: datetime

Decision
├── id: int (auto)
├── market_id: str
├── action: str                 # "BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO", "HOLD"
├── ensemble_prob: float        # our probability estimate
├── market_prob: float          # current market price
├── edge: float                 # ensemble_prob - market_prob (adjusted for fees)
├── size_usd: float             # position size in USD
├── kelly_fraction: float       # raw kelly fraction before caps
├── risk_checks: json           # which risk checks passed/failed
├── signals_used: json          # list of signal IDs that fed this decision
├── reason: str                 # human-readable decision rationale
├── executed: bool              # did we actually trade?
└── created_at: datetime

Order
├── id: str                     # exchange order ID (or local ID for paper)
├── market_id: str
├── token_id: str
├── side: str                   # "BUY" or "SELL"
├── size: float                 # number of shares
├── price: float                # limit price
├── order_type: str             # "GTC", "FOK", "GTD"
├── status: str                 # "pending", "submitted", "partial", "filled",
│                               # "cancelled", "rejected"
├── filled_size: float
├── avg_fill_price: float
├── decision_id: int            # links back to the decision
├── submitted_at: datetime
├── filled_at: datetime
└── cancelled_at: datetime

Position
├── market_id: str
├── token_id: str
├── side: str                   # "YES" or "NO"
├── size: float                 # current shares held
├── avg_cost: float             # weighted average entry price
├── unrealized_pnl: float       # mark-to-market P&L
├── realized_pnl: float         # closed P&L (from fills + resolutions)
└── updated_at: datetime

CalibrationRecord
├── id: int (auto)
├── market_id: str
├── source: str                 # which signal source
├── predicted_prob: float       # what we predicted
├── market_prob_at_signal: float # what the market was at signal time
├── actual_outcome: bool        # did YES win?
├── resolved_at: datetime
└── created_at: datetime
```

### 3.2 SQLite Schema

```sql
-- Markets: canonical metadata, refreshed periodically from Gamma API
CREATE TABLE markets (
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
    raw_metadata    TEXT,  -- full JSON from Gamma
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Price snapshots: periodic market state captures
CREATE TABLE market_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    yes_price       REAL,
    no_price        REAL,
    spread          REAL,
    best_bid        REAL,
    best_ask        REAL,
    bid_depth_10    REAL,  -- USD depth within 10 cents of mid
    ask_depth_10    REAL,
    volume_24h      REAL,
    source          TEXT DEFAULT 'polymarket',  -- or 'metaculus', 'manifold', etc.
    captured_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_snapshots_market ON market_snapshots(market_id, captured_at);

-- Signals: every probability estimate from every source
CREATE TABLE signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    source          TEXT NOT NULL,  -- 'llm_forecaster', 'cross_platform', 'news', 'ensemble'
    probability     REAL NOT NULL,
    confidence      REAL,
    reasoning       TEXT,
    context_json    TEXT,
    model           TEXT,
    latency_ms      INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_signals_market ON signals(market_id, created_at);
CREATE INDEX idx_signals_source ON signals(source, created_at);

-- Decisions: every trade decision (including decisions NOT to trade)
CREATE TABLE decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    action          TEXT NOT NULL,  -- 'BUY_YES', 'BUY_NO', 'HOLD'
    ensemble_prob   REAL NOT NULL,
    market_prob     REAL NOT NULL,
    edge            REAL NOT NULL,
    size_usd        REAL,
    kelly_fraction  REAL,
    risk_checks     TEXT,  -- JSON: {"max_position": "pass", "daily_loss": "pass", ...}
    signals_used    TEXT,  -- JSON: [signal_id, signal_id, ...]
    reason          TEXT,
    executed        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_decisions_market ON decisions(market_id, created_at);

-- Orders: full order lifecycle
CREATE TABLE orders (
    id              TEXT PRIMARY KEY,  -- exchange order ID
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
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_market ON orders(market_id);

-- Positions: current holdings (upserted on fills/resolutions)
CREATE TABLE positions (
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
CREATE TABLE calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(market_id),
    source          TEXT NOT NULL,
    predicted_prob  REAL NOT NULL,
    market_prob     REAL NOT NULL,
    actual_outcome  INTEGER,  -- 1=YES won, 0=NO won, NULL=unresolved
    resolved_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_calibration_source ON calibration(source);

-- News events: raw news items and relevance assessments
CREATE TABLE news_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,  -- 'newsapi', 'rss'
    title           TEXT NOT NULL,
    url             TEXT,
    content_summary TEXT,
    relevant_markets TEXT,  -- JSON: [market_id, ...] or null
    relevance_score REAL,
    processed       INTEGER DEFAULT 0,
    published_at    TEXT,
    ingested_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_news_processed ON news_events(processed, ingested_at);

-- Daily P&L tracking
CREATE TABLE daily_pnl (
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

-- System state: key-value store for operational state
CREATE TABLE system_state (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);
-- Stores: last_news_fetch, last_market_refresh, kill_switch, etc.
```

---

## 4. Signal Pipeline

The signal pipeline is the core of the system. It transforms raw data into
actionable probability estimates.

### 4.1 Signal Sources

#### Source 1: LLM Forecaster (Primary Signal)

**Purpose:** Structured probability estimation using Claude.

**Tiered model usage (cost optimization):**

| Task                     | Model  | When                           |
|--------------------------|--------|--------------------------------|
| News relevance filter    | Haiku  | Every news item (100s/day)     |
| Standard forecasting     | Sonnet | Scheduled analysis (20-40/day) |
| High-edge deep analysis  | Opus   | Edge > 10% only (1-5/day)     |

All LLM calls go through **Amazon Bedrock**, covered by AWS credits. Zero external LLM cost.

**Two-stage superforecasting prompt (anti-anchoring design):**

Giving the LLM the current market price creates strong anchoring bias. Instead,
we use a two-stage approach: estimate blind first, then adjust.

**Stage 1 — Blind estimate (no market price provided):**

```
SYSTEM: You are a superforecaster. You will be given a question about a future
event and relevant context. Estimate the probability of the YES outcome.

You do NOT know what the market currently thinks. Form your own independent view.

Follow this structured reasoning process:
1. DECOMPOSE: Break the question into key sub-questions
2. BASE RATE: What is the historical base rate for similar events?
3. EVIDENCE FOR: List factors that increase probability
4. EVIDENCE AGAINST: List factors that decrease probability
5. SYNTHESIS: Weigh the evidence and arrive at a final probability

CONTEXT:
Question: {question}
Market end date: {end_date}
Category: {category}

Recent relevant news:
{news_context}

OUTPUT FORMAT (JSON):
{
  "decomposition": "...",
  "base_rate": "...",
  "evidence_for": ["...", "..."],
  "evidence_against": ["...", "..."],
  "synthesis": "...",
  "initial_probability": 0.XX
}
```

**Stage 2 — Calibration adjustment (market + cross-platform prices revealed):**

```
Your independent estimate: {initial_probability}

Now consider this additional information:
- Current market price (YES): {market_price}
- Cross-platform estimates: {cross_platform_prices}

The market reflects the consensus of many participants, some of whom may have
information you don't. But the market can also be wrong, especially:
- Right after news breaks (slow to reprice)
- On niche topics with few informed traders
- When anchored on stale information

Questions to consider:
1. Why might the market differ from your estimate?
2. Do you have information the market might not have priced in?
3. Is the market more likely right, or are you?

OUTPUT FORMAT (JSON):
{
  "market_delta_reasoning": "why my estimate differs from market (or doesn't)",
  "adjustment": "...",
  "final_probability": 0.XX,
  "confidence": 0.XX,
  "edge_reasoning": "why I think the market is wrong (or: I agree with market)"
}
```

**Why two stages:** In testing, single-stage prompts with market price produce
estimates within 2-3% of the market price ~80% of the time — the LLM anchors
regardless of "don't anchor" instructions. Two-stage prompting produces genuinely
independent estimates that diverge when the LLM has real reasoning to back it up.

**Key design choices:**
- Stage 1 is blind — prevents anchoring on market consensus
- Stage 2 reveals market as a "second opinion", not ground truth
- Structured reasoning in both stages reduces hallucination
- Confidence output allows downstream filtering (low confidence → don't trade)
- JSON output for reliable parsing

#### Source 2: Cross-Platform Aggregator

**Purpose:** Aggregate probability estimates from other prediction markets.

**Platforms:**
- Metaculus (API: free, high-quality calibrated forecasters)
- Manifold Markets (API: free, high volume)
- Kalshi (if accessible — API available)

**Matching logic:**
Markets across platforms don't have shared IDs. Matching is done by:
1. **V1 (simple):** Use Haiku to match — "Are these two questions about the same event?"
   Cheap (~$0.001/call), accurate enough, and trivial to implement.
2. **V2 (later, if needed):** Embed market questions, cosine similarity, cache matches.
3. Cache all matches — they rarely change once found.
4. Manual override table for important/high-volume markets.

**Signal output:**
```python
CrossPlatformSignal(
    market_id="poly_abc123",
    sources={
        "metaculus": {"prob": 0.65, "num_forecasters": 234},
        "manifold": {"prob": 0.62, "volume": 15000},
    },
    weighted_avg=0.635,  # weighted by forecaster count / volume
    agreement=0.97,       # how much platforms agree (1.0 = identical)
)
```

**Value:** Cross-platform consensus that diverges from Polymarket price is a
strong signal. If Metaculus (calibrated forecasters) says 65% and Polymarket
says 55%, that's meaningful.

#### Source 3: News Analyzer

**Purpose:** Detect breaking news that should shift probabilities for open markets.

**Pipeline:**
1. Ingest news from NewsAPI, RSS feeds (every 5 min)
2. Haiku filter: "Is this news relevant to any of these markets? {market_list}"
3. If relevant → extract: which market, direction (increases/decreases prob), magnitude
4. Trigger re-estimation for affected markets using Sonnet

**This is the speed edge.** News breaks → market is slow to reprice (minutes to hours
in prediction markets) → we trade the gap.

#### Source 4: Ensemble Combiner

**Purpose:** Combine all signals into a single calibrated probability estimate.

**Method:**
```python
def compute_ensemble(signals: dict[str, Signal]) -> tuple[float, float]:
    """
    Combine available signals into a single probability estimate.
    Handles missing signals by renormalizing weights.

    signals: {"llm": Signal, "cross_platform": Signal | None, "news": Signal | None}
    """
    BASE_WEIGHTS = {
        "llm": 0.50,
        "cross_platform": 0.35,
        "news": 0.15,
    }

    # Only include signals that exist and are fresh
    active = {}
    for source, signal in signals.items():
        if signal is not None and signal.age_minutes < MAX_SIGNAL_AGE[source]:
            active[source] = signal

    if not active:
        return None, 0  # no usable signals — skip this market

    # LLM signal is required — it's our primary edge
    if "llm" not in active:
        return None, 0

    # Renormalize weights to sum to 1.0 across available sources
    total_weight = sum(BASE_WEIGHTS[s] for s in active)
    weights = {s: BASE_WEIGHTS[s] / total_weight for s in active}

    ensemble_prob = sum(weights[s] * active[s].probability for s in active)
    confidence = min(s.confidence for s in active.values())

    return ensemble_prob, confidence
```

**Signal freshness thresholds:**
```python
MAX_SIGNAL_AGE = {
    "llm": 30,             # minutes — rerun LLM if older
    "cross_platform": 60,  # minutes — external platforms update slowly
    "news": 15,            # minutes — news impact decays fast
}
```

**Calibration loop:**
After markets resolve, compare each signal source's predictions to actual outcomes.
Adjust BASE_WEIGHTS to favor more accurate sources. This runs daily and is stored
in the calibration table. Weight updates use exponential moving average to avoid
overreacting to short-term noise.

### 4.2 Signal Pipeline Flow

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│ Scheduled│     │  News   │     │  Cross  │
│  Timer   │     │  Event  │     │Platform │
│ (5 min)  │     │ Trigger │     │  Timer  │
└────┬─────┘     └────┬────┘     └────┬────┘
     │                │               │
     │     ┌──────────┴───────┐       │
     │     │ Is news relevant │       │
     │     │  to any market?  │       │
     │     │    (Haiku)       │       │
     │     └────────┬─────────┘       │
     │              │ yes             │
     ▼              ▼                 ▼
┌────────────────────────────────────────┐
│       For each target market:          │
│                                        │
│  1. Gather current context             │
│     - market price, book state         │
│     - cross-platform prices            │
│     - recent news (if any)             │
│                                        │
│  2. LLM Forecast (Sonnet)             │
│     - structured superforecasting      │
│     - outputs: prob, confidence, why   │
│                                        │
│  3. Ensemble Combine                   │
│     - weighted avg of all signals      │
│     - confidence = min(signal confs)   │
│                                        │
│  4. Store all signals in DB            │
└───────────────────┬────────────────────┘
                    │
                    ▼
            Decision Engine
```

### 4.3 Market Selection

Not all markets are worth analyzing. Filter criteria:

```python
MARKET_FILTERS = {
    "min_volume_24h": 1000,      # USD — skip dead markets
    "min_liquidity": 500,        # USD — need enough depth to trade
    "min_time_to_resolution": 2,  # hours — keep low: near-resolution is high-edge
    "max_time_to_resolution": 90, # days — skip too-far-out markets
    "price_range": (0.10, 0.90), # skip near-certain outcomes
    "categories": None,           # None = all, or ["politics", "crypto"]
}
```

**Why these filters:**
- Dead/illiquid markets: can't trade meaningfully
- About-to-resolve (<2h): too little time for limit orders to fill
- Too far out: high uncertainty, capital locked for too long
- Near-certain: 95% YES market has no room for edge

**NegRisk (multi-outcome) markets:** Markets with `neg_risk=True` are part of a
multi-outcome group (e.g., "Who will win the election?" with candidates A, B, C as
separate YES/NO markets). These have special properties — outcome prices should sum
to ~1.0, and deviations are arbitrageable. For v1, we treat each sub-market as an
independent binary market. Cross-outcome arbitrage is deferred to a later phase.

---

## 5. Decision Engine

The decision engine converts signals into trade actions.

### 5.1 Edge Calculation

```python
def calculate_edge(ensemble_prob, market_price, fee_rate):
    """
    Calculate the expected edge of a trade.

    For BUY YES at market_price P when we estimate true prob Q:
      edge = Q - P - fees

    For BUY NO (equivalent to SELL YES):
      edge = P - Q - fees

    fee_rate is fetched dynamically from the CLOB API (/fee-rate endpoint),
    not hardcoded. Polymarket fees vary — makers often get rebates.

    Returns (net_edge, side). net_edge is always positive if there's an edge.
    """
    if ensemble_prob > market_price:
        raw_edge = ensemble_prob - market_price
        side = "BUY_YES"
    else:
        raw_edge = market_price - ensemble_prob
        side = "BUY_NO"

    net_edge = raw_edge - fee_rate
    return net_edge, side
```

**Dynamic fee rates:** Fee rate is fetched from the CLOB API `/fee-rate` endpoint
per market and cached hourly. Polymarket fees differ for makers vs takers and can
vary by market. Never hardcode.

**Minimum edge threshold:** Only trade when `net_edge > 0.05` (5 cents after fees).
This is conservative but avoids overtrading on marginal signals.

### 5.2 Position Sizing (Kelly Criterion)

```python
def kelly_size(ensemble_prob, market_price, side, bankroll, fee_rate, kelly_fraction=0.25):
    """
    Fractional Kelly for binary prediction markets.

    For BUY YES at price P, true prob Q:
      f* = (Q - P) / (1 - P)       # fraction of bankroll to spend

    For BUY NO at price (1-P), true prob (1-Q):
      f* = (P - Q) / Q             # fraction of bankroll to spend

    We use quarter-Kelly to reduce variance from noisy edge estimates.

    Example: market YES at 0.40, we estimate 0.55
      f* = (0.55 - 0.40) / (1 - 0.40) = 0.15 / 0.60 = 0.25 (25% of bankroll)
      quarter_kelly = 0.0625 (6.25% of bankroll)
      on $1000 bankroll → $62.50 position
    """
    if side == "BUY_YES":
        edge = ensemble_prob - market_price - fee_rate
        if edge <= 0:
            return 0
        full_kelly = edge / (1 - market_price)
    elif side == "BUY_NO":
        edge = market_price - ensemble_prob - fee_rate
        if edge <= 0:
            return 0
        full_kelly = edge / ensemble_prob
    else:
        return 0

    sized = full_kelly * kelly_fraction * bankroll
    return min(sized, MAX_POSITION_SIZE)  # hard cap
```

**Why quarter-Kelly:** Full Kelly is mathematically optimal for maximizing log-wealth
but assumes perfect edge estimation. Our edge estimates are noisy. Quarter-Kelly
gives ~75% of the growth rate with dramatically lower variance and drawdown risk.

### 5.3 Order Placement Strategy

```python
# DON'T: market order (pay full spread + slippage)
# DO: limit order at a price that captures most of the edge but still fills

def compute_limit_price(market_price, ensemble_prob, side):
    """
    Place limit order that keeps ~60% of edge for us, gives ~40% to the market.
    This balances fill probability against edge retention.
    """
    edge = abs(ensemble_prob - market_price)

    if side == "BUY_YES":
        # Bid above market but below our estimate
        limit_price = market_price + (edge * 0.4)
    elif side == "BUY_NO":
        # Offer below market but above our estimate
        limit_price = market_price - (edge * 0.4)

    # Round to tick size
    return round(limit_price, 2)
```

**Order type: GTC with timeout.** Place as Good-Til-Cancelled, but cancel after
15 minutes if unfilled. Re-evaluate whether the edge still exists before replacing.

### 5.4 Position Exit Strategy

Entering positions is only half the problem. The system must also decide when to EXIT
an existing position before market resolution.

**Exit triggers (checked every analysis cycle for all open positions):**

```python
def should_exit(position, current_signal, market_price, fee_rate):
    """
    Evaluate whether to close or reduce an existing position.
    Returns: ("hold", "reduce", or "close") + reason
    """
    # 1. Edge reversal: our estimate flipped to the other side
    #    e.g., we hold YES but now estimate prob < market_price
    if position.side == "YES" and current_signal.probability < market_price - fee_rate:
        return "close", "edge_reversed"
    if position.side == "NO" and current_signal.probability > market_price + fee_rate:
        return "close", "edge_reversed"

    # 2. Edge decayed: we still agree with our side but edge is now < min threshold
    #    Don't exit immediately — only if edge has been below threshold for 2+ cycles
    remaining_edge = abs(current_signal.probability - market_price) - fee_rate
    if remaining_edge < MIN_EDGE_THRESHOLD:
        return "reduce", "edge_decayed"

    # 3. Stop loss: position has lost more than X% of its cost basis
    if position.unrealized_pnl < -(position.avg_cost * STOP_LOSS_PCT):
        return "close", "stop_loss"

    # 4. Time decay: market resolving soon, reduce uncertainty exposure
    if market.hours_to_resolution < 2 and remaining_edge < 0.10:
        return "close", "time_decay"

    return "hold", None
```

**Exit execution:** Exits use the same order placement logic (limit orders, not
market orders) unless it's a stop-loss, which uses FOK (Fill-Or-Kill) for urgency.

**Capital reallocation:** When a position is closed, the freed capital is immediately
available for the next analysis cycle to deploy elsewhere. The system does NOT
automatically rebalance — it waits for a signal with sufficient edge.

### 5.5 Decision Flow

```
Signal arrives (ensemble_prob, confidence, market_price, market_id)
    │
    ├── Do we have an existing position in this market?
    │   │
    │   YES ──▶ Run should_exit() ──▶ CLOSE/REDUCE/HOLD
    │   │       (exit logic takes priority over entry)
    │   │
    │   NO (or HOLD from exit check)
    │   │
    ├── confidence < 0.3? ──────────────────── HOLD (low confidence)
    │
    ├── edge < 0.05? ──────────────────────── HOLD (insufficient edge)
    │
    ├── Compute kelly_size
    │   ├── Subtract existing position from kelly target
    │   │   e.g., kelly says $80, already hold $50 → only add $30
    │   └── If remaining_size < MIN_ORDER_SIZE → HOLD
    │
    ├── risk_checks_pass? ──── NO ──────────── HOLD (risk limit hit)
    │                          │
    │                         YES
    │                          │
    ▼                          ▼
  Log HOLD decision        EXECUTE TRADE
  with full reasoning      ├── compute limit price
                           ├── submit order
                           ├── log decision + order
                           └── send Telegram alert
```

---

## 6. Risk Framework

Three-layer defense. Each layer can independently veto a trade.

### Layer 1: Pre-Trade Checks (per-decision)

```python
RISK_LIMITS = {
    # Position limits
    "max_position_per_market": 100,     # USD max in any single market
    "max_positions_total": 10,          # max number of open positions
    "max_exposure_total": 500,          # USD total capital at risk

    # Category concentration
    "max_exposure_per_category": 200,   # USD max in politics, crypto, etc.

    # Loss limits
    "max_daily_loss": 50,               # USD — hard stop for the day
    "max_weekly_loss": 150,             # USD — reduce size or pause
    "max_drawdown_pct": 0.15,           # 15% from peak equity

    # Data freshness
    "max_signal_age_minutes": 30,       # don't trade on old signals
    "max_price_age_seconds": 60,        # don't trade on stale prices

    # Operational
    "min_bankroll": 100,                # USD — stop trading if below this
    "max_orders_per_hour": 20,          # rate limit ourselves
}
```

Every check returns pass/fail with a reason. All results are logged in the
decision record regardless of outcome.

### Layer 2: Portfolio Monitoring (continuous)

Runs every 30 seconds:
- Recalculate unrealized P&L for all positions (mark to market)
- Update total exposure by category
- Track running daily P&L
- Check drawdown from peak equity

If any limit is breached, flag for Layer 3.

### Layer 3: Circuit Breakers (emergency)

```python
CIRCUIT_BREAKERS = {
    "kill_switch": False,               # manual override — stops everything
    "daily_loss_exceeded": False,       # auto-set when daily loss > max
    "consecutive_losses": 5,            # pause after N consecutive losing trades
    "data_stale_minutes": 10,           # pause if no fresh data for 10 min
    "api_errors_threshold": 5,          # pause after N consecutive API errors
}
```

When a circuit breaker trips:
1. Cancel all open orders immediately
2. Send Telegram alert with reason
3. Log the event
4. No new trades until manually cleared (for loss-based breakers)
   or auto-clear when condition resolves (for data staleness)

### 6.1 Position Reconciliation

**On startup and every 15 minutes:**
1. Fetch actual positions from Polymarket (CLOB API `/data/positions`)
2. Compare with local position table
3. If mismatch: log warning, update local to match exchange (exchange is truth)
4. If large mismatch: trip circuit breaker, alert via Telegram

**Why this matters:** Local state can drift due to missed fills, restarts, or bugs.
The exchange is always the source of truth for what you actually hold.

### 6.2 Market Resolution Handling

When a market resolves, the system must settle positions and record outcomes
for calibration. This is checked every market data poll cycle (60s).

**Resolution flow:**

```
Gamma API reports market as resolved
    │
    ▼
1. IDENTIFY affected positions
   └── Query positions table for this market_id
    │
    ▼
2. SETTLE positions
   ├── Winning tokens (YES on YES outcome, NO on NO outcome) → $1.00 each
   ├── Losing tokens → $0.00
   ├── realized_pnl += (payout - avg_cost) * size
   └── Remove position from active positions table
    │
    ▼
3. RECORD calibration data
   ├── For each signal ever generated for this market:
   │   INSERT INTO calibration (market_id, source, predicted_prob,
   │       market_prob, actual_outcome)
   └── This feeds the ensemble weight calibration loop
    │
    ▼
4. CANCEL any open orders for this market
    │
    ▼
5. UPDATE daily P&L
    │
    ▼
6. LOG + ALERT
   ├── Structured log: market, outcome, our prediction, P&L
   └── Telegram: "Market resolved: {question} → {outcome}, P&L: ${amount}"
```

**Why this matters for calibration:** Resolution is the only ground truth we get.
Every resolved market is a data point for measuring which signal sources are
accurate and adjusting ensemble weights. Without this flow, the system can never
learn or improve.

---

## 7. Execution Engine

### 7.1 Interface

```python
from abc import ABC, abstractmethod

class Executor(ABC):
    @abstractmethod
    async def place_order(self, decision: Decision) -> Order:
        """Submit an order based on a trade decision."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        pass

    @abstractmethod
    async def get_open_orders(self) -> list[Order]:
        """Get all currently open orders."""
        pass

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get current positions from the exchange."""
        pass

    @abstractmethod
    async def get_balance(self) -> float:
        """Get available USDC balance."""
        pass
```

### 7.2 LiveExecutor

Wraps `py-clob-client` with:
- Automatic retry with exponential backoff on transient errors
- Rate limiting (respect API limits)
- Order status tracking via polling (check every 10s for open orders)
- Automatic allowance management (USDC + CTF token approvals)
- Fee rate caching (fetch once per hour from `/fee-rate`)

```python
class LiveExecutor(Executor):
    def __init__(self, private_key: str, chain_id: int = 137):
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=chain_id,
        )
        # Derive API creds on init
        self.client.set_api_creds(self.client.derive_api_key())
```

### 7.3 PaperExecutor

Simulates execution against real market data:
- Market orders: fill at current best bid/ask + slippage estimate
- Limit orders: fill when market price crosses limit price
- Tracks simulated positions and P&L identically to live
- Applies realistic fees (same fee schedule as Polymarket)

```python
class PaperExecutor(Executor):
    def __init__(self):
        self.simulated_orders: dict[str, Order] = {}
        self.simulated_positions: dict[str, Position] = {}
        self.simulated_balance: float = INITIAL_PAPER_BALANCE

    async def place_order(self, decision: Decision) -> Order:
        order = self._create_simulated_order(decision)
        # For aggressive orders (crossing spread), fill immediately
        # For passive orders, mark as open and check on each price update
        if self._would_fill_immediately(order):
            self._simulate_fill(order)
        else:
            self.simulated_orders[order.id] = order
        return order
```

### 7.4 Mode Switching

```python
# In config/settings.py
EXECUTION_MODE = "paper"  # or "live"

# In main.py
if settings.EXECUTION_MODE == "live":
    executor = LiveExecutor(private_key=settings.PRIVATE_KEY)
else:
    executor = PaperExecutor()

# Everything upstream is identical. The decision engine doesn't know or care.
```

---

## 8. Main Loop (Orchestrator)

The orchestrator is a single async Python process managing concurrent tasks.

```python
async def main():
    # ── STARTUP ──────────────────────────────────────────
    db = Database("agent.db")
    store = DataStore(db)
    executor = create_executor(settings.EXECUTION_MODE)

    # Reconcile positions on startup
    exchange_positions = await executor.get_positions()
    store.reconcile_positions(exchange_positions)

    # ── CONCURRENT TASKS ─────────────────────────────────
    async with asyncio.TaskGroup() as tg:

        # Data ingestion (continuous)
        tg.create_task(market_data_loop(store))        # every 60s: poll markets + prices
        tg.create_task(news_ingestion_loop(store))     # every 5m: fetch + classify news
        tg.create_task(cross_platform_loop(store))     # every 10m: fetch external prices
        tg.create_task(websocket_listener(store))      # continuous: real-time price updates

        # Signal generation (triggered)
        tg.create_task(scheduled_analysis_loop(        # every 5m: analyze target markets
            store, signal_engine, decision_engine, executor
        ))
        tg.create_task(news_triggered_analysis(        # on-demand: breaking news
            store, signal_engine, decision_engine, executor
        ))

        # Operations (continuous)
        tg.create_task(order_management_loop(executor, store))  # every 10s: check fills
        tg.create_task(risk_monitoring_loop(store, executor))   # every 30s: risk checks
        tg.create_task(position_reconciliation_loop(            # every 15m: reconcile
            executor, store
        ))
        tg.create_task(health_check_loop(store, executor))     # every 60s: self-check
        tg.create_task(daily_operations(store))                 # daily: P&L report, backup
```

### 8.1 Task Timing Summary

| Task                      | Interval   | Purpose                           |
|---------------------------|------------|-----------------------------------|
| Market data poll          | 60s        | Refresh prices, books, metadata   |
| News ingestion            | 5 min      | Fetch and classify news           |
| Cross-platform fetch      | 10 min     | External probability estimates    |
| WebSocket listener        | Continuous | Real-time price/trade updates     |
| Scheduled analysis        | 5 min      | LLM forecasting on target markets |
| News-triggered analysis   | On-demand  | Fast re-estimation on breaking news|
| Order management          | 10s        | Check fills, cancel stale orders  |
| Risk monitoring           | 30s        | Update P&L, check limits          |
| Position reconciliation   | 15 min     | Sync local state with exchange    |
| Health check              | 60s        | Verify data freshness, API health |
| Daily ops                 | Daily      | P&L report, S3 backup, calibration|

---

## 9. Observability

### 9.1 Structured Logging

Every significant event is logged as structured JSON:

```json
{
  "ts": "2026-03-20T14:30:00Z",
  "level": "INFO",
  "event": "trade_decision",
  "market_id": "abc123",
  "action": "BUY_YES",
  "ensemble_prob": 0.65,
  "market_prob": 0.55,
  "edge": 0.08,
  "size_usd": 25.00,
  "risk_checks": "all_pass",
  "signals": ["sig_001", "sig_002", "sig_003"]
}
```

Log to stdout → CloudWatch Logs (on EC2) or local file (dev).

### 9.2 Telegram Alerts

**Real-time alerts for:**
- Trade executed (market, side, size, price, edge)
- Order filled / partially filled
- Risk limit breached
- Circuit breaker tripped
- API errors (consecutive)
- Data feed stale

**Daily digest:**
- Starting/ending balance
- Day's P&L (realized + unrealized)
- Number of trades, win rate
- Biggest winner and loser
- Current open positions
- Signal accuracy summary

### 9.3 Health Checks

Every 60 seconds, verify:
- [ ] Market data is fresh (last update < 2 min ago)
- [ ] News feed is alive (last fetch < 10 min ago)
- [ ] WebSocket is connected
- [ ] CLOB API is responding
- [ ] USDC balance matches expectations
- [ ] No orphaned orders (submitted but not tracked)
- [ ] SQLite DB is writable
- [ ] System clock is synced (< 5s drift)

If 3+ checks fail, trip the data-stale circuit breaker.

---

## 10. Deployment

### 10.1 AWS Infrastructure

```
EC2 t3.medium (2 vCPU, 4GB RAM)
├── Ubuntu 22.04
├── Python 3.12 + venv
├── systemd service: polymarket-agent
├── Logs → CloudWatch via awslogs agent
└── Security group: outbound only (no inbound)

S3 bucket: polymarket-agent-data
├── /backups/          # nightly SQLite snapshots
├── /raw-events/       # archived market data
└── /signals/          # signal history (Parquet)

CloudWatch
├── Log group: /polymarket-agent
├── Alarm: process died (no logs for 5 min)
└── Alarm: error rate > 10/min

IAM Role (attached to EC2)
├── s3:PutObject, s3:GetObject on bucket
├── logs:PutLogEvents on log group
└── bedrock:InvokeModel* on us.anthropic.claude-* (for LLM calls)
```

### 10.2 Process Management

```ini
# /etc/systemd/system/polymarket-agent.service
[Unit]
Description=Polymarket Trading Agent
After=network.target

[Service]
Type=simple
User=agent
WorkingDirectory=/home/agent/polymarket-agent
ExecStart=/home/agent/polymarket-agent/.venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**On crash:** systemd restarts in 10s. On startup, the agent:
1. Checks system_state table for kill_switch
2. Reconciles positions with exchange
3. Cancels any orphaned open orders
4. Resumes normal operation

### 10.3 Secrets Management

```bash
# Stored as environment variables (via systemd EnvironmentFile)
POLYMARKET_PRIVATE_KEY=0x...
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...          # or use EC2 instance IAM role (preferred)
AWS_SECRET_ACCESS_KEY=...
NEWSAPI_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Not in code. Not in git. Loaded from `/home/agent/.env` with restrictive permissions.

---

## 11. Project Structure

```
polymarket-agent/
│
├── main.py                     # Entry point: arg parsing, orchestrator setup
│
├── config/
│   ├── __init__.py
│   ├── settings.py             # All configuration (from env + defaults)
│   └── constants.py            # Contract addresses, API URLs, enums
│
├── core/
│   ├── __init__.py
│   ├── models.py               # Dataclasses: Market, Signal, Decision, Order, Position
│   ├── events.py               # Simple pub/sub for internal event routing
│   └── db.py                   # SQLite connection, schema init, migrations
│
├── data/
│   ├── __init__.py
│   ├── polymarket.py           # Gamma API + CLOB REST client wrapper
│   ├── websocket.py            # WebSocket subscriber + reconnection
│   ├── news.py                 # NewsAPI + RSS ingestor
│   └── cross_platform.py      # Metaculus, Manifold fetchers + market matching
│
├── signals/
│   ├── __init__.py
│   ├── forecaster.py           # LLM superforecasting (Claude via Bedrock)
│   ├── news_analyzer.py        # News relevance + probability impact
│   ├── cross_platform.py       # External probability aggregation
│   ├── ensemble.py             # Weighted signal combination
│   └── calibration.py          # Track predicted vs actual, adjust weights
│
├── strategy/
│   ├── __init__.py
│   ├── edge.py                 # Edge calculation
│   ├── sizing.py               # Kelly criterion + position sizing
│   └── decision.py             # Trade decision logic (the brain)
│
├── risk/
│   ├── __init__.py
│   ├── limits.py               # Risk limit definitions and checks
│   ├── monitor.py              # Continuous portfolio risk monitoring
│   └── circuit_breaker.py      # Emergency stop logic
│
├── execution/
│   ├── __init__.py
│   ├── interface.py            # Abstract Executor base class
│   ├── live.py                 # LiveExecutor (py-clob-client wrapper)
│   ├── paper.py                # PaperExecutor (simulator)
│   ├── orders.py               # Order lifecycle management
│   └── portfolio.py            # Position tracking + P&L calculation
│
├── ops/
│   ├── __init__.py
│   ├── health.py               # Health checks
│   ├── reconcile.py            # Position reconciliation with exchange
│   ├── telegram.py             # Alert notifications
│   └── backup.py               # S3 backup logic
│
├── backtest/
│   ├── __init__.py
│   ├── data_loader.py          # Load historical data (Becker dataset + API)
│   ├── replay.py               # Replay engine for offline testing
│   └── evaluator.py            # Backtest metrics + reporting
│
├── schema.sql                  # SQLite schema (from Section 3.2)
├── requirements.txt
├── .env.example                # Template for secrets
├── Makefile                    # Common commands (run, test, backtest, deploy)
└── README.md
```

---

## 12. Development Phases

### Phase 1: Foundation + Signal Validation (Week 1-2)
- Project scaffold, config, data models
- Polymarket data client (Gamma + CLOB)
- SQLite persistence layer
- LLM forecaster with superforecasting prompt
- **Signal validation script:** test Claude against 200+ resolved markets
- Answer: "Does this approach have edge?"

### Phase 2: Full Signal Pipeline (Week 3)
- News ingestor (NewsAPI + RSS)
- Cross-platform fetcher (Metaculus + Manifold)
- Market matching (cross-platform)
- Ensemble combiner
- News-triggered analysis

### Phase 3: Decision + Execution (Week 4)
- Edge calculator + Kelly sizing
- Risk framework (all 3 layers)
- PaperExecutor (simulator)
- LiveExecutor (py-clob-client wrapper)
- Order lifecycle management
- Position tracking

### Phase 4: Operations (Week 5)
- Telegram alerting
- Health checks
- Position reconciliation
- S3 backup
- systemd service + EC2 deployment
- CloudWatch logging + alarms

### Phase 5: Live + Iterate (Week 6+)
- Paper trade for 1-2 weeks, validate
- Tiny live ($5-10/trade)
- Calibration tracking + weight adjustment
- Add data sources based on what's working
- Scale capital on profitable strategies

---

## 13. Key Metrics to Track

### Signal Quality
- **Brier score** per signal source (lower = better calibrated)
- **Calibration plot:** predicted probability vs actual frequency
- **Edge accuracy:** when we predict 60% and market says 50%, how often does YES win?
- **Signal latency:** time from data input to signal output

### Trading Performance
- **P&L:** daily, weekly, cumulative (realized + unrealized)
- **Win rate:** % of trades that are profitable
- **Average edge captured:** actual return vs predicted edge
- **Sharpe ratio:** risk-adjusted returns

### Operational
- **Uptime:** % of time the bot is running and healthy
- **Fill rate:** % of orders that fill
- **Slippage:** actual fill price vs intended price
- **API error rate:** failed calls / total calls
