"""
execution/base_executor.py — Abstract base class for all exchange executors.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseExecutor(ABC):

    @abstractmethod
    async def execute_signal(self, signal: Dict) -> Optional[Dict]:
        """
        Execute a LONG or SHORT signal on the exchange.

        Args:
            signal: Parsed signal dict from SignalEngine.

        Returns:
            Execution result dict, or None on failure / HOLD.
        """

    @abstractmethod
    async def close_position(self, symbol: str) -> Optional[Dict]:
        """
        Market-close an open position for the given symbol.

        Args:
            symbol: Exchange-native symbol string.

        Returns:
            Result dict, or None on failure / no position found.
        """

    @abstractmethod
    async def get_open_positions(self) -> List[Dict]:
        """
        Return list of currently open positions.
        In DRY_RUN mode, returns an empty list.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release resources (close HTTP sessions, exchange connections, etc.)"""
