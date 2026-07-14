"""
trade_instruction.py
TradeInstruction dataclass — output of the RiskManager, input to OrderExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from models.trade_signal import TradeSignal
except ImportError:
    from trade_signal import TradeSignal  # type: ignore[no-redef]


@dataclass
class TradeInstruction:
    """A risk-validated trade instruction ready for execution.

    Produced by RiskManager.validate(). Only instructions with
    validated=True should be passed to the OrderExecutor.

    Attributes:
        signal: The originating TradeSignal.
        volume_units: Calculated position size in instrument units.
        validated: True if all risk checks passed.
        rejection_reason: Human-readable reason if validated is False.
    """

    signal: TradeSignal
    volume_units: float
    validated: bool
    rejection_reason: str | None = field(default=None)

    def __repr__(self) -> str:
        if self.validated:
            return (
                f"TradeInstruction("
                f"symbol={self.signal.symbol!r}, "
                f"direction={self.signal.direction.value}, "
                f"volume={self.volume_units:.0f}, "
                f"validated=True"
                f")"
            )
        return (
            f"TradeInstruction("
            f"symbol={self.signal.symbol!r}, "
            f"validated=False, "
            f"reason={self.rejection_reason!r}"
            f")"
        )
