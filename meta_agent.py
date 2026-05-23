#!/usr/bin/env python3
"""
Meta-Agent: Self-improving trading system supervisor.

Uses Claude Opus 4.6 to periodically:
1. Check trading agent performance
2. Diagnose issues
3. Generate and apply fixes
4. Restart the agent
5. Repeat

This automates the human-in-the-loop iteration cycle.
"""

import asyncio
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [META] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("meta_agent")

# ── Config ──────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "agent.db"
AGENT_LOG = PROJECT_DIR / "agent.log"
AGENT_SCRIPT = PROJECT_DIR / "main_v2.py"
ITERATION_LOG = PROJECT_DIR / "meta_iterations.jsonl"

CHECK_INTERVAL_MINUTES = 10   # How often to check performance
MIN_TRADES_TO_EVALUATE = 3    # Need at least this many trades before evaluating
MAX_ITERATIONS = 20           # Stop after this many improvement cycles
MAX_CONSECUTIVE_LOSSES = 3    # Stop if we can't improve after N tries
PROFIT_TARGET = 10.0          # Stop iterating once we're profitable by this much

# Files the meta-agent is allowed to modify
MODIFIABLE_FILES = [
    "config/constants.py",
    "main_v2.py",
    "signals/forecaster_v2.py",
    "strategy/edge.py",
    "strategy/sizing.py",
    "execution/paper.py",
]

BEDROCK_MODEL = "us.anthropic.claude-opus-4-6-v1"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_bedrock_client():
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def call_opus(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Call Claude Opus 4.6 via Bedrock."""
    client = get_bedrock_client()

    messages = [{"role": "user", "content": prompt}]

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system

    response = client.invoke_model(
        modelId=BEDROCK_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


# ── Performance Analysis ────────────────────────────────

def get_performance() -> dict:
    """Extract current trading performance from the database."""
    if not DB_PATH.exists():
        return {"status": "no_database"}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    perf = {}

    # Basic counts
    try:
        c.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN executed=1 THEN 1 END) as executed FROM decisions")
        row = c.fetchone()
        perf["total_decisions"] = row["total"]
        perf["executed_trades"] = row["executed"]
    except Exception:
        perf["total_decisions"] = 0
        perf["executed_trades"] = 0

    # P&L
    try:
        c.execute("SELECT SUM(unrealized_pnl) as unrealized, SUM(realized_pnl) as realized FROM positions")
        row = c.fetchone()
        perf["unrealized_pnl"] = round(row["unrealized"] or 0, 2)
        perf["realized_pnl"] = round(row["realized"] or 0, 2)
        perf["total_pnl"] = round(perf["unrealized_pnl"] + perf["realized_pnl"], 2)
    except Exception:
        perf["unrealized_pnl"] = 0
        perf["realized_pnl"] = 0
        perf["total_pnl"] = 0

    # Per-position breakdown
    try:
        c.execute("""
            SELECT m.question, p.side, p.size, ROUND(p.avg_cost, 4) as cost,
                   ROUND(p.unrealized_pnl, 2) as pnl
            FROM positions p
            JOIN markets m ON p.market_id = m.market_id
            WHERE p.size > 0
            ORDER BY p.unrealized_pnl
        """)
        perf["positions"] = [dict(r) for r in c.fetchall()]
    except Exception:
        perf["positions"] = []

    # Trade history
    try:
        c.execute("""
            SELECT d.action, m.question, ROUND(d.edge * 100, 1) as edge_pct,
                   ROUND(d.size_usd, 2) as size, d.reason
            FROM decisions d
            JOIN markets m ON d.market_id = m.market_id
            WHERE d.executed = 1
            ORDER BY d.created_at DESC
            LIMIT 15
        """)
        perf["recent_trades"] = [dict(r) for r in c.fetchall()]
    except Exception:
        perf["recent_trades"] = []

    # Filtered trades (BUY_YES blocked)
    try:
        c.execute("SELECT COUNT(*) FROM decisions WHERE reason='filtered_buy_yes_bias'")
        perf["filtered_buy_yes"] = c.fetchone()[0]
    except Exception:
        perf["filtered_buy_yes"] = 0

    # Recent logs
    try:
        with open(AGENT_LOG, "r") as f:
            lines = f.readlines()
            perf["recent_logs"] = "".join(lines[-30:])
    except Exception:
        perf["recent_logs"] = ""

    conn.close()
    return perf


def get_file_contents(filepath: str) -> str:
    """Read a file from the project."""
    full_path = PROJECT_DIR / filepath
    if full_path.exists():
        return full_path.read_text()
    return f"FILE NOT FOUND: {filepath}"


# ── Agent Process Management ────────────────────────────

def start_agent() -> subprocess.Popen:
    """Start the trading agent as a subprocess."""
    log.info("Starting trading agent...")
    proc = subprocess.Popen(
        [str(PROJECT_DIR / ".venv" / "bin" / "python"), str(AGENT_SCRIPT), "--mode", "paper"],
        stdout=open(AGENT_LOG, "w"),
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_DIR),
    )
    log.info(f"Agent started (PID {proc.pid})")
    return proc


def stop_agent(proc: subprocess.Popen):
    """Stop the trading agent."""
    if proc and proc.poll() is None:
        log.info(f"Stopping agent (PID {proc.pid})...")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.info("Agent stopped.")


def reset_database():
    """Backup and reset the database."""
    if DB_PATH.exists():
        backup = PROJECT_DIR / f"agent_meta_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        DB_PATH.rename(backup)
        log.info(f"Database backed up to {backup.name}")


# ── Meta-Agent Core ─────────────────────────────────────

SYSTEM_PROMPT = """You are a meta-agent supervising a Polymarket prediction market trading system.

Your job: analyze performance data, diagnose issues, and output SPECIFIC code fixes.

CONTEXT:
- The trading agent uses Claude Opus 4.6 to forecast prediction market probabilities
- It buys NO tokens when it thinks the market overprices YES
- BUY_YES trades are filtered out (proven to lose money due to LLM bullish bias)
- Paper trading with $1000 starting balance
- The agent has had persistent issues with losing money

KNOWN HISTORY:
- v1 lost -$251 (LLM massively overconfident, 20-30% predicted edges that were wrong)
- v2 with Opus 4.6 is much more calibrated (6% avg edge vs 18%)
- Fixed bugs: BUY_NO pricing, order fills, P&L tracking
- Current approach: BUY_NO only, max $25/position, max 8 positions
- These are long-dated markets: positions may take days/weeks to resolve
- Small negative P&L (-$0.10 per position) from entry slippage is NORMAL, not a bug

RULES:
1. Only suggest changes to these files: {modifiable_files}
2. CRITICAL: When you output code changes, copy the old code EXACTLY from the file contents provided. Character-for-character match including whitespace and indentation.
3. Output changes as JSON: {{"file": "path", "old": "exact old code copied from file", "new": "new code"}}
4. Be conservative - small targeted fixes, not rewrites
5. If losses are tiny (<$2 total) and positions are new (<1 hour), say "NO_CHANGES_NEEDED" - this is just entry slippage
6. If you need more data, say "NEED_MORE_DATA"
7. Focus on the BIGGEST issue first
8. Never remove risk controls or safety limits
""".replace("{modifiable_files}", str(MODIFIABLE_FILES))


def analyze_and_fix(perf: dict, iteration: int, history: list) -> dict:
    """Use Opus 4.6 to analyze performance and suggest fixes."""

    # Build context
    history_summary = ""
    if history:
        history_summary = "\n\nPREVIOUS ITERATIONS:\n"
        for h in history[-5:]:  # Last 5 iterations
            history_summary += f"- Iteration {h['iteration']}: P&L=${h['pnl']}, trades={h['trades']}, action={h['action'][:100]}\n"

    # Include key file contents so Opus can write exact matches
    key_files = {}
    for f in ["config/constants.py", "main_v2.py", "execution/paper.py", "strategy/sizing.py", "strategy/edge.py"]:
        key_files[f] = get_file_contents(f)

    files_context = "\n\n".join(
        f"=== {f} ===\n{content}" for f, content in key_files.items()
    )

    prompt = f"""ITERATION {iteration} PERFORMANCE CHECK

CURRENT METRICS:
- Total P&L: ${perf['total_pnl']}
- Unrealized: ${perf['unrealized_pnl']}
- Realized: ${perf['realized_pnl']}
- Executed Trades: {perf['executed_trades']}
- Total Decisions: {perf['total_decisions']}
- Filtered BUY_YES: {perf['filtered_buy_yes']}

OPEN POSITIONS:
{json.dumps(perf['positions'], indent=2)}

RECENT TRADES:
{json.dumps(perf['recent_trades'], indent=2)}

RECENT LOGS (last 50 lines):
{perf['recent_logs'][-3000:]}
{history_summary}

SOURCE FILES (use these for EXACT code matching):
{files_context}

TASK:
1. Is the strategy profitable or on track? If P&L is only -$0 to -$2 and positions are young, say NO_CHANGES_NEEDED (entry slippage is normal).
2. If losing more than $2, what is the SINGLE biggest issue?
3. If a fix is needed, provide it as JSON. CRITICAL: Copy the "old" field character-for-character from the source files above.

Format: {{"file": "path/to/file.py", "old": "exact code from file above", "new": "replacement code"}}

If there aren't enough trades yet (< {MIN_TRADES_TO_EVALUATE}), say NEED_MORE_DATA.

Respond with your analysis followed by EXACTLY ONE of:
- NO_CHANGES_NEEDED
- NEED_MORE_DATA
- CHANGES: [list of JSON fix objects]
"""

    log.info("Calling Opus 4.6 for analysis...")
    response = call_opus(prompt, system=SYSTEM_PROMPT, max_tokens=4096)
    log.info(f"Opus response ({len(response)} chars)")

    # Parse response
    result = {
        "iteration": iteration,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pnl": perf["total_pnl"],
        "trades": perf["executed_trades"],
        "analysis": response,
        "action": "none",
        "changes": [],
    }

    if "NO_CHANGES_NEEDED" in response:
        result["action"] = "no_changes"
        log.info("Opus says: Strategy is working, no changes needed!")
    elif "NEED_MORE_DATA" in response:
        result["action"] = "wait"
        log.info("Opus says: Need more data, waiting...")
    elif "CHANGES:" in response:
        result["action"] = "fix"
        # Extract JSON changes
        try:
            changes_text = response.split("CHANGES:")[1]
            # Find JSON array
            start = changes_text.find("[")
            end = changes_text.rfind("]") + 1
            if start >= 0 and end > start:
                changes = json.loads(changes_text[start:end])
                result["changes"] = changes
                log.info(f"Opus suggests {len(changes)} change(s)")
        except (json.JSONDecodeError, IndexError) as e:
            log.warning(f"Could not parse changes: {e}")
            result["action"] = "parse_error"

    return result


def _fuzzy_find(content: str, target: str, threshold: float = 0.85) -> tuple[int, int] | None:
    """Find target in content using fuzzy matching. Returns (start, end) or None."""
    import difflib

    # First try exact match
    idx = content.find(target)
    if idx >= 0:
        return idx, idx + len(target)

    # Try with normalized whitespace
    def normalize(s):
        return "\n".join(line.rstrip() for line in s.splitlines())

    norm_content = normalize(content)
    norm_target = normalize(target)
    idx = norm_content.find(norm_target)
    if idx >= 0:
        # Map back to original content position
        # Find the line in original content
        lines_before = norm_content[:idx].count("\n")
        original_lines = content.splitlines(keepends=True)
        start = sum(len(original_lines[i]) for i in range(lines_before))
        target_lines = len(target.splitlines())
        end = sum(len(original_lines[i]) for i in range(lines_before, min(lines_before + target_lines, len(original_lines))))
        return start, end

    # Try line-by-line fuzzy matching
    content_lines = content.splitlines()
    target_lines = target.strip().splitlines()
    if not target_lines:
        return None

    # Find best match for first line of target
    first_line = target_lines[0].strip()
    best_ratio = 0
    best_start_line = -1

    for i, line in enumerate(content_lines):
        ratio = difflib.SequenceMatcher(None, line.strip(), first_line).ratio()
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best_start_line = i

    if best_start_line < 0:
        return None

    # Check if subsequent lines also match
    end_line = min(best_start_line + len(target_lines), len(content_lines))
    matched_content = "\n".join(content_lines[best_start_line:end_line])
    overall_ratio = difflib.SequenceMatcher(None, matched_content.strip(), target.strip()).ratio()

    if overall_ratio >= threshold:
        start = sum(len(content_lines[i]) + 1 for i in range(best_start_line))
        end = sum(len(content_lines[i]) + 1 for i in range(end_line))
        return start, end

    return None


def apply_changes(changes: list) -> bool:
    """Apply code changes suggested by Opus with fuzzy matching."""
    applied = 0
    for change in changes:
        filepath = change.get("file", "")
        old_code = change.get("old", "")
        new_code = change.get("new", "")

        if not filepath or not old_code or not new_code:
            log.warning(f"Skipping invalid change: {change}")
            continue

        # Security: only modify allowed files
        if filepath not in MODIFIABLE_FILES:
            log.warning(f"Skipping unauthorized file: {filepath}")
            continue

        full_path = PROJECT_DIR / filepath
        if not full_path.exists():
            log.warning(f"File not found: {filepath}")
            continue

        content = full_path.read_text()

        # Try exact match first, then fuzzy
        match = _fuzzy_find(content, old_code)
        if not match:
            log.warning(f"Code not found in {filepath} (even with fuzzy matching), skipping")
            log.warning(f"  Looking for: {old_code[:80]}...")
            continue

        start, end = match
        new_content = content[:start] + new_code + content[end:]
        full_path.write_text(new_content)
        applied += 1
        log.info(f"Applied change to {filepath}")

    return applied > 0


def log_iteration(result: dict):
    """Append iteration result to the log file."""
    with open(ITERATION_LOG, "a") as f:
        f.write(json.dumps(result) + "\n")


# ── Main Loop ───────────────────────────────────────────

async def run():
    log.info("=" * 60)
    log.info("META-AGENT STARTING")
    log.info(f"Check interval: {CHECK_INTERVAL_MINUTES} min")
    log.info(f"Max iterations: {MAX_ITERATIONS}")
    log.info(f"Profit target: ${PROFIT_TARGET}")
    log.info("=" * 60)

    history = []
    consecutive_no_improvement = 0
    best_pnl = float("-inf")

    # Start fresh
    reset_database()
    agent_proc = start_agent()

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            # Wait for data collection
            wait_minutes = CHECK_INTERVAL_MINUTES
            log.info(f"\n--- Waiting {wait_minutes} min for iteration {iteration} ---")
            await asyncio.sleep(wait_minutes * 60)

            # Check if agent is still running
            if agent_proc.poll() is not None:
                log.error("Agent crashed! Restarting...")
                reset_database()
                agent_proc = start_agent()
                await asyncio.sleep(60)
                continue

            # Get performance
            perf = get_performance()
            log.info(
                f"Iteration {iteration}: P&L=${perf['total_pnl']} | "
                f"Trades={perf['executed_trades']} | "
                f"Positions={len(perf['positions'])}"
            )

            # Check if we've hit profit target
            if perf["total_pnl"] >= PROFIT_TARGET:
                log.info(f"PROFIT TARGET REACHED: ${perf['total_pnl']}")
                log_iteration({
                    "iteration": iteration,
                    "pnl": perf["total_pnl"],
                    "action": "profit_target_reached",
                })
                break

            # Analyze with Opus
            result = analyze_and_fix(perf, iteration, history)
            log_iteration(result)
            history.append(result)

            if result["action"] == "no_changes":
                log.info("Strategy is working. Continuing to monitor.")
                if perf["total_pnl"] > best_pnl:
                    best_pnl = perf["total_pnl"]
                    consecutive_no_improvement = 0
                continue

            elif result["action"] == "wait":
                log.info("Not enough data yet. Continuing to wait.")
                continue

            elif result["action"] == "fix" and result["changes"]:
                # Stop agent, apply fixes, restart
                stop_agent(agent_proc)

                log.info("Applying Opus-suggested changes...")
                success = apply_changes(result["changes"])

                if success:
                    log.info("Changes applied. Resetting and restarting agent.")
                    reset_database()
                    agent_proc = start_agent()

                    # Track improvement
                    if perf["total_pnl"] > best_pnl:
                        best_pnl = perf["total_pnl"]
                        consecutive_no_improvement = 0
                    else:
                        consecutive_no_improvement += 1
                else:
                    log.warning("No changes could be applied. Restarting agent as-is.")
                    agent_proc = start_agent()

            else:
                log.warning(f"Unexpected action: {result['action']}")

            # Check if we're stuck
            if consecutive_no_improvement >= MAX_CONSECUTIVE_LOSSES:
                log.error(
                    f"No improvement after {MAX_CONSECUTIVE_LOSSES} iterations. "
                    f"Best P&L: ${best_pnl}. Stopping."
                )
                break

    except KeyboardInterrupt:
        log.info("Meta-agent interrupted by user.")
    finally:
        stop_agent(agent_proc)
        log.info("Meta-agent shutdown complete.")

        # Final summary
        log.info("\n" + "=" * 60)
        log.info("META-AGENT SUMMARY")
        log.info(f"Total iterations: {len(history)}")
        log.info(f"Best P&L: ${best_pnl}")
        if history:
            actions = [h["action"] for h in history]
            log.info(f"Actions: {dict((a, actions.count(a)) for a in set(actions))}")
        log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
