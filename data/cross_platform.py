"""Cross-platform prediction market data fetcher.

Fetches probability estimates from Metaculus and Manifold Markets.
These serve as external signal sources for our ensemble.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

METACULUS_API = "https://www.metaculus.com/api2"
MANIFOLD_API = "https://api.manifold.markets/v0"


@dataclass
class ExternalMarket:
    """A market from an external prediction platform."""
    platform: str          # 'metaculus' or 'manifold'
    external_id: str       # platform-specific ID
    question: str
    probability: float     # current probability [0, 1]
    num_forecasters: int = 0   # Metaculus: num forecasters
    volume: float = 0.0        # Manifold: trading volume
    url: str = ""


class MetaculusClient:
    """Metaculus API client — currently requires auth (returns empty gracefully)."""

    async def close(self): pass

    async def get_open_binary_questions(self, limit: int = 200) -> list[ExternalMarket]:
        # Metaculus API requires authentication as of 2025.
        # Stub returns empty — add auth token to enable.
        # See: https://www.metaculus.com/api2/schema/
        logger.debug("Metaculus disabled (requires auth token)")
        return []


class ManifoldClient:
    """Client for Manifold Markets API — high-volume community markets."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def get_open_binary_markets(self, limit: int = 200) -> list[ExternalMarket]:
        """Fetch open binary markets sorted by liquidity."""
        session = await self._get_session()
        markets = []

        try:
            async with session.get(
                f"{MANIFOLD_API}/search-markets",
                params={
                    "limit": min(limit, 1000),
                    "sort": "liquidity",
                    "filter": "open",
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Manifold API returned {resp.status}")
                    return markets
                data = await resp.json()

            # Filter to binary markets only
            data = [m for m in data if m.get("outcomeType") == "BINARY"]

            for m in data:
                prob = m.get("probability")
                if prob is None:
                    continue
                if m.get("isResolved") or m.get("closeTime") is None:
                    continue

                markets.append(ExternalMarket(
                    platform="manifold",
                    external_id=m.get("id", ""),
                    question=m.get("question", ""),
                    probability=float(prob),
                    volume=float(m.get("volume", 0)),
                    url=m.get("url", ""),
                ))

        except aiohttp.ClientError as e:
            logger.error(f"Manifold API error: {e}")

        logger.debug(f"Fetched {len(markets)} Manifold markets")
        return markets
