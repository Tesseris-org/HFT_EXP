from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Market:
    market_id: str
    question: str
    outcome_yes_id: str
    outcome_no_id: str
    slug: str = ""
    end_date: Optional[datetime] = None
    category: str = ""
    neg_risk: bool = False
    min_tick: float = 0.01
    active: bool = True
    volume: float = 0.0
    liquidity: float = 0.0
    raw_metadata: Optional[dict] = None

    # Live pricing (not persisted in markets table, filled from snapshots/API)
    yes_price: float = 0.0
    no_price: float = 0.0
    spread: float = 0.0

    @classmethod
    def from_gamma(cls, data: dict) -> Market:
        """Parse a market from Gamma API response."""
        end_date = None
        if data.get("endDate"):
            try:
                end_date = datetime.fromisoformat(data["endDate"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # Gamma API returns clobTokenIds as a JSON string: '["id1","id2"]'
        clob_token_ids = data.get("clobTokenIds", "[]")
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = []

        yes_id = clob_token_ids[0] if len(clob_token_ids) > 0 else ""
        no_id = clob_token_ids[1] if len(clob_token_ids) > 1 else ""

        # Parse prices
        yes_price = 0.0
        no_price = 0.0
        outcomes_prices = data.get("outcomePrices", "[]")
        if isinstance(outcomes_prices, str):
            try:
                outcomes_prices = json.loads(outcomes_prices)
            except json.JSONDecodeError:
                outcomes_prices = []
        if len(outcomes_prices) >= 2:
            try:
                yes_price = float(outcomes_prices[0])
                no_price = float(outcomes_prices[1])
            except (ValueError, TypeError):
                pass

        return cls(
            market_id=data.get("conditionId", data.get("id", "")),
            question=data.get("question", ""),
            outcome_yes_id=yes_id,
            outcome_no_id=no_id,
            slug=data.get("slug", ""),
            end_date=end_date,
            category=data.get("groupItemTitle", data.get("category", "")),
            neg_risk=bool(data.get("negRisk", False)),
            min_tick=float(data.get("minimumTickSize", 0.01)),
            active=data.get("active", True) and data.get("closed", False) is False,
            volume=float(data.get("volume", 0) or 0),
            liquidity=float(data.get("liquidity", 0) or 0),
            yes_price=yes_price,
            no_price=no_price,
            spread=abs(yes_price - (1 - no_price)) if yes_price and no_price else 0,
            raw_metadata=data,
        )

    @property
    def resolved(self) -> bool:
        md = self.raw_metadata or {}
        if md.get("resolved"):
            return True
        # Infer from prices: if closed and one price is near 0/1, it's resolved
        if md.get("closed") and (self.yes_price > 0.95 or self.yes_price < 0.05):
            return True
        return False

    @property
    def outcome(self) -> Optional[str]:
        """Return 'Yes' or 'No' if resolved, None otherwise."""
        md = self.raw_metadata or {}
        # Try explicit outcome field first
        if md.get("outcome"):
            return md["outcome"]
        # Infer from prices: YES price near 1.0 = YES won, near 0.0 = NO won
        if md.get("closed") or not self.active:
            if self.yes_price > 0.95:
                return "Yes"
            elif self.yes_price < 0.05:
                return "No"
        return None


@dataclass
class Signal:
    market_id: str
    source: str  # 'llm_forecaster', 'cross_platform', 'news', 'ensemble'
    probability: float
    confidence: float = 0.0
    reasoning: str = ""
    context_json: str = ""
    model: str = ""
    latency_ms: int = 0
    created_at: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def age_minutes(self) -> float:
        if not self.created_at:
            return float("inf")
        delta = datetime.utcnow() - self.created_at
        return delta.total_seconds() / 60


@dataclass
class Decision:
    market_id: str
    action: str  # 'BUY_YES', 'BUY_NO', 'SELL_YES', 'SELL_NO', 'HOLD'
    ensemble_prob: float
    market_prob: float
    edge: float
    size_usd: float = 0.0
    kelly_fraction: float = 0.0
    risk_checks: dict = field(default_factory=dict)
    signals_used: list[int] = field(default_factory=list)
    reason: str = ""
    executed: bool = False
    created_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class Order:
    id: str
    market_id: str
    token_id: str
    side: str  # 'BUY' or 'SELL'
    size: float
    price: float
    order_type: str = "GTC"
    status: str = "pending"  # pending, submitted, partial, filled, cancelled, rejected
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    decision_id: Optional[int] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class Position:
    market_id: str
    token_id: str
    side: str  # 'YES' or 'NO'
    size: float = 0.0
    avg_cost: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    updated_at: Optional[datetime] = None

    @property
    def cost_basis(self) -> float:
        return self.size * self.avg_cost


@dataclass
class CalibrationRecord:
    market_id: str
    source: str
    predicted_prob: float
    market_prob: float
    actual_outcome: Optional[bool] = None
    resolved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    id: Optional[int] = None
