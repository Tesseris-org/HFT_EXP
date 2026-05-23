"""
Information Edge Forecaster v2 — Opus 4.6 powered.

KEY INSIGHT: Don't try to out-predict the market.
Instead, find SPECIFIC INFORMATION the market hasn't priced in.

Old approach (v1): "What's the probability?" → Compete with market → Lose
New approach (v2): "Is the market wrong? Why specifically?" → Find edge → Win

Architecture:
1. SCREENING (Haiku, cheap): Quick check if market might be mispriced
2. DEEP ANALYSIS (Opus 4.6, expensive): Full context analysis on flagged markets only
3. CONSERVATIVE ADJUSTMENT: Start from market price, deviate only with evidence
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

import boto3

from config import settings
from config.constants import BEDROCK_HAIKU, BEDROCK_OPUS, BEDROCK_SONNET
from core.models import Market, Signal

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────

SCREENING_SYSTEM = """You are a prediction market analyst. Your job is to quickly assess
whether a market might be mispriced.

You will see a market question and its current price. Your task:
1. Is there any SPECIFIC, RECENT information that the market might not have priced in?
2. Is the price obviously wrong based on widely known facts?
3. Are there any logical inconsistencies with related markets?

Be SKEPTICAL. Most markets are efficiently priced. Only flag genuine opportunities.

OUTPUT FORMAT (JSON only):
{
  "worth_analyzing": true/false,
  "reason": "brief explanation",
  "direction": "overpriced" | "underpriced" | "fair",
  "confidence": 0.0-1.0
}"""

DEEP_ANALYSIS_SYSTEM = """You are a world-class prediction market analyst using rigorous reasoning.

CRITICAL RULES:
1. The market price is your STARTING POINT. Assume it reflects the wisdom of many informed traders.
2. You need SPECIFIC, VERIFIABLE reasons to disagree with the market — not just "I think differently."
3. Your adjustment from market price should be SMALL (usually 0-5 percentage points).
4. Be honest about uncertainty. If you don't have an edge, say so.

TYPES OF GENUINE EDGE:
- Breaking news not yet reflected (< 24 hours old)
- Mathematical/logical error in market pricing
- Cross-market inconsistency (related markets imply different probabilities)
- Base rate analysis showing systematic mispricing
- Specific domain expertise the average trader lacks

NOT GENUINE EDGE:
- "I just think it's more/less likely" (narrative bias)
- Anchoring on your first impression
- Overweighting dramatic scenarios
- Ignoring base rates for rare events
- Disagreeing with market without specific evidence

REASONING PROCESS:
1. STATE the market price and what it implies
2. IDENTIFY any specific information that might not be priced in
3. EVALUATE the strength of that information
4. DECIDE: Is this enough to deviate from the market?
5. If yes, HOW MUCH should you adjust? (Usually small: 1-5%)

OUTPUT FORMAT (JSON only):
{
  "market_price_implies": "what the current price tells us",
  "potential_edge_sources": [
    {"source": "description", "strength": "weak|moderate|strong", "direction": "higher|lower"}
  ],
  "has_genuine_edge": true/false,
  "edge_reasoning": "why you believe there is/isn't an edge",
  "recommended_probability": 0.XX,
  "adjustment_from_market": 0.XX,
  "confidence_in_edge": 0.0-1.0
}"""


class InformationEdgeForecaster:
    """
    Two-tier forecasting system:
    - Tier 1 (Haiku): Cheap screening of all markets
    - Tier 2 (Opus 4.6): Deep analysis of flagged markets only
    """

    def __init__(self, region: Optional[str] = None):
        self.region = region or settings.AWS_REGION
        self.client = boto3.client("bedrock-runtime", region_name=self.region)
        self._analyzed_markets: set[str] = set()  # Prevent re-analysis within session

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """Robustly extract JSON from LLM response."""
        import re
        text = text.strip()

        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = text.find(start_char)
            if start == -1:
                continue
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            fragment = text[start:i + 1]
                            cleaned = re.sub(r'[\x00-\x1f\x7f]', ' ', fragment)
                            return json.loads(cleaned)
                        except json.JSONDecodeError:
                            break
        return None

    async def _invoke_async(self, model_id: str, system: str, user_message: str,
                            max_tokens: int = 2048) -> Optional[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._invoke(model_id, system, user_message, max_tokens)
        )

    def _invoke(self, model_id: str, system: str, user_message: str,
                max_tokens: int = 2048) -> Optional[dict]:
        try:
            response = self.client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user_message}],
                }),
            )
            body = json.loads(response["body"].read())
            text = body["content"][0]["text"]
            return self._parse_json(text)

        except Exception as e:
            logger.error(f"Bedrock invocation failed: {e}")
            return None

    # ── Tier 1: Screening ────────────────────────────────

    async def screen_market(self, market: Market, news_context: str = "",
                            cross_platform_data: Optional[dict] = None) -> Optional[dict]:
        """
        Quick screening with Haiku. Returns screening result or None.
        Cost: ~$0.001 per market.
        """
        prompt_parts = [
            f"Market: {market.question}",
            f"Current YES price: {market.yes_price:.2f} ({market.yes_price*100:.1f}%)",
            f"Category: {market.category or 'General'}",
            f"End date: {market.end_date.strftime('%Y-%m-%d') if market.end_date else 'Unknown'}",
            f"24h Volume: ${market.volume:,.0f}" if market.volume else "",
        ]

        if news_context:
            prompt_parts.append(f"\nRecent news:\n{news_context[:500]}")

        if cross_platform_data:
            prompt_parts.append("\nCross-platform estimates:")
            for platform, data in cross_platform_data.items():
                prob = data.get("prob", data) if isinstance(data, dict) else data
                prompt_parts.append(f"  - {platform}: {prob}")

        user_msg = "\n".join(p for p in prompt_parts if p)

        result = await self._invoke_async(BEDROCK_HAIKU, SCREENING_SYSTEM, user_msg, max_tokens=512)

        if result and result.get("worth_analyzing"):
            logger.info(
                f"SCREEN PASS: {market.question[:50]} | "
                f"direction={result.get('direction')} "
                f"reason={result.get('reason', '')[:80]}"
            )
        return result

    # ── Tier 2: Deep Analysis ────────────────────────────

    async def deep_analyze(self, market: Market, news_context: str = "",
                           cross_platform_data: Optional[dict] = None,
                           related_markets: Optional[list] = None) -> Optional[Signal]:
        """
        Full analysis with Opus 4.6. Only called on screened markets.
        Cost: ~$0.50-2.00 per market (depends on context size).
        """
        # Prevent re-analyzing same market within session
        if market.market_id in self._analyzed_markets:
            logger.debug(f"Already analyzed {market.question[:40]} this session")
            return None
        self._analyzed_markets.add(market.market_id)

        start = time.time()

        prompt_parts = [
            f"MARKET: {market.question}",
            f"CURRENT YES PRICE: {market.yes_price:.3f} ({market.yes_price*100:.1f}%)",
            f"CURRENT NO PRICE: {market.no_price:.3f} ({market.no_price*100:.1f}%)",
            f"CATEGORY: {market.category or 'General'}",
            f"END DATE: {market.end_date.strftime('%Y-%m-%d') if market.end_date else 'Unknown'}",
            f"24H VOLUME: ${market.volume:,.0f}" if market.volume else "",
            f"LIQUIDITY: ${market.liquidity:,.0f}" if market.liquidity else "",
        ]

        if news_context:
            prompt_parts.append(f"\n--- RECENT NEWS ---\n{news_context}")

        if cross_platform_data:
            prompt_parts.append("\n--- CROSS-PLATFORM ESTIMATES ---")
            for platform, data in cross_platform_data.items():
                prob = data.get("prob", data) if isinstance(data, dict) else data
                prompt_parts.append(f"  {platform}: {prob}")

        if related_markets:
            prompt_parts.append("\n--- RELATED MARKETS ---")
            for rm in related_markets[:10]:
                prompt_parts.append(f"  {rm.question}: YES={rm.yes_price:.2f}")

        prompt_parts.append(
            "\n--- YOUR TASK ---"
            "\nStart from the market price. The market is probably right."
            "\nOnly recommend a different probability if you have SPECIFIC evidence."
            "\nRemember: a +5% adjustment is a BIG move. Most of the time, adjustment should be 0-3%."
        )

        user_msg = "\n".join(p for p in prompt_parts if p)

        result = await self._invoke_async(
            BEDROCK_OPUS, DEEP_ANALYSIS_SYSTEM, user_msg, max_tokens=4096
        )

        if not result:
            logger.warning(f"Deep analysis failed for {market.question[:40]}")
            return None

        latency_ms = int((time.time() - start) * 1000)

        # Extract results
        has_edge = result.get("has_genuine_edge", False)
        recommended_prob = float(result.get("recommended_probability", market.yes_price))
        adjustment = float(result.get("adjustment_from_market", 0))
        confidence = float(result.get("confidence_in_edge", 0))

        # SAFETY: Cap adjustment to ±10% from market price
        max_adjustment = 0.10
        if abs(adjustment) > max_adjustment:
            logger.warning(
                f"Capping adjustment from {adjustment:.3f} to ±{max_adjustment}"
            )
            adjustment = max(-max_adjustment, min(max_adjustment, adjustment))
            recommended_prob = market.yes_price + adjustment

        recommended_prob = max(0.01, min(0.99, recommended_prob))

        # If no genuine edge found, return signal at market price (no trade)
        if not has_edge:
            logger.info(
                f"DEEP ANALYSIS: No edge found for {market.question[:50]} | "
                f"market={market.yes_price:.3f}"
            )

        logger.info(
            f"DEEP ANALYSIS: {market.question[:50]} | "
            f"market={market.yes_price:.3f} recommended={recommended_prob:.3f} "
            f"adj={adjustment:+.3f} has_edge={has_edge} conf={confidence:.2f} "
            f"latency={latency_ms}ms"
        )

        return Signal(
            market_id=market.market_id,
            source="opus_deep_analysis",
            probability=recommended_prob,
            confidence=confidence if has_edge else 0.0,
            reasoning=json.dumps(result),
            context_json=json.dumps({
                "news_context": news_context[:500] if news_context else "",
                "cross_platform": cross_platform_data,
                "market_price": market.yes_price,
                "has_genuine_edge": has_edge,
                "adjustment": adjustment,
            }),
            model=BEDROCK_OPUS,
            latency_ms=latency_ms,
            created_at=datetime.utcnow(),
        )

    # ── Full Pipeline ────────────────────────────────────

    async def analyze(self, market: Market, news_context: str = "",
                      cross_platform_data: Optional[dict] = None,
                      related_markets: Optional[list] = None) -> Optional[Signal]:
        """
        Full two-tier pipeline:
        1. Screen with Haiku (cheap, all markets)
        2. Deep analyze with Opus 4.6 (expensive, only flagged markets)
        """
        # Skip if already analyzed
        if market.market_id in self._analyzed_markets:
            return None

        # Tier 1: Screen
        screening = await self.screen_market(market, news_context, cross_platform_data)

        if not screening or not screening.get("worth_analyzing"):
            logger.debug(f"SCREEN REJECT: {market.question[:50]}")
            return None

        screen_confidence = screening.get("confidence", 0)
        if screen_confidence < 0.4:
            logger.debug(f"SCREEN LOW CONF: {market.question[:50]} ({screen_confidence:.2f})")
            return None

        # Tier 2: Deep analysis with Opus 4.6
        return await self.deep_analyze(
            market, news_context, cross_platform_data, related_markets
        )

    def reset_session(self):
        """Reset analyzed markets for a new analysis cycle."""
        self._analyzed_markets.clear()
