#!/usr/bin/env python3
"""
Signal Validation Script — Phase 1 Key Deliverable

Tests whether Claude can forecast prediction market outcomes.

Two-part validation:
  1. CALIBRATION: Are Claude's probability estimates well-calibrated?
     (When Claude says 70%, does YES win ~70% of the time?)
  2. EDGE (where data allows): Does Claude beat the market consensus?
     (Only for markets with pre-resolution price history)

Usage:
    python scripts/validate_signal.py --num-markets 30 --model sonnet
    python scripts/validate_signal.py --num-markets 10 --model haiku
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.constants import BEDROCK_SONNET, BEDROCK_HAIKU, BEDROCK_OPUS, DEFAULT_FEE_RATE
from core.models import Market
from data.polymarket import GammaClient, CLOBClient
from signals.forecaster import Forecaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    market_id: str
    question: str
    category: str
    claude_estimate: float
    confidence: float
    actual_outcome: bool       # True = YES won
    market_price: Optional[float]  # pre-resolution market price (None if unavailable)
    latency_ms: int

    @property
    def claude_correct_side(self) -> bool:
        return (self.claude_estimate > 0.5) == self.actual_outcome

    @property
    def claude_brier(self) -> float:
        outcome = 1.0 if self.actual_outcome else 0.0
        return (self.claude_estimate - outcome) ** 2

    @property
    def market_brier(self) -> Optional[float]:
        if self.market_price is None:
            return None
        outcome = 1.0 if self.actual_outcome else 0.0
        return (self.market_price - outcome) ** 2

    @property
    def hypothetical_pnl(self) -> float:
        if self.market_price is None:
            return 0
        edge = abs(self.claude_estimate - self.market_price)
        if edge < DEFAULT_FEE_RATE + 0.03:
            return 0
        if self.claude_estimate > self.market_price:
            buy_price = self.market_price
            payout = 1.0 if self.actual_outcome else 0.0
        else:
            buy_price = 1.0 - self.market_price
            payout = 1.0 if not self.actual_outcome else 0.0
        shares = 10.0 / buy_price if buy_price > 0 else 0
        return shares * payout - 10.0 - 10.0 * DEFAULT_FEE_RATE


async def fetch_resolved_markets(num_markets: int) -> list[tuple[Market, Optional[float]]]:
    """
    Fetch resolved markets and attempt to get pre-resolution prices.

    Returns list of (market, pre_resolution_price_or_None) tuples.
    Markets without price history still have question + outcome for calibration testing.
    """
    gamma = GammaClient()
    clob = CLOBClient()
    try:
        raw_markets = await gamma.get_resolved_markets(limit=num_markets * 3, min_volume=10000)
        logger.info(f"Got {len(raw_markets)} resolved markets, checking price history...")

        results = []
        for m in raw_markets:
            # Capture outcome BEFORE modifying prices (outcome is inferred from yes_price)
            outcome = m.outcome
            if not outcome or not m.outcome_yes_id:
                continue

            # Try to get pre-resolution price (may return None)
            pre_price = await clob.get_pre_resolution_price(m.outcome_yes_id)
            if pre_price is not None and (pre_price < 0.05 or pre_price > 0.95):
                pre_price = None  # price was near-certain, not useful for edge testing

            # Store the outcome before we overwrite prices
            if not m.raw_metadata:
                m.raw_metadata = {}
            m.raw_metadata["_cached_outcome"] = outcome

            # For the forecaster, set price to pre-resolution price or neutral
            if pre_price is not None:
                m.yes_price = pre_price
                m.no_price = 1.0 - pre_price
            else:
                m.yes_price = 0.50
                m.no_price = 0.50

            results.append((m, pre_price))
            if len(results) >= num_markets:
                break
            await asyncio.sleep(0.1)

    finally:
        await gamma.close()
        await clob.close()

    with_price = sum(1 for _, p in results if p is not None)
    logger.info(f"Ready: {len(results)} markets ({with_price} with price history)")
    return results


def run_validation(market_pairs: list[tuple[Market, Optional[float]]],
                   model_id: str) -> list[ValidationResult]:
    """Run Claude forecaster on each resolved market."""
    forecaster = Forecaster()
    results = []

    for i, (market, pre_price) in enumerate(market_pairs):
        logger.info(f"[{i+1}/{len(market_pairs)}] {market.question[:75]}...")

        signal = asyncio.run(forecaster.forecast(market, model_id=model_id))
        if not signal:
            logger.warning(f"  Forecast failed, skipping")
            continue

        # Use cached outcome (captured before we modified prices)
        cached = (market.raw_metadata or {}).get("_cached_outcome", market.outcome)
        actual = cached == "Yes"

        result = ValidationResult(
            market_id=market.market_id,
            question=market.question,
            category=market.category,
            claude_estimate=signal.probability,
            confidence=signal.confidence,
            actual_outcome=actual,
            market_price=pre_price,
            latency_ms=signal.latency_ms,
        )
        results.append(result)

        price_str = f"Mkt: {pre_price:.2f}" if pre_price else "Mkt: N/A"
        correct = "OK" if result.claude_correct_side else "WRONG"
        logger.info(
            f"  Claude: {signal.probability:.2f} | {price_str} | "
            f"Actual: {'YES' if actual else 'NO'} | {correct} | {signal.latency_ms}ms"
        )
        time.sleep(0.5)  # rate limit

    return results


def print_report(results: list[ValidationResult]):
    if not results:
        print("\nNo results to report.")
        return

    n = len(results)
    with_price = [r for r in results if r.market_price is not None]

    print("\n" + "=" * 70)
    print("SIGNAL VALIDATION REPORT")
    print("=" * 70)
    print(f"\nMarkets tested: {n} ({len(with_price)} with pre-resolution market prices)")

    # ── Part 1: Calibration (all markets) ────────────
    print(f"\n{'─' * 40}")
    print("PART 1: CALIBRATION (all markets)")
    print(f"{'─' * 40}")

    claude_correct = sum(1 for r in results if r.claude_correct_side)
    print(f"\n  Directional accuracy: {claude_correct}/{n} ({claude_correct/n*100:.1f}%)")

    claude_brier_avg = sum(r.claude_brier for r in results) / n
    print(f"  Brier score: {claude_brier_avg:.4f} (0=perfect, 0.25=random)")

    # Calibration buckets
    print(f"\n  Calibration (predicted prob → actual YES frequency):")
    print(f"  {'Predicted':>12} {'Count':>6} {'Actual YES%':>12} {'Ideal':>8}")
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in buckets:
        in_bucket = [r for r in results if lo <= r.claude_estimate < hi]
        if in_bucket:
            actual_pct = sum(1 for r in in_bucket if r.actual_outcome) / len(in_bucket)
            ideal = (lo + min(hi, 1.0)) / 2
            delta = abs(actual_pct - ideal)
            marker = " !" if delta > 0.15 else ""
            print(f"  {lo:.1f}-{min(hi,1.0):.1f}      {len(in_bucket):>4}   {actual_pct*100:>10.1f}%  {ideal*100:>6.1f}%{marker}")

    # Confidence analysis
    high_conf = [r for r in results if r.confidence >= 0.6]
    low_conf = [r for r in results if r.confidence < 0.4]
    print(f"\n  Confidence breakdown:")
    if high_conf:
        hc = sum(1 for r in high_conf if r.claude_correct_side)
        print(f"    High confidence (>=0.6): {hc}/{len(high_conf)} correct ({hc/len(high_conf)*100:.1f}%)")
    if low_conf:
        lc = sum(1 for r in low_conf if r.claude_correct_side)
        print(f"    Low confidence (<0.4):  {lc}/{len(low_conf)} correct ({lc/len(low_conf)*100:.1f}%)")

    # ── Part 2: Edge vs Market (only markets with price history) ─
    if with_price:
        print(f"\n{'─' * 40}")
        print(f"PART 2: EDGE VS MARKET ({len(with_price)} markets)")
        print(f"{'─' * 40}")

        market_brier = sum(r.market_brier for r in with_price) / len(with_price)
        claude_brier_wp = sum(r.claude_brier for r in with_price) / len(with_price)
        print(f"\n  Brier score — Claude: {claude_brier_wp:.4f} vs Market: {market_brier:.4f}")
        if claude_brier_wp < market_brier:
            print(f"  --> Claude is BETTER by {market_brier - claude_brier_wp:.4f}")
        else:
            print(f"  --> Market is better by {claude_brier_wp - market_brier:.4f}")

        # Hypothetical P&L
        total_pnl = sum(r.hypothetical_pnl for r in with_price)
        trades = [r for r in with_price if r.hypothetical_pnl != 0]
        winning = [r for r in trades if r.hypothetical_pnl > 0]
        print(f"\n  Hypothetical P&L ($10/trade where edge > threshold):")
        print(f"    Trades taken: {len(trades)}/{len(with_price)}")
        if trades:
            print(f"    Win rate: {len(winning)}/{len(trades)} ({len(winning)/len(trades)*100:.1f}%)")
            print(f"    Total P&L: ${total_pnl:+.2f}")
            print(f"    Avg per trade: ${total_pnl/len(trades):+.2f}")

    # ── Performance ──────────────────────────────────
    avg_latency = sum(r.latency_ms for r in results) / n
    print(f"\n{'─' * 40}")
    print(f"PERFORMANCE")
    print(f"{'─' * 40}")
    print(f"  Avg forecast latency: {avg_latency:.0f}ms")
    print(f"  Total time: {sum(r.latency_ms for r in results)/1000:.1f}s")

    # ── Verdict ──────────────────────────────────────
    print(f"\n{'=' * 70}")
    if claude_brier_avg < 0.20:
        if with_price and sum(r.hypothetical_pnl for r in with_price) > 0:
            print("VERDICT: STRONG SIGNAL — Well-calibrated + positive edge. Build Phase 2.")
        else:
            print("VERDICT: PROMISING — Good calibration. Need more edge data. Continue.")
    elif claude_correct / n > 0.55:
        print("VERDICT: MODERATE — Directionally useful but calibration needs work.")
        print("         Iterate on prompts, especially the two-stage design.")
    else:
        print("VERDICT: WEAK — Not clearly better than chance.")
        print("         Consider: domain focus, better prompts, or alternative signals.")
    print("=" * 70)

    # Save raw results
    output_path = Path(__file__).parent.parent / "validation_results.json"
    raw = [
        {
            "market_id": r.market_id,
            "question": r.question,
            "category": r.category,
            "claude_estimate": r.claude_estimate,
            "confidence": r.confidence,
            "actual_outcome": r.actual_outcome,
            "market_price": r.market_price,
            "claude_brier": r.claude_brier,
            "market_brier": r.market_brier,
            "hypothetical_pnl": r.hypothetical_pnl,
            "latency_ms": r.latency_ms,
        }
        for r in results
    ]
    output_path.write_text(json.dumps(raw, indent=2))
    print(f"\nRaw results saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Validate LLM forecasting signal")
    parser.add_argument("--num-markets", type=int, default=30,
                        help="Number of resolved markets to test (default: 30)")
    parser.add_argument("--model", choices=["haiku", "sonnet", "opus"], default="sonnet",
                        help="Claude model to use (default: sonnet)")
    args = parser.parse_args()

    model_map = {
        "haiku": BEDROCK_HAIKU,
        "sonnet": BEDROCK_SONNET,
        "opus": BEDROCK_OPUS,
    }
    model_id = model_map[args.model]

    print(f"Signal Validation: testing {args.num_markets} markets with {args.model}")
    print(f"Model ID: {model_id}\n")

    # Fetch resolved markets (with price history where available)
    market_pairs = asyncio.run(fetch_resolved_markets(args.num_markets))
    if not market_pairs:
        print("ERROR: No resolved markets found. Check API connectivity.")
        sys.exit(1)

    print(f"Loaded {len(market_pairs)} markets. Starting forecasts...\n")

    # Run validation
    results = run_validation(market_pairs, model_id)

    # Print report
    print_report(results)


if __name__ == "__main__":
    main()
