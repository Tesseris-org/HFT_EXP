"""
Polymarket Agent v2 — Information Edge Architecture.

Key changes from v1:
1. Two-tier analysis: Haiku screening → Opus 4.6 deep analysis
2. Market-anchored: Start from market price, adjust only with evidence
3. Position deduplication: Max 1 position per market
4. Strict risk controls: Small sizes, loss limits, max positions
5. Quality over quantity: Fewer trades, higher conviction
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from config.constants import (
    MIN_EDGE_THRESHOLD, MIN_ORDER_SIZE_USD, DEFAULT_FEE_RATE,
    BEDROCK_OPUS, BEDROCK_HAIKU,
)
from core.db import Database
from core.models import Decision, Signal
from data.polymarket import GammaClient, CLOBClient
from data.news import NewsIngestor
from signals.forecaster import Forecaster
from signals.forecaster_v2 import InformationEdgeForecaster
from signals.cross_platform import CrossPlatformSignal
from signals.news_analyzer import NewsAnalyzer
from strategy.edge import calculate_edge, has_sufficient_edge
from strategy.sizing import kelly_size
from execution.paper import PaperExecutor
from ops.telegram import TelegramAlerter
from ops.health import HealthCheck

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("agent_v2")


# ── Risk Parameters ──────────────────────────────────────

MAX_POSITIONS = 8           # Max concurrent positions
MAX_POSITION_SIZE = 25.0    # Max $25 per position (was $100 - way too much)
MAX_DAILY_TRADES = 10       # Don't overtrade
MIN_EDGE = 0.03             # 3% net edge (after fees) - lower threshold since we're more accurate
MAX_ADJUSTMENT = 0.10       # Never deviate more than 10% from market
COOLDOWN_AFTER_LOSS = 1800  # 30 min cooldown after a losing exit


class AgentV2:
    def __init__(self):
        self.db = Database(settings.DB_PATH)
        self.gamma = GammaClient()
        self.clob = CLOBClient()
        self.forecaster = InformationEdgeForecaster()
        self._old_forecaster = Forecaster()  # For news relevance checks (uses Haiku)
        self.cross_platform = CrossPlatformSignal(forecaster=self._old_forecaster)
        self.news_ingestor = NewsIngestor()
        self.news_analyzer = NewsAnalyzer(forecaster=self._old_forecaster)
        self.executor = PaperExecutor()
        self.alerter = TelegramAlerter()
        self.health = HealthCheck(self.db)

        # State tracking
        self._pending_news: dict[str, list] = {}
        self._news_lock: asyncio.Lock | None = None
        self._traded_markets: set[str] = set()  # Markets with open positions
        self._daily_trade_count = 0
        self._last_trade_time: datetime | None = None
        self._consecutive_losses = 0
        self._cooldown_until: datetime | None = None

    async def close(self):
        await self.gamma.close()
        await self.clob.close()
        await self.cross_platform.close()
        await self.news_ingestor.close()
        self.db.close()

    # ── Data Loops ────────────────────────────────────

    async def market_data_loop(self):
        """Fetch markets every 60s."""
        while True:
            try:
                markets = await self.gamma.get_all_active_markets()
                self.db.upsert_markets_batch(markets)
                logger.info(f"market_refresh count={len(markets)}")
            except Exception as e:
                logger.error(f"market_data_loop error: {type(e).__name__}: {e}")
            await asyncio.sleep(settings.INTERVALS["market_data_poll"])

    async def news_ingestion_loop(self):
        """Fetch news every 5 min."""
        await asyncio.sleep(10)
        while True:
            try:
                news_items = await self.news_ingestor.fetch_recent(since_minutes=6)
                if news_items:
                    markets = self.db.get_active_markets()
                    tradeable = self._filter_markets(markets)
                    if tradeable and news_items:
                        relevant = await self.news_analyzer.filter_relevant(
                            news_items, tradeable
                        )
                        async with self._get_lock():
                            for mid, items in relevant.items():
                                if mid not in self._pending_news:
                                    self._pending_news[mid] = []
                                self._pending_news[mid].extend(items)
                        if relevant:
                            logger.info(f"news_relevant markets={len(relevant)}")
            except Exception as e:
                logger.error(f"news_ingestion_loop error={e}")
            await asyncio.sleep(settings.INTERVALS["news_ingestion"])

    # ── Main Analysis Loop ────────────────────────────

    async def analysis_loop(self):
        """
        v2 analysis: Screen with Haiku, analyze with Opus 4.6.
        Runs every 10 min (slower but smarter).
        """
        await asyncio.sleep(20)  # Wait for market data
        while True:
            try:
                # Check cooldown
                if self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until:
                    remaining = (self._cooldown_until - datetime.now(timezone.utc)).seconds
                    logger.info(f"In cooldown after loss. {remaining}s remaining.")
                    await asyncio.sleep(60)
                    continue

                # Check daily trade limit
                if self._daily_trade_count >= MAX_DAILY_TRADES:
                    logger.info(f"Daily trade limit reached ({MAX_DAILY_TRADES})")
                    await asyncio.sleep(300)
                    continue

                markets = self.db.get_active_markets()
                tradeable = self._filter_markets(markets)

                # Sort by liquidity, take top 50 for screening
                tradeable = sorted(tradeable, key=lambda m: m.liquidity, reverse=True)[:50]
                logger.info(f"analysis_start screening={len(tradeable)} markets")

                # Reset forecaster session for new cycle
                self.forecaster.reset_session()

                # Get cross-platform data once (shared across markets)
                xplat_cache = {}

                screened_count = 0
                analyzed_count = 0

                for market in tradeable:
                    # Skip markets we already have positions in
                    if market.market_id in self._traded_markets:
                        continue

                    # Skip if at position limit
                    positions = await self.executor.get_positions()
                    if len([p for p in positions if p.size > 0]) >= MAX_POSITIONS:
                        logger.info("Position limit reached, stopping analysis")
                        break

                    try:
                        result = await self._screen_and_analyze(market)
                        if result == "screened":
                            screened_count += 1
                        elif result == "traded":
                            analyzed_count += 1
                    except Exception as e:
                        logger.error(f"Error on {market.question[:30]}: {e}")

                    await asyncio.sleep(2)  # Rate limit

                logger.info(
                    f"analysis_complete screened={screened_count} "
                    f"deep_analyzed={analyzed_count} "
                    f"positions={len(self._traded_markets)}"
                )

            except Exception as e:
                logger.error(f"analysis_loop error={e}", exc_info=True)

            # Longer interval - quality over quantity
            await asyncio.sleep(600)  # 10 minutes between cycles

    async def _screen_and_analyze(self, market) -> str:
        """Screen and optionally deep-analyze a market. Returns action taken."""

        # Get fresh market price
        mid = await self.clob.get_midpoint(market.outcome_yes_id)
        if mid:
            market.yes_price = mid
            market.no_price = 1.0 - mid
        else:
            return "skipped"

        # Get pending news
        async with self._get_lock():
            news_items = self._pending_news.get(market.market_id, [])
        news_ctx = self.news_analyzer.build_news_context(news_items) if news_items else ""

        # Get cross-platform data
        xplat_signal = await self.cross_platform.get_signal(market)
        xplat_data = None
        if xplat_signal:
            xplat_data = {"cross_platform": {"prob": xplat_signal.probability}}

        # Two-tier analysis
        signal = await self.forecaster.analyze(
            market, news_ctx, xplat_data
        )

        if not signal:
            return "screened"

        # Save signal
        signal.id = self.db.insert_signal(signal)

        # Check if there's a genuine edge
        context = json.loads(signal.context_json) if signal.context_json else {}
        has_edge = context.get("has_genuine_edge", False)

        if not has_edge or signal.confidence < 0.3:
            logger.info(
                f"NO EDGE: {market.question[:50]} | "
                f"prob={signal.probability:.3f} market={market.yes_price:.3f}"
            )
            # Record decision as HOLD
            decision = Decision(
                market_id=market.market_id,
                action="HOLD",
                ensemble_prob=signal.probability,
                market_prob=market.yes_price,
                edge=abs(signal.probability - market.yes_price),
                reason="no_genuine_edge",
                signals_used=[signal.id] if signal.id else [],
            )
            self.db.insert_decision(decision)
            return "screened"

        # Calculate edge
        net_edge, side = calculate_edge(signal.probability, market.yes_price)

        # CRITICAL: Filter out BUY_YES trades - data shows LLM bullish bias loses money
        # Only take BUY_NO (where LLM thinks market overprices YES)
        if side == "BUY_YES":
            decision = Decision(
                market_id=market.market_id,
                action="HOLD",
                ensemble_prob=signal.probability,
                market_prob=market.yes_price,
                edge=net_edge,
                reason="filtered_buy_yes_bias",
                signals_used=[signal.id] if signal.id else [],
            )
            self.db.insert_decision(decision)
            return "screened"

        if net_edge < MIN_EDGE:
            decision = Decision(
                market_id=market.market_id,
                action="HOLD",
                ensemble_prob=signal.probability,
                market_prob=market.yes_price,
                edge=net_edge,
                reason=f"insufficient_edge={net_edge:.3f}",
                signals_used=[signal.id] if signal.id else [],
            )
            self.db.insert_decision(decision)
            return "screened"

        # Size the trade (conservative)
        balance = await self.executor.get_balance()
        size_usd = kelly_size(signal.probability, market.yes_price, side, balance)
        size_usd = min(size_usd, MAX_POSITION_SIZE)  # Hard cap

        if size_usd < MIN_ORDER_SIZE_USD:
            return "screened"

        # Execute
        decision = Decision(
            market_id=market.market_id,
            action=side,
            ensemble_prob=signal.probability,
            market_prob=market.yes_price,
            edge=net_edge,
            size_usd=size_usd,
            reason=f"info_edge={net_edge:.3f} conf={signal.confidence:.2f}",
            executed=True,
            signals_used=[signal.id] if signal.id else [],
        )
        decision.id = self.db.insert_decision(decision)

        order = await self.executor.place_order(decision)
        self.db.insert_order(order)

        # Track position
        self._traded_markets.add(market.market_id)
        self._daily_trade_count += 1
        self._last_trade_time = datetime.now(timezone.utc)
        self._sync_positions_to_db()

        logger.info(
            f"TRADE EXECUTED: {side} {market.question[:50]} | "
            f"edge={net_edge:.3f} size=${size_usd:.2f} "
            f"ai={signal.probability:.3f} market={market.yes_price:.3f}"
        )

        await self.alerter.alert_trade(decision, order, market.question)
        return "traded"

    # ── Position Management ───────────────────────────

    async def position_management_loop(self):
        """Monitor positions and update P&L every 2 min."""
        await asyncio.sleep(60)
        while True:
            try:
                positions = await self.executor.get_positions()
                updated = 0

                for position in positions:
                    if position.size <= 0:
                        continue

                    try:
                        market = self.db.get_market(position.market_id)
                        if not market:
                            logger.warning(f"Market not found for position: {position.market_id}")
                            continue

                        token_id = market.outcome_yes_id if position.side == "YES" else market.outcome_no_id
                        if not token_id:
                            logger.warning(f"No token_id for {position.side} side of {market.question[:40]}")
                            continue

                        mid = await self.clob.get_midpoint(market.outcome_yes_id)
                        if mid and mid > 0:
                            current_yes = mid
                            current_no = 1.0 - mid

                            if position.side == "YES":
                                position.unrealized_pnl = (current_yes - position.avg_cost) * position.size
                            else:
                                position.unrealized_pnl = (current_no - position.avg_cost) * position.size

                            updated += 1
                            logger.debug(
                                f"P&L update: {market.question[:30]} | "
                                f"side={position.side} cost={position.avg_cost:.3f} "
                                f"current={'%.3f' % (current_yes if position.side == 'YES' else current_no)} "
                                f"pnl=${position.unrealized_pnl:+.2f}"
                            )

                    except Exception as e:
                        logger.warning(f"Price update failed for {position.market_id[:20]}: {type(e).__name__}: {e}")

                self._sync_positions_to_db()
                if updated > 0:
                    logger.info(f"Updated P&L for {updated}/{len(positions)} positions")

            except Exception as e:
                logger.error(f"position_management_loop error: {type(e).__name__}: {e}", exc_info=True)
            await asyncio.sleep(120)

    # ── Risk Monitoring ───────────────────────────────

    async def risk_monitoring_loop(self):
        """Monitor P&L and enforce risk limits every 30s."""
        while True:
            try:
                positions = await self.executor.get_positions()
                balance = await self.executor.get_balance()
                total_pnl = sum(p.unrealized_pnl + p.realized_pnl for p in positions)
                total_exposure = sum(p.size * p.avg_cost for p in positions if p.size > 0)

                # Circuit breaker: stop trading if down > $50
                if total_pnl < -50:
                    logger.error(f"CIRCUIT BREAKER: Total P&L ${total_pnl:.2f} < -$50")
                    self._cooldown_until = datetime.now(timezone.utc).replace(hour=23, minute=59)

                self._sync_positions_to_db()

                logger.debug(
                    f"risk_check balance=${balance:.2f} positions={len(positions)} "
                    f"pnl=${total_pnl:+.2f} exposure=${total_exposure:.2f}"
                )
            except Exception as e:
                logger.error(f"risk_monitoring error={e}")
            await asyncio.sleep(30)

    async def status_loop(self):
        """Log status every 5 min."""
        while True:
            await asyncio.sleep(300)
            try:
                balance = await self.executor.get_balance()
                positions = await self.executor.get_positions()
                total_pnl = sum(p.unrealized_pnl + p.realized_pnl for p in positions)
                active = [p for p in positions if p.size > 0]

                logger.info(
                    f"STATUS balance=${balance:.2f} "
                    f"positions={len(active)}/{MAX_POSITIONS} "
                    f"trades_today={self._daily_trade_count}/{MAX_DAILY_TRADES} "
                    f"pnl=${total_pnl:+.2f}"
                )
            except Exception as e:
                logger.error(f"status_loop error={e}")

    # ── Helpers ───────────────────────────────────────

    def _filter_markets(self, markets: list) -> list:
        """Filter to tradeable markets with stricter criteria."""
        f = settings.MARKET_FILTERS
        result = []
        for m in markets:
            if m.volume < f["min_volume_24h"]:
                continue
            if m.liquidity < f["min_liquidity"]:
                continue
            if not (f["price_range"][0] <= m.yes_price <= f["price_range"][1]):
                continue
            # Skip joke/meme markets
            skip_keywords = ["jesus", "god", "alien", "ufo", "rapture"]
            if any(kw in m.question.lower() for kw in skip_keywords):
                continue
            result.append(m)
        return result

    def _sync_positions_to_db(self):
        for pos in self.executor.positions.values():
            if pos.size > 0:
                self.db.upsert_position(pos)

    def _get_lock(self) -> asyncio.Lock:
        if self._news_lock is None:
            self._news_lock = asyncio.Lock()
        return self._news_lock

    # ── Entry Point ───────────────────────────────────

    async def run(self):
        logger.info(f"agent_v2_start mode={settings.EXECUTION_MODE}")
        logger.info(f"Architecture: Information Edge (Haiku screen → Opus 4.6 deep analysis)")
        logger.info(f"Risk: max_positions={MAX_POSITIONS} max_size=${MAX_POSITION_SIZE} max_trades={MAX_DAILY_TRADES}")

        balance = await self.executor.get_balance()
        logger.info(f"starting_balance=${balance:.2f}")

        await self.alerter.alert_agent_start(settings.EXECUTION_MODE, balance)

        try:
            await asyncio.gather(
                self.market_data_loop(),
                self.analysis_loop(),
                self.position_management_loop(),
                self.risk_monitoring_loop(),
                self.status_loop(),
                self.news_ingestion_loop(),
                return_exceptions=True,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("agent_v2_shutdown")
        finally:
            await self.close()


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Agent v2")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    args = parser.parse_args()
    settings.EXECUTION_MODE = args.mode

    async def _run():
        agent = AgentV2()
        await agent.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
