# Monitoring Your Paper Trading Agent

## Quick Commands

### Check Status (One-time)
```bash
python monitor.py
```
Shows: agent state, recent activity (10m), all-time stats, recent trades

### Watch Mode (Live Updates)
```bash
python monitor.py --watch
```
Auto-refreshes every 10 seconds. Press `Ctrl+C` to exit.

### Trade History
```bash
python monitor.py --trades
```
Shows last 20 decisions with full details (trades + holds)

### Database Queries
```bash
# Recent signals
sqlite3 agent.db "SELECT datetime(created_at), source, probability FROM signals ORDER BY created_at DESC LIMIT 10;"

# All decisions
sqlite3 agent.db "SELECT datetime(created_at), action, edge, size_usd FROM decisions ORDER BY created_at DESC;"

# Current positions
sqlite3 agent.db "SELECT * FROM positions WHERE size > 0;"

# P&L summary
sqlite3 agent.db "SELECT SUM(realized_pnl) as total_realized FROM positions;"
```

### Check Agent Logs
```bash
# Tail live logs
tail -f agent_run.log

# Search for trades
grep "TRADE" agent_run.log

# Search for errors
grep "ERROR" agent_run.log
```

## Telegram Setup (Optional)

### 1. Create Telegram Bot
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow prompts
3. Save your bot token (looks like `123456:ABC-DEF1234...`)

### 2. Get Your Chat ID
1. Start a chat with your new bot
2. Send any message
3. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Find your `chat_id` in the JSON response

### 3. Add to .env
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=123456789
```

### 4. Test
```bash
python -c "
from ops.telegram import TelegramAlerter
import asyncio
alerter = TelegramAlerter()
asyncio.run(alerter.send_message('🤖 Test message from Polymarket Agent!'))
"
```

## Understanding the Dashboard

### Agent States
- 🟢 **ACTIVE**: Signals generated in last 60 seconds
- 🟡 **IDLE**: Last activity 1-5 minutes ago (normal)
- 🔴 **STOPPED**: No activity for >5 minutes (check if agent is running)

### Key Metrics
- **Signals generated**: LLM forecasts + cross-platform data
- **Decisions made**: Buy/sell/hold determinations
- **Orders placed**: Actual trades executed
- **Trades executed**: Filled orders (paper or live)

### What's Normal?
- Agent analyzes 30 markets every 2 minutes (120s interval)
- Takes ~20-30s per market (LLM calls are slow)
- Full cycle: ~10-15 minutes for 30 markets
- Most decisions are HOLD (insufficient edge is normal!)
- Expect 1-3 trades per hour in active markets

## Troubleshooting

### Agent not generating signals?
```bash
# Check if agent is running
ps aux | grep "python.*main.py"

# Check for errors
tail -50 agent_run.log | grep ERROR
```

### No trades despite signals?
- Check edge threshold (5% is conservative)
- Look at HOLD reasons: `python monitor.py --trades`
- Most holds due to "insufficient_edge" is normal

### Telegram not working?
```bash
# Test bot token
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"

# Check if enabled
python -c "from ops.telegram import TelegramAlerter; print(TelegramAlerter().enabled)"
```

## Running the Agent

### Start Paper Trading
```bash
# In background (recommended)
nohup .venv/bin/python main.py --mode paper > agent_run.log 2>&1 &
echo $! > agent.pid

# Monitor live
python monitor.py --watch
```

### Stop Agent
```bash
# Using PID file
kill $(cat agent.pid)

# Or find and kill
pkill -f "python.*main.py"
```

### Restart Agent
```bash
# Stop and start
pkill -f "python.*main.py"
sleep 2
nohup .venv/bin/python main.py --mode paper > agent_run.log 2>&1 &
echo $! > agent.pid
```

## Advanced Monitoring

### Real-time P&L Tracking
```bash
# Watch balance changes
watch -n 10 "sqlite3 agent.db 'SELECT SUM(realized_pnl), SUM(unrealized_pnl) FROM positions;'"
```

### Signal Quality Check
```bash
# Calibration: predicted vs actual
sqlite3 agent.db "
SELECT
    source,
    COUNT(*) as total,
    AVG(CASE WHEN actual_outcome = 1 THEN predicted_prob ELSE 1-predicted_prob END) as avg_calibration
FROM calibration
GROUP BY source;
"
```

### Performance Metrics
```bash
# Win rate
sqlite3 agent.db "
SELECT
    COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) * 100.0 / COUNT(*) as win_rate
FROM positions
WHERE realized_pnl != 0;
"
```
