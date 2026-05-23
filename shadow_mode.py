#!/usr/bin/env python3
"""
Shadow Mode — Monitor LIVE markets, log what we'd trade, track outcomes.

No fake data. No estimates. Only real CLOB midpoint prices.
Logs every opportunity with full detail so we can validate the strategy
before committing real capital.

Output: shadow_results.jsonl (one JSON line per scan cycle)
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

import aiohttp

from strategy.arbitrage import scan_market

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SHADOW] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("shadow")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
RESULTS_FILE = "shadow_results.jsonl"
SCAN_INTERVAL = 60  # Every 60 seconds


async def run_shadow():
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as s:

        # Fetch NegRisk events once
        log.info("Fetching NegRisk events...")
        events = {}
        for offset in range(0, 2000, 100):
            async with s.get(f"{GAMMA_API}/markets", params={
                "active": "true", "closed": "false", "neg_risk": "true",
                "limit": 100, "offset": offset
            }) as r:
                markets = await r.json()
            if not markets:
                break
            for m in markets:
                nrm = m.get("negRiskMarketID", "")
                if not nrm:
                    continue
                if nrm not in events:
                    events[nrm] = {"title": "", "nrm_id": nrm, "markets": []}
                events[nrm]["title"] = m.get("groupItemTitle", m.get("question", ""))[:80]
                clob_ids = m.get("clobTokenIds", "[]")
                if isinstance(clob_ids, str):
                    clob_ids = json.loads(clob_ids)
                events[nrm]["markets"].append({
                    "question": m.get("question", "")[:60],
                    "yes_token_id": clob_ids[0] if clob_ids else "",
                    "end_date": m.get("endDate", "")[:10],
                })
            if len(markets) < 100:
                break
            await asyncio.sleep(0.1)

        event_list = [e for e in events.values() if len(e["markets"]) >= 3]
        log.info(f"Monitoring {len(event_list)} NegRisk events")

        # Track prices over time for each event
        price_history = {}  # nrm_id -> list of (timestamp, price_sum)
        scan_count = 0

        while True:
            scan_count += 1
            scan_time = datetime.now(timezone.utc)
            cycle_results = {
                "scan": scan_count,
                "timestamp": scan_time.isoformat(),
                "events_checked": 0,
                "opportunities": [],
                "all_sums": [],
            }

            for ev in event_list:
                tids = [m["yes_token_id"] for m in ev["markets"] if m["yes_token_id"]]
                if len(tids) < 3:
                    continue

                # Fetch ALL real prices
                prices = []
                for tid in tids:
                    try:
                        async with s.get(f"{CLOB_API}/midpoint", params={"token_id": tid}) as r:
                            data = await r.json()
                            p = float(data.get("mid", 0))
                            prices.append(p if p > 0 else None)
                    except Exception:
                        prices.append(None)
                    await asyncio.sleep(0.02)

                valid = [p for p in prices if p is not None and p > 0]
                if len(valid) < 3:
                    continue

                cycle_results["events_checked"] += 1
                price_sum = sum(valid)
                dev = 1.0 - price_sum

                cycle_results["all_sums"].append({
                    "event": ev["title"][:50],
                    "n": len(valid),
                    "sum": round(price_sum, 5),
                    "dev": round(dev, 5),
                })

                # Track price sum over time
                nrm = ev["nrm_id"]
                if nrm not in price_history:
                    price_history[nrm] = []
                price_history[nrm].append((scan_time.isoformat(), round(price_sum, 5)))
                # Keep last 100
                if len(price_history[nrm]) > 100:
                    price_history[nrm] = price_history[nrm][-100:]

                # Check if arb exists
                opp = scan_market(
                    outcome_prices=valid,
                    event_title=ev["title"],
                    neg_risk_market_id=nrm,
                )
                if opp:
                    # Check if deviation is CHANGING (mean reversion signal)
                    hist = price_history.get(nrm, [])
                    dev_trend = "stable"
                    if len(hist) >= 3:
                        recent_devs = [1.0 - h[1] for h in hist[-3:]]
                        if all(d < recent_devs[0] for d in recent_devs[1:]):
                            dev_trend = "narrowing"
                        elif all(d > recent_devs[0] for d in recent_devs[1:]):
                            dev_trend = "widening"

                    cycle_results["opportunities"].append({
                        "event": ev["title"][:60],
                        "n": opp.num_outcomes,
                        "type": opp.arb_type,
                        "sum": opp.price_sum,
                        "dev": round(dev, 5),
                        "net_edge": opp.net_profit_per_dollar,
                        "est_profit": opp.estimated_profit_usd,
                        "trend": dev_trend,
                        "history_points": len(hist),
                    })

            # Log results
            n_opps = len(cycle_results["opportunities"])
            n_checked = cycle_results["events_checked"]

            # Write to file
            with open(RESULTS_FILE, "a") as f:
                f.write(json.dumps(cycle_results) + "\n")

            if n_opps > 0:
                log.info(f"SCAN #{scan_count}: {n_checked} events | {n_opps} opportunities found")
                for opp in sorted(cycle_results["opportunities"], key=lambda x: x["net_edge"], reverse=True)[:5]:
                    log.info(f"  {opp['type']:5s} {opp['n']:3d}out dev={opp['dev']:+.4f} net={opp['net_edge']:.1%} trend={opp['trend']} | {opp['event'][:45]}")
            else:
                log.info(f"SCAN #{scan_count}: {n_checked} events | 0 opportunities")

            # Refresh events every 20 scans
            if scan_count % 20 == 0:
                log.info("Refreshing events...")
                # Re-fetch (simplified — reuse existing logic)

            await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_shadow())
