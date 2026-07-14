"""
base_strategy.py
IStrategy — abstract base class that all trading strategies must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.data_provider import DataProvider
    from core.logger import Logger
    from models.trade_signal import TradeSignal


class IStrategy(ABC):
    """Abstract base class for all ArgoAlgo trading strategies.

    Each strategy encapsulates a self-contained set of entry/exit rules.
    Strategies are stateless with respect to market evaluation — evaluate()
    reads from DataProvider each call and returns a fresh signal.

    Subclasses must define the class-level ``name`` attribute.

    Args:
        api: The cTrader Algo API object.
        data_provider: Shared DataProvider instance.
        logger: Shared Logger instance.
        params: Dict of strategy-specific parameter values.
    """

    name: str = "BaseStrategy"  # Override in subclasses

    def __init__(
        self,
        api,
        data_provider: "DataProvider",
        logger: "Logger",
        params: dict,
    ) -> None:
        self._api = api
        self._data_provider = data_provider
        self._logger = logger
        self._params = params

    @abstractmethod
    def evaluate(self, symbol: str) -> "TradeSignal":
        """Evaluate current market conditions and return a trade signal.

        This method is called on every on_bar_closed event for each
        tracked symbol. It must be fast and side-effect free.

        Args:
            symbol: The instrument name to evaluate (e.g. "EURUSD").

        Returns:
            A TradeSignal. If no trade is warranted, return a signal
            with direction=Direction.NONE.
        """

    @abstractmethod
    def should_close(self, position) -> bool:
        """Determine whether this strategy wants to close an open position.

        Called by StrategyEngine.check_exits() for each open position
        associated with this strategy.

        Args:
            position: The cTrader Position object to evaluate.

        Returns:
            True if the strategy's exit condition is met.
        """

    def on_position_closed(self, position) -> None:
        """Optional hook called after a position managed by this strategy closes.

        Use this to reset any per-symbol state the strategy maintains.

        Args:
            position: The cTrader Position object that was closed.
        """

    def _no_signal(self, symbol: str) -> "TradeSignal":
        """Helper to return a NONE signal with sensible defaults.

        Args:
            symbol: Instrument name.

        Returns:
            A TradeSignal with Direction.NONE.
        """
        from datetime import datetime

        from models.trade_signal import TradeSignal
        from utils.constants import Direction

        return TradeSignal(
            strategy_name=self.name,
            symbol=symbol,
            direction=Direction.NONE,
            stop_loss_pips=0.0,
            take_profit_pips=0.0,
            entry_price=0.0,
            timestamp=datetime.utcnow(),
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
