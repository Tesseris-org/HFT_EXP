"""Position exit logic - when to close or reduce positions."""

import logging
from typing import Optional, Tuple

from config.constants import DEFAULT_FEE_RATE, STOP_LOSS_PCT, MIN_EDGE_THRESHOLD
from core.models import Position, Market, Signal

logger = logging.getLogger(__name__)


class ExitReason:
    """Exit reason codes."""
    EDGE_REVERSED = "edge_reversed"
    EDGE_DECAYED = "edge_decayed"
    STOP_LOSS = "stop_loss"
    TIME_DECAY = "time_decay"
    TARGET_REACHED = "target_reached"


def should_exit_position(
    position: Position,
    market: Market,
    current_signal: Signal,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Determine if a position should be exited.

    Returns:
        (should_exit, exit_type, reason)
        - should_exit: True if position should be closed/reduced
        - exit_type: "close" or "reduce" or None
        - reason: Human-readable explanation
    """

    # ── 0. Minimum Hold Time: Don't exit too quickly ──
    from datetime import datetime, timezone, timedelta
    if position.updated_at:
        try:
            if position.updated_at.tzinfo is None:
                pos_time = position.updated_at.replace(tzinfo=timezone.utc)
            else:
                pos_time = position.updated_at

            hold_duration = (datetime.now(timezone.utc) - pos_time).total_seconds() / 3600

            # Require at least 1 hour hold before considering ANY exit (except stop-loss)
            if hold_duration < 1.0:
                return False, None, None
        except Exception:
            pass  # If time comparison fails, continue with other checks

    # ── 1. Edge Reversal: Our estimate STRONGLY flipped to the other side ──
    # Only exit if edge reversed by MORE than 10% (not just a small move)

    if position.side == "YES":
        # Holding YES position - only exit if we now think NO has 10%+ edge
        if current_signal.probability < market.yes_price - 0.10:
            edge_diff = market.yes_price - current_signal.probability
            reason = (
                f"Strong edge reversal: was bullish, now estimate {current_signal.probability:.1%} "
                f"vs market {market.yes_price:.1%} (delta: {edge_diff:.1%})"
            )
            return True, "close", ExitReason.EDGE_REVERSED

    elif position.side == "NO":
        # Holding NO position - only exit if we now think YES has 10%+ edge
        if current_signal.probability > market.yes_price + 0.10:
            edge_diff = current_signal.probability - market.yes_price
            reason = (
                f"Strong edge reversal: was bearish, now estimate {current_signal.probability:.1%} "
                f"vs market {market.yes_price:.1%} (delta: {edge_diff:.1%})"
            )
            return True, "close", ExitReason.EDGE_REVERSED

    # ── 2. Edge Decayed: DISABLED (too aggressive) ──
    # Small edge decay is normal market noise - don't exit on it
    # Positions need time to play out

    # ── 3. Stop Loss: Only on catastrophic losses ──
    unrealized_pnl_pct = position.unrealized_pnl / (position.size * position.avg_cost) if position.size * position.avg_cost > 0 else 0

    # Raised to 50% loss (from 30%) - give positions room to breathe
    if unrealized_pnl_pct < -0.50:
        reason = (
            f"Stop loss triggered: {unrealized_pnl_pct:.1%} loss "
            f"(threshold: -50%)"
        )
        return True, "close", ExitReason.STOP_LOSS

    # ── 4. Time Decay: DISABLED ──
    # Let positions run until resolution - we took them for a reason
    # Only exit if actively bad (covered by edge reversal above)

    # ── 5. Target Reached: Take profits at 100% gain ──
    # Raised from 50% to 100% - let winners run longer
    if unrealized_pnl_pct > 1.0:  # 100% profit (doubled position)
        reason = f"Target reached: {unrealized_pnl_pct:.1%} profit, taking profits"
        return True, "reduce", ExitReason.TARGET_REACHED

    # ── No exit conditions met ──
    return False, None, None


def calculate_exit_size(position: Position, exit_type: str) -> float:
    """
    Calculate how much of the position to exit.

    Args:
        position: Current position
        exit_type: "close" or "reduce"

    Returns:
        Size to sell (shares)
    """
    if exit_type == "close":
        return position.size  # Exit entire position

    elif exit_type == "reduce":
        # Reduce by 50%
        return position.size * 0.5

    return 0.0
