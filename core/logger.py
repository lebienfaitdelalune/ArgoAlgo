"""
logger.py
Centralized logging service for ArgoAlgo.
All output is routed through this class — no bare print() calls in production code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from utils.constants import LogLevel, NotificationLevel
except ImportError:
    from constants import LogLevel, NotificationLevel  # type: ignore[no-redef]

if TYPE_CHECKING:
    from models.performance import PerformanceSnapshot
    from models.trade_instruction import TradeInstruction


class Logger:
    """Centralized logging and notification service.

    Wraps cTrader's api.Print() and optionally writes to a daily log file.
    All other modules receive a Logger instance via constructor injection.

    Args:
        api: The cTrader Algo API object.
        log_level: Minimum level to emit.
        file_logging: If True, also write to a daily .log file (local only).
        label_prefix: Bot label used in log headers.
    """

    def __init__(self, api, log_level: LogLevel, file_logging: bool, label_prefix: str) -> None:
        self._api = api
        self._log_level = log_level
        self._file_logging = file_logging
        self._label_prefix = label_prefix
        self._log_file = None  # opened lazily on first file write

    # ------------------------------------------------------------------
    # Core log methods
    # ------------------------------------------------------------------

    def debug(self, msg: str) -> None:
        """Emit a DEBUG-level message."""
        if self._log_level.value <= LogLevel.DEBUG.value:
            self._emit("DEBUG", msg)

    def info(self, msg: str) -> None:
        """Emit an INFO-level message."""
        if self._log_level.value <= LogLevel.INFO.value:
            self._emit("INFO", msg)

    def warning(self, msg: str) -> None:
        """Emit a WARNING-level message (always emitted if level <= WARNING)."""
        if self._log_level.value <= LogLevel.WARNING.value:
            self._emit("WARN", msg)

    def error(self, msg: str, exc: Exception | None = None) -> None:
        """Emit an ERROR-level message with optional exception details."""
        full_msg = msg
        if exc is not None:
            full_msg = f"{msg} | Exception: {type(exc).__name__}: {exc}"
        self._emit("ERROR", full_msg)

    # ------------------------------------------------------------------
    # Structured log methods
    # ------------------------------------------------------------------

    def trade_entry(self, instruction: "TradeInstruction", result) -> None:
        """Log a trade entry with full structured details.

        Args:
            instruction: The validated TradeInstruction that was executed.
            result: The execution result object from OrderExecutor.
        """
        sig = instruction.signal
        self._emit(
            "TRADE",
            f"ENTRY | {sig.symbol} {sig.direction.value} "
            f"vol={instruction.volume_units:.0f} "
            f"entry={sig.entry_price:.5f} "
            f"sl={sig.stop_loss_pips:.1f}pips "
            f"tp={sig.take_profit_pips:.1f}pips "
            f"strategy={sig.strategy_name}",
        )

    def trade_exit(self, position, reason: str) -> None:
        """Log a trade exit with structured details.

        Args:
            position: The cTrader Position object that was closed.
            reason: Human-readable reason for closure (e.g. "SL hit", "Strategy exit").
        """
        self._emit(
            "TRADE",
            f"EXIT  | {position.SymbolName} "
            f"pnl={position.NetProfit:+.2f} "
            f"reason={reason}",
        )

    def risk_action(self, msg: str) -> None:
        """Log a risk management action (always INFO level or above)."""
        self._emit("RISK", msg)

    def daily_summary(self, snapshot: "PerformanceSnapshot") -> None:
        """Log a formatted daily performance summary.

        Args:
            snapshot: The current PerformanceSnapshot.
        """
        lines = [
            "=" * 50,
            f"  DAILY SUMMARY  {snapshot.timestamp.strftime('%Y-%m-%d')}",
            "=" * 50,
            f"  Trades today : {snapshot.trade_count_today}",
            f"  Daily P/L    : {snapshot.daily_pnl:+.2f}",
            f"  Daily DD     : {snapshot.daily_drawdown_pct:.2f}%",
            f"  Total DD     : {snapshot.total_drawdown_pct:.2f}%",
            f"  Balance      : {snapshot.balance:.2f}",
            f"  Equity       : {snapshot.equity:.2f}",
            "=" * 50,
        ]
        for line in lines:
            self._emit("INFO", line)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, level: str, msg: str) -> None:
        """Format and dispatch a log line to all configured outputs."""
        from datetime import datetime

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp} UTC] [{level:5s}] [{self._label_prefix}] {msg}"

        # Always write to cTrader log tab.
        # In cTrader Cloud, EngineHelper sets builtins.cs_print to a C# delegate
        # that calls Robot.Print. Use builtins.print() (which routes to cs_print)
        # rather than self._api.Print() directly: a direct Python.NET call to
        # api.Print(params object[]) can throw a NullReferenceException that cTrader
        # Cloud's Python.NET does NOT wrap as a Python exception, so
        # except BaseException cannot catch it (DEMO-003/DEMO-005).
        # In test environments (no cs_print in builtins) use api.Print() directly
        # so tests can verify logging via api.Print.call_args_list.
        import builtins as _builtins
        _in_ctrader = hasattr(_builtins, 'cs_print')
        try:
            if _in_ctrader:
                print(line)
            else:
                self._api.Print(line)
        except BaseException:
            pass

        # Optionally write to file (local execution only)
        if self._file_logging:
            self._write_to_file(line)

    def _write_to_file(self, line: str) -> None:
        """Append *line* to the daily log file, handling Cloud restrictions."""
        from datetime import datetime

        try:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            filename = f"ArgoAlgo_{date_str}.log"
            with open(filename, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except BaseException:
            # Cloud does not support file I/O — fail silently
            pass

    def __repr__(self) -> str:
        return f"Logger(level={self._log_level.name}, file_logging={self._file_logging})"
