#!/usr/bin/env python3
"""
Validate LLM calibration against actual market movements.

For positions that have been held for a while, check:
- Did the market move in our predicted direction?
- How does our predicted edge correlate with actual P&L?
- Are we systematically wrong in one direction?
"""

import sqlite3
from pathlib import Path
import numpy as np
from collections import defaultdict

DB_PATH = Path(__file__).parent / "agent_backup_conservative_20260320_163117.db"


def analyze_calibration():
    """Analyze if our predictions correlate with actual outcomes."""

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 80)
    print("CALIBRATION ANALYSIS")
    print("=" * 80)

    # Get all trades with their current P&L
    c.execute("""
        SELECT
            d.action,
            d.ensemble_prob,
            d.market_prob,
            d.edge,
            d.size_usd,
            p.unrealized_pnl,
            p.realized_pnl,
            p.size,
            p.avg_cost,
            m.question,
            m.yes_price as current_price
        FROM decisions d
        JOIN positions p ON d.market_id = p.market_id
        JOIN markets m ON d.market_id = m.market_id
        WHERE d.executed = 1
            AND d.action IN ('BUY_YES', 'BUY_NO')
        ORDER BY d.created_at
    """)

    trades = c.fetchall()

    if not trades:
        print("\nNo trades found in database.")
        return

    print(f"\nAnalyzing {len(trades)} trades...")
    print()

    # Group by edge buckets
    edge_buckets = defaultdict(list)

    for trade in trades:
        edge = trade['edge']
        pnl = (trade['unrealized_pnl'] or 0) + (trade['realized_pnl'] or 0)
        pnl_pct = pnl / trade['size_usd'] if trade['size_usd'] > 0 else 0

        # Bucket by edge
        if edge < 0.10:
            bucket = "0-10%"
        elif edge < 0.15:
            bucket = "10-15%"
        elif edge < 0.20:
            bucket = "15-20%"
        elif edge < 0.30:
            bucket = "20-30%"
        else:
            bucket = "30%+"

        edge_buckets[bucket].append({
            'edge': edge,
            'pnl_pct': pnl_pct,
            'pnl_usd': pnl,
            'action': trade['action'],
            'question': trade['question']
        })

    # Analyze each bucket
    print("EDGE vs ACTUAL P&L")
    print("-" * 80)
    print(f"{'Edge Bucket':<15} {'Count':<8} {'Avg P&L %':<12} {'Win Rate':<12} {'Avg P&L $':<12}")
    print("-" * 80)

    for bucket in ["0-10%", "10-15%", "15-20%", "20-30%", "30%+"]:
        if bucket not in edge_buckets:
            continue

        trades_in_bucket = edge_buckets[bucket]

        avg_pnl_pct = np.mean([t['pnl_pct'] for t in trades_in_bucket])
        avg_pnl_usd = np.mean([t['pnl_usd'] for t in trades_in_bucket])
        win_rate = sum(1 for t in trades_in_bucket if t['pnl_pct'] > 0) / len(trades_in_bucket)

        print(f"{bucket:<15} {len(trades_in_bucket):<8} {avg_pnl_pct:>+10.1%}  {win_rate:>10.1%}  ${avg_pnl_usd:>+10.2f}")

    print()
    print("KEY INSIGHTS:")
    print("-" * 80)

    # Overall stats
    all_trades = [t for bucket in edge_buckets.values() for t in bucket]
    total_pnl_usd = sum(t['pnl_usd'] for t in all_trades)
    total_pnl_pct = np.mean([t['pnl_pct'] for t in all_trades])
    overall_win_rate = sum(1 for t in all_trades if t['pnl_pct'] > 0) / len(all_trades)

    print(f"1. Overall Performance:")
    print(f"   - Total P&L: ${total_pnl_usd:+.2f}")
    print(f"   - Avg P&L per trade: {total_pnl_pct:+.1%}")
    print(f"   - Win rate: {overall_win_rate:.1%}")
    print()

    # Correlation check
    edges = [t['edge'] for t in all_trades]
    pnls = [t['pnl_pct'] for t in all_trades]

    if len(edges) > 2:
        correlation = np.corrcoef(edges, pnls)[0, 1]
        print(f"2. Edge-to-P&L Correlation: {correlation:+.3f}")
        if correlation > 0.3:
            print("   ✓ Positive correlation - higher predicted edges do better")
        elif correlation < -0.3:
            print("   ✗ NEGATIVE correlation - higher edges do WORSE (major problem!)")
        else:
            print("   ⚠ No correlation - predicted edges are random noise")
        print()

    # High edge trades - should be winners
    high_edge_trades = [t for t in all_trades if t['edge'] > 0.20]
    if high_edge_trades:
        high_edge_win_rate = sum(1 for t in high_edge_trades if t['pnl_pct'] > 0) / len(high_edge_trades)
        high_edge_avg_pnl = np.mean([t['pnl_usd'] for t in high_edge_trades])

        print(f"3. High Edge Trades (>20% predicted edge):")
        print(f"   - Count: {len(high_edge_trades)}")
        print(f"   - Win rate: {high_edge_win_rate:.1%}")
        print(f"   - Avg P&L: ${high_edge_avg_pnl:+.2f}")

        if high_edge_win_rate < 0.5:
            print("   ✗ PROBLEM: High-confidence trades are losing!")
        print()

    # Worst performing trades
    print("4. Worst Trades (by P&L):")
    worst_trades = sorted(all_trades, key=lambda t: t['pnl_usd'])[:5]
    for i, trade in enumerate(worst_trades, 1):
        print(f"   {i}. {trade['question'][:60]:60} | Edge: {trade['edge']:>+6.1%} | P&L: ${trade['pnl_usd']:>+8.2f}")
    print()

    # Best performing trades
    print("5. Best Trades (by P&L):")
    best_trades = sorted(all_trades, key=lambda t: t['pnl_usd'], reverse=True)[:5]
    for i, trade in enumerate(best_trades, 1):
        print(f"   {i}. {trade['question'][:60]:60} | Edge: {trade['edge']:>+6.1%} | P&L: ${trade['pnl_usd']:>+8.2f}")

    print()
    print("=" * 80)
    print("CONCLUSION")
    print("=" * 80)

    if total_pnl_usd < -50:
        print("\n✗ MAJOR PROBLEM: Significant losses")
        print("   → LLM predictions have NO EDGE or NEGATIVE EDGE")
        print("   → Do not trade with real money")
    elif correlation < 0:
        print("\n✗ INVERSE CORRELATION: Higher predicted edges lose more")
        print("   → LLM is anti-predictive (worse than random)")
        print("   → Fundamental calibration issue")
    elif overall_win_rate < 0.45:
        print("\n⚠ LOW WIN RATE: Losing more trades than winning")
        print("   → Strategy not profitable")
        print("   → Need better signal quality")
    else:
        print("\n✓ Some promise, but needs improvement")
        print("   → Continue testing with adjustments")

    print()
    conn.close()


if __name__ == "__main__":
    analyze_calibration()
