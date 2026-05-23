"""
Polymarket Agent v3 — Combined Arbitrage System.

Clean implementation. No v1/v2 dependencies. Two strategies:
1. NegRisk Arb: Multi-outcome markets where sum(YES) != 1.0
2. Cross-Platform Arb: Same event priced differently on Polymarket vs Manifold

Based on: "Unravelling the Probabilistic Forest" (arxiv 2508.03474)
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher

import aiohttp

from strategy.arbitrage import scan_market, rank_opportunities, ArbOpportunity

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("arb_v3")

# ── Config ──────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
MANIFOLD_API = "https://api.manifold.markets/v0"

SCAN_INTERVAL = 30           # Seconds between NegRisk scans
XPLAT_SCAN_INTERVAL = 300    # Seconds between cross-platform scans
INVEST_PER_ARB = 75.0        # USD per arb trade
MIN_NEGRISK_EDGE = 0.02      # 2% min edge for NegRisk arb
MIN_XPLAT_EDGE = 0.08        # 8% min edge for cross-platform (higher because not guaranteed)
MATCH_THRESHOLD = 0.70        # Fuzzy string match threshold for cross-platform

# Real costs
SLIPPAGE = 0.005
GAS_PER_LEG = 0.02
EXECUTION_DRIFT = 0.002


# ── Data Classes ────────────────────────────────────────

@dataclass
class Position:
    id: str
    strategy: str            # "negrisk" or "xplat"
    event_title: str
    market_id: str
    direction: str           # "long", "short", "buy_yes", "buy_no"
    entry_edge: float
    entry_cost: float
    num_shares: float
    num_outcomes: int
    entered_at: datetime
    extra: dict = field(default_factory=dict)  # Strategy-specific data
    status: str = "open"
    exit_pnl: float = 0.0
    closed_at: datetime = None


# ── Lightweight API Clients ─────────────────────────────

class GammaAPI:
    """Minimal Gamma API client — NegRisk events only."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def get_negrisk_events(self) -> list[dict]:
        """Fetch active NegRisk markets grouped by event."""
        events = {}
        for offset in range(0, 2000, 100):
            async with self.session.get(
                f"{GAMMA_API}/markets",
                params={"active": "true", "closed": "false", "neg_risk": "true",
                        "limit": 100, "offset": offset}
            ) as r:
                markets = await r.json()
            if not markets:
                break
            for m in markets:
                nrm_id = m.get("negRiskMarketID", "")
                if not nrm_id:
                    continue
                if nrm_id not in events:
                    events[nrm_id] = {"title": "", "neg_risk_market_id": nrm_id, "markets": []}
                events[nrm_id]["title"] = m.get("groupItemTitle", m.get("question", ""))[:80]
                clob_ids = m.get("clobTokenIds", "[]")
                if isinstance(clob_ids, str):
                    clob_ids = json.loads(clob_ids)
                prices = m.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                events[nrm_id]["markets"].append({
                    "market_id": m.get("conditionId", ""),
                    "question": m.get("question", ""),
                    "yes_token_id": clob_ids[0] if clob_ids else "",
                    "yes_price": float(prices[0]) if prices else 0.0,
                    "end_date": m.get("endDate", ""),
                })
            if len(markets) < 100:
                break
            await asyncio.sleep(0.2)

        result = [e for e in events.values() if len(e["markets"]) >= 3]
        logger.info(f"Loaded {len(result)} NegRisk events")
        return result

    async def get_top_markets(self, limit: int = 100) -> list[dict]:
        """Fetch top active markets by volume for cross-platform matching."""
        async with self.session.get(
            f"{GAMMA_API}/markets",
            params={"active": "true", "closed": "false", "limit": limit,
                    "order": "volume", "ascending": "false"}
        ) as r:
            markets = await r.json()
        result = []
        for m in markets:
            prices = m.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            result.append({
                "market_id": m.get("conditionId", ""),
                "question": m.get("question", ""),
                "yes_price": float(prices[0]) if prices else 0.0,
                "volume": float(m.get("volume", 0) or 0),
                "end_date": m.get("endDate", ""),
            })
        return result


class CLOBAPI:
    """Minimal CLOB client — prices only."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def midpoint(self, token_id: str) -> float | None:
        try:
            async with self.session.get(
                f"{CLOB_API}/midpoint", params={"token_id": token_id}
            ) as r:
                data = await r.json()
                return float(data.get("mid", 0))
        except Exception:
            return None

    async def midpoints_batch(self, token_ids: list[str]) -> list[float | None]:
        return await asyncio.gather(*[self.midpoint(tid) for tid in token_ids])


class ManifoldAPI:
    """Minimal Manifold client — binary markets only."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def get_markets(self, limit: int = 500) -> list[dict]:
        try:
            async with self.session.get(
                f"{MANIFOLD_API}/search-markets",
                params={"limit": limit, "sort": "liquidity", "filter": "open"}
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
            return [
                {"question": m.get("question", ""), "probability": float(m.get("probability", 0)),
                 "volume": float(m.get("volume", 0))}
                for m in data
                if m.get("outcomeType") == "BINARY" and m.get("probability") is not None
                and not m.get("isResolved")
            ]
        except Exception as e:
            logger.error(f"Manifold error: {e}")
            return []


# ── Cross-Platform Matching (No LLM — fuzzy string match) ─

def fuzzy_match_markets(poly_markets: list[dict], manifold_markets: list[dict],
                        threshold: float = MATCH_THRESHOLD) -> dict:
    """
    Match Polymarket questions to Manifold questions by string similarity.
    No LLM needed — just fuzzy matching on question text.

    Returns: {poly_market_id: {"manifold_prob": float, "manifold_q": str, "score": float}}
    """
    matches = {}

    # Preprocess Manifold questions for faster matching
    manifold_lower = [(m["question"].lower().strip(), m) for m in manifold_markets if m["volume"] > 100]

    for pm in poly_markets:
        poly_q = pm["question"].lower().strip()
        best_score = 0
        best_match = None

        for mq_lower, mm in manifold_lower:
            score = SequenceMatcher(None, poly_q, mq_lower).ratio()
            if score > best_score:
                best_score = score
                best_match = mm

        if best_score >= threshold and best_match:
            matches[pm["market_id"]] = {
                "manifold_prob": best_match["probability"],
                "manifold_q": best_match["question"][:60],
                "manifold_volume": best_match["volume"],
                "score": round(best_score, 3),
            }

    return matches


# ── Agent ───────────────────────────────────────────────

class ArbitrageAgent:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self.gamma: GammaAPI | None = None
        self.clob: CLOBAPI | None = None
        self.manifold: ManifoldAPI | None = None

        self.balance = 1000.0
        self.open_positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self.scan_count = 0
        self.total_entries = 0
        self.realized_pnl = 0.0

        # Logs for dashboard
        self.negrisk_opps: list[dict] = []
        self.xplat_opps: list[dict] = []

        # Caches
        self._events: list[dict] = []
        self._traded_ids: set[str] = set()

    async def _init_session(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self.gamma = GammaAPI(self._session)
        self.clob = CLOBAPI(self._session)
        self.manifold = ManifoldAPI(self._session)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def run(self):
        await self._init_session()
        logger.info(f"arb_v3_start balance=${self.balance:.2f}")

        self._events = await self.gamma.get_negrisk_events()

        try:
            await asyncio.gather(
                self._negrisk_loop(),
                self._xplat_loop(),
                self._status_loop(),
                return_exceptions=True,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("shutdown")
        finally:
            self._print_summary()
            await self.close()

    # ── NegRisk Arb Loop ────────────────────────────────

    async def _negrisk_loop(self):
        while True:
            self.scan_count += 1
            try:
                # Scan all events, collect opportunities
                candidates = []
                now = datetime.now(timezone.utc)

                for event in self._events:
                    if event["neg_risk_market_id"] in self._traded_ids:
                        continue

                    markets = event["markets"]
                    tids = [m["yes_token_id"] for m in markets if m["yes_token_id"]]
                    if len(tids) < 3:
                        continue

                    # Get resolution date
                    end_date_str = markets[0].get("end_date", "") if markets else ""
                    if end_date_str:
                        try:
                            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                            days = max(1, (end_dt - now).days)
                        except Exception:
                            days = 365
                    else:
                        days = 365

                    prices = await self.clob.midpoints_batch(tids)
                    valid_prices = [p for p in prices if p and p > 0]
                    if len(valid_prices) < 3:
                        continue

                    opp = scan_market(
                        outcome_prices=valid_prices,
                        event_title=event["title"],
                        neg_risk_market_id=event["neg_risk_market_id"],
                    )
                    if not opp or opp.net_profit_per_dollar < MIN_NEGRISK_EDGE:
                        continue
                    if opp.arb_type == "short" and opp.num_outcomes > 15:
                        continue

                    # Calculate annualized return for ranking
                    ann = (1 + opp.net_profit_per_dollar) ** (365 / days) - 1
                    candidates.append((ann, days, opp))

                # Sort by annualized return (best first)
                candidates.sort(key=lambda x: x[0], reverse=True)

                entries = 0
                for ann, days, opp in candidates:
                    if self.balance < 25:
                        break

                    self.negrisk_opps.append({
                        "ts": now.isoformat(),
                        "event": opp.event_title[:60],
                        "type": opp.arb_type,
                        "n": opp.num_outcomes,
                        "sum": opp.price_sum,
                        "net": opp.net_profit_per_dollar,
                        "days": days,
                        "ann": round(ann * 100, 1),
                    })
                    if len(self.negrisk_opps) > 200:
                        self.negrisk_opps = self.negrisk_opps[-200:]

                    pos = self._enter(
                        strategy="negrisk",
                        title=opp.event_title,
                        market_id=opp.neg_risk_market_id,
                        direction=opp.arb_type,
                        edge=opp.net_profit_per_dollar,
                        num_outcomes=opp.num_outcomes,
                        price_sum=opp.price_sum,
                        days_to_resolution=days,
                    )
                    if pos:
                        entries += 1

                if entries:
                    logger.info(f"NEGRISK SCAN #{self.scan_count}: +{entries} entries | open={len(self.open_positions)} balance=${self.balance:.2f}")

                # Refresh events every 10 scans
                if self.scan_count % 10 == 0:
                    self._events = await self.gamma.get_negrisk_events()

            except Exception as e:
                logger.error(f"NegRisk scan error: {type(e).__name__}: {e}")

            await asyncio.sleep(SCAN_INTERVAL)

    # ── Cross-Platform Arb Loop ─────────────────────────

    async def _xplat_loop(self):
        await asyncio.sleep(15)  # Let NegRisk go first
        while True:
            try:
                poly_markets = await self.gamma.get_top_markets(100)
                manifold_markets = await self.manifold.get_markets(500)

                if not manifold_markets:
                    logger.warning("No Manifold markets fetched")
                    await asyncio.sleep(XPLAT_SCAN_INTERVAL)
                    continue

                matches = fuzzy_match_markets(poly_markets, manifold_markets)
                logger.info(f"XPLAT: matched {len(matches)}/{len(poly_markets)} Polymarket markets to Manifold")

                entries = 0
                for pm in poly_markets:
                    mid = pm["market_id"]
                    if mid not in matches or mid in self._traded_ids:
                        continue
                    if self.balance < INVEST_PER_ARB:
                        break

                    m = matches[mid]
                    poly_price = pm["yes_price"]
                    mani_price = m["manifold_prob"]
                    disc = mani_price - poly_price

                    self.xplat_opps.append({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "question": pm["question"][:55],
                        "poly": round(poly_price, 3),
                        "manifold": round(mani_price, 3),
                        "disc": round(disc, 3),
                        "match_score": m["score"],
                    })
                    if len(self.xplat_opps) > 200:
                        self.xplat_opps = self.xplat_opps[-200:]

                    if abs(disc) < MIN_XPLAT_EDGE:
                        continue

                    direction = "buy_yes" if disc > 0 else "buy_no"
                    pos = self._enter(
                        strategy="xplat",
                        title=pm["question"],
                        market_id=mid,
                        direction=direction,
                        edge=abs(disc),
                        num_outcomes=2,
                        price_sum=poly_price,
                        extra={"manifold_prob": mani_price, "match_score": m["score"]},
                    )
                    if pos:
                        entries += 1
                        logger.info(
                            f"XPLAT ENTRY: {pm['question'][:45]} | "
                            f"{direction} poly={poly_price:.3f} manifold={mani_price:.3f} "
                            f"disc={disc:+.3f} match={m['score']:.2f}"
                        )

                if entries:
                    logger.info(f"XPLAT SCAN: +{entries} entries | open={len(self.open_positions)} balance=${self.balance:.2f}")

            except Exception as e:
                logger.error(f"XPLAT scan error: {type(e).__name__}: {e}")

            await asyncio.sleep(XPLAT_SCAN_INTERVAL)

    # ── Position Management ─────────────────────────────

    def _enter(self, strategy: str, title: str, market_id: str,
               direction: str, edge: float, num_outcomes: int,
               price_sum: float, days_to_resolution: int = 365,
               extra: dict = None) -> Position | None:
        """Enter a new position with Kelly-weighted sizing."""

        # Kelly-inspired sizing: invest more in higher-edge, shorter-duration opportunities
        # Base: $75. Scale by edge (more edge = more capital) and urgency (sooner = more)
        edge_multiplier = min(edge / 0.05, 3.0)  # 1x at 5% edge, 3x at 15%+
        time_multiplier = min(90 / max(days_to_resolution, 1), 3.0)  # 3x if <30d, 1x if 90d
        optimal_size = INVEST_PER_ARB * edge_multiplier * time_multiplier
        optimal_size = max(25, min(optimal_size, 200))  # Clamp $25-$200

        gas = GAS_PER_LEG * num_outcomes
        cost = optimal_size * (1 + SLIPPAGE + EXECUTION_DRIFT) + gas

        if cost > self.balance:
            # Try with reduced size
            cost = self.balance * 0.9
            optimal_size = (cost - gas) / (1 + SLIPPAGE + EXECUTION_DRIFT)
            if optimal_size < 25:
                return None

        if strategy == "negrisk":
            if direction == "long":
                num_shares = optimal_size / price_sum
            else:
                num_shares = optimal_size / (num_outcomes - price_sum)
        else:
            token_price = price_sum if direction == "buy_yes" else (1.0 - price_sum)
            num_shares = optimal_size / token_price if token_price > 0 else 0

        self.balance -= cost
        self._traded_ids.add(market_id)
        self.total_entries += 1

        # Annualized return for tracking
        ann_return = (1 + edge) ** (365 / max(days_to_resolution, 1)) - 1

        pos = Position(
            id=f"{strategy}_{uuid.uuid4().hex[:8]}",
            strategy=strategy,
            event_title=title[:80],
            market_id=market_id,
            direction=direction,
            entry_edge=edge,
            entry_cost=round(cost, 2),
            num_shares=num_shares,
            num_outcomes=num_outcomes,
            entered_at=datetime.now(timezone.utc),
            extra={
                **(extra or {}),
                "days_to_resolution": days_to_resolution,
                "annualized_return": round(ann_return * 100, 1),
                "optimal_size": round(optimal_size, 2),
            },
        )
        self.open_positions.append(pos)

        logger.info(
            f"{'NEGRISK' if strategy == 'negrisk' else 'XPLAT'} ENTRY: {title[:40]} | "
            f"{direction} edge={edge:.1%} days={days_to_resolution} ann={ann_return:.0%} | "
            f"size=${optimal_size:.0f} cost=${cost:.2f}"
        )
        return pos

    # ── Status ──────────────────────────────────────────

    async def _status_loop(self):
        while True:
            await asyncio.sleep(60)
            unrealized = self._unrealized()
            nr = sum(1 for p in self.open_positions if p.strategy == "negrisk")
            xp = sum(1 for p in self.open_positions if p.strategy == "xplat")
            logger.info(
                f"STATUS | negrisk={nr} xplat={xp} closed={len(self.closed_positions)} | "
                f"realized=${self.realized_pnl:+.2f} unrealized=${unrealized:+.2f} "
                f"total=${self.realized_pnl + unrealized:+.2f} | "
                f"balance=${self.balance:.2f}"
            )

    def _unrealized(self) -> float:
        total = 0.0
        for pos in self.open_positions:
            if pos.strategy == "negrisk":
                if pos.direction == "long":
                    total += pos.num_shares * 1.0 - pos.entry_cost
                else:
                    total += pos.num_shares * (pos.num_outcomes - 1) - pos.entry_cost
            else:
                # Cross-platform: estimate based on edge
                total += pos.entry_cost * pos.entry_edge * 0.5  # Conservative 50% of edge
        return total

    def _print_summary(self):
        unrealized = self._unrealized()
        logger.info("=" * 60)
        logger.info("FINAL SUMMARY")
        logger.info(f"  Entries: {self.total_entries}")
        logger.info(f"  Open: {len(self.open_positions)} (negrisk={sum(1 for p in self.open_positions if p.strategy=='negrisk')}, xplat={sum(1 for p in self.open_positions if p.strategy=='xplat')})")
        logger.info(f"  Realized P&L: ${self.realized_pnl:+.2f}")
        logger.info(f"  Unrealized P&L: ${unrealized:+.2f}")
        logger.info(f"  Total P&L: ${self.realized_pnl + unrealized:+.2f}")
        logger.info(f"  Balance: ${self.balance:.2f}")
        logger.info(f"  Capital deployed: ${self.starting_balance - self.balance:.2f}")
        logger.info("=" * 60)

    @property
    def starting_balance(self):
        return 1000.0


# ── Entry Point ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket Arbitrage Agent v3")
    parser.add_argument("--invest", type=float, default=75.0)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    global INVEST_PER_ARB, SCAN_INTERVAL
    INVEST_PER_ARB = args.invest
    SCAN_INTERVAL = args.interval

    asyncio.run(ArbitrageAgent().run())


if __name__ == "__main__":
    main()
