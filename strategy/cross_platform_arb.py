"""
Cross-platform arbitrage detection.

Finds the same event priced differently on Polymarket vs Manifold.
If Polymarket says 40% and Manifold says 55%, that's a 15% discrepancy.
Buy cheap on one platform, sell expensive on the other.

For paper trading: we trade on Polymarket toward the Manifold consensus,
assuming Manifold (with many forecasters) is often more accurate.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MIN_DISCREPANCY = 0.05    # 5% minimum price difference to flag
MIN_MANIFOLD_VOLUME = 500  # Minimum Manifold volume for reliability


@dataclass
class CrossPlatformOpportunity:
    """A price discrepancy between platforms."""
    polymarket_question: str
    polymarket_market_id: str
    polymarket_price: float      # YES price on Polymarket
    manifold_question: str
    manifold_price: float        # Probability on Manifold
    discrepancy: float           # manifold - polymarket (positive = Poly underpriced)
    abs_discrepancy: float
    direction: str               # "BUY_YES" if Poly underpriced, "BUY_NO" if overpriced
    manifold_volume: float
    confidence: float            # Higher when Manifold has more volume/forecasters


def find_cross_platform_arbs(
    polymarket_markets: list,     # List of dicts with question, yes_price, market_id
    manifold_markets: list,       # List of ExternalMarket objects
    matches: dict,                # market_id -> list of matched ExternalMarkets
) -> list[CrossPlatformOpportunity]:
    """
    Find price discrepancies between matched Polymarket and Manifold markets.

    Args:
        polymarket_markets: Active Polymarket markets with live prices
        manifold_markets: Manifold markets (for reference)
        matches: Pre-computed matches from LLM matching (market_id -> [ExternalMarket])

    Returns:
        List of opportunities sorted by discrepancy
    """
    opportunities = []

    for pm in polymarket_markets:
        market_id = pm.get("market_id") or pm.get("conditionId", "")
        poly_price = pm.get("yes_price", 0)
        question = pm.get("question", "")

        if not market_id or poly_price <= 0:
            continue

        matched = matches.get(market_id, [])
        if not matched:
            continue

        # Use weighted average of matched external markets
        total_weight = 0
        weighted_prob = 0
        best_volume = 0

        for ext in matched:
            if ext.volume < MIN_MANIFOLD_VOLUME:
                continue
            weight = max(ext.volume / 1000, 1)
            weighted_prob += ext.probability * weight
            total_weight += weight
            best_volume = max(best_volume, ext.volume)

        if total_weight == 0:
            continue

        manifold_price = weighted_prob / total_weight
        discrepancy = manifold_price - poly_price
        abs_disc = abs(discrepancy)

        if abs_disc < MIN_DISCREPANCY:
            continue

        # Direction: buy on Polymarket in the direction Manifold suggests
        if discrepancy > 0:
            direction = "BUY_YES"  # Poly underpriced, Manifold higher
        else:
            direction = "BUY_NO"   # Poly overpriced, Manifold lower

        # Confidence based on volume and number of matches
        confidence = min(1.0, (best_volume / 10000) * len(matched))

        opportunities.append(CrossPlatformOpportunity(
            polymarket_question=question,
            polymarket_market_id=market_id,
            polymarket_price=poly_price,
            manifold_question=matched[0].question if matched else "",
            manifold_price=round(manifold_price, 4),
            discrepancy=round(discrepancy, 4),
            abs_discrepancy=round(abs_disc, 4),
            direction=direction,
            manifold_volume=best_volume,
            confidence=round(confidence, 3),
        ))

    opportunities.sort(key=lambda o: o.abs_discrepancy, reverse=True)
    return opportunities
