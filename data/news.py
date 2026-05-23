"""News ingestor — NewsAPI + RSS feeds.

Fetches recent news relevant to open prediction markets.
Phase 2 signal source: news breaks → we re-forecast affected markets → trade the gap.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
import feedparser

from config import settings

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"

# Free RSS feeds — no API key required
RSS_FEEDS = [
    ("Reuters Top News",     "https://feeds.reuters.com/reuters/topNews"),
    ("Reuters Politics",     "https://feeds.reuters.com/Reuters/PoliticsNews"),
    ("Reuters Business",     "https://feeds.reuters.com/reuters/businessNews"),
    ("BBC Top Stories",      "https://feeds.bbci.co.uk/news/rss.xml"),
    ("AP Top News",          "https://feeds.apnews.com/apnews/topnews"),
    ("Politico",             "https://rss.politico.com/politics-news.xml"),
    ("CoinDesk",             "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CryptoSlate",          "https://cryptoslate.com/feed/"),
    ("ESPN Headlines",       "https://www.espn.com/espn/rss/news"),
]


@dataclass
class NewsItem:
    source: str
    title: str
    description: str
    url: str
    published_at: Optional[datetime]

    @property
    def text(self) -> str:
        """Combined title + description for relevance checking."""
        return f"{self.title}. {self.description or ''}"


class NewsIngestor:
    """Fetches news from NewsAPI and RSS feeds."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None
        self._seen_urls: set[str] = set()  # dedup across calls

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def fetch_recent(self, since_minutes: int = 30) -> list[NewsItem]:
        """
        Fetch all news from the last `since_minutes` minutes.
        Combines NewsAPI (if key available) and RSS feeds.
        """
        tasks = [self._fetch_rss_all()]
        if settings.NEWSAPI_KEY:
            tasks.append(self._fetch_newsapi(since_minutes))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[NewsItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"News fetch error: {result}")
                continue
            items.extend(result)

        # Dedup by URL
        fresh = []
        for item in items:
            if item.url and item.url not in self._seen_urls:
                self._seen_urls.add(item.url)
                fresh.append(item)

        # Trim seen set to avoid unbounded growth
        if len(self._seen_urls) > 5000:
            self._seen_urls = set(list(self._seen_urls)[-2000:])

        logger.debug(f"Fetched {len(fresh)} new articles ({len(items)} total)")
        return fresh

    async def _fetch_newsapi(self, since_minutes: int) -> list[NewsItem]:
        """Fetch from NewsAPI (requires API key)."""
        session = await self._get_session()
        from_dt = (datetime.utcnow() - timedelta(minutes=since_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        try:
            async with session.get(
                NEWSAPI_URL,
                params={
                    "apiKey": settings.NEWSAPI_KEY,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "from": from_dt,
                    "pageSize": 100,
                    "q": (
                        "politics OR election OR crypto OR bitcoin OR "
                        "economy OR market OR court OR congress OR fed"
                    ),
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"NewsAPI returned {resp.status}")
                    return []
                data = await resp.json()

            items = []
            for a in data.get("articles", []):
                pub = None
                if a.get("publishedAt"):
                    try:
                        pub = datetime.fromisoformat(
                            a["publishedAt"].replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass
                items.append(NewsItem(
                    source=f"newsapi:{a.get('source', {}).get('name', 'unknown')}",
                    title=a.get("title", ""),
                    description=a.get("description", ""),
                    url=a.get("url", ""),
                    published_at=pub,
                ))
            return items
        except aiohttp.ClientError as e:
            logger.error(f"NewsAPI error: {e}")
            return []

    async def _fetch_rss_all(self) -> list[NewsItem]:
        """Fetch from all RSS feeds concurrently."""
        tasks = [self._fetch_rss_feed(name, url) for name, url in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        items = []
        for r in results:
            if isinstance(r, list):
                items.extend(r)
        return items

    async def _fetch_rss_feed(self, feed_name: str, feed_url: str) -> list[NewsItem]:
        """Fetch a single RSS feed."""
        session = await self._get_session()
        try:
            async with session.get(
                feed_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return []
                content = await resp.text()

            # Run feedparser in thread pool — it's synchronous and would block asyncio
            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, content)
            items = []
            for entry in feed.entries[:20]:  # cap per feed
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub = datetime(*entry.published_parsed[:6])
                    except (ValueError, TypeError):
                        pass

                items.append(NewsItem(
                    source=f"rss:{feed_name}",
                    title=entry.get("title", ""),
                    description=entry.get("summary", ""),
                    url=entry.get("link", ""),
                    published_at=pub,
                ))
            return items
        except Exception as e:
            logger.debug(f"RSS feed {feed_name} error: {e}")
            return []
