"""
Polymarket Agent v4 — Short-Duration Arbitrage.

Only enters markets resolving within MAX_DAYS days.
Scans continuously. As markets resolve and capital frees up,
immediately redeploys into new short-dated opportunities.

Strategy: guaranteed-profit arb with fast capital cycling.
"""

import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp

from strategy.arbitrage import scan_market

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("arb_v4")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# ── Strategy Config ─────────────────────────────────────
MAX_DAYS = 15              # Only enter markets resolving within this many days
MIN_EDGE = 0.02            # 2% minimum net edge
SCAN_INTERVAL = 120        # Scan every 2 minutes
EVENT_REFRESH = 600        # Refresh event list every 10 minutes
INVEST_BASE = 100.0        # Base invest per trade
MAX_INVEST = 200.0         # Cap per trade
MIN_INVEST = 25.0          # Floor per trade

# Costs
SLIPPAGE = 0.005
GAS_PER_LEG = 0.02
DRIFT = 0.002


@dataclass
class Position:
    id: str
    event_title: str
    nrm_id: str
    direction: str
    edge: float
    cost: float
    num_shares: float
    num_outcomes: int
    days_to_resolution: int
    entered_at: datetime
    resolution_date: str
    status: str = "open"
    pnl: float = 0.0


class ShortDurationArbAgent:
    def __init__(self):
        self._session: aiohttp.ClientSession = None
        self.balance = 1000.0
        self.positions: list[Position] = []
        self.closed: list[Position] = []
        self.scan_count = 0
        self.total_profit = 0.0
        self._events: list[dict] = []
        self._traded_ids: set[str] = set()
        self._last_refresh = 0.0

    async def run(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        log.info(f"arb_v4_start balance=${self.balance:.2f} max_days={MAX_DAYS} min_edge={MIN_EDGE:.0%}")

        try:
            await asyncio.gather(
                self._main_loop(),
                self._resolution_check_loop(),
                self._status_loop(),
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._summary()
            await self._session.close()

    # ── Main Scan Loop ──────────────────────────────────

    async def _main_loop(self):
        while True:
            self.scan_count += 1
            try:
                await self._refresh_events()
                entries = await self._scan_and_enter()
                if entries:
                    log.info(
                        f"SCAN #{self.scan_count}: +{entries} entries | "
                        f"open={len(self.positions)} balance=${self.balance:.2f}"
                    )
            except Exception as e:
                log.error(f"Scan error: {type(e).__name__}: {e}")
            await asyncio.sleep(SCAN_INTERVAL)

    async def _refresh_events(self):
        """Refresh NegRisk event list periodically."""
        import time
        if time.time() - self._last_refresh < EVENT_REFRESH:
            return

        events = {}
        for offset in range(0, 2000, 100):
            async with self._session.get(f"{GAMMA_API}/markets", params={
                "active": "true", "closed": "false", "neg_risk": "true",
                "limit": 100, "offset": offset,
            }) as r:
                mkts = await r.json()
            if not mkts:
                break
            for m in mkts:
                nrm = m.get("negRiskMarketID", "")
                if not nrm:
                    continue
                if nrm not in events:
                    events[nrm] = {"title": "", "nrm_id": nrm, "markets": [], "end_date": ""}
                events[nrm]["title"] = m.get("groupItemTitle", m.get("question", ""))[:70]
                events[nrm]["end_date"] = m.get("endDate", "")[:10]
                clob_ids = m.get("clobTokenIds", "[]")
                if isinstance(clob_ids, str):
                    clob_ids = json.loads(clob_ids)
                events[nrm]["markets"].append({
                    "yes_token_id": clob_ids[0] if clob_ids else "",
                    "question": m.get("question", "")[:60],
                })
            if len(mkts) < 100:
                break
            await asyncio.sleep(0.1)

        # Filter: 3+ outcomes AND resolves within MAX_DAYS
        now = datetime.now(timezone.utc)
        self._events = []
        for ev in events.values():
            if len(ev["markets"]) < 3:
                continue
            end = ev["end_date"]
            if not end:
                continue
            try:
                end_dt = datetime.fromisoformat(end + "T00:00:00+00:00")
                days = (end_dt - now).days
            except Exception:
                continue
            if 0 <= days <= MAX_DAYS:
                ev["days"] = days
                self._events.append(ev)

        self._last_refresh = time.time()
        log.info(f"Events resolving within {MAX_DAYS}d: {len(self._events)}")

    async def _scan_and_enter(self) -> int:
        """Scan short-dated events for arb, enter positions."""
        entries = 0
        for ev in self._events:
            if ev["nrm_id"] in self._traded_ids:
                continue
            if self.balance < MIN_INVEST:
                break

            tids = [m["yes_token_id"] for m in ev["markets"] if m["yes_token_id"]]
            if len(tids) < 3:
                continue

            # Fetch live prices
            prices = []
            for tid in tids:
                try:
                    async with self._session.get(
                        f"{CLOB_API}/midpoint", params={"token_id": tid}
                    ) as r:
                        data = await r.json()
                        p = float(data.get("mid", 0))
                        prices.append(p if p > 0 else None)
                except Exception:
                    prices.append(None)
                await asyncio.sleep(0.02)

            valid = [p for p in prices if p is not None and p > 0]
            if len(valid) < 3:
                continue

            opp = scan_market(
                outcome_prices=valid,
                event_title=ev["title"],
                neg_risk_market_id=ev["nrm_id"],
            )
            if not opp or opp.net_profit_per_dollar < MIN_EDGE:
                continue
            if opp.arb_type == "short" and opp.num_outcomes > 20:
                continue

            # Kelly sizing: more edge and fewer days = bigger position
            days = ev["days"]
            edge_mult = min(opp.net_profit_per_dollar / 0.05, 2.5)
            time_mult = min(15 / max(days, 1), 3.0)
            invest = INVEST_BASE * edge_mult * time_mult
            invest = max(MIN_INVEST, min(MAX_INVEST, invest))

            gas = GAS_PER_LEG * opp.num_outcomes
            cost = invest * (1 + SLIPPAGE + DRIFT) + gas
            if cost > self.balance:
                cost = self.balance * 0.95
                invest = (cost - gas) / (1 + SLIPPAGE + DRIFT)
                if invest < MIN_INVEST:
                    continue

            if opp.arb_type == "long":
                shares = invest / opp.price_sum
            else:
                shares = invest / (opp.num_outcomes - opp.price_sum)

            self.balance -= cost
            self._traded_ids.add(ev["nrm_id"])
            entries += 1

            pos = Position(
                id=f"v4_{uuid.uuid4().hex[:8]}",
                event_title=ev["title"],
                nrm_id=ev["nrm_id"],
                direction=opp.arb_type,
                edge=opp.net_profit_per_dollar,
                cost=round(cost, 2),
                num_shares=shares,
                num_outcomes=opp.num_outcomes,
                days_to_resolution=days,
                entered_at=datetime.now(timezone.utc),
                resolution_date=ev["end_date"],
            )
            self.positions.append(pos)

            log.info(
                f"ENTRY: {ev['title'][:45]} | {opp.arb_type} "
                f"edge={opp.net_profit_per_dollar:.1%} "
                f"days={days} size=${invest:.0f} cost=${cost:.2f}"
            )

        return entries

    # ── Resolution Check ────────────────────────────────

    async def _resolution_check_loop(self):
        """Check if any positions' markets have resolved. Simulate payout."""
        while True:
            await asyncio.sleep(300)  # Check every 5 minutes
            now = datetime.now(timezone.utc)
            remaining = []

            for pos in self.positions:
                # Check if past resolution date
                try:
                    res_dt = datetime.fromisoformat(pos.resolution_date + "T23:59:59+00:00")
                except Exception:
                    remaining.append(pos)
                    continue

                if now >= res_dt:
                    # Market should be resolved — simulate payout
                    if pos.direction == "long":
                        payout = pos.num_shares * 1.0
                    else:
                        payout = pos.num_shares * (pos.num_outcomes - 1)

                    gas = GAS_PER_LEG * pos.num_outcomes
                    pnl = payout - pos.cost - gas
                    pos.pnl = round(pnl, 2)
                    pos.status = "resolved"
                    self.closed.append(pos)
                    self.balance += payout - gas
                    self.total_profit += pnl

                    log.info(
                        f"RESOLVED: {pos.event_title[:40]} | "
                        f"pnl=${pnl:+.2f} payout=${payout:.2f} | "
                        f"balance=${self.balance:.2f}"
                    )
                else:
                    remaining.append(pos)

            self.positions = remaining

    # ── Status ──────────────────────────────────────────

    async def _status_loop(self):
        while True:
            await asyncio.sleep(120)
            # Unrealized estimate
            unrealized = 0.0
            for pos in self.positions:
                if pos.direction == "long":
                    unrealized += pos.num_shares * 1.0 - pos.cost
                else:
                    unrealized += pos.num_shares * (pos.num_outcomes - 1) - pos.cost

            days_str = ", ".join(f"{p.days_to_resolution}d" for p in self.positions[:5])
            log.info(
                f"STATUS | open={len(self.positions)} resolved={len(self.closed)} | "
                f"realized=${self.total_profit:+.2f} unrealized=${unrealized:+.2f} "
                f"total=${self.total_profit + unrealized:+.2f} | "
                f"balance=${self.balance:.2f} | "
                f"resolving=[{days_str}]"
            )

    def _summary(self):
        unrealized = sum(
            (p.num_shares * (1.0 if p.direction == "long" else p.num_outcomes - 1)) - p.cost
            for p in self.positions
        )
        log.info("=" * 60)
        log.info(f"SUMMARY | entries={len(self.positions)+len(self.closed)} "
                 f"resolved={len(self.closed)} | "
                 f"realized=${self.total_profit:+.2f} unrealized=${unrealized:+.2f} "
                 f"total=${self.total_profit+unrealized:+.2f}")
        log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(ShortDurationArbAgent().run())
