"""
trade_signal.py
TradeSignal dataclass — output of the StrategyEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

try:
    from utils.constants import Direction
except ImportError:
    from constants import Direction  # type: ignore[no-redef]


@dataclass
class TradeSignal:
    """Represents a raw trade signal produced by a strategy.

    A signal captures the strategy's intent before any risk validation.
    The RiskManager consumes a TradeSignal and produces a TradeInstruction.

    Attributes:
        strategy_name: Name of the strategy that produced this signal.
        symbol: Instrument name (e.g. "EURUSD").
        direction: BUY, SELL, or NONE.
        stop_loss_pips: Recommended stop-loss distance in pips.
        take_profit_pips: Recommended take-profit distance in pips (0 = no TP).
        entry_price: Indicative entry price at signal time.
        timestamp: UTC datetime when the signal was generated.
        metadata: Strategy-specific debug data (indicator values, etc.).
    """

    strategy_name: str
    symbol: str
    direction: Direction
    stop_loss_pips: float
    take_profit_pips: float
    entry_price: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)

    def is_actionable(self) -> bool:
        """Return True if the signal represents an actual trade direction."""
        return self.direction != Direction.NONE

    def __repr__(self) -> str:
        return (
            f"TradeSignal("
            f"strategy={self.strategy_name!r}, "
            f"symbol={self.symbol!r}, "
            f"direction={self.direction.value}, "
            f"sl={self.stop_loss_pips:.1f}pips, "
            f"tp={self.take_profit_pips:.1f}pips, "
            f"entry={self.entry_price:.5f}, "
            f"ts={self.timestamp.strftime('%H:%M:%S')}"
            f")"
        )
