#!/usr/bin/env python3
"""
Real-time monitoring dashboard for the paper trading agent.

Usage:
    python monitor.py              # Show current status
    python monitor.py --watch      # Auto-refresh every 10s
    python monitor.py --trades     # Show trade history
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def get_db_path():
    return Path(__file__).parent / "agent.db"


def format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m"
    else:
        return f"{seconds/3600:.1f}h"


def get_status():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    status = {}

    # System state
    c.execute("SELECT COUNT(*) FROM markets WHERE active = 1")
    status['active_markets'] = c.fetchone()[0]

    # Recent activity (last 10 minutes)
    since = (datetime.utcnow() - timedelta(minutes=10)).isoformat()

    c.execute(f"SELECT COUNT(*) FROM signals WHERE created_at > ?", (since,))
    status['signals_10m'] = c.fetchone()[0]

    c.execute(f"SELECT COUNT(*) FROM decisions WHERE created_at > ?", (since,))
    status['decisions_10m'] = c.fetchone()[0]

    c.execute(f"SELECT COUNT(*) FROM orders WHERE created_at > ?", (since,))
    status['orders_10m'] = c.fetchone()[0]

    # All-time counts
    c.execute("SELECT COUNT(*) FROM signals")
    status['total_signals'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM decisions")
    status['total_decisions'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders WHERE status = 'filled'")
    status['total_filled'] = c.fetchone()[0]

    # Recent decisions breakdown
    c.execute(f"SELECT action, COUNT(*) as cnt FROM decisions WHERE created_at > ? GROUP BY action", (since,))
    status['recent_actions'] = {row['action']: row['cnt'] for row in c.fetchall()}

    # Latest activity timestamp
    c.execute("SELECT MAX(created_at) FROM signals")
    last_signal = c.fetchone()[0]
    if last_signal:
        status['last_activity'] = datetime.fromisoformat(last_signal)
        status['idle_seconds'] = (datetime.utcnow() - status['last_activity']).total_seconds()
    else:
        status['last_activity'] = None
        status['idle_seconds'] = None

    # Recent trades
    c.execute("""
        SELECT m.question, o.side, o.size, o.price, o.status, o.created_at
        FROM orders o
        JOIN markets m ON o.market_id = m.market_id
        WHERE o.created_at > ?
        ORDER BY o.created_at DESC
        LIMIT 5
    """, (since,))
    status['recent_trades'] = c.fetchall()

    conn.close()
    return status


def print_status(status, clear=False):
    if clear:
        print("\033[2J\033[H", end="")  # Clear screen and move to top

    print("=" * 70)
    print("🤖 POLYMARKET AGENT - PAPER TRADING MONITOR")
    print("=" * 70)

    # Agent status
    if status['idle_seconds'] is not None:
        if status['idle_seconds'] < 60:
            state = "🟢 ACTIVE"
            idle = format_duration(status['idle_seconds'])
        elif status['idle_seconds'] < 300:
            state = "🟡 IDLE"
            idle = format_duration(status['idle_seconds'])
        else:
            state = "🔴 STOPPED"
            idle = format_duration(status['idle_seconds'])
        print(f"\nAgent State: {state} (last signal: {idle} ago)")
    else:
        print(f"\nAgent State: 🔴 NO ACTIVITY")

    print(f"Active Markets: {status['active_markets']:,}")

    # Recent activity (last 10 min)
    print(f"\n{'─' * 40}")
    print("RECENT ACTIVITY (last 10 min)")
    print(f"{'─' * 40}")
    print(f"  Signals generated: {status['signals_10m']}")
    print(f"  Decisions made:    {status['decisions_10m']}")
    print(f"  Orders placed:     {status['orders_10m']}")

    if status['recent_actions']:
        print(f"\n  Breakdown:")
        for action, count in sorted(status['recent_actions'].items()):
            icon = "✓" if action.startswith("BUY") or action.startswith("SELL") else "✗"
            print(f"    {icon} {action}: {count}")

    # All-time stats
    print(f"\n{'─' * 40}")
    print("ALL-TIME STATS")
    print(f"{'─' * 40}")
    print(f"  Total signals:     {status['total_signals']:,}")
    print(f"  Total decisions:   {status['total_decisions']:,}")
    print(f"  Trades executed:   {status['total_filled']}")

    # Recent trades
    if status['recent_trades']:
        print(f"\n{'─' * 40}")
        print("RECENT TRADES")
        print(f"{'─' * 40}")
        for trade in status['recent_trades']:
            q = trade['question'][:45]
            ts = datetime.fromisoformat(trade['created_at']).strftime("%H:%M:%S")
            icon = "📈" if trade['side'] == "BUY" else "📉"
            print(f"  {icon} {ts} | {trade['side']:4} {trade['size']:>6.1f} @ ${trade['price']:.3f} | {trade['status']}")
            print(f"     {q}...")

    print(f"\n{'=' * 70}")
    print(f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if status['orders_10m'] == 0 and status['signals_10m'] > 0:
        print("\n⚠️  Agent is generating signals but not trading (check edge thresholds)")


def show_trades():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 80)
    print("📊 TRADE HISTORY")
    print("=" * 80)

    c.execute("""
        SELECT
            m.question,
            d.action,
            d.ensemble_prob,
            d.market_prob,
            d.edge,
            d.size_usd,
            d.executed,
            d.reason,
            d.created_at
        FROM decisions d
        JOIN markets m ON d.market_id = m.market_id
        ORDER BY d.created_at DESC
        LIMIT 20
    """)

    for row in c.fetchall():
        ts = datetime.fromisoformat(row['created_at']).strftime("%m-%d %H:%M")
        q = row['question'][:50]

        if row['executed']:
            icon = "✓"
            color = "\033[92m"  # Green
        else:
            icon = "✗"
            color = "\033[91m"  # Red

        reset = "\033[0m"
        print(f"\n{color}{icon} {ts} | {row['action']:8}{reset}")
        print(f"   {q}")
        print(f"   Ensemble: {row['ensemble_prob']:.1%} | Market: {row['market_prob']:.1%} | Edge: {row['edge']:+.1%}")

        if row['executed']:
            print(f"   Size: ${row['size_usd']:.2f}")
        else:
            print(f"   Reason: {row['reason']}")

    conn.close()


def watch_mode():
    print("Starting watch mode (Ctrl+C to exit)...\n")
    try:
        while True:
            status = get_status()
            print_status(status, clear=True)
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n\nExiting watch mode.")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Monitor the paper trading agent")
    parser.add_argument("--watch", action="store_true", help="Auto-refresh every 10s")
    parser.add_argument("--trades", action="store_true", help="Show trade history")
    args = parser.parse_args()

    if args.trades:
        show_trades()
    elif args.watch:
        watch_mode()
    else:
        status = get_status()
        print_status(status)
        print("\nTip: Use --watch for live updates, --trades for full history")


if __name__ == "__main__":
    main()
