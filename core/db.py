import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.models import Market, Signal, Decision, Order, Position, CalibrationRecord

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        schema = SCHEMA_PATH.read_text()
        self.conn.executescript(schema)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ── Markets ──────────────────────────────────────────

    def upsert_market(self, m: Market):
        self.conn.execute(
            """INSERT INTO markets (market_id, question, slug, outcome_yes_id, outcome_no_id,
                   end_date, category, neg_risk, min_tick, active, volume, liquidity, raw_metadata,
                   updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(market_id) DO UPDATE SET
                   question=excluded.question, slug=excluded.slug,
                   outcome_yes_id=excluded.outcome_yes_id, outcome_no_id=excluded.outcome_no_id,
                   end_date=excluded.end_date, category=excluded.category,
                   neg_risk=excluded.neg_risk, min_tick=excluded.min_tick,
                   active=excluded.active, volume=excluded.volume,
                   liquidity=excluded.liquidity, raw_metadata=excluded.raw_metadata,
                   updated_at=datetime('now')""",
            (
                m.market_id, m.question, m.slug, m.outcome_yes_id, m.outcome_no_id,
                m.end_date.isoformat() if m.end_date else None,
                m.category, int(m.neg_risk), m.min_tick, int(m.active),
                m.volume, m.liquidity,
                json.dumps(m.raw_metadata) if m.raw_metadata else None,
            ),
        )
        self.conn.commit()

    def upsert_markets_batch(self, markets: list):
        """Upsert multiple markets in a single transaction — much faster than individual commits."""
        rows = []
        for m in markets:
            rows.append((
                m.market_id, m.question, m.slug, m.outcome_yes_id, m.outcome_no_id,
                m.end_date.isoformat() if m.end_date else None,
                m.category, int(m.neg_risk), m.min_tick, int(m.active),
                m.volume, m.liquidity,
                json.dumps(m.raw_metadata) if m.raw_metadata else None,
            ))
        self.conn.executemany(
            """INSERT INTO markets (market_id, question, slug, outcome_yes_id, outcome_no_id,
                   end_date, category, neg_risk, min_tick, active, volume, liquidity, raw_metadata,
                   updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(market_id) DO UPDATE SET
                   question=excluded.question, slug=excluded.slug,
                   outcome_yes_id=excluded.outcome_yes_id, outcome_no_id=excluded.outcome_no_id,
                   end_date=excluded.end_date, category=excluded.category,
                   neg_risk=excluded.neg_risk, min_tick=excluded.min_tick,
                   active=excluded.active, volume=excluded.volume,
                   liquidity=excluded.liquidity, raw_metadata=excluded.raw_metadata,
                   updated_at=datetime('now')""",
            rows,
        )
        self.conn.commit()  # single commit for all 2000 rows

    def get_market(self, market_id: str) -> Optional[Market]:
        row = self.conn.execute(
            "SELECT * FROM markets WHERE market_id = ?", (market_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_market(row)

    def get_active_markets(self) -> list[Market]:
        rows = self.conn.execute(
            "SELECT * FROM markets WHERE active = 1"
        ).fetchall()
        return [self._row_to_market(r) for r in rows]

    def _row_to_market(self, row: sqlite3.Row) -> Market:
        raw = json.loads(row["raw_metadata"]) if row["raw_metadata"] else None
        end_date = None
        if row["end_date"]:
            try:
                end_date = datetime.fromisoformat(row["end_date"])
            except ValueError:
                pass

        # Parse prices from stored raw_metadata (outcomePrices not in schema columns)
        yes_price, no_price = 0.0, 0.0
        if raw:
            prices = raw.get("outcomePrices", [])
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except (json.JSONDecodeError, ValueError):
                    prices = []
            if len(prices) >= 2:
                try:
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                except (ValueError, TypeError):
                    pass

        return Market(
            market_id=row["market_id"],
            question=row["question"],
            slug=row["slug"] or "",
            outcome_yes_id=row["outcome_yes_id"],
            outcome_no_id=row["outcome_no_id"],
            end_date=end_date,
            category=row["category"] or "",
            neg_risk=bool(row["neg_risk"]),
            min_tick=row["min_tick"],
            active=bool(row["active"]),
            volume=row["volume"],
            liquidity=row["liquidity"],
            raw_metadata=raw,
            yes_price=yes_price,
            no_price=no_price,
        )

    # ── Snapshots ────────────────────────────────────────

    def insert_snapshot(self, market_id: str, yes_price: float, no_price: float,
                        spread: float = 0, best_bid: float = 0, best_ask: float = 0,
                        volume_24h: float = 0, source: str = "polymarket"):
        self.conn.execute(
            """INSERT INTO market_snapshots
               (market_id, yes_price, no_price, spread, best_bid, best_ask, volume_24h, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (market_id, yes_price, no_price, spread, best_bid, best_ask, volume_24h, source),
        )
        self.conn.commit()

    # ── Signals ──────────────────────────────────────────

    def insert_signal(self, s: Signal) -> int:
        cursor = self.conn.execute(
            """INSERT INTO signals (market_id, source, probability, confidence,
                   reasoning, context_json, model, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (s.market_id, s.source, s.probability, s.confidence,
             s.reasoning, s.context_json, s.model, s.latency_ms),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_latest_signal(self, market_id: str, source: str) -> Optional[Signal]:
        row = self.conn.execute(
            """SELECT * FROM signals
               WHERE market_id = ? AND source = ?
               ORDER BY created_at DESC LIMIT 1""",
            (market_id, source),
        ).fetchone()
        if not row:
            return None
        return self._row_to_signal(row)

    def _row_to_signal(self, row: sqlite3.Row) -> Signal:
        created = None
        if row["created_at"]:
            try:
                created = datetime.fromisoformat(row["created_at"])
            except ValueError:
                pass
        return Signal(
            id=row["id"],
            market_id=row["market_id"],
            source=row["source"],
            probability=row["probability"],
            confidence=row["confidence"] or 0,
            reasoning=row["reasoning"] or "",
            context_json=row["context_json"] or "",
            model=row["model"] or "",
            latency_ms=row["latency_ms"] or 0,
            created_at=created,
        )

    # ── Decisions ────────────────────────────────────────

    def insert_decision(self, d: Decision) -> int:
        cursor = self.conn.execute(
            """INSERT INTO decisions (market_id, action, ensemble_prob, market_prob,
                   edge, size_usd, kelly_fraction, risk_checks, signals_used, reason, executed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d.market_id, d.action, d.ensemble_prob, d.market_prob,
                d.edge, d.size_usd, d.kelly_fraction,
                json.dumps(d.risk_checks), json.dumps(d.signals_used),
                d.reason, int(d.executed),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    # ── Orders ───────────────────────────────────────────

    def insert_order(self, o: Order):
        self.conn.execute(
            """INSERT INTO orders (id, market_id, token_id, side, size, price,
                   order_type, status, decision_id, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                o.id, o.market_id, o.token_id, o.side, o.size, o.price,
                o.order_type, o.status, o.decision_id,
                o.submitted_at.isoformat() if o.submitted_at else None,
            ),
        )
        self.conn.commit()

    def update_order_status(self, order_id: str, status: str,
                            filled_size: float = 0, avg_fill_price: float = 0):
        now = datetime.utcnow().isoformat()
        filled_at = now if status == "filled" else None
        cancelled_at = now if status == "cancelled" else None
        self.conn.execute(
            """UPDATE orders SET status=?, filled_size=?, avg_fill_price=?,
                   filled_at=?, cancelled_at=?
               WHERE id=?""",
            (status, filled_size, avg_fill_price, filled_at, cancelled_at, order_id),
        )
        self.conn.commit()

    def get_open_orders(self) -> list[Order]:
        rows = self.conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending', 'submitted', 'partial')"
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        return Order(
            id=row["id"],
            market_id=row["market_id"],
            token_id=row["token_id"],
            side=row["side"],
            size=row["size"],
            price=row["price"],
            order_type=row["order_type"],
            status=row["status"],
            filled_size=row["filled_size"] or 0,
            avg_fill_price=row["avg_fill_price"] or 0,
            decision_id=row["decision_id"],
        )

    # ── Positions ────────────────────────────────────────

    def upsert_position(self, p: Position):
        self.conn.execute(
            """INSERT INTO positions (market_id, token_id, side, size, avg_cost,
                   unrealized_pnl, realized_pnl, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(market_id, token_id) DO UPDATE SET
                   side=excluded.side, size=excluded.size, avg_cost=excluded.avg_cost,
                   unrealized_pnl=excluded.unrealized_pnl,
                   realized_pnl=excluded.realized_pnl,
                   updated_at=datetime('now')""",
            (p.market_id, p.token_id, p.side, p.size, p.avg_cost,
             p.unrealized_pnl, p.realized_pnl),
        )
        self.conn.commit()

    def get_position(self, market_id: str, token_id: str) -> Optional[Position]:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE market_id=? AND token_id=?",
            (market_id, token_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_position(row)

    def get_all_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE size > 0"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            market_id=row["market_id"],
            token_id=row["token_id"],
            side=row["side"],
            size=row["size"],
            avg_cost=row["avg_cost"],
            unrealized_pnl=row["unrealized_pnl"],
            realized_pnl=row["realized_pnl"],
        )

    # ── Calibration ──────────────────────────────────────

    def insert_calibration(self, c: CalibrationRecord):
        self.conn.execute(
            """INSERT INTO calibration (market_id, source, predicted_prob, market_prob,
                   actual_outcome, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (c.market_id, c.source, c.predicted_prob, c.market_prob,
             int(c.actual_outcome) if c.actual_outcome is not None else None,
             c.resolved_at.isoformat() if c.resolved_at else None),
        )
        self.conn.commit()

    def get_calibration_records(self, source: Optional[str] = None) -> list[CalibrationRecord]:
        if source:
            rows = self.conn.execute(
                "SELECT * FROM calibration WHERE source = ? AND actual_outcome IS NOT NULL",
                (source,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM calibration WHERE actual_outcome IS NOT NULL"
            ).fetchall()
        return [
            CalibrationRecord(
                id=r["id"],
                market_id=r["market_id"],
                source=r["source"],
                predicted_prob=r["predicted_prob"],
                market_prob=r["market_prob"],
                actual_outcome=bool(r["actual_outcome"]) if r["actual_outcome"] is not None else None,
            )
            for r in rows
        ]

    # ── System State ─────────────────────────────────────

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str):
        self.conn.execute(
            """INSERT INTO system_state (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
            (key, value),
        )
        self.conn.commit()
