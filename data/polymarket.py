"""Polymarket data client — Gamma API + CLOB REST endpoints."""

import asyncio
import logging
from typing import Optional

import aiohttp

from config.constants import GAMMA_API_URL, CLOB_API_URL
from core.models import Market

logger = logging.getLogger(__name__)


class GammaClient:
    """Client for the Gamma API (market metadata, no auth required)."""

    _DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._DEFAULT_TIMEOUT)
            self._owns_session = True
        return self._session

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
    ) -> list[Market]:
        """Fetch markets from Gamma API."""
        params = {"limit": limit, "offset": offset}
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()

        session = await self._get_session()
        try:
            async with session.get(f"{GAMMA_API_URL}/markets", params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return [Market.from_gamma(m) for m in data if m.get("conditionId")]
        except aiohttp.ClientError as e:
            logger.error(f"Gamma API error fetching markets: {e}")
            return []

    async def get_all_active_markets(self, max_pages: int = 20) -> list[Market]:
        """Fetch all active markets with pagination."""
        all_markets = []
        for page in range(max_pages):
            batch = await self.get_markets(
                limit=100, offset=page * 100, active=True, closed=False
            )
            if not batch:
                break
            all_markets.extend(batch)
            if len(batch) < 100:
                break
            await asyncio.sleep(0.2)  # rate limit courtesy
        logger.info(f"Fetched {len(all_markets)} active markets from Gamma")
        return all_markets

    async def get_resolved_markets(self, limit: int = 500, min_volume: float = 10000) -> list[Market]:
        """Fetch resolved markets via the events endpoint (has recent data)."""
        session = await self._get_session()
        all_markets = []
        offset = 0

        while len(all_markets) < limit:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/events",
                    params={
                        "limit": 50,
                        "offset": offset,
                        "closed": "true",
                        "order": "volume",
                        "ascending": "false",
                    },
                ) as resp:
                    resp.raise_for_status()
                    events = await resp.json()
                    if not events:
                        break

                    for evt in events:
                        for m_data in evt.get("markets", []):
                            m = Market.from_gamma(m_data)
                            if not m.resolved or not m.outcome:
                                continue
                            if m.volume < min_volume:
                                continue
                            if not m.outcome_yes_id:
                                continue
                            all_markets.append(m)
                            if len(all_markets) >= limit:
                                break
                        if len(all_markets) >= limit:
                            break

                    offset += 50
                    await asyncio.sleep(0.2)

            except aiohttp.ClientError as e:
                logger.error(f"Gamma events API error: {e}")
                break

        logger.info(f"Fetched {len(all_markets)} resolved markets from Gamma events")
        return all_markets

    async def get_negrisk_events(self, max_pages: int = 10) -> list[dict]:
        """
        Fetch active NegRisk events with their grouped outcomes.

        Returns list of dicts:
        {
            "title": "Who wins the NBA Finals?",
            "neg_risk_market_id": "0x...",
            "markets": [
                {"market_id": "...", "question": "...", "yes_token_id": "...", "yes_price": 0.35},
                ...
            ]
        }
        """
        session = await self._get_session()
        events_out = []

        for page in range(max_pages):
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "neg_risk": "true",
                        "limit": 100,
                        "offset": page * 100,
                    },
                ) as resp:
                    resp.raise_for_status()
                    markets = await resp.json()

                if not markets:
                    break

                # Group by negRiskMarketID
                for m in markets:
                    nrm_id = m.get("negRiskMarketID", "")
                    if not nrm_id:
                        continue

                    # Find or create event group
                    existing = None
                    for ev in events_out:
                        if ev["neg_risk_market_id"] == nrm_id:
                            existing = ev
                            break

                    if existing is None:
                        existing = {
                            "title": m.get("groupItemTitle", m.get("question", ""))[:100],
                            "neg_risk_market_id": nrm_id,
                            "markets": [],
                        }
                        events_out.append(existing)

                    # Parse token IDs and prices
                    clob_ids = m.get("clobTokenIds", "[]")
                    if isinstance(clob_ids, str):
                        import json
                        clob_ids = json.loads(clob_ids)

                    prices = m.get("outcomePrices", "[]")
                    if isinstance(prices, str):
                        import json
                        prices = json.loads(prices)

                    yes_token = clob_ids[0] if clob_ids else ""
                    yes_price = float(prices[0]) if prices else 0.0

                    existing["markets"].append({
                        "market_id": m.get("conditionId", ""),
                        "question": m.get("question", ""),
                        "yes_token_id": yes_token,
                        "yes_price": yes_price,
                    })

                if len(markets) < 100:
                    break
                await asyncio.sleep(0.2)

            except aiohttp.ClientError as e:
                logger.error(f"Gamma NegRisk fetch error: {e}")
                break

        # Filter to events with 3+ outcomes (actual multi-outcome markets)
        events_out = [e for e in events_out if len(e["markets"]) >= 3]
        logger.info(f"Fetched {len(events_out)} NegRisk events with 3+ outcomes")
        return events_out

    async def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """Fetch a single market by slug."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{GAMMA_API_URL}/markets", params={"slug": slug}
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if data:
                    return Market.from_gamma(data[0])
                return None
        except aiohttp.ClientError as e:
            logger.error(f"Gamma API error fetching market {slug}: {e}")
            return None


class CLOBClient:
    """Client for CLOB public endpoints (prices, books — no auth required)."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    _DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=5)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._DEFAULT_TIMEOUT)
            self._owns_session = True
        return self._session

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{CLOB_API_URL}/midpoint", params={"token_id": token_id}
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return float(data.get("mid", 0))
        except (aiohttp.ClientError, ValueError) as e:
            logger.error(f"CLOB midpoint error for {token_id}: {e}")
            return None

    async def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        """Get best price for a token (buy or sell side)."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{CLOB_API_URL}/price",
                params={"token_id": token_id, "side": side},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return float(data.get("price", 0))
        except (aiohttp.ClientError, ValueError) as e:
            logger.error(f"CLOB price error for {token_id}: {e}")
            return None

    async def get_book(self, token_id: str) -> Optional[dict]:
        """Get full order book for a token."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{CLOB_API_URL}/book", params={"token_id": token_id}
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.error(f"CLOB book error for {token_id}: {e}")
            return None

    async def get_spread(self, token_id: str) -> Optional[float]:
        """Get bid-ask spread for a token."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{CLOB_API_URL}/spread", params={"token_id": token_id}
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return float(data.get("spread", 0))
        except (aiohttp.ClientError, ValueError) as e:
            logger.error(f"CLOB spread error for {token_id}: {e}")
            return None

    async def get_midpoints_batch(self, token_ids: list[str]) -> list[Optional[float]]:
        """Fetch midpoint prices for multiple tokens in parallel."""
        tasks = [self.get_midpoint(tid) for tid in token_ids]
        return await asyncio.gather(*tasks)

    async def get_price_history(self, token_id: str, fidelity: int = 60) -> list[dict]:
        """
        Get historical prices for a token.

        Returns list of {"t": timestamp, "p": price} dicts.
        Useful for getting pre-resolution prices for backtesting.
        """
        session = await self._get_session()
        try:
            async with session.get(
                f"{CLOB_API_URL}/prices-history",
                params={"market": token_id, "interval": "max", "fidelity": fidelity},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if isinstance(data, dict) and data.get("history"):
                    return data["history"]
                return []
        except aiohttp.ClientError as e:
            logger.error(f"CLOB price history error for {token_id}: {e}")
            return []

    async def get_pre_resolution_price(self, token_id: str, percentile: float = 0.70) -> Optional[float]:
        """
        Get the market price before resolution (at `percentile` through the market's life).

        Uses 70% by default — late enough to have meaningful price discovery,
        early enough to avoid the resolution rush where prices go to 0/1.
        """
        history = await self.get_price_history(token_id)
        if not history or len(history) < 10:
            return None
        idx = int(len(history) * percentile)
        try:
            return float(history[idx]["p"])
        except (KeyError, ValueError, IndexError):
            return None
