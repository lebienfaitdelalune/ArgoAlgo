"""
performance.py
PerformanceSnapshot dataclass — used by UIPanel, Logger, and notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class PerformanceSnapshot:
    """A point-in-time snapshot of the bot's performance metrics.

    Attributes:
        timestamp: UTC time of the snapshot.
        balance: Account balance in account currency.
        equity: Account equity (balance + unrealized P/L).
        open_positions: Number of currently open positions.
        daily_pnl: Realized + unrealized P/L since daily reset.
        daily_drawdown_pct: Drawdown from daily start balance (%).
        total_drawdown_pct: Drawdown from high-water mark (%).
        trade_count_today: Number of trades opened today.
    """

    timestamp: datetime
    balance: float
    equity: float
    open_positions: int
    daily_pnl: float
    daily_drawdown_pct: float
    total_drawdown_pct: float
    trade_count_today: int

    def __repr__(self) -> str:
        return (
            f"PerformanceSnapshot("
            f"equity={self.equity:.2f}, "
            f"positions={self.open_positions}, "
            f"daily_pnl={self.daily_pnl:+.2f}, "
            f"daily_dd={self.daily_drawdown_pct:.2f}%, "
            f"total_dd={self.total_drawdown_pct:.2f}%"
            f")"
        )
