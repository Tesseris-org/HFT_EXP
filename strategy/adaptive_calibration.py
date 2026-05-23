"""
Adaptive Calibration System - Learn from mistakes and adjust strategy dynamically.

Instead of full RL, this implements a simpler feedback loop:
1. Track prediction accuracy vs actual market movements
2. Apply calibration curve to overconfident predictions
3. Filter market types that consistently lose
4. Dynamically adjust edge threshold based on recent performance
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class AdaptiveCalibrator:
    """Learns from trading history to improve signal quality."""

    def __init__(self, db_path: Path, lookback_hours: int = 24):
        self.db_path = db_path
        self.lookback_hours = lookback_hours

    def get_recent_performance(self) -> Dict:
        """Get performance metrics from recent trades."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)

        # Get recent entries with current P&L
        c.execute("""
            SELECT
                d.ensemble_prob,
                d.market_prob,
                d.edge,
                d.action,
                d.size_usd,
                p.unrealized_pnl,
                p.realized_pnl,
                m.category,
                datetime(d.created_at) as entry_time
            FROM decisions d
            JOIN positions p ON d.market_id = p.market_id
            JOIN markets m ON d.market_id = m.market_id
            WHERE d.executed = 1
                AND d.action IN ('BUY_YES', 'BUY_NO')
                AND d.created_at > ?
        """, (cutoff.isoformat(),))

        trades = [dict(row) for row in c.fetchall()]
        conn.close()

        # Compute metrics
        total_pnl = sum((t['unrealized_pnl'] or 0) + (t['realized_pnl'] or 0) for t in trades)
        avg_edge = np.mean([t['edge'] for t in trades]) if trades else 0

        # Calibration: how often do high-edge predictions actually win?
        high_edge_trades = [t for t in trades if t['edge'] > 0.15]
        high_edge_winners = [t for t in high_edge_trades if (t['unrealized_pnl'] or 0) + (t['realized_pnl'] or 0) > 0]
        high_edge_win_rate = len(high_edge_winners) / len(high_edge_trades) if high_edge_trades else 0

        # Category performance
        category_pnl = {}
        for trade in trades:
            cat = trade['category'] or 'unknown'
            if cat not in category_pnl:
                category_pnl[cat] = []
            category_pnl[cat].append((trade['unrealized_pnl'] or 0) + (trade['realized_pnl'] or 0))

        category_avg_pnl = {
            cat: np.mean(pnls) for cat, pnls in category_pnl.items()
        }

        return {
            'total_trades': len(trades),
            'total_pnl': total_pnl,
            'avg_pnl_per_trade': total_pnl / len(trades) if trades else 0,
            'avg_edge': avg_edge,
            'high_edge_win_rate': high_edge_win_rate,
            'category_performance': category_avg_pnl,
            'trades': trades,
        }

    def compute_calibration_curve(self) -> Dict[str, float]:
        """
        Compare predicted probabilities to actual outcomes.

        Returns calibration adjustments by probability bucket.
        Example: if LLM predicts 70% but reality is 60%, adjustment = -0.10
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Get trades where we can measure outcome
        # For now, use P&L as proxy (positive = correct direction)
        c.execute("""
            SELECT
                d.ensemble_prob,
                d.market_prob,
                d.action,
                (p.unrealized_pnl + p.realized_pnl) as total_pnl
            FROM decisions d
            JOIN positions p ON d.market_id = p.market_id
            WHERE d.executed = 1
                AND d.action IN ('BUY_YES', 'BUY_NO')
        """)

        trades = c.fetchall()
        conn.close()

        if not trades:
            return {}

        # Bucket predictions and compute actual success rate
        buckets = {
            '0.5-0.6': [],
            '0.6-0.7': [],
            '0.7-0.8': [],
            '0.8-0.9': [],
            '0.9-1.0': [],
        }

        for trade in trades:
            prob = trade['ensemble_prob']
            is_winner = trade['total_pnl'] > 0

            if 0.5 <= prob < 0.6:
                buckets['0.5-0.6'].append(is_winner)
            elif 0.6 <= prob < 0.7:
                buckets['0.6-0.7'].append(is_winner)
            elif 0.7 <= prob < 0.8:
                buckets['0.7-0.8'].append(is_winner)
            elif 0.8 <= prob < 0.9:
                buckets['0.8-0.9'].append(is_winner)
            elif 0.9 <= prob <= 1.0:
                buckets['0.9-1.0'].append(is_winner)

        # Compute calibration error
        calibration = {}
        for bucket, outcomes in buckets.items():
            if outcomes:
                predicted = float(bucket.split('-')[0]) + 0.05  # midpoint
                actual = sum(outcomes) / len(outcomes)
                calibration[bucket] = actual - predicted  # positive = underconfident, negative = overconfident

        return calibration

    def get_recommended_edge_threshold(self, base_threshold: float = 0.08) -> float:
        """
        Dynamically adjust edge threshold based on recent performance.

        If losing money: raise threshold (be more selective)
        If making money: can lower threshold (take more trades)
        """
        perf = self.get_recent_performance()

        if perf['total_trades'] < 10:
            # Not enough data, use base
            return base_threshold

        avg_pnl = perf['avg_pnl_per_trade']

        if avg_pnl < -5:  # Losing more than $5/trade
            # Very bad - raise threshold significantly
            adjustment = 0.04  # +4%
            logger.warning(f"Performance poor (${avg_pnl:.2f}/trade) - raising edge threshold by {adjustment:.1%}")
        elif avg_pnl < 0:  # Losing money but not catastrophic
            adjustment = 0.02  # +2%
            logger.info(f"Performance negative (${avg_pnl:.2f}/trade) - raising edge threshold by {adjustment:.1%}")
        elif avg_pnl > 5:  # Making good money
            adjustment = -0.01  # -1% (can be slightly more aggressive)
            logger.info(f"Performance good (${avg_pnl:.2f}/trade) - slightly lowering edge threshold")
        else:  # Breakeven
            adjustment = 0
            logger.info(f"Performance neutral (${avg_pnl:.2f}/trade) - keeping edge threshold")

        new_threshold = max(0.05, min(0.20, base_threshold + adjustment))  # Keep between 5-20%
        return new_threshold

    def should_filter_category(self, category: str) -> bool:
        """Blacklist categories that consistently lose money."""
        perf = self.get_recent_performance()

        if category not in perf['category_performance']:
            return False  # No data, allow

        category_avg = perf['category_performance'][category]
        category_trades = sum(1 for t in perf['trades'] if (t['category'] or 'unknown') == category)

        # Need at least 5 trades to make a decision
        if category_trades < 5:
            return False

        # If average loss > $10 per trade in this category, filter it
        if category_avg < -10:
            logger.warning(f"Filtering category '{category}' - avg loss ${category_avg:.2f}/trade")
            return True

        return False

    def apply_calibration(self, probability: float) -> float:
        """
        Apply calibration curve to adjust overconfident predictions.

        Example: if LLM says 80% but calibration shows it's really 70%, adjust down.
        """
        calibration = self.compute_calibration_curve()

        if not calibration:
            return probability  # No data yet

        # Find which bucket this probability falls into
        if 0.5 <= probability < 0.6:
            bucket = '0.5-0.6'
        elif 0.6 <= probability < 0.7:
            bucket = '0.6-0.7'
        elif 0.7 <= probability < 0.8:
            bucket = '0.7-0.8'
        elif 0.8 <= probability < 0.9:
            bucket = '0.8-0.9'
        elif 0.9 <= probability <= 1.0:
            bucket = '0.9-1.0'
        else:
            return probability

        if bucket in calibration:
            adjustment = calibration[bucket]
            calibrated_prob = probability + adjustment
            calibrated_prob = max(0.0, min(1.0, calibrated_prob))  # Clamp to [0, 1]

            logger.debug(f"Calibrating {probability:.2%} → {calibrated_prob:.2%} (adjustment: {adjustment:+.2%})")
            return calibrated_prob

        return probability

    def get_adaptation_report(self) -> str:
        """Generate human-readable report of current adaptations."""
        perf = self.get_recent_performance()
        calibration = self.compute_calibration_curve()
        threshold = self.get_recommended_edge_threshold()

        report = []
        report.append("=" * 60)
        report.append("ADAPTIVE CALIBRATION REPORT")
        report.append("=" * 60)
        report.append(f"\nLookback: Last {self.lookback_hours} hours")
        report.append(f"Trades: {perf['total_trades']}")
        report.append(f"Total P&L: ${perf['total_pnl']:.2f}")
        report.append(f"Avg P&L/Trade: ${perf['avg_pnl_per_trade']:.2f}")
        report.append(f"\nAverage Edge Predicted: {perf['avg_edge']:.1%}")
        report.append(f"High Edge Win Rate (>15%): {perf['high_edge_win_rate']:.1%}")

        if calibration:
            report.append("\n--- CALIBRATION CURVE ---")
            for bucket, adjustment in sorted(calibration.items()):
                sign = '+' if adjustment > 0 else ''
                report.append(f"  {bucket}: {sign}{adjustment:.2%} adjustment")

        report.append(f"\n--- RECOMMENDED ADJUSTMENTS ---")
        report.append(f"Edge Threshold: {threshold:.1%} (base: 8.0%)")

        if perf['category_performance']:
            report.append("\n--- CATEGORY PERFORMANCE ---")
            for cat, avg_pnl in sorted(perf['category_performance'].items(), key=lambda x: x[1], reverse=True):
                filtered = " [FILTERED]" if self.should_filter_category(cat) else ""
                report.append(f"  {cat}: ${avg_pnl:.2f}/trade{filtered}")

        report.append("\n" + "=" * 60)
        return "\n".join(report)
