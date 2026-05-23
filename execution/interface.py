"""Abstract executor interface — same contract for paper and live."""

from abc import ABC, abstractmethod
from core.models import Decision, Order, Position


class Executor(ABC):
    @abstractmethod
    async def place_order(self, decision: Decision) -> Order:
        """Submit an order based on a trade decision."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successfully cancelled."""

    @abstractmethod
    async def get_open_orders(self) -> list[Order]:
        """Get all currently open orders."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get current positions from the exchange."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Get available USDC balance."""
