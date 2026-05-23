#!/usr/bin/env python3
"""Performance analysis - understand what's happening."""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "agent.db"

def analyze():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 80)
    print("📊 POLYMARKET AGENT PERFORMANCE ANALYSIS")
    print("=" * 80)

    # Time range
    c.execute("SELECT MIN(datetime(created_at)), MAX(datetime(created_at)) FROM decisions")
    row = c.fetchone()
    start, end = row[0], row[1]
    if start and end:
        duration = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
        print(f"\n⏰ Trading Period: {start} → {end} ({duration:.0f} minutes)")

    # Decision breakdown
    c.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN executed = 1 THEN 1 END) as executed,
            COUNT(CASE WHEN executed = 0 THEN 1 END) as holds
        FROM decisions
    """)
    row = c.fetchone()
    print(f"\n📋 Decisions Made: {row['total']}")
    print(f"   ✓ Executed: {row['executed']} ({row['executed']/row['total']*100:.1f}%)")
    print(f"   ✗ Holds: {row['holds']} ({row['holds']/row['total']*100:.1f}%)")

    # Trade types
    print(f"\n🔄 Trade Breakdown:")
    c.execute("""
        SELECT action, COUNT(*) as count, AVG(edge) as avg_edge, AVG(size_usd) as avg_size
        FROM decisions WHERE executed = 1
        GROUP BY action ORDER BY count DESC
    """)
    for row in c.fetchall():
        print(f"   {row['action']:10} × {row['count']:2}  |  Avg Edge: {row['avg_edge']:+.1%}  |  Avg Size: ${row['avg_size']:.2f}")

    # Entry vs Exit
    c.execute("SELECT COUNT(*) FROM decisions WHERE executed=1 AND action IN ('BUY_YES', 'BUY_NO')")
    entries = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM decisions WHERE executed=1 AND action IN ('SELL_YES', 'SELL_NO')")
    exits = c.fetchone()[0]
    print(f"\n   📈 Entries: {entries}  |  📉 Exits: {exits}")

    # Current positions
    c.execute("SELECT COUNT(*), SUM(size * avg_cost) FROM positions WHERE size > 0")
    row = c.fetchone()
    print(f"\n💼 Current Positions: {row[0]} open (${row[1]:.2f} exposure)")

    # P&L
    c.execute("SELECT SUM(realized_pnl), SUM(unrealized_pnl) FROM positions")
    row = c.fetchone()
    realized, unrealized = row[0] or 0, row[1] or 0
    total_pnl = realized + unrealized

    print(f"\n💰 Profit & Loss:")
    print(f"   Realized:   ${realized:+.2f}")
    print(f"   Unrealized: ${unrealized:+.2f}")
    print(f"   ─────────────────")
    print(f"   Total P&L:  ${total_pnl:+.2f}")

    if total_pnl < 0:
        print(f"\n   ⚠️  Currently losing money")
    else:
        print(f"\n   ✓ Currently profitable")

    # Balance
    c.execute("SELECT COUNT(*) FROM orders WHERE status = 'filled'")
    filled_orders = c.fetchone()[0]

    # Edge analysis
    c.execute("""
        SELECT
            AVG(edge) as avg_edge,
            MIN(edge) as min_edge,
            MAX(edge) as max_edge
        FROM decisions WHERE executed = 1 AND action IN ('BUY_YES', 'BUY_NO')
    """)
    row = c.fetchone()
    print(f"\n📊 Edge Analysis (Entries Only):")
    print(f"   Average: {row['avg_edge']:+.1%}")
    print(f"   Min: {row['min_edge']:+.1%}")
    print(f"   Max: {row['max_edge']:+.1%}")

    # Top trades (by edge)
    print(f"\n🏆 Biggest Edges Taken:")
    c.execute("""
        SELECT m.question, d.action, d.edge, d.size_usd, datetime(d.created_at)
        FROM decisions d
        JOIN markets m ON d.market_id = m.market_id
        WHERE d.executed = 1 AND d.action IN ('BUY_YES', 'BUY_NO')
        ORDER BY d.edge DESC LIMIT 5
    """)
    for i, row in enumerate(c.fetchall(), 1):
        print(f"   {i}. {row[0][:50]:50} | {row[1]:7} | Edge: {row[2]:+.1%} | ${row[3]:.2f}")

    # Worst trades (biggest losses)
    print(f"\n📉 Positions with Losses:")
    c.execute("""
        SELECT m.question, p.side, p.size, p.avg_cost, p.realized_pnl, p.unrealized_pnl
        FROM positions p
        JOIN markets m ON p.market_id = m.market_id
        WHERE (p.realized_pnl < 0 OR p.unrealized_pnl < 0)
        ORDER BY (p.realized_pnl + p.unrealized_pnl) ASC LIMIT 5
    """)
    for row in c.fetchall():
        total_loss = (row[4] or 0) + (row[5] or 0)
        print(f"   {row[0][:50]:50} | {row[1]:3} | Loss: ${total_loss:+.2f}")

    # DIAGNOSIS
    print(f"\n" + "=" * 80)
    print("🔍 DIAGNOSIS")
    print("=" * 80)

    if exits > entries:
        print("\n⚠️  MORE EXITS THAN ENTRIES")
        print("   → Position management is very aggressive")
        print("   → Exits happening before positions can profit")
        print("   → May need to adjust exit thresholds")

    if total_pnl < -50:
        print("\n⚠️  SIGNIFICANT LOSSES")
        print("   → Strategy is not profitable in paper trading")
        print("   → DO NOT go live with real money yet")
        print("   → Need to debug: edge calculation, exit logic, or signal quality")

    c.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN executed = 1 THEN 1 END) as executed FROM decisions")
    stats = c.fetchone()
    execution_rate = (stats['executed'] / stats['total'] * 100) if stats['total'] > 0 else 0
    if execution_rate > 70:
        print("\n⚠️  HIGH EXECUTION RATE (>70%)")
        print("   → Trading too frequently")
        print("   → May need stricter edge threshold (currently 5%)")
        print("   → Consider raising to 7-10%")

    if realized < -30:
        print("\n⚠️  REALIZED LOSSES > $30")
        print("   → Exits are locking in losses")
        print("   → Exit logic may be too aggressive")
        print("   → Or entries don't have real edge")

    print("\n" + "=" * 80)
    print("💡 RECOMMENDATION")
    print("=" * 80)

    if total_pnl < -30:
        print("\n🛑 DO NOT GO LIVE")
        print("\nStrategy needs improvement:")
        print("  1. Raise edge threshold (5% → 8%)")
        print("  2. Review exit logic (too aggressive?)")
        print("  3. Check signal quality (LLM estimates vs reality)")
        print("  4. Run paper trading longer to see if positions recover")
        print("\n  → Fix issues before risking real money")
    elif total_pnl < 0:
        print("\n⚠️  WAIT BEFORE GOING LIVE")
        print("\nStrategy is borderline:")
        print("  1. Let paper trading run 24 more hours")
        print("  2. Wait for more positions to resolve")
        print("  3. Analyze if losses are temporary or systematic")
        print("\n  → Need more data before decision")
    else:
        print("\n✓ STRATEGY LOOKS PROMISING")
        print("\nNext steps:")
        print("  1. Continue paper trading to validate")
        print("  2. Build LiveExecutor")
        print("  3. Test with tiny capital ($5-10/trade)")

    print("\n" + "=" * 80)

    conn.close()

if __name__ == "__main__":
    analyze()
