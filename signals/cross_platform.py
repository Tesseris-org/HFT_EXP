"""Cross-platform signal processor.

Matches Polymarket markets to external platforms (Metaculus, Manifold)
and aggregates their probabilities into a cross-platform signal.
"""

import asyncio
import json
import logging
from typing import Optional

from config.constants import BEDROCK_HAIKU
from core.models import Market, Signal
from data.cross_platform import ExternalMarket, MetaculusClient, ManifoldClient

logger = logging.getLogger(__name__)

# Cache: polymarket market_id -> list of matching ExternalMarkets
# Refreshed hourly — matches rarely change
_match_cache: dict[str, list[ExternalMarket]] = {}
_external_cache: dict[str, list[ExternalMarket]] = {}  # platform -> markets
_cache_ts: float = 0


class CrossPlatformSignal:
    """Generates a probability signal from external platform consensus."""

    def __init__(self, forecaster=None):
        # forecaster is injected to use Haiku for matching (avoids circular import)
        self._forecaster = forecaster
        self._metaculus = MetaculusClient()
        self._manifold = ManifoldClient()

    async def close(self):
        await self._metaculus.close()
        await self._manifold.close()

    async def refresh_external_markets(self):
        """Fetch and cache all open markets from external platforms."""
        import time
        global _external_cache, _cache_ts

        if time.time() - _cache_ts < 3600:  # refresh hourly
            return

        logger.info("Refreshing external markets cache...")
        meta_markets, manifold_markets = await asyncio.gather(
            self._metaculus.get_open_binary_questions(limit=500),
            self._manifold.get_open_binary_markets(limit=500),
        )
        _external_cache["metaculus"] = meta_markets
        _external_cache["manifold"] = manifold_markets
        _cache_ts = time.time()
        logger.info(
            f"External cache: {len(meta_markets)} Metaculus, "
            f"{len(manifold_markets)} Manifold markets"
        )

    async def get_signal(self, market: Market) -> Optional[Signal]:
        """
        Find matching markets on external platforms and return aggregated signal.
        Returns None if no matches found.
        """
        await self.refresh_external_markets()

        matches = await self._find_matches(market)
        if not matches:
            return None

        return self._aggregate(market, matches)

    async def _find_matches(self, market: Market) -> list[ExternalMarket]:
        """Find external markets matching this Polymarket question."""
        if market.market_id in _match_cache:
            return _match_cache[market.market_id]

        all_external = (
            _external_cache.get("metaculus", []) +
            _external_cache.get("manifold", [])
        )
        if not all_external:
            return []

        # Use Haiku to match — cheap (~$0.001) and accurate enough
        matches = await self._llm_match(market.question, all_external)

        _match_cache[market.market_id] = matches
        return matches

    async def _llm_match(self, polymarket_question: str,
                         candidates: list[ExternalMarket]) -> list[ExternalMarket]:
        """
        Use Haiku to find external markets matching the Polymarket question.
        Batches candidates to stay within context limits.
        """
        if not self._forecaster:
            return []

        # Only consider top candidates by volume/forecasters to save tokens
        ranked = sorted(
            candidates,
            key=lambda m: m.num_forecasters + m.volume / 100,
            reverse=True,
        )[:100]

        matches = []
        batch_size = 20

        for i in range(0, len(ranked), batch_size):
            batch = ranked[i:i + batch_size]
            numbered = "\n".join(
                f"{j}. [{m.platform}] {m.question}"
                for j, m in enumerate(batch)
            )
            user_msg = (
                f"Polymarket question: \"{polymarket_question}\"\n\n"
                f"Which of these external prediction market questions are asking about "
                f"the SAME event? Return ONLY a JSON array of matching indices (0-based). "
                f"Return [] if none match closely.\n\n{numbered}"
            )

            result = await self._forecaster._invoke_async(
                BEDROCK_HAIKU,
                "You are a precise question matcher. Return only JSON.",
                user_msg,
                max_tokens=128,
            )

            # Result may be list directly or dict containing a list
            if isinstance(result, list):
                indices = result
            elif isinstance(result, dict):
                indices = next((v for v in result.values() if isinstance(v, list)), [])
            else:
                indices = []
            for idx in indices:
                if isinstance(idx, (int, float)) and 0 <= int(idx) < len(batch):
                    matches.append(batch[int(idx)])

            await asyncio.sleep(0.1)

        return matches

    def _aggregate(self, market: Market, matches: list[ExternalMarket]) -> Signal:
        """
        Weighted average of external probabilities.
        Metaculus weighted by forecaster count, Manifold by volume.
        """
        total_weight = 0.0
        weighted_sum = 0.0
        sources = {}

        for m in matches:
            if m.platform == "metaculus":
                weight = max(m.num_forecasters, 1)
            else:  # manifold
                weight = max(m.volume / 1000, 1)

            weighted_sum += m.probability * weight
            total_weight += weight
            sources[m.platform] = {
                "prob": round(m.probability, 3),
                "weight": round(weight, 1),
                "question": m.question[:60],
            }

        if total_weight == 0:
            return None

        agg_prob = weighted_sum / total_weight

        # Confidence: higher when platforms agree with each other
        if len(matches) > 1:
            probs = [m.probability for m in matches]
            spread = max(probs) - min(probs)
            confidence = max(0.3, 1.0 - spread * 2)
        else:
            confidence = 0.5  # single source

        from datetime import datetime
        return Signal(
            market_id=market.market_id,
            source="cross_platform",
            probability=round(agg_prob, 3),
            confidence=round(confidence, 3),
            reasoning=f"Aggregated from {len(matches)} external markets",
            context_json=json.dumps(sources),
            created_at=datetime.utcnow(),
        )
