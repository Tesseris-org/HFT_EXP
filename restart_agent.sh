#!/bin/bash
# Quick restart script for the paper trading agent

echo "Stopping agent..."
pkill -f "python.*main.py" 2>/dev/null
sleep 2

echo "Starting agent..."
nohup .venv/bin/python main.py --mode paper > agent_run.log 2>&1 &
echo $! > agent.pid

sleep 3

if ps aux | grep "$(cat agent.pid)" | grep -v grep > /dev/null; then
    echo "✓ Agent restarted successfully (PID: $(cat agent.pid))"
    echo ""
    echo "Monitor with:"
    echo "  python monitor.py --watch"
else
    echo "✗ Failed to start agent"
    echo "Check logs: tail -50 agent_run.log"
    exit 1
fi
