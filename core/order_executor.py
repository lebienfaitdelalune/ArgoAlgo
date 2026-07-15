"""
order_executor.py
OrderExecutor — translates validated TradeInstructions into cTrader API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from utils.constants import Direction, OrderResult, RateLimits
except ImportError:
    from constants import Direction, OrderResult, RateLimits  # type: ignore[no-redef]

if TYPE_CHECKING:
    from core.logger import Logger
    from models.trade_instruction import TradeInstruction


@dataclass
class ExecutionResult:
    """Result of an order execution attempt.

    Attributes:
        outcome: SUCCESS, FAILED, or SKIPPED.
        position_id: ID of the opened/modified position (on success).
        error_message: Description of failure (on failure).
    """

    outcome: OrderResult
    position_id: int | None = None
    error_message: str | None = None

    def __repr__(self) -> str:
        if self.outcome == OrderResult.SUCCESS:
            return f"ExecutionResult(SUCCESS, pos_id={self.position_id})"
        return f"ExecutionResult({self.outcome.name}, error={self.error_message!r})"


class OrderExecutor:
    """Handles all cTrader API order interactions.

    Receives validated TradeInstructions from RiskManager and executes them,
    enforcing per-minute rate limits and routing all log output via Logger.

    Args:
        api: The cTrader Algo API object (robot instance).
        logger: Shared Logger instance.
        label_prefix: Prefix for all order labels (identifies bot positions).
    """

    def __init__(self, api, logger: "Logger", label_prefix: str = "ArgoAlgo") -> None:
        self._api = api
        self._logger = logger
        self._label_prefix = label_prefix

        # Per-minute rate limit counters (reset by reset_rate_counters())
        self._orders_this_minute: int = 0
        self._cancels_this_minute: int = 0
        self._sl_mods_this_minute: int = 0

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def execute(self, instruction: "TradeInstruction") -> ExecutionResult:
        """Execute a market order from a validated TradeInstruction.

        Skips unvalidated instructions and honours per-minute new-order rate
        limits. On success, logs the trade entry and returns SUCCESS. On API
        failure or exception, returns FAILED with an error message.

        Args:
            instruction: A risk-validated TradeInstruction.

        Returns:
            ExecutionResult describing the outcome.
        """
        if not instruction.validated:
            return ExecutionResult(
                outcome=OrderResult.SKIPPED,
                error_message=f"Not validated: {instruction.rejection_reason}",
            )

        if self._orders_this_minute >= RateLimits.NEW_ORDERS:
            self._logger.warning(
                f"New order rate limit reached ({self._orders_this_minute}/min)"
            )
            return ExecutionResult(
                outcome=OrderResult.SKIPPED,
                error_message="Rate limit: new orders per minute exceeded",
            )

        sig = instruction.signal
        label = self.build_label(sig.strategy_name, sig.symbol)
        tp_pips = sig.take_profit_pips if sig.take_profit_pips > 0.0 else None

        _tt = self._resolve_trade_type()
        if _tt is None:
            self._logger.error(
                f"execute() TradeType not available for {sig.symbol} — "
                "cannot resolve Buy/Sell enum"
            )
            return ExecutionResult(
                outcome=OrderResult.FAILED,
                error_message="TradeType not available",
            )

        try:
            trade_type = _tt.Buy if sig.direction == Direction.BUY else _tt.Sell
            # Use positional args — Python.NET does not reliably resolve keyword
            # arguments for overloaded .NET methods (ExecuteMarketOrder has many
            # overloads). Using keyword args silently throws TypeError every call.
            # Order: tradeType, symbolName, volumeInUnits, label, stopLossPips[, takeProfitPips]
            if tp_pips is not None:
                result = self._api.ExecuteMarketOrder(
                    trade_type,
                    sig.symbol,
                    instruction.volume_units,
                    label,
                    sig.stop_loss_pips,
                    tp_pips,
                )
            else:
                result = self._api.ExecuteMarketOrder(
                    trade_type,
                    sig.symbol,
                    instruction.volume_units,
                    label,
                    sig.stop_loss_pips,
                )
            self._orders_this_minute += 1

            if result.IsSuccessful:
                self._logger.trade_entry(instruction, result)
                return ExecutionResult(
                    outcome=OrderResult.SUCCESS,
                    position_id=result.Position.Id,
                )
            else:
                error_msg = str(result.Error)
                self._logger.error(f"Order failed for {sig.symbol}: {error_msg}")
                return ExecutionResult(
                    outcome=OrderResult.FAILED,
                    error_message=error_msg,
                )

        except BaseException as exc:
            _ename = type(exc).__name__
            try:
                _emsg = str(exc)
            except BaseException:
                _emsg = ""
            _full = f"{_ename}: {_emsg}" if _emsg else _ename
            self._logger.error(f"execute() exception for {sig.symbol}: {_full}")
            return ExecutionResult(
                outcome=OrderResult.FAILED,
                error_message=_full,
            )

    def execute_market_simple(self, symbol: str, direction: Direction,
                              volume_units: int, label: str) -> bool:
        """Execute a plain market order with NO stop-loss or take-profit.

        Used by the cross-sectional forward test, whose legs are managed by
        daily rebalance rather than protective stops. Honours the new-order
        rate limit. Returns True on success.

        Args:
            symbol: Instrument name (e.g. "GBPUSD").
            direction: Direction.BUY or Direction.SELL.
            volume_units: Position size in units.
            label: Full order label (identifies the leg).

        Returns:
            True if the order filled, False otherwise.
        """
        if self._orders_this_minute >= RateLimits.NEW_ORDERS:
            self._logger.warning(
                f"New order rate limit reached ({self._orders_this_minute}/min)"
            )
            return False

        _tt = self._resolve_trade_type()
        if _tt is None:
            self._logger.error(
                f"execute_market_simple() TradeType not available for {symbol}"
            )
            return False

        try:
            trade_type = _tt.Buy if direction == Direction.BUY else _tt.Sell
            # Positional args (see execute() note on Python.NET overloads):
            # tradeType, symbolName, volumeInUnits, label
            result = self._api.ExecuteMarketOrder(trade_type, symbol, volume_units, label)
            self._orders_this_minute += 1
            if result.IsSuccessful:
                self._logger.info(
                    f"Opened {direction.value} {symbol} vol={volume_units} label={label} (no SL)"
                )
                return True
            self._logger.error(f"Order failed for {symbol}: {result.Error}")
            return False
        except BaseException as exc:
            self._logger.error(
                f"execute_market_simple() exception for {symbol}: {type(exc).__name__}"
            )
            return False

    def _resolve_trade_type(self):
        """Resolve the cAlgo TradeType enum class.

        TradeType is a standalone cAlgo.API class — it is NOT a property of
        the Robot/api object. order_executor.py does not import from
        cAlgo.API directly (it would break tests), so we try three routes:
        (1) direct import, (2) builtins (injected by ArgoAlgo_main.py),
        (3) attribute on api as last resort. Returns None if unavailable.
        """
        try:
            from cAlgo.API import TradeType as _tt  # type: ignore[import]
            return _tt
        except ImportError:
            pass
        import builtins as _builtins
        _tt = getattr(_builtins, 'TradeType', None)
        if _tt is None:
            _tt = getattr(self._api, 'TradeType', None)
        return _tt

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def close_position(self, position, reason: str) -> bool:
        """Close a single position via the cTrader API.

        Logs the closure on success. On API failure or exception, logs the
        error and returns False without raising.

        Args:
            position: The cTrader Position object to close.
            reason: Human-readable reason for closure (for logging).

        Returns:
            True on success, False on failure.
        """
        pos_id = getattr(position, "Id", "?")
        try:
            result = self._api.ClosePosition(position)
            if result.IsSuccessful:
                self._logger.trade_exit(position, reason)
                return True
            else:
                self._logger.error(
                    f"ClosePosition failed for #{pos_id}: {result.Error}"
                )
                return False
        except BaseException as exc:
            self._logger.error(
                f"close_position() exception for #{pos_id}", exc=exc
            )
            return False

    def close_all_positions(self, reason: str) -> int:
        """Close all positions managed by this bot.

        Only positions whose label starts with the bot's label prefix are
        closed; others are left untouched. Failures are logged individually
        and do not abort the remaining closures.

        Args:
            reason: Human-readable reason (logged per closure and in summary).

        Returns:
            Number of positions successfully closed.
        """
        try:
            positions = list(self._api.Positions)
        except BaseException as exc:
            self._logger.error("close_all_positions() failed to list positions", exc=exc)
            return 0

        closed = 0
        for position in positions:
            if self.is_bot_position(position):
                if self.close_position(position, reason):
                    closed += 1

        if closed:
            self._logger.risk_action(f"Closed {closed} position(s): {reason}")
        return closed

    def modify_sl(self, position, new_sl_price: float) -> bool:
        """Modify the stop-loss price of an open position.

        Respects the per-minute SL modification rate limit defined in
        RateLimits.MODIFY_PROTECTION_L1. On exception, logs and returns False.

        Args:
            position: The cTrader Position object.
            new_sl_price: New stop-loss price level.

        Returns:
            True on success, False on failure or rate-limit rejection.
        """
        if self._sl_mods_this_minute >= RateLimits.MODIFY_PROTECTION_L1:
            self._logger.warning(
                f"SL modification rate limit reached ({self._sl_mods_this_minute}/min)"
            )
            return False

        pos_id = getattr(position, "Id", "?")
        try:
            position.ModifyStopLossPrice(new_sl_price)
            self._sl_mods_this_minute += 1
            return True
        except BaseException as exc:
            self._logger.error(
                f"modify_sl() exception for #{pos_id}", exc=exc
            )
            return False

    # ------------------------------------------------------------------
    # Label / identity helpers
    # ------------------------------------------------------------------

    def build_label(self, strategy_name: str, symbol: str) -> str:
        """Build a standardised order label for a trade.

        Format: ``{prefix}_{StrategyAbbrev}_{Symbol}``.
        StrategyAbbrev is strategy_name[:2].upper() — "TR" for TrendFollowing,
        "ME" for MeanReversion, "BR" for Breakout.
        Example: ``ArgoAlgo_TR_EURUSD``

        Args:
            strategy_name: Full strategy name (e.g. "TrendFollowing").
            symbol: Instrument name (e.g. "EURUSD").

        Returns:
            Order label string.
        """
        abbrev = strategy_name[:2].upper()
        return f"{self._label_prefix}_{abbrev}_{symbol}"

    def is_bot_position(self, position) -> bool:
        """Return True if this position was opened by this bot instance."""
        return str(position.Label).startswith(self._label_prefix)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def reset_rate_counters(self) -> None:
        """Reset per-minute rate limit counters.

        Called every 60 seconds by a timer configured in TradingBot.on_start().
        """
        self._orders_this_minute = 0
        self._cancels_this_minute = 0
        self._sl_mods_this_minute = 0

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def reset_rate_counters(self) -> None:
        """Reset per-minute rate counters.

        Called from on_bar_closed (H1 bars, so at most hourly) — stricter
        than cTrader's per-minute limits, never looser.
        """
        self._orders_this_minute = 0
        self._cancels_this_minute = 0
        self._sl_mods_this_minute = 0

    def __repr__(self) -> str:
        return f"OrderExecutor(prefix={self._label_prefix!r})"
