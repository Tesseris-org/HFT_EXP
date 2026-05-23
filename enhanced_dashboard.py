#!/usr/bin/env python3
"""
Premium Glassmorphism Dashboard - Polymarket Trading Agent
Dark theme with glassmorphic effects and modern design.

Usage:
    python enhanced_dashboard.py

Then open: http://localhost:5556
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web


DB_PATH = Path(__file__).parent / "agent.db"


def get_full_status():
    """Get comprehensive status including P&L, trades, positions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    status = {}

    # Agent state
    c.execute("SELECT COUNT(*) FROM markets WHERE active = 1")
    status['active_markets'] = c.fetchone()[0]

    # Time range
    c.execute("SELECT MIN(datetime(created_at)), MAX(datetime(created_at)) FROM decisions")
    row = c.fetchone()
    if row[0] and row[1]:
        start_time = datetime.fromisoformat(row[0])
        end_time = datetime.fromisoformat(row[1])
        status['start_time'] = start_time.isoformat()
        status['end_time'] = end_time.isoformat()
        status['duration_minutes'] = (end_time - start_time).total_seconds() / 60
    else:
        status['start_time'] = None
        status['end_time'] = None
        status['duration_minutes'] = 0

    # Decisions
    c.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN executed=1 THEN 1 END) as executed FROM decisions")
    row = c.fetchone()
    status['total_decisions'] = row['total']
    status['executed_trades'] = row['executed']
    status['execution_rate'] = (row['executed'] / row['total'] * 100) if row['total'] > 0 else 0

    # Trade breakdown
    c.execute("SELECT action, COUNT(*) as count FROM decisions WHERE executed=1 GROUP BY action")
    status['trade_breakdown'] = {row['action']: row['count'] for row in c.fetchall()}

    # P&L
    c.execute("SELECT SUM(realized_pnl) as realized, SUM(unrealized_pnl) as unrealized FROM positions")
    row = c.fetchone()
    status['realized_pnl'] = row['realized'] or 0
    status['unrealized_pnl'] = row['unrealized'] or 0
    status['total_pnl'] = status['realized_pnl'] + status['unrealized_pnl']

    # Current positions
    c.execute("SELECT COUNT(*), SUM(size * avg_cost) FROM positions WHERE size > 0")
    row = c.fetchone()
    status['open_positions'] = row[0] or 0
    status['total_exposure'] = row[1] or 0

    # P&L over time (for chart)
    c.execute("""
        SELECT
            datetime(created_at) as timestamp,
            SUM(CASE WHEN action IN ('BUY_YES', 'BUY_NO') THEN size_usd ELSE 0 END)
                OVER (ORDER BY created_at) as cumulative_capital
        FROM decisions
        WHERE executed = 1
        ORDER BY created_at
    """)
    status['capital_deployed_chart'] = [
        {'time': row['timestamp'], 'capital': row['cumulative_capital']}
        for row in c.fetchall()
    ]

    # Recent trades with full details
    c.execute("""
        SELECT
            m.question,
            d.action,
            d.ensemble_prob,
            d.market_prob,
            d.edge,
            d.size_usd,
            d.reason,
            datetime(d.created_at) as created_at
        FROM decisions d
        JOIN markets m ON d.market_id = m.market_id
        WHERE d.executed = 1
        ORDER BY d.created_at DESC
        LIMIT 30
    """)
    status['recent_trades'] = [dict(row) for row in c.fetchall()]

    # Top positions by P&L
    c.execute("""
        SELECT
            m.question,
            p.side,
            p.size,
            p.avg_cost,
            p.unrealized_pnl,
            p.realized_pnl
        FROM positions p
        JOIN markets m ON p.market_id = m.market_id
        WHERE p.size > 0
        ORDER BY (p.unrealized_pnl + p.realized_pnl) DESC
        LIMIT 10
    """)
    status['top_positions'] = [dict(row) for row in c.fetchall()]

    # Performance metrics
    c.execute("""
        SELECT
            AVG(edge) as avg_edge,
            MIN(edge) as min_edge,
            MAX(edge) as max_edge,
            AVG(size_usd) as avg_size
        FROM decisions
        WHERE executed = 1 AND action IN ('BUY_YES', 'BUY_NO')
    """)
    row = c.fetchone()
    if row and row['avg_edge'] is not None:
        status['avg_edge'] = row['avg_edge']
        status['min_edge'] = row['min_edge']
        status['max_edge'] = row['max_edge']
        status['avg_trade_size'] = row['avg_size']
    else:
        status['avg_edge'] = 0
        status['min_edge'] = 0
        status['max_edge'] = 0
        status['avg_trade_size'] = 0

    # Last activity
    c.execute("SELECT MAX(created_at) FROM signals")
    last_signal = c.fetchone()[0]
    if last_signal:
        status['last_activity'] = datetime.fromisoformat(last_signal).isoformat()
        status['idle_seconds'] = (datetime.utcnow() - datetime.fromisoformat(last_signal)).total_seconds()
    else:
        status['last_activity'] = None
        status['idle_seconds'] = None

    # Recent decisions (all, not just executed) for activity feed
    c.execute("""
        SELECT
            m.question,
            d.action,
            d.ensemble_prob,
            d.market_prob,
            d.edge,
            d.size_usd,
            d.reason,
            datetime(d.created_at) as created_at
        FROM decisions d
        JOIN markets m ON d.market_id = m.market_id
        ORDER BY d.created_at DESC
        LIMIT 20
    """)
    status['recent_decisions'] = [dict(row) for row in c.fetchall()]

    # Positions with potential profit
    c.execute("""
        SELECT
            m.question,
            p.side,
            p.size,
            p.avg_cost,
            p.unrealized_pnl,
            p.realized_pnl,
            ROUND(p.size * (1.0 - p.avg_cost), 2) as potential_profit
        FROM positions p
        JOIN markets m ON p.market_id = m.market_id
        WHERE p.size > 0
        ORDER BY p.unrealized_pnl DESC
    """)
    status['positions_detail'] = [dict(row) for row in c.fetchall()]

    # Meta-agent iterations
    meta_log = Path(__file__).parent / "meta_iterations.jsonl"
    status['meta_iterations'] = []
    if meta_log.exists():
        try:
            with open(meta_log) as f:
                for line in f:
                    entry = json.loads(line.strip())
                    status['meta_iterations'].append({
                        'iteration': entry.get('iteration'),
                        'pnl': entry.get('pnl'),
                        'trades': entry.get('trades'),
                        'action': entry.get('action'),
                        'timestamp': entry.get('timestamp', ''),
                    })
        except Exception:
            pass

    conn.close()
    return status


async def handle_api(request):
    """API endpoint for status data."""
    status = get_full_status()
    return web.json_response(status)


async def handle_index(request):
    """Serve premium glassmorphic dashboard."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Polymarket Agent - Trading Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Exo+2:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --bg-primary: #0a0e1a;
            --bg-secondary: #0f1419;
            --glass-bg: rgba(15, 20, 30, 0.7);
            --glass-border: rgba(0, 248, 250, 0.15);
            --accent-primary: #00f8fa;
            --accent-secondary: #00c4c6;
            --accent-tertiary: #009a9c;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
        }

        body {
            font-family: 'Exo 2', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            background-image:
                radial-gradient(at 0% 0%, rgba(0, 248, 250, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(0, 196, 198, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(0, 248, 250, 0.12) 0px, transparent 50%),
                radial-gradient(at 0% 100%, rgba(0, 154, 156, 0.15) 0px, transparent 50%);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 24px;
            position: relative;
            overflow-x: hidden;
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
        }

        /* Glassmorphic Card */
        .glass-card {
            background: var(--glass-bg);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid var(--glass-border);
            border-radius: 20px;
            padding: 24px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }

        .glass-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--glass-border), transparent);
        }

        .glass-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }

        /* Header */
        .header {
            text-align: center;
            margin-bottom: 40px;
            animation: fadeInDown 0.6s ease-out;
        }

        .header h1 {
            font-size: 3em;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary), var(--accent-tertiary));
            background-size: 200% 200%;
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: gradientFlow 8s ease infinite;
            margin-bottom: 8px;
            letter-spacing: -0.02em;
        }

        .header .subtitle {
            color: var(--text-secondary);
            font-size: 1.1em;
            font-weight: 400;
            letter-spacing: 0.02em;
        }

        /* P&L Hero Section */
        .pnl-hero {
            background: linear-gradient(135deg, rgba(0, 248, 250, 0.1), rgba(0, 196, 198, 0.08));
            backdrop-filter: blur(20px);
            border: 1px solid var(--glass-border);
            border-radius: 24px;
            padding: 48px 32px;
            margin-bottom: 32px;
            text-align: center;
            position: relative;
            overflow: hidden;
            animation: fadeInUp 0.6s ease-out 0.1s backwards;
        }

        .pnl-hero::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(0, 248, 250, 0.15) 0%, transparent 70%);
            animation: rotate 20s linear infinite;
        }

        .pnl-hero-content {
            position: relative;
            z-index: 1;
        }

        .pnl-label {
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            color: var(--text-secondary);
            margin-bottom: 12px;
            font-weight: 600;
        }

        .pnl-main {
            font-size: 5em;
            font-weight: 800;
            margin-bottom: 12px;
            line-height: 1;
            letter-spacing: -0.02em;
            text-shadow: 0 0 40px currentColor;
        }

        .pnl-positive {
            color: var(--success);
        }

        .pnl-negative {
            color: var(--danger);
        }

        .pnl-neutral {
            color: var(--text-secondary);
        }

        .pnl-breakdown {
            display: flex;
            justify-content: center;
            gap: 48px;
            margin-top: 32px;
            flex-wrap: wrap;
        }

        .pnl-item {
            text-align: center;
        }

        .pnl-item-label {
            font-size: 0.75em;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            margin-bottom: 8px;
            font-weight: 600;
        }

        .pnl-item-value {
            font-size: 1.8em;
            font-weight: 700;
            letter-spacing: -0.01em;
        }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 32px;
        }

        .stat-card {
            animation: fadeInUp 0.6s ease-out backwards;
        }

        .stat-card:nth-child(1) { animation-delay: 0.2s; }
        .stat-card:nth-child(2) { animation-delay: 0.25s; }
        .stat-card:nth-child(3) { animation-delay: 0.3s; }
        .stat-card:nth-child(4) { animation-delay: 0.35s; }

        .stat-card h3 {
            font-size: 0.75em;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-secondary);
            margin-bottom: 12px;
            font-weight: 600;
        }

        .stat-value {
            font-size: 2.5em;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 8px;
            letter-spacing: -0.01em;
        }

        .stat-sub {
            color: var(--text-muted);
            font-size: 0.9em;
        }

        /* Status Indicator */
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .status-active {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            box-shadow: 0 0 20px rgba(16, 185, 129, 0.3);
        }

        .status-idle {
            background: rgba(245, 158, 11, 0.15);
            color: var(--warning);
        }

        .status-stopped {
            background: rgba(239, 68, 68, 0.15);
            color: var(--danger);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
            animation: pulse 2s ease-in-out infinite;
        }

        /* Charts */
        .chart-section {
            animation: fadeInUp 0.6s ease-out 0.4s backwards;
            margin-bottom: 32px;
        }

        .chart-section h2 {
            color: var(--text-primary);
            margin-bottom: 20px;
            font-size: 1.3em;
            font-weight: 700;
            letter-spacing: -0.01em;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .chart-section h2 svg {
            color: var(--accent-primary);
        }

        .chart-wrapper {
            position: relative;
            height: 300px;
        }

        /* Trades Section */
        .trades-section {
            animation: fadeInUp 0.6s ease-out 0.5s backwards;
        }

        .trades-section h2 {
            color: var(--text-primary);
            margin-bottom: 20px;
            font-size: 1.3em;
            font-weight: 700;
            letter-spacing: -0.01em;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .trades-section h2 svg {
            color: var(--accent-primary);
        }

        .trade-card {
            background: rgba(15, 20, 30, 0.5);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
            border-left: 3px solid;
            transition: all 0.2s ease;
        }

        .trade-card:hover {
            background: rgba(15, 20, 30, 0.7);
            transform: translateX(4px);
        }

        .trade-buy {
            border-left-color: var(--success);
        }

        .trade-sell {
            border-left-color: var(--danger);
        }

        .trade-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            margin-bottom: 12px;
        }

        .trade-question {
            font-weight: 600;
            color: var(--text-primary);
            flex: 1;
            font-size: 0.95em;
        }

        .trade-badge {
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 0.75em;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
        }

        .badge-buy {
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
        }

        .badge-sell {
            background: rgba(239, 68, 68, 0.2);
            color: var(--danger);
        }

        .trade-metrics {
            display: flex;
            gap: 24px;
            font-size: 0.85em;
            color: var(--text-secondary);
            flex-wrap: wrap;
        }

        .trade-metric {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .metric-label {
            color: var(--text-muted);
        }

        .metric-value {
            font-weight: 600;
            color: var(--text-primary);
        }

        .edge-positive {
            color: var(--success) !important;
            font-weight: 700;
        }

        .edge-negative {
            color: var(--danger) !important;
            font-weight: 700;
        }

        /* Footer */
        .footer {
            text-align: center;
            color: var(--text-muted);
            margin-top: 48px;
            padding: 24px;
            font-size: 0.85em;
        }

        .footer-item {
            margin: 8px 0;
        }

        /* Animations */
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes gradientFlow {
            0%, 100% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
        }

        @keyframes rotate {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(1.1); }
        }

        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 10px;
        }

        ::-webkit-scrollbar-track {
            background: var(--bg-secondary);
        }

        ::-webkit-scrollbar-thumb {
            background: var(--glass-border);
            border-radius: 5px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.2);
        }

        /* Responsive */
        @media (max-width: 768px) {
            .header h1 {
                font-size: 2em;
            }

            .pnl-main {
                font-size: 3em;
            }

            .pnl-breakdown {
                gap: 24px;
            }

            .stats-grid {
                grid-template-columns: 1fr;
            }

            .trade-metrics {
                flex-direction: column;
                gap: 8px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>POLYMARKET AGENT</h1>
            <div class="subtitle">Adaptive AI Trading System</div>
        </div>

        <!-- P&L Hero Section -->
        <div class="pnl-hero">
            <div class="pnl-hero-content">
                <div class="pnl-label">Total P&L</div>
                <div class="pnl-main pnl-neutral" id="pnl-main">$0.00</div>
                <div class="pnl-breakdown">
                    <div class="pnl-item">
                        <div class="pnl-item-label">Realized</div>
                        <div class="pnl-item-value" id="pnl-realized">$0.00</div>
                    </div>
                    <div class="pnl-item">
                        <div class="pnl-item-label">Unrealized</div>
                        <div class="pnl-item-value" id="pnl-unrealized">$0.00</div>
                    </div>
                    <div class="pnl-item">
                        <div class="pnl-item-label">Return</div>
                        <div class="pnl-item-value" id="pnl-return">0.0%</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Stats Grid -->
        <div class="stats-grid">
            <div class="stat-card glass-card">
                <h3>Agent Status</h3>
                <div class="stat-value">
                    <span class="status-badge status-stopped" id="agent-status">
                        <span class="status-dot"></span>
                        <span>Loading</span>
                    </span>
                </div>
            </div>
            <div class="stat-card glass-card">
                <h3>Trades Executed</h3>
                <div class="stat-value" id="trades-count">0</div>
                <div class="stat-sub" id="trades-sub">0% execution rate</div>
            </div>
            <div class="stat-card glass-card">
                <h3>Open Positions</h3>
                <div class="stat-value" id="positions-count">0</div>
                <div class="stat-sub" id="positions-exposure">$0 exposure</div>
            </div>
            <div class="stat-card glass-card">
                <h3>Average Edge</h3>
                <div class="stat-value" id="avg-edge">0.0%</div>
                <div class="stat-sub" id="edge-range">Min 0% - Max 0%</div>
            </div>
        </div>

        <!-- Chart -->
        <div class="chart-section glass-card">
            <h2>
                <i data-lucide="bar-chart-3" width="24" height="24"></i>
                Capital Deployed Over Time
            </h2>
            <div class="chart-wrapper">
                <canvas id="capitalChart"></canvas>
            </div>
        </div>

        <!-- Two Column: Positions + Meta Agent -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 32px;">

            <!-- Open Positions -->
            <div class="glass-card" style="animation: fadeInUp 0.6s ease-out 0.45s backwards;">
                <h2 style="color: var(--text-primary); margin-bottom: 16px; font-size: 1.3em; font-weight: 700; display: flex; align-items: center; gap: 12px;">
                    <i data-lucide="layers" width="24" height="24" style="color: var(--accent-primary);"></i>
                    Open Positions
                </h2>
                <div id="positions-list" style="max-height: 400px; overflow-y: auto;">
                    <div style="text-align: center; padding: 30px; color: var(--text-muted);">No positions yet</div>
                </div>
            </div>

            <!-- Meta Agent Log -->
            <div class="glass-card" style="animation: fadeInUp 0.6s ease-out 0.45s backwards;">
                <h2 style="color: var(--text-primary); margin-bottom: 16px; font-size: 1.3em; font-weight: 700; display: flex; align-items: center; gap: 12px;">
                    <i data-lucide="cpu" width="24" height="24" style="color: var(--accent-primary);"></i>
                    Meta-Agent Iterations
                </h2>
                <div id="meta-log" style="max-height: 400px; overflow-y: auto;">
                    <div style="text-align: center; padding: 30px; color: var(--text-muted);">No iterations yet</div>
                </div>
            </div>
        </div>

        <!-- Activity Feed -->
        <div class="glass-card" style="margin-bottom: 32px; animation: fadeInUp 0.6s ease-out 0.48s backwards;">
            <h2 style="color: var(--text-primary); margin-bottom: 16px; font-size: 1.3em; font-weight: 700; display: flex; align-items: center; gap: 12px;">
                <i data-lucide="activity" width="24" height="24" style="color: var(--accent-primary);"></i>
                Live Activity Feed
            </h2>
            <div id="activity-feed" style="max-height: 300px; overflow-y: auto;">
                <div style="text-align: center; padding: 30px; color: var(--text-muted);">Waiting for decisions...</div>
            </div>
        </div>

        <!-- Recent Trades -->
        <div class="trades-section glass-card">
            <h2>
                <i data-lucide="briefcase" width="24" height="24"></i>
                Recent Trades
            </h2>
            <div id="trades-list">
                <div style="text-align: center; padding: 40px; color: var(--text-muted);">
                    No trades yet. Agent is analyzing markets...
                </div>
            </div>
        </div>

        <div class="footer">
            <div class="footer-item" id="last-updated">Last updated: --</div>
            <div class="footer-item">Auto-refreshing every 5 seconds</div>
            <div class="footer-item" style="margin-top: 12px; color: var(--text-muted);">
                Powered by Claude & Adaptive Learning
            </div>
        </div>
    </div>

    <script>
        // Initialize Lucide icons
        lucide.createIcons();
    </script>
    <script>
        let capitalChart = null;
        const STARTING_BALANCE = 1000;

        async function updateDashboard() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                // P&L Hero Section
                const totalPnl = data.total_pnl;
                const pnlElement = document.getElementById('pnl-main');
                pnlElement.textContent = `$${totalPnl.toFixed(2)}`;

                if (totalPnl > 0) {
                    pnlElement.className = 'pnl-main pnl-positive';
                } else if (totalPnl < 0) {
                    pnlElement.className = 'pnl-main pnl-negative';
                } else {
                    pnlElement.className = 'pnl-main pnl-neutral';
                }

                document.getElementById('pnl-realized').textContent = `$${data.realized_pnl.toFixed(2)}`;
                document.getElementById('pnl-unrealized').textContent = `$${data.unrealized_pnl.toFixed(2)}`;

                const returnPct = (totalPnl / STARTING_BALANCE * 100).toFixed(2);
                const returnElement = document.getElementById('pnl-return');
                returnElement.textContent = `${returnPct > 0 ? '+' : ''}${returnPct}%`;
                returnElement.className = totalPnl > 0 ? 'pnl-item-value pnl-positive' : totalPnl < 0 ? 'pnl-item-value pnl-negative' : 'pnl-item-value pnl-neutral';

                // Agent Status
                let statusClass = 'status-stopped';
                let statusText = 'STOPPED';

                if (data.idle_seconds !== null) {
                    if (data.idle_seconds < 60) {
                        statusClass = 'status-active';
                        statusText = 'ACTIVE';
                    } else if (data.idle_seconds < 300) {
                        statusClass = 'status-idle';
                        statusText = 'IDLE';
                    }
                }

                document.getElementById('agent-status').innerHTML =
                    `<span class="status-dot"></span><span>${statusText}</span>`;
                document.getElementById('agent-status').className = `status-badge ${statusClass}`;

                // Trades Stats
                document.getElementById('trades-count').textContent = data.executed_trades;
                document.getElementById('trades-sub').textContent =
                    `${data.execution_rate.toFixed(1)}% execution rate`;

                // Positions
                document.getElementById('positions-count').textContent = data.open_positions;
                document.getElementById('positions-exposure').textContent =
                    `$${data.total_exposure.toFixed(2)} exposure`;

                // Edge Stats
                document.getElementById('avg-edge').textContent =
                    `${(data.avg_edge * 100).toFixed(1)}%`;
                document.getElementById('edge-range').textContent =
                    `Min ${(data.min_edge * 100).toFixed(1)}% - Max ${(data.max_edge * 100).toFixed(1)}%`;

                // Capital Chart
                if (data.capital_deployed_chart && data.capital_deployed_chart.length > 0) {
                    const labels = data.capital_deployed_chart.map(d => {
                        const time = new Date(d.time);
                        return time.toLocaleTimeString();
                    });
                    const values = data.capital_deployed_chart.map(d => d.capital);

                    if (capitalChart) {
                        capitalChart.data.labels = labels;
                        capitalChart.data.datasets[0].data = values;
                        capitalChart.update('none');
                    } else {
                        const ctx = document.getElementById('capitalChart').getContext('2d');
                        capitalChart = new Chart(ctx, {
                            type: 'line',
                            data: {
                                labels: labels,
                                datasets: [{
                                    label: 'Capital Deployed',
                                    data: values,
                                    borderColor: 'rgba(0, 248, 250, 1)',
                                    backgroundColor: 'rgba(0, 248, 250, 0.1)',
                                    tension: 0.4,
                                    fill: true,
                                    borderWidth: 3,
                                    pointBackgroundColor: 'rgba(0, 248, 250, 1)',
                                    pointBorderColor: '#0a0e1a',
                                    pointBorderWidth: 2,
                                    pointRadius: 4,
                                    pointHoverRadius: 6
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {
                                    legend: {
                                        display: false
                                    }
                                },
                                scales: {
                                    y: {
                                        ticks: {
                                            color: '#94a3b8',
                                            font: { family: 'Exo 2' }
                                        },
                                        grid: { color: 'rgba(0, 248, 250, 0.05)' }
                                    },
                                    x: {
                                        ticks: {
                                            color: '#94a3b8',
                                            font: { family: 'Exo 2' }
                                        },
                                        grid: { color: 'rgba(0, 248, 250, 0.05)' }
                                    }
                                },
                                interaction: {
                                    intersect: false,
                                    mode: 'index'
                                }
                            }
                        });
                    }
                }

                // Open Positions
                let posHtml = '';
                if (data.positions_detail && data.positions_detail.length > 0) {
                    data.positions_detail.forEach(pos => {
                        const pnlColor = pos.unrealized_pnl >= 0 ? 'var(--success)' : 'var(--danger)';
                        const pnlSign = pos.unrealized_pnl >= 0 ? '+' : '';
                        const potentialProfit = pos.potential_profit || 0;
                        const costBasis = (pos.size * pos.avg_cost).toFixed(2);
                        posHtml += `
                            <div style="background: rgba(15,20,30,0.5); border: 1px solid var(--glass-border); border-radius: 10px; padding: 12px; margin-bottom: 8px; border-left: 3px solid ${pnlColor};">
                                <div style="font-weight: 600; font-size: 0.9em; margin-bottom: 6px; color: var(--text-primary);">${pos.question.substring(0, 60)}</div>
                                <div style="display: flex; gap: 16px; font-size: 0.8em; flex-wrap: wrap;">
                                    <span style="color: var(--text-muted);">Side: <span style="color: var(--accent-primary); font-weight: 600;">${pos.side}</span></span>
                                    <span style="color: var(--text-muted);">Cost: <span style="color: var(--text-primary); font-weight: 600;">$${costBasis}</span></span>
                                    <span style="color: var(--text-muted);">P&L: <span style="color: ${pnlColor}; font-weight: 700;">${pnlSign}$${pos.unrealized_pnl.toFixed(2)}</span></span>
                                    <span style="color: var(--text-muted);">If correct: <span style="color: var(--success); font-weight: 600;">+$${potentialProfit.toFixed(2)}</span></span>
                                </div>
                            </div>
                        `;
                    });
                    // Total potential
                    const totalPotential = data.positions_detail.reduce((s, p) => s + (p.potential_profit || 0), 0);
                    posHtml += `<div style="text-align: right; margin-top: 8px; font-size: 0.85em; color: var(--text-muted);">Total potential if all correct: <span style="color: var(--success); font-weight: 700;">+$${totalPotential.toFixed(2)}</span></div>`;
                } else {
                    posHtml = '<div style="text-align: center; padding: 30px; color: var(--text-muted);">No positions yet</div>';
                }
                document.getElementById('positions-list').innerHTML = posHtml;

                // Meta-Agent Iterations
                let metaHtml = '';
                if (data.meta_iterations && data.meta_iterations.length > 0) {
                    data.meta_iterations.forEach(iter => {
                        const pnlColor = iter.pnl >= 0 ? 'var(--success)' : 'var(--danger)';
                        let actionBadge = '';
                        if (iter.action === 'no_changes') actionBadge = '<span style="background: rgba(16,185,129,0.2); color: var(--success); padding: 2px 8px; border-radius: 6px; font-size: 0.75em; font-weight: 600;">OK</span>';
                        else if (iter.action === 'fix') actionBadge = '<span style="background: rgba(245,158,11,0.2); color: var(--warning); padding: 2px 8px; border-radius: 6px; font-size: 0.75em; font-weight: 600;">FIX</span>';
                        else if (iter.action === 'wait') actionBadge = '<span style="background: rgba(148,163,184,0.2); color: var(--text-secondary); padding: 2px 8px; border-radius: 6px; font-size: 0.75em; font-weight: 600;">WAIT</span>';
                        else actionBadge = `<span style="background: rgba(148,163,184,0.1); color: var(--text-muted); padding: 2px 8px; border-radius: 6px; font-size: 0.75em;">${iter.action}</span>`;

                        metaHtml += `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border-bottom: 1px solid rgba(0,248,250,0.05); font-size: 0.85em;">
                                <span style="color: var(--text-secondary); font-weight: 600;">Iter ${iter.iteration}</span>
                                <span style="color: ${pnlColor}; font-weight: 700;">$${iter.pnl.toFixed(2)}</span>
                                <span style="color: var(--text-muted);">${iter.trades} trades</span>
                                ${actionBadge}
                            </div>
                        `;
                    });
                } else {
                    metaHtml = '<div style="text-align: center; padding: 30px; color: var(--text-muted);">No iterations yet</div>';
                }
                document.getElementById('meta-log').innerHTML = metaHtml;

                // Activity Feed (all decisions, not just trades)
                let actHtml = '';
                if (data.recent_decisions && data.recent_decisions.length > 0) {
                    data.recent_decisions.slice(0, 15).forEach(d => {
                        const time = new Date(d.created_at).toLocaleTimeString();
                        let icon = '', color = '';
                        if (d.action === 'HOLD') { icon = 'pause'; color = 'var(--text-muted)'; }
                        else if (d.action.includes('BUY')) { icon = 'trending-up'; color = 'var(--success)'; }
                        else { icon = 'trending-down'; color = 'var(--danger)'; }

                        const edgeStr = d.edge ? `${(d.edge * 100).toFixed(1)}%` : '--';
                        const reason = d.reason ? d.reason.substring(0, 40) : '';

                        actHtml += `
                            <div style="display: flex; align-items: center; gap: 12px; padding: 6px 0; border-bottom: 1px solid rgba(0,248,250,0.03); font-size: 0.82em;">
                                <span style="color: var(--text-muted); min-width: 65px;">${time}</span>
                                <span style="color: ${color}; font-weight: 700; min-width: 70px;">${d.action}</span>
                                <span style="color: var(--text-primary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${d.question.substring(0, 50)}</span>
                                <span style="color: var(--accent-primary); min-width: 50px; text-align: right;">${edgeStr}</span>
                                <span style="color: var(--text-muted); min-width: 100px; text-align: right; font-size: 0.9em;">${reason}</span>
                            </div>
                        `;
                    });
                } else {
                    actHtml = '<div style="text-align: center; padding: 30px; color: var(--text-muted);">Waiting for decisions...</div>';
                }
                document.getElementById('activity-feed').innerHTML = actHtml;

                // Recent Trades
                let tradesHtml = '';
                if (data.recent_trades && data.recent_trades.length > 0) {
                    data.recent_trades.slice(0, 15).forEach(trade => {
                        const isBuy = trade.action.includes('BUY');
                        const tradeClass = isBuy ? 'trade-buy' : 'trade-sell';
                        const badgeClass = isBuy ? 'badge-buy' : 'badge-sell';
                        const edgeClass = trade.edge >= 0 ? 'edge-positive' : 'edge-negative';
                        const edgeSign = trade.edge >= 0 ? '+' : '';
                        const time = new Date(trade.created_at).toLocaleTimeString();

                        tradesHtml += `
                            <div class="trade-card ${tradeClass}">
                                <div class="trade-header">
                                    <div class="trade-question">${trade.question.substring(0, 100)}</div>
                                    <div class="trade-badge ${badgeClass}">${trade.action}</div>
                                </div>
                                <div class="trade-metrics">
                                    <div class="trade-metric">
                                        <span class="metric-label">AI:</span>
                                        <span class="metric-value">${(trade.ensemble_prob * 100).toFixed(1)}%</span>
                                    </div>
                                    <div class="trade-metric">
                                        <span class="metric-label">Market:</span>
                                        <span class="metric-value">${(trade.market_prob * 100).toFixed(1)}%</span>
                                    </div>
                                    <div class="trade-metric">
                                        <span class="metric-label">Edge:</span>
                                        <span class="metric-value ${edgeClass}">${edgeSign}${(trade.edge * 100).toFixed(1)}%</span>
                                    </div>
                                    <div class="trade-metric">
                                        <span class="metric-label">Size:</span>
                                        <span class="metric-value">$${trade.size_usd.toFixed(2)}</span>
                                    </div>
                                    <div class="trade-metric">
                                        <span class="metric-label">Time:</span>
                                        <span class="metric-value">${time}</span>
                                    </div>
                                </div>
                            </div>
                        `;
                    });
                } else {
                    tradesHtml = '<div style="text-align: center; padding: 40px; color: var(--text-muted);">No trades yet. Agent is analyzing markets...</div>';
                }

                document.getElementById('trades-list').innerHTML = tradesHtml;

                // Last Updated
                const now = new Date().toLocaleString();
                document.getElementById('last-updated').textContent = `Last updated: ${now}`;

            } catch (error) {
                console.error('Error updating dashboard:', error);
            }
        }

        // Initial load
        updateDashboard();

        // Auto-refresh every 5 seconds
        setInterval(updateDashboard, 5000);
    </script>
</body>
</html>
    """
    return web.Response(text=html, content_type='text/html')


async def start_server():
    """Start the enhanced glassmorphic dashboard server."""
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/status', handle_api)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 5556)
    await site.start()

    print("=" * 70)
    print("🎨 PREMIUM GLASSMORPHIC DASHBOARD STARTED")
    print("=" * 70)
    print()
    print("Open in your browser:")
    print("  👉 http://localhost:5556")
    print()
    print("Features:")
    print("  • Premium glassmorphic design")
    print("  • Adaptive learning visualization")
    print("  • Real-time P&L tracking")
    print("  • Smooth animations & gradients")
    print("  • Auto-updates every 5 seconds")
    print()
    print("Press Ctrl+C to stop")
    print()
    print("=" * 70)

    # Keep the server running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == '__main__':
    asyncio.run(start_server())
