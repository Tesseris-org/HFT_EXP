# Quick Start Guide

## 🚀 Your Agent is NOW RUNNING

### Check Status (Real-Time)
```bash
python monitor.py --watch
```
Press `Ctrl+C` to exit. Refreshes every 10 seconds.

### One-Time Status Check
```bash
python monitor.py
```

### View Trade History
```bash
python monitor.py --trades
```

### Check Live Logs
```bash
tail -f agent_run.log
```

## 📊 What to Expect

### Normal Operation
- Agent analyzes 30 markets every 2 minutes (120s interval)
- Each market takes ~20-30s to analyze (LLM calls)
- **Most decisions will be HOLD** (insufficient edge is normal!)
- Expect **1-3 trades per hour** in active market conditions

### Status Colors
- 🟢 **ACTIVE**: Generating signals (last 60s)
- 🟡 **IDLE**: Last activity 1-5 min ago (normal between cycles)
- 🔴 **STOPPED**: No activity >5 min (check if agent crashed)

## 🛠️ Agent Control

### Stop Agent
```bash
kill $(cat agent.pid)
# OR
pkill -f "python.*main.py"
```

### Restart Agent
```bash
# Stop
kill $(cat agent.pid)
sleep 2

# Start
nohup .venv/bin/python main.py --mode paper > agent_run.log 2>&1 &
echo $! > agent.pid
```

### Check if Running
```bash
ps aux | grep "python.*main.py" | grep -v grep
```

## 🔔 Telegram Alerts (Optional)

**Currently:** Telegram is NOT configured (you'll see warning in logs)

**To Enable:**
1. See [MONITORING.md](MONITORING.md) for setup instructions
2. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```
3. Restart agent

**What You'll Get:**
- 🟢 Trade execution alerts
- ⚠️ Error notifications
- 🚨 Risk limit breaches
- 📊 Daily summaries

## 📈 Current Session

**Started:** 2026-03-20 14:25:37 UTC
**Mode:** Paper Trading
**Balance:** $1,000.00
**Markets:** 2,001 active
**Tradeable:** ~1,121 (after filters)

## 🎯 Next Steps

1. **Let it run for 2-4 hours** - Collect 10-20 trades
2. **Monitor with:** `python monitor.py --watch`
3. **Check trades:** `python monitor.py --trades`
4. **Analyze performance:**
   ```bash
   sqlite3 agent.db "SELECT
       COUNT(*) as total_trades,
       SUM(CASE WHEN action LIKE 'BUY%' OR action LIKE 'SELL%' THEN 1 ELSE 0 END) as executed,
       AVG(edge) as avg_edge
   FROM decisions;"
   ```

## ⚠️ Troubleshooting

### No new signals?
```bash
# Check if agent is running
ps aux | grep "python.*main.py"

# Check last error
tail -20 agent_run.log | grep ERROR
```

### Agent crashed?
```bash
# Check last logs
tail -50 agent_run.log

# Restart
./restart_agent.sh
```

### Too many HOLDs?
This is **normal**! The edge threshold is 5% (conservative).
- Markets need to be mispriced by >5% to trade
- Real alpha is rare
- Better to wait than overtrade

## 📝 Files Overview

- `agent_run.log` - Live agent logs
- `agent.pid` - Process ID of running agent
- `agent.db` - SQLite database (all data)
- `monitor.py` - Status dashboard
- `main.py` - Agent orchestrator

## 🎓 Learn More

- Full monitoring guide: [MONITORING.md](MONITORING.md)
- System design: [DESIGN.md](DESIGN.md)
- All documentation: Run `ls *.md`
