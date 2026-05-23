"""News signal analyzer.

Detects breaking news that should shift probabilities for open markets,
and triggers re-estimation on affected markets.

This is the speed edge: news breaks → market is slow to reprice →
we trade the gap before consensus catches up.
"""

import json
import logging
from typing import Optional

from config.constants import BEDROCK_HAIKU, BEDROCK_SONNET
from core.models import Market, Signal
from data.news import NewsItem

logger = logging.getLogger(__name__)

# Max news items to batch per relevance check call
BATCH_SIZE = 15


class NewsAnalyzer:
    """Analyzes news items for market relevance and probability impact."""

    def __init__(self, forecaster):
        self._forecaster = forecaster  # injected Forecaster instance

    async def filter_relevant(
        self, news_items: list[NewsItem], markets: list[Market]
    ) -> dict[str, list[NewsItem]]:
        """
        Check which news items are relevant to which markets (async).

        Uses Haiku for cheap, fast relevance filtering.
        Returns dict: market_id -> list of relevant news items.
        """
        if not news_items or not markets:
            return {}

        # Build market question list for the prompt
        market_questions = [m.question for m in markets]
        market_ids = [m.market_id for m in markets]

        relevant: dict[str, list[NewsItem]] = {}

        # Process news items in batches to avoid token limits
        for i in range(0, len(news_items), BATCH_SIZE):
            batch = news_items[i:i + BATCH_SIZE]

            for item in batch:
                # Ask Haiku: which markets does this news affect? (non-blocking)
                affected_indices = await self._forecaster.quick_relevance_check(
                    item.title, market_questions
                )
                for idx in affected_indices:
                    mid = market_ids[idx]
                    if mid not in relevant:
                        relevant[mid] = []
                    relevant[mid].append(item)

        total_relevant = sum(len(v) for v in relevant.values())
        if total_relevant:
            logger.info(
                f"Found {total_relevant} news-market connections "
                f"across {len(relevant)} markets"
            )

        return relevant

    def build_news_context(self, news_items: list[NewsItem], max_items: int = 5) -> str:
        """Format news items into context string for the LLM forecaster."""
        if not news_items:
            return ""

        # Sort by recency
        sorted_items = sorted(
            news_items,
            key=lambda x: x.published_at or __import__("datetime").datetime.min,
            reverse=True,
        )[:max_items]

        lines = []
        for item in sorted_items:
            time_str = (
                item.published_at.strftime("%Y-%m-%d %H:%M UTC")
                if item.published_at else "recent"
            )
            desc = f" — {item.description[:150]}" if item.description else ""
            lines.append(f"[{time_str}] {item.title}{desc}")

        return "\n".join(lines)

    async def get_news_signal(
        self,
        market: Market,
        relevant_news: list[NewsItem],
        llm_signal: Optional[Signal],
    ) -> Optional[Signal]:
        """
        Generate a news-adjusted signal.

        Takes the base LLM estimate and asks the model to adjust it
        given breaking news. Returns a new Signal with source='news'.
        """
        if not relevant_news or not llm_signal:
            return None

        news_context = self.build_news_context(relevant_news)

        system = (
            "You are evaluating how breaking news should change a prediction market estimate.\n"
            "Be precise and concise. Output only JSON."
        )

        user_msg = (
            f"Prediction market: {market.question}\n"
            f"Current estimate: {llm_signal.probability:.2f}\n"
            f"Market price: {market.yes_price:.2f}\n\n"
            f"Breaking news:\n{news_context}\n\n"
            f"How does this news change the probability?\n"
            f"Output JSON: {{\"adjustment\": 0.XX, \"direction\": \"up/down/none\", "
            f"\"reasoning\": \"brief reason\", \"confidence\": 0.XX}}"
        )

        result = await self._forecaster._invoke_async(BEDROCK_SONNET, system, user_msg, max_tokens=256)
        if not result:
            return None

        adjustment = float(result.get("adjustment", 0))
        direction = result.get("direction", "none")
        confidence = float(result.get("confidence", 0.4))

        if direction == "none" or abs(adjustment) < 0.02:
            return None  # news doesn't meaningfully change the estimate

        # Apply adjustment to base estimate
        if direction == "up":
            new_prob = min(0.99, llm_signal.probability + adjustment)
        elif direction == "down":
            new_prob = max(0.01, llm_signal.probability - adjustment)
        else:
            return None

        from datetime import datetime
        return Signal(
            market_id=market.market_id,
            source="news",
            probability=round(new_prob, 3),
            confidence=round(confidence, 3),
            reasoning=result.get("reasoning", ""),
            context_json=json.dumps({
                "base_estimate": llm_signal.probability,
                "news_items": [n.title for n in relevant_news[:3]],
                "adjustment": adjustment,
                "direction": direction,
            }),
            model=BEDROCK_SONNET,
            created_at=datetime.utcnow(),
        )
