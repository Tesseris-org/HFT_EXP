#!/usr/bin/env python3
"""
Simple web dashboard for monitoring the paper trading agent.

Usage:
    python web_dashboard.py

Then open: http://localhost:5555
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from aiohttp import web


DB_PATH = Path(__file__).parent / "agent.db"


def get_status():
    """Get current agent status."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    status = {}

    # Active markets
    c.execute("SELECT COUNT(*) FROM markets WHERE active = 1")
    status['active_markets'] = c.fetchone()[0]

    # Recent activity (last 10 min)
    since = (datetime.utcnow() - timedelta(minutes=10)).isoformat()

    c.execute(f"SELECT COUNT(*) FROM signals WHERE created_at > ?", (since,))
    status['signals_10m'] = c.fetchone()[0]

    c.execute(f"SELECT COUNT(*) FROM decisions WHERE created_at > ?", (since,))
    status['decisions_10m'] = c.fetchone()[0]

    c.execute(f"SELECT COUNT(*) FROM orders WHERE created_at > ?", (since,))
    status['orders_10m'] = c.fetchone()[0]

    # All-time
    c.execute("SELECT COUNT(*) FROM signals")
    status['total_signals'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM decisions")
    status['total_decisions'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders WHERE status = 'filled'")
    status['total_filled'] = c.fetchone()[0]

    # Recent actions
    c.execute(f"SELECT action, COUNT(*) as cnt FROM decisions WHERE created_at > ? GROUP BY action", (since,))
    status['recent_actions'] = {row['action']: row['cnt'] for row in c.fetchall()}

    # Last activity
    c.execute("SELECT MAX(created_at) FROM signals")
    last_signal = c.fetchone()[0]
    if last_signal:
        status['last_activity'] = datetime.fromisoformat(last_signal).isoformat()
        status['idle_seconds'] = (datetime.utcnow() - datetime.fromisoformat(last_signal)).total_seconds()
    else:
        status['last_activity'] = None
        status['idle_seconds'] = None

    # Recent trades
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
            datetime(d.created_at) as created_at
        FROM decisions d
        JOIN markets m ON d.market_id = m.market_id
        ORDER BY d.created_at DESC
        LIMIT 20
    """)
    trades = []
    for row in c.fetchall():
        trades.append({
            'question': row['question'],
            'action': row['action'],
            'ensemble_prob': row['ensemble_prob'],
            'market_prob': row['market_prob'],
            'edge': row['edge'],
            'size_usd': row['size_usd'],
            'executed': bool(row['executed']),
            'reason': row['reason'],
            'created_at': row['created_at'],
        })
    status['recent_trades'] = trades

    conn.close()
    return status


async def handle_api(request):
    """API endpoint for status data."""
    status = get_status()
    return web.json_response(status)


async def handle_index(request):
    """Serve the main HTML page."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Polymarket Agent Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        h1 {
            color: white;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.5em;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }

        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .card {
            background: white;
            border-radius: 12px;
            padding: 25px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }

        .card h2 {
            color: #333;
            font-size: 1.2em;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }

        .status-badge {
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9em;
        }

        .status-active {
            background: #10b981;
            color: white;
        }

        .status-idle {
            background: #fbbf24;
            color: white;
        }

        .status-stopped {
            background: #ef4444;
            color: white;
        }

        .metric {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #e5e7eb;
        }

        .metric:last-child {
            border-bottom: none;
        }

        .metric-label {
            color: #6b7280;
            font-weight: 500;
        }

        .metric-value {
            color: #111827;
            font-weight: bold;
        }

        .trades-table {
            width: 100%;
            background: white;
            border-radius: 12px;
            padding: 25px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }

        .trades-table h2 {
            color: #333;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }

        .trade-row {
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 8px;
            border-left: 4px solid;
        }

        .trade-executed {
            background: #d1fae5;
            border-color: #10b981;
        }

        .trade-hold {
            background: #fee2e2;
            border-color: #ef4444;
        }

        .trade-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }

        .trade-question {
            font-weight: bold;
            color: #111827;
            flex: 1;
        }

        .trade-action {
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: bold;
        }

        .action-buy {
            background: #10b981;
            color: white;
        }

        .action-sell {
            background: #ef4444;
            color: white;
        }

        .action-hold {
            background: #9ca3af;
            color: white;
        }

        .trade-details {
            display: flex;
            gap: 20px;
            font-size: 0.9em;
            color: #6b7280;
        }

        .trade-detail {
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .edge-positive {
            color: #10b981;
            font-weight: bold;
        }

        .edge-negative {
            color: #ef4444;
            font-weight: bold;
        }

        .last-updated {
            text-align: center;
            color: white;
            margin-top: 20px;
            font-size: 0.9em;
        }

        .refresh-info {
            text-align: center;
            color: rgba(255,255,255,0.8);
            margin-top: 10px;
            font-size: 0.85em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Polymarket Agent Dashboard</h1>

        <div class="dashboard" id="dashboard">
            <div class="card">
                <h2>Agent Status</h2>
                <div id="agent-status">Loading...</div>
            </div>

            <div class="card">
                <h2>Recent Activity (10 min)</h2>
                <div id="recent-activity">Loading...</div>
            </div>

            <div class="card">
                <h2>All-Time Stats</h2>
                <div id="alltime-stats">Loading...</div>
            </div>
        </div>

        <div class="trades-table">
            <h2>Recent Decisions</h2>
            <div id="trades">Loading...</div>
        </div>

        <div class="last-updated" id="last-updated"></div>
        <div class="refresh-info">Auto-refreshing every 5 seconds</div>
    </div>

    <script>
        async function updateDashboard() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                // Agent Status
                let statusHtml = '';
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

                statusHtml += `<div class="metric">
                    <span class="metric-label">State</span>
                    <span class="status-badge ${statusClass}">${statusText}</span>
                </div>`;

                if (data.idle_seconds !== null) {
                    const idleMin = Math.floor(data.idle_seconds / 60);
                    const idleSec = Math.floor(data.idle_seconds % 60);
                    statusHtml += `<div class="metric">
                        <span class="metric-label">Last Signal</span>
                        <span class="metric-value">${idleMin}m ${idleSec}s ago</span>
                    </div>`;
                }

                statusHtml += `<div class="metric">
                    <span class="metric-label">Active Markets</span>
                    <span class="metric-value">${data.active_markets.toLocaleString()}</span>
                </div>`;

                document.getElementById('agent-status').innerHTML = statusHtml;

                // Recent Activity
                let activityHtml = '';
                activityHtml += `<div class="metric">
                    <span class="metric-label">Signals</span>
                    <span class="metric-value">${data.signals_10m}</span>
                </div>`;
                activityHtml += `<div class="metric">
                    <span class="metric-label">Decisions</span>
                    <span class="metric-value">${data.decisions_10m}</span>
                </div>`;
                activityHtml += `<div class="metric">
                    <span class="metric-label">Orders</span>
                    <span class="metric-value">${data.orders_10m}</span>
                </div>`;

                document.getElementById('recent-activity').innerHTML = activityHtml;

                // All-Time Stats
                let statsHtml = '';
                statsHtml += `<div class="metric">
                    <span class="metric-label">Total Signals</span>
                    <span class="metric-value">${data.total_signals.toLocaleString()}</span>
                </div>`;
                statsHtml += `<div class="metric">
                    <span class="metric-label">Total Decisions</span>
                    <span class="metric-value">${data.total_decisions}</span>
                </div>`;
                statsHtml += `<div class="metric">
                    <span class="metric-label">Trades Executed</span>
                    <span class="metric-value">${data.total_filled}</span>
                </div>`;

                document.getElementById('alltime-stats').innerHTML = statsHtml;

                // Recent Trades
                let tradesHtml = '';
                data.recent_trades.forEach(trade => {
                    const isExecuted = trade.executed;
                    const tradeClass = isExecuted ? 'trade-executed' : 'trade-hold';

                    let actionClass = 'action-hold';
                    if (trade.action.includes('BUY')) actionClass = 'action-buy';
                    if (trade.action.includes('SELL')) actionClass = 'action-sell';

                    const edgeClass = trade.edge >= 0 ? 'edge-positive' : 'edge-negative';
                    const edgeSign = trade.edge >= 0 ? '+' : '';

                    const timestamp = new Date(trade.created_at).toLocaleTimeString();

                    tradesHtml += `
                        <div class="trade-row ${tradeClass}">
                            <div class="trade-header">
                                <div class="trade-question">${trade.question.substring(0, 70)}${trade.question.length > 70 ? '...' : ''}</div>
                                <div class="trade-action ${actionClass}">${trade.action}</div>
                            </div>
                            <div class="trade-details">
                                <div class="trade-detail">
                                    <span>📊</span>
                                    <span>Ensemble: ${(trade.ensemble_prob * 100).toFixed(1)}%</span>
                                </div>
                                <div class="trade-detail">
                                    <span>💹</span>
                                    <span>Market: ${(trade.market_prob * 100).toFixed(1)}%</span>
                                </div>
                                <div class="trade-detail">
                                    <span>⚡</span>
                                    <span class="${edgeClass}">Edge: ${edgeSign}${(trade.edge * 100).toFixed(1)}%</span>
                                </div>
                                ${isExecuted ? `
                                <div class="trade-detail">
                                    <span>💰</span>
                                    <span>Size: $${trade.size_usd.toFixed(2)}</span>
                                </div>
                                ` : `
                                <div class="trade-detail">
                                    <span>❌</span>
                                    <span>${trade.reason}</span>
                                </div>
                                `}
                                <div class="trade-detail">
                                    <span>🕐</span>
                                    <span>${timestamp}</span>
                                </div>
                            </div>
                        </div>
                    `;
                });

                document.getElementById('trades').innerHTML = tradesHtml || '<p>No recent trades</p>';

                // Update timestamp
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
    """Start the web dashboard server."""
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/status', handle_api)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 5555)
    await site.start()

    print("=" * 70)
    print("🌐 WEB DASHBOARD STARTED")
    print("=" * 70)
    print()
    print("Open in your browser:")
    print("  👉 http://localhost:5555")
    print()
    print("Dashboard updates every 5 seconds")
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
