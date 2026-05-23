"""Polymarket Agent — Main orchestrator."""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

from config import settings
from config.constants import MIN_EDGE_THRESHOLD, MIN_ORDER_SIZE_USD
from core.db import Database
from core.models import Decision, Signal
from data.polymarket import GammaClient, CLOBClient
from data.news import NewsIngestor
from signals.forecaster import Forecaster
from signals.cross_platform import CrossPlatformSignal
from signals.news_analyzer import NewsAnalyzer
from signals.ensemble import compute_ensemble
from strategy.edge import calculate_edge, has_sufficient_edge
from strategy.sizing import kelly_size
from strategy.exit import should_exit_position, calculate_exit_size
from strategy.adaptive_calibration import AdaptiveCalibrator
from execution.paper import PaperExecutor
from ops.telegram import TelegramAlerter
from ops.health import HealthCheck

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("agent")


class Agent:
    def __init__(self):
        self.db = Database(settings.DB_PATH)
        self.gamma = GammaClient()
        self.clob = CLOBClient()
        self.forecaster = Forecaster()
        self.cross_platform = CrossPlatformSignal(forecaster=self.forecaster)
        self.news_ingestor = NewsIngestor()
        self.news_analyzer = NewsAnalyzer(forecaster=self.forecaster)
        self.executor = PaperExecutor()
        self.alerter = TelegramAlerter()
        self.health = HealthCheck(self.db)
        self.calibrator = AdaptiveCalibrator(settings.DB_PATH, lookback_hours=24)

        # In-memory state
        self._pending_news: dict[str, list] = {}  # market_id -> news items
        self._news_lock: asyncio.Lock | None = None  # created lazily inside async context

    async def close(self):
        await self.gamma.close()
        await self.clob.close()
        await self.cross_platform.close()
        await self.news_ingestor.close()
        self.db.close()

    # ── Data Ingestion Loops ─────────────────────────

    async def market_data_loop(self):
        """Fetch and store active markets every 60s."""
        while True:
            try:
                markets = await self.gamma.get_all_active_markets()
                # Batch all upserts in a single transaction — avoids 2000 individual disk flushes
                self.db.upsert_markets_batch(markets)
                logger.info(f"market_refresh count={len(markets)}")
            except Exception as e:
                logger.error(f"market_data_loop error={e}")
            await asyncio.sleep(settings.INTERVALS["market_data_poll"])

    async def news_ingestion_loop(self):
        """Fetch news every 5 min and classify against open markets."""
        await asyncio.sleep(10)  # let markets load first
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
                            logger.info(
                                f"news_relevant markets={len(relevant)} "
                                f"articles={sum(len(v) for v in relevant.values())}"
                            )
            except Exception as e:
                logger.error(f"news_ingestion_loop error={e}")
            await asyncio.sleep(settings.INTERVALS["news_ingestion"])

    # ── Signal + Decision Loop ───────────────────────

    async def analysis_loop(self):
        """Analyze markets for trading opportunities every 5 min."""
        await asyncio.sleep(20)  # wait for initial market data fetch
        while True:
            try:
                markets = self.db.get_active_markets()
                tradeable = self._filter_markets(markets)

                # Cap per cycle to control LLM costs (~$0.02/market × 30 = $0.60/cycle)
                # Prioritize by liquidity — highest-liquidity markets have the most edge opportunity
                tradeable = sorted(tradeable, key=lambda m: m.liquidity, reverse=True)[:30]
                logger.info(f"analysis_start tradeable={len(tradeable)}")

                for market in tradeable:
                    await self._analyze_and_decide(market)
                    await asyncio.sleep(2)  # rate limit LLM calls

            except Exception as e:
                logger.error(f"analysis_loop error={e}")
            await asyncio.sleep(settings.INTERVALS["scheduled_analysis"])

    async def _analyze_and_decide(self, market):
        """Full signal → decision → execution pipeline for one market."""
        try:
            # ── 1. Gather pending news for this market ──
            async with self._get_lock():
                news_items = self._pending_news.pop(market.market_id, [])

            # ── 2. Get current market price from CLOB ──
            mid = await self.clob.get_midpoint(market.outcome_yes_id)
            if mid:
                market.yes_price = mid
                market.no_price = 1.0 - mid
            else:
                logger.warning(f"No midpoint for market={market.market_id[:16]}")
                return

            # ── 3. LLM forecast (primary signal) ────────
            news_ctx = self.news_analyzer.build_news_context(news_items)
            llm_signal = await self.forecaster.forecast(market, news_context=news_ctx)
            if not llm_signal:
                logger.debug(f"No LLM signal for market=\"{market.question[:50]}\"")
                return

            llm_signal.id = self.db.insert_signal(llm_signal)
            logger.info(f"llm_signal prob={llm_signal.probability:.3f} conf={llm_signal.confidence:.2f} market=\"{market.question[:40]}\"")

            # ── 4. Cross-platform signal ─────────────────
            xplat_signal = await self.cross_platform.get_signal(market)
            if xplat_signal:
                xplat_signal.id = self.db.insert_signal(xplat_signal)
                logger.info(f"xplat_signal prob={xplat_signal.probability:.3f} conf={xplat_signal.confidence:.2f}")

            # ── 5. News signal (if fresh news exists) ───
            news_signal = None
            if news_items:
                news_signal = await self.news_analyzer.get_news_signal(
                    market, news_items, llm_signal
                )
                if news_signal:
                    news_signal.id = self.db.insert_signal(news_signal)
                    logger.info(f"news_signal prob={news_signal.probability:.3f}")

            # ── 6. Ensemble ──────────────────────────────
            ensemble_result = compute_ensemble(
                llm_signal, xplat_signal, news_signal,
                calibrator=self.calibrator
            )
            if not ensemble_result:
                logger.warning(f"Ensemble failed for market=\"{market.question[:50]}\"")
                return

            ensemble_prob, confidence, breakdown = ensemble_result

            # Store ensemble signal
            ensemble_signal = Signal(
                market_id=market.market_id,
                source="ensemble",
                probability=ensemble_prob,
                confidence=confidence,
                context_json=json.dumps(breakdown),
            )
            self.db.insert_signal(ensemble_signal)
            logger.info(f"ensemble prob={ensemble_prob:.3f} conf={confidence:.2f} breakdown={breakdown}")

            # ── 7. Decision ──────────────────────────────
            decision = await self._make_decision(
                market, ensemble_prob, confidence,
                [s.id for s in [llm_signal, xplat_signal, news_signal] if s and s.id]
            )
            decision.id = self.db.insert_decision(decision)

            # ── 8. Execute if warranted ──────────────────
            if decision.executed:
                order = await self.executor.place_order(decision)
                self.db.insert_order(order)

                # Update positions in database
                self._sync_positions_to_db()

                logger.info(
                    f"✓ TRADE action={decision.action} "
                    f"market=\"{market.question[:50]}\" "
                    f"edge={decision.edge:.3f} size=${decision.size_usd:.2f} "
                    f"ensemble={ensemble_prob:.3f} market_price={market.yes_price:.3f}"
                )

                # Send Telegram alert (add confidence for alert display)
                decision.confidence = confidence
                await self.alerter.alert_trade(decision, order, market.question)
            else:
                logger.info(
                    f"✗ HOLD market=\"{market.question[:50]}\" "
                    f"ensemble={ensemble_prob:.3f} market_price={market.yes_price:.3f} "
                    f"edge={decision.edge:.3f} reason=\"{decision.reason}\""
                )
        except Exception as e:
            logger.error(f"Error analyzing market {market.market_id[:16]}: {e}", exc_info=True)

    async def _make_decision(
        self, market, ensemble_prob: float, confidence: float,
        signal_ids: list[int]
    ) -> Decision:
        """Apply decision rules and return a Decision."""
        net_edge, side = calculate_edge(ensemble_prob, market.yes_price)

        base = dict(
            market_id=market.market_id,
            ensemble_prob=ensemble_prob,
            market_prob=market.yes_price,
            edge=net_edge,
            signals_used=signal_ids,
        )

        logger.debug(f"Decision: ensemble={ensemble_prob:.3f} market={market.yes_price:.3f} edge={net_edge:.3f} side={side} conf={confidence:.2f}")

        if confidence < 0.3:
            logger.debug(f"  REJECT: low confidence ({confidence:.2f} < 0.3)")
            return Decision(**base, action="HOLD", reason=f"low_confidence={confidence:.2f}")

        # Use adaptive edge threshold based on recent performance
        dynamic_threshold = self.calibrator.get_recommended_edge_threshold(base_threshold=MIN_EDGE_THRESHOLD)
        if abs(net_edge) < dynamic_threshold:
            logger.debug(f"  REJECT: insufficient edge ({net_edge:.3f} < {dynamic_threshold:.3f} [adaptive])")
            return Decision(**base, action="HOLD", reason=f"insufficient_edge={net_edge:.3f}")

        balance = await self.executor.get_balance()
        size_usd = kelly_size(ensemble_prob, market.yes_price, side, balance)

        logger.debug(f"  Kelly sizing: balance=${balance:.2f} → size=${size_usd:.2f}")

        if size_usd < MIN_ORDER_SIZE_USD:
            logger.debug(f"  REJECT: size too small (${size_usd:.2f} < ${MIN_ORDER_SIZE_USD})")
            return Decision(**base, action="HOLD", size_usd=size_usd,
                            reason=f"size_too_small=${size_usd:.2f}")

        logger.debug(f"  ACCEPT: trade {side} ${size_usd:.2f}")
        return Decision(
            **base,
            action=side,
            size_usd=size_usd,
            kelly_fraction=size_usd / balance if balance > 0 else 0,
            reason=f"edge={net_edge:.3f} conf={confidence:.2f}",
            executed=True,
        )

    # ── Operations ───────────────────────────────────

    def _sync_positions_to_db(self):
        """Sync paper executor positions to database."""
        for pos in self.executor.positions.values():
            if pos.size > 0:
                self.db.upsert_position(pos)

    async def position_management_loop(self):
        """Check open positions for exit conditions every 2 minutes."""
        await asyncio.sleep(60)  # Wait for first positions to be established

        while True:
            try:
                positions = await self.executor.get_positions()

                for position in positions:
                    # Get current market data
                    market = self.db.get_market(position.market_id)
                    if not market or not market.active:
                        continue

                    # Get fresh market price
                    mid = await self.clob.get_midpoint(market.outcome_yes_id)
                    if mid:
                        market.yes_price = mid
                        market.no_price = 1.0 - mid

                        # Update unrealized P&L
                        if position.side == "YES":
                            position.unrealized_pnl = (market.yes_price - position.avg_cost) * position.size
                        else:  # NO
                            position.unrealized_pnl = (market.no_price - position.avg_cost) * position.size

                    # Get latest signal for this market
                    latest_signal = self.db.get_latest_signal(position.market_id, "llm_forecaster")
                    if not latest_signal:
                        continue

                    # Check exit conditions
                    should_exit, exit_type, reason = should_exit_position(
                        position, market, latest_signal
                    )

                    if should_exit:
                        exit_size = calculate_exit_size(position, exit_type)

                        logger.info(
                            f"🔄 EXIT SIGNAL: {exit_type.upper()} {position.side} position "
                            f"in {market.question[:40]}... | "
                            f"Size: {exit_size:.1f} shares | Reason: {reason}"
                        )

                        # Create exit decision
                        exit_action = f"SELL_{position.side}"
                        decision = Decision(
                            market_id=position.market_id,
                            action=exit_action,
                            ensemble_prob=latest_signal.probability,
                            market_prob=market.yes_price,
                            edge=0.0,  # Exit is risk management, not edge-seeking
                            size_usd=exit_size * market.yes_price,
                            reason=reason,
                            executed=True,
                        )
                        decision.id = self.db.insert_decision(decision)

                        # Execute exit
                        order = await self.executor.place_order(decision)
                        self.db.insert_order(order)
                        self._sync_positions_to_db()

                        logger.info(
                            f"✓ EXIT EXECUTED: {exit_type} {position.side} | "
                            f"PnL: ${position.unrealized_pnl:+.2f}"
                        )

                        # Alert
                        await self.alerter.send_message(
                            f"🔄 <b>POSITION EXIT</b>\n\n"
                            f"<b>Market:</b> {market.question[:60]}\n"
                            f"<b>Type:</b> {exit_type.upper()}\n"
                            f"<b>Size:</b> {exit_size:.1f} shares\n"
                            f"<b>P&L:</b> ${position.unrealized_pnl:+.2f}\n\n"
                            f"<b>Reason:</b> {reason}"
                        )

            except Exception as e:
                logger.error(f"position_management_loop error={e}", exc_info=True)

            await asyncio.sleep(120)  # Check every 2 minutes

    async def risk_monitoring_loop(self):
        """Monitor P&L and risk limits every 30s."""
        while True:
            try:
                # Get current positions from executor
                positions = await self.executor.get_positions()
                balance = await self.executor.get_balance()

                # Calculate total P&L
                total_unrealized = sum(p.unrealized_pnl for p in positions)
                total_realized = sum(p.realized_pnl for p in positions)
                total_pnl = total_unrealized + total_realized

                # Check risk limits
                limits = settings.RISK_LIMITS

                # Check daily loss limit
                if total_pnl < -limits["max_daily_loss"]:
                    logger.error(f"Daily loss limit breached: ${total_pnl:.2f}")
                    await self.alerter.alert_risk_limit(
                        "Daily Loss", abs(total_pnl), limits["max_daily_loss"]
                    )

                # Check total exposure
                total_exposure = sum(p.size * p.avg_cost for p in positions)
                if total_exposure > limits["max_exposure_total"]:
                    logger.warning(f"Total exposure high: ${total_exposure:.2f}")

                # Sync positions to DB
                self._sync_positions_to_db()

                logger.debug(
                    f"risk_check balance=${balance:.2f} positions={len(positions)} "
                    f"pnl=${total_pnl:+.2f} exposure=${total_exposure:.2f}"
                )

            except Exception as e:
                logger.error(f"risk_monitoring_loop error={e}")

            await asyncio.sleep(settings.INTERVALS.get("risk_monitoring", 30))

    async def order_management_loop(self):
        """Check fills and cancel stale orders every 10s."""
        while True:
            try:
                open_orders = self.db.get_open_orders()
                for order in open_orders:
                    age_minutes = (
                        datetime.utcnow() - order.submitted_at
                    ).total_seconds() / 60 if order.submitted_at else 0

                    if age_minutes > settings.ORDER_TIMEOUT_MINUTES if hasattr(settings, 'ORDER_TIMEOUT_MINUTES') else 15:
                        cancelled = await self.executor.cancel_order(order.id)
                        if cancelled:
                            self.db.update_order_status(order.id, "cancelled")
                            logger.info(f"order_cancelled id={order.id} age={age_minutes:.1f}m")
            except Exception as e:
                logger.error(f"order_management_loop error={e}")
            await asyncio.sleep(settings.INTERVALS["order_management"])

    async def health_check_loop(self):
        """Monitor system health every 60s."""
        while True:
            await asyncio.sleep(60)
            try:
                checks = self.health.run_checks()

                if not checks["healthy"]:
                    logger.warning(
                        f"Health check failed: {checks['failed_checks']} "
                        f"({checks['failure_count']} failures)"
                    )

                    # Alert on persistent failures
                    for check_name in checks["failed_checks"]:
                        self.health.update_failure_count(check_name, True)
                        if self.health.should_alert(check_name):
                            await self.alerter.alert_error(
                                "Health Check Failed",
                                f"{check_name} has failed {self.health.failures[check_name]} times",
                                {"checks": checks}
                            )
                else:
                    # Reset failure counters on success
                    for check_name in checks["data_freshness"].keys():
                        self.health.update_failure_count(check_name, False)

                    logger.debug(f"Health check passed: all systems operational")

            except Exception as e:
                logger.error(f"health_check_loop error={e}")

    async def status_loop(self):
        """Log periodic status every 5 min."""
        while True:
            await asyncio.sleep(300)
            try:
                balance = await self.executor.get_balance()
                positions = await self.executor.get_positions()
                open_orders = await self.executor.get_open_orders()

                # Calculate total P&L
                total_unrealized = sum(p.unrealized_pnl for p in positions)
                total_realized = sum(p.realized_pnl for p in positions)

                logger.info(
                    f"status balance=${balance:.2f} "
                    f"positions={len(positions)} open_orders={len(open_orders)} "
                    f"pnl=${total_unrealized + total_realized:+.2f}"
                )
            except Exception as e:
                logger.error(f"status_loop error={e}")

    async def calibration_report_loop(self):
        """Log adaptive calibration report every 30 min."""
        await asyncio.sleep(1800)  # Wait 30 min for initial data
        while True:
            try:
                report = self.calibrator.get_adaptation_report()
                logger.info(f"\n{report}")
            except Exception as e:
                logger.error(f"calibration_report_loop error={e}")
            await asyncio.sleep(1800)  # Every 30 min

    # ── Helpers ──────────────────────────────────────

    def _filter_markets(self, markets: list) -> list:
        f = settings.MARKET_FILTERS
        result = []
        for m in markets:
            if m.volume < f["min_volume_24h"]:
                continue
            if m.liquidity < f["min_liquidity"]:
                continue
            if not (f["price_range"][0] <= m.yes_price <= f["price_range"][1]):
                continue
            if f["categories"] and m.category not in f["categories"]:
                continue
            # Adaptive filtering: skip categories that lose money consistently
            if self.calibrator.should_filter_category(m.category or "unknown"):
                logger.debug(f"Filtering {m.category} market based on adaptive calibration")
                continue
            result.append(m)
        return result

    # ── Entry Point ──────────────────────────────────

    def _get_lock(self) -> asyncio.Lock:
        """Create news lock lazily inside async context."""
        if self._news_lock is None:
            self._news_lock = asyncio.Lock()
        return self._news_lock

    async def run(self):
        logger.info(f"agent_start mode={settings.EXECUTION_MODE}")
        balance = await self.executor.get_balance()
        logger.info(f"starting_balance=${balance:.2f}")

        # Send startup alert
        await self.alerter.alert_agent_start(settings.EXECUTION_MODE, balance)

        try:
            await asyncio.gather(
                self.market_data_loop(),
                self.analysis_loop(),
                self.position_management_loop(),
                self.order_management_loop(),
                self.risk_monitoring_loop(),
                self.health_check_loop(),
                self.status_loop(),
                self.calibration_report_loop(),
                self.news_ingestion_loop(),
                return_exceptions=True,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("agent_shutdown")
        finally:
            await self.close()


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Agent")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    args = parser.parse_args()
    settings.EXECUTION_MODE = args.mode

    async def _run():
        agent = Agent()  # created inside event loop so all async primitives are valid
        await agent.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
