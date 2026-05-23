"""
Arbitrage detection for Polymarket NegRisk (multi-outcome) markets.

In a multi-outcome market (e.g., "Who wins the NBA Finals?"), each outcome has a
YES token. The sum of all YES prices should equal ~$1.00. When it doesn't,
guaranteed profit exists:

- Long arb (sum < 1): Buy all YES tokens → one pays $1 at resolution → profit = 1 - sum
- Short arb (sum > 1): Buy all NO tokens → (n-1) pay $1 at resolution → profit = sum - 1

Based on: "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets"
(arxiv 2508.03474) which found $40M in arbitrage profit on Polymarket.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Thresholds from paper
MIN_ARB_THRESHOLD = 0.02   # 2% price deviation (paper used this)
MIN_LIQUIDITY_USD = 10.0   # Minimum per-outcome liquidity to consider
MIN_PROFIT_USD = 0.50      # Don't bother with sub-$0.50 opportunities
MAX_SLIPPAGE_EST = 0.005   # 0.5% estimated slippage per leg


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    event_title: str
    neg_risk_market_id: str
    num_outcomes: int
    price_sum: float
    deviation: float          # 1.0 - price_sum (positive = long arb)
    arb_type: str             # "long" or "short"
    profit_per_dollar: float  # Gross profit per $1 invested
    estimated_slippage: float
    net_profit_per_dollar: float  # After slippage
    max_invest_usd: float     # Limited by liquidity
    estimated_profit_usd: float
    outcome_prices: list[float] = field(default_factory=list)
    outcome_token_ids: list[str] = field(default_factory=list)
    outcome_questions: list[str] = field(default_factory=list)
    outcome_market_ids: list[str] = field(default_factory=list)


def scan_market(
    outcome_prices: list[float],
    outcome_token_ids: list[str] = None,
    outcome_questions: list[str] = None,
    outcome_market_ids: list[str] = None,
    event_title: str = "",
    neg_risk_market_id: str = "",
    min_liquidity_per_outcome: float = MIN_LIQUIDITY_USD,
) -> Optional[ArbOpportunity]:
    """
    Check if a multi-outcome market has an arbitrage opportunity.

    Args:
        outcome_prices: YES price for each outcome
        outcome_token_ids: CLOB token IDs for each outcome's YES token
        outcome_questions: Human-readable question for each outcome
        outcome_market_ids: Market/condition ID for each outcome
        event_title: Parent event title
        neg_risk_market_id: NegRisk market group ID

    Returns:
        ArbOpportunity if profitable opportunity exists, None otherwise.
    """
    if not outcome_prices or len(outcome_prices) < 2:
        return None

    # Filter out zero-priced outcomes (inactive/delisted)
    valid_indices = [i for i, p in enumerate(outcome_prices) if p > 0]
    if len(valid_indices) < 2:
        return None

    prices = [outcome_prices[i] for i in valid_indices]
    price_sum = sum(prices)
    deviation = 1.0 - price_sum

    # Check threshold
    if abs(deviation) < MIN_ARB_THRESHOLD:
        return None

    # Determine arb type
    if deviation > 0:
        arb_type = "long"
        profit_per_dollar = deviation  # Buy all YES for < $1
    else:
        arb_type = "short"
        profit_per_dollar = -deviation  # Sell all YES for > $1

    # Estimate slippage: more outcomes = more legs = more slippage
    # But slippage per leg decreases for high-probability outcomes (tight spreads)
    # Low-prob outcomes (< 5 cents) have wider spreads but tiny capital needed
    num_outcomes = len(prices)
    # Weight slippage by price: expensive legs have tighter spreads
    weighted_slippage = sum(MAX_SLIPPAGE_EST * max(p, 0.01) for p in prices)
    total_slippage = weighted_slippage
    net_profit_per_dollar = profit_per_dollar - total_slippage

    if net_profit_per_dollar <= 0:
        return None

    # Max investable is limited by thinnest outcome
    # For long arb: need to buy 1 YES of each, limited by most expensive / least liquid
    max_invest_usd = 100.0  # Conservative default cap
    estimated_profit_usd = max_invest_usd * net_profit_per_dollar

    if estimated_profit_usd < MIN_PROFIT_USD:
        return None

    # Build token/question lists for valid outcomes only
    token_ids = [outcome_token_ids[i] for i in valid_indices] if outcome_token_ids else []
    questions = [outcome_questions[i] for i in valid_indices] if outcome_questions else []
    market_ids = [outcome_market_ids[i] for i in valid_indices] if outcome_market_ids else []

    return ArbOpportunity(
        event_title=event_title,
        neg_risk_market_id=neg_risk_market_id,
        num_outcomes=num_outcomes,
        price_sum=round(price_sum, 6),
        deviation=round(deviation, 6),
        arb_type=arb_type,
        profit_per_dollar=round(profit_per_dollar, 6),
        estimated_slippage=round(total_slippage, 6),
        net_profit_per_dollar=round(net_profit_per_dollar, 6),
        max_invest_usd=max_invest_usd,
        estimated_profit_usd=round(estimated_profit_usd, 2),
        outcome_prices=prices,
        outcome_token_ids=token_ids,
        outcome_questions=questions,
        outcome_market_ids=market_ids,
    )


def rank_opportunities(opportunities: list[ArbOpportunity]) -> list[ArbOpportunity]:
    """Rank arbitrage opportunities by estimated profit."""
    return sorted(opportunities, key=lambda o: o.estimated_profit_usd, reverse=True)
