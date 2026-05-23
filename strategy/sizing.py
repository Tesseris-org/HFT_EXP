"""Position sizing using Kelly criterion."""

from config.constants import KELLY_FRACTION, MAX_POSITION_SIZE_USD, DEFAULT_FEE_RATE


def kelly_size(
    ensemble_prob: float,
    market_price: float,
    side: str,
    bankroll: float,
    fee_rate: float = DEFAULT_FEE_RATE,
    kelly_fraction: float = KELLY_FRACTION,
    max_position: float = MAX_POSITION_SIZE_USD,
    existing_position_usd: float = 0.0,
) -> float:
    """
    Fractional Kelly for binary prediction markets.

    For BUY YES at price P, true prob Q:
      f* = (Q - P) / (1 - P)

    For BUY NO at price (1-P), true prob (1-Q):
      f* = (P - Q) / Q

    Subtracts existing position from target to avoid over-allocating.

    Returns dollar amount to trade (0 if no trade).
    """
    if side == "BUY_YES":
        edge = ensemble_prob - market_price - fee_rate
        if edge <= 0:
            return 0.0
        full_kelly = edge / (1 - market_price)
    elif side == "BUY_NO":
        edge = market_price - ensemble_prob - fee_rate
        if edge <= 0:
            return 0.0
        full_kelly = edge / ensemble_prob
    else:
        return 0.0

    target_usd = full_kelly * kelly_fraction * bankroll
    # Subtract what we already hold in this market
    remaining = target_usd - existing_position_usd
    if remaining <= 0:
        return 0.0

    return min(remaining, max_position - existing_position_usd)


def compute_limit_price(market_price: float, ensemble_prob: float, side: str) -> float:
    """
    Place limit order that keeps ~60% of edge for us, gives ~40% to fill probability.
    """
    edge = abs(ensemble_prob - market_price)

    if side == "BUY_YES":
        limit_price = market_price + (edge * 0.4)
    elif side == "BUY_NO":
        limit_price = market_price - (edge * 0.4)
    else:
        return market_price

    # Clamp to valid range and round to cent
    limit_price = max(0.01, min(0.99, limit_price))
    return round(limit_price, 2)
