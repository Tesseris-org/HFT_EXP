"""Paper trading executor — simulates order fills against real market data."""

import logging
import uuid
from datetime import datetime
from typing import Optional

from config.constants import DEFAULT_FEE_RATE
from config.settings import INITIAL_PAPER_BALANCE
from core.models import Decision, Order, Position
from execution.interface import Executor
from strategy.sizing import compute_limit_price

logger = logging.getLogger(__name__)


class PaperExecutor(Executor):
    """Simulates trade execution for paper trading mode."""

    def __init__(self, initial_balance: float = INITIAL_PAPER_BALANCE):
        self.balance = initial_balance
        self.positions: dict[str, Position] = {}  # keyed by "market_id:token_id"
        self.open_orders: dict[str, Order] = {}
        self.fill_history: list[Order] = []

    async def place_order(self, decision: Decision) -> Order:
        # Determine token and market price for that token
        if decision.action == "BUY_YES":
            token_id = "yes"
            side = "BUY"
            market_token_price = decision.market_prob
        elif decision.action == "BUY_NO":
            token_id = "no"
            side = "BUY"
            market_token_price = 1.0 - decision.market_prob
        elif decision.action in ("SELL_YES", "SELL_NO"):
            token_id = "yes" if "YES" in decision.action else "no"
            side = "SELL"
            market_token_price = decision.market_prob if "YES" in decision.action else (1.0 - decision.market_prob)
        else:
            raise ValueError(f"Unknown action: {decision.action}")

        # For paper trading: fill at market price (realistic simulation)
        price = market_token_price
        size = decision.size_usd / price if price > 0 else 0

        order = Order(
            id=f"paper_{uuid.uuid4().hex[:12]}",
            market_id=decision.market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            order_type="GTC",
            status="submitted",
            decision_id=decision.id,
            submitted_at=datetime.utcnow(),
        )

        # Paper trading: always fill immediately at market price
        self._simulate_fill(order)

        logger.info(
            f"Paper order: {side} {token_id.upper()} {order.size:.1f} shares "
            f"@ {order.price:.3f} → {order.status}"
        )
        return order

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self.open_orders:
            self.open_orders[order_id].status = "cancelled"
            self.open_orders[order_id].cancelled_at = datetime.utcnow()
            del self.open_orders[order_id]
            return True
        return False

    async def get_open_orders(self) -> list[Order]:
        return list(self.open_orders.values())

    async def get_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.size > 0]

    async def get_balance(self) -> float:
        return self.balance

    def check_pending_fills(self, market_id: str, current_price: float):
        """Called on each price update to check if passive orders should fill."""
        to_fill = []
        for oid, order in self.open_orders.items():
            if order.market_id != market_id:
                continue
            if order.side == "BUY" and current_price <= order.price:
                to_fill.append(oid)
            elif order.side == "SELL" and current_price >= order.price:
                to_fill.append(oid)

        for oid in to_fill:
            self._simulate_fill(self.open_orders.pop(oid))

    def _would_fill_immediately(self, order: Order, market_price: float) -> bool:
        """Check if the order crosses the spread (would fill immediately)."""
        if order.side == "BUY":
            return order.price >= market_price
        else:
            return order.price <= market_price

    def _simulate_fill(self, order: Order):
        """Simulate a fill: update balance and positions."""
        slippage = 0.005  # 0.5% conservative slippage
        fee = DEFAULT_FEE_RATE

        if order.side == "BUY":
            fill_price = order.price * (1 + slippage)
            cost = order.size * fill_price * (1 + fee)
            if cost > self.balance:
                order.status = "rejected"
                logger.warning(f"Paper order rejected: insufficient balance ({cost:.2f} > {self.balance:.2f})")
                return
            self.balance -= cost
            self._update_position(order.market_id, order.token_id, order.size, fill_price, "BUY")
        else:
            fill_price = order.price * (1 - slippage)
            proceeds = order.size * fill_price * (1 - fee)
            self.balance += proceeds
            self._update_position(order.market_id, order.token_id, -order.size, fill_price, "SELL")

        order.status = "filled"
        order.filled_size = order.size
        order.avg_fill_price = fill_price
        order.filled_at = datetime.utcnow()
        self.fill_history.append(order)

    def _update_position(self, market_id: str, token_id: str,
                         size_delta: float, fill_price: float, side: str):
        key = f"{market_id}:{token_id}"
        pos = self.positions.get(key)

        if pos is None:
            pos = Position(
                market_id=market_id,
                token_id=token_id,
                side="YES" if token_id == "yes" else "NO",
                size=0,
                avg_cost=0,
            )
            self.positions[key] = pos

        if side == "BUY":
            # Update weighted average cost
            total_cost = pos.avg_cost * pos.size + fill_price * size_delta
            pos.size += size_delta
            pos.avg_cost = total_cost / pos.size if pos.size > 0 else 0
        else:
            # Realize P&L on sell
            realized = (fill_price - pos.avg_cost) * abs(size_delta)
            pos.realized_pnl += realized
            pos.size += size_delta  # size_delta is negative for sells

        pos.updated_at = datetime.utcnow()

        # Clean up zero positions
        if pos.size <= 0:
            pos.size = 0
            pos.avg_cost = 0

    # ── Arbitrage Execution ──────────────────────────────

    async def execute_arb(self, opp, invest_usd: float = 50.0) -> dict:
        """
        Execute a multi-leg arbitrage trade.

        For long arb (sum < 1): Buy 1 YES token for each outcome.
        For short arb (sum > 1): Buy 1 NO token for each outcome.

        Args:
            opp: ArbOpportunity from strategy.arbitrage
            invest_usd: Total USD to invest across all legs

        Returns:
            dict with execution results
        """
        if opp.arb_type == "long":
            # Buy YES for every outcome. Cost = sum of YES prices per share.
            cost_per_share = opp.price_sum
            num_shares = invest_usd / cost_per_share
        else:
            # Buy NO for every outcome. Cost = sum of NO prices = num_outcomes - sum(YES)
            no_price_sum = opp.num_outcomes - opp.price_sum
            cost_per_share = no_price_sum
            num_shares = invest_usd / cost_per_share

        orders = []
        total_cost = 0

        for i, price in enumerate(opp.outcome_prices):
            if opp.arb_type == "long":
                token_side = "YES"
                buy_price = price
            else:
                token_side = "NO"
                buy_price = 1.0 - price

            if buy_price <= 0:
                continue

            token_id = opp.outcome_token_ids[i] if i < len(opp.outcome_token_ids) else f"tok_{i}"
            market_id = opp.outcome_market_ids[i] if i < len(opp.outcome_market_ids) else f"mkt_{i}"

            order = Order(
                id=f"arb_{uuid.uuid4().hex[:12]}",
                market_id=market_id,
                token_id=token_id,
                side="BUY",
                size=num_shares,
                price=buy_price,
                order_type="GTC",
                status="submitted",
                submitted_at=datetime.utcnow(),
            )

            self._simulate_fill(order)
            orders.append(order)

            if order.status == "filled":
                total_cost += num_shares * order.avg_fill_price * (1 + DEFAULT_FEE_RATE)

        filled = [o for o in orders if o.status == "filled"]

        # Payout at resolution:
        # Long arb (bought all YES): one YES pays $1 → payout = num_shares * $1
        # Short arb (bought all NO): (n-1) NOs pay $1, 1 pays $0 → payout = num_shares * (n-1)
        if opp.arb_type == "long":
            guaranteed_payout = num_shares * 1.0
        else:
            guaranteed_payout = num_shares * (opp.num_outcomes - 1)
        expected_profit = guaranteed_payout - total_cost

        result = {
            "orders": len(orders),
            "filled": len(filled),
            "total_cost": round(total_cost, 2),
            "num_shares": round(num_shares, 2),
            "guaranteed_payout": round(guaranteed_payout, 2),
            "expected_profit": round(expected_profit, 2),
            "event": opp.event_title,
            "arb_type": opp.arb_type,
        }

        logger.info(
            f"ARB EXECUTED: {opp.event_title[:40]} | "
            f"type={opp.arb_type} legs={len(filled)}/{len(orders)} "
            f"cost=${total_cost:.2f} payout=${guaranteed_payout:.2f} "
            f"profit=${expected_profit:.2f}"
        )

        return result
