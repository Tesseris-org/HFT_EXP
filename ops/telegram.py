"""Telegram notifications for trade alerts and errors."""

import asyncio
import json
import logging
from typing import Optional

import aiohttp

from config import settings
from core.models import Decision, Order, Position

logger = logging.getLogger(__name__)


class TelegramAlerter:
    """Send alerts via Telegram bot."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)

        if not self.enabled:
            logger.warning("Telegram alerts disabled (missing BOT_TOKEN or CHAT_ID)")

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message via Telegram bot API."""
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        logger.error(f"Telegram API error: {resp.status} {await resp.text()}")
                        return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def alert_trade(self, decision: Decision, order: Order, market_question: str):
        """Alert on a trade execution."""
        if not self.enabled:
            return

        icon = "🟢" if decision.action.startswith("BUY") else "🔴"
        text = (
            f"{icon} <b>TRADE EXECUTED</b>\n\n"
            f"<b>Market:</b> {market_question[:80]}\n"
            f"<b>Action:</b> {decision.action}\n"
            f"<b>Size:</b> ${decision.size_usd:.2f}\n"
            f"<b>Price:</b> ${order.price:.3f}\n\n"
            f"<b>Edge:</b> {decision.edge:+.1%}\n"
            f"<b>Ensemble:</b> {decision.ensemble_prob:.1%}\n"
            f"<b>Market:</b> {decision.market_prob:.1%}\n"
            f"<b>Confidence:</b> {decision.confidence if hasattr(decision, 'confidence') else 'N/A'}\n\n"
            f"<code>Order ID: {order.id}</code>"
        )
        await self.send_message(text)

    async def alert_error(self, error_type: str, message: str, context: Optional[dict] = None):
        """Alert on system errors."""
        if not self.enabled:
            return

        text = (
            f"⚠️ <b>ERROR: {error_type}</b>\n\n"
            f"{message}\n"
        )
        if context:
            text += f"\n<code>{json.dumps(context, indent=2)[:500]}</code>"

        await self.send_message(text)

    async def alert_risk_limit(self, limit_type: str, current: float, threshold: float):
        """Alert when risk limits are breached."""
        if not self.enabled:
            return

        text = (
            f"🚨 <b>RISK LIMIT BREACH</b>\n\n"
            f"<b>Type:</b> {limit_type}\n"
            f"<b>Current:</b> {current:.2f}\n"
            f"<b>Threshold:</b> {threshold:.2f}\n\n"
            f"Trading paused. Review positions."
        )
        await self.send_message(text)

    async def send_daily_summary(self, balance: float, positions: list[Position],
                                  pnl_realized: float, pnl_unrealized: float,
                                  num_trades: int):
        """Send end-of-day summary."""
        if not self.enabled:
            return

        total_pnl = pnl_realized + pnl_unrealized
        text = (
            f"📊 <b>DAILY SUMMARY</b>\n\n"
            f"<b>Balance:</b> ${balance:.2f}\n"
            f"<b>Realized P&L:</b> ${pnl_realized:+.2f}\n"
            f"<b>Unrealized P&L:</b> ${pnl_unrealized:+.2f}\n"
            f"<b>Total P&L:</b> ${total_pnl:+.2f}\n\n"
            f"<b>Trades:</b> {num_trades}\n"
            f"<b>Open Positions:</b> {len(positions)}\n"
        )

        if positions:
            text += "\n<b>Positions:</b>\n"
            for p in positions[:5]:  # show top 5
                text += f"  • {p.side} {p.size:.0f} @ ${p.avg_cost:.3f}\n"

        await self.send_message(text)

    async def alert_agent_start(self, mode: str, balance: float):
        """Alert when agent starts."""
        if not self.enabled:
            return

        icon = "📄" if mode == "paper" else "💰"
        text = (
            f"{icon} <b>AGENT STARTED</b>\n\n"
            f"<b>Mode:</b> {mode.upper()}\n"
            f"<b>Starting Balance:</b> ${balance:.2f}\n\n"
            f"Monitoring for opportunities..."
        )
        await self.send_message(text)

    async def alert_agent_stop(self, reason: str):
        """Alert when agent stops."""
        if not self.enabled:
            return

        text = (
            f"⏸️ <b>AGENT STOPPED</b>\n\n"
            f"<b>Reason:</b> {reason}\n"
        )
        await self.send_message(text)
