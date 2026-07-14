"""
strategy_engine.py
StrategyEngine — orchestrates active strategies and produces trade signals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from utils.constants import StrategyMode
except ImportError:
    from constants import StrategyMode  # type: ignore[no-redef]

if TYPE_CHECKING:
    from core.data_provider import DataProvider
    from core.logger import Logger
    from models.trade_signal import TradeSignal
    from strategies.base_strategy import IStrategy


class StrategyEngine:
    """Orchestrates all active strategies and returns consolidated signals.

    Delegates evaluation to each active strategy and applies the configured
    strategy selection mode (manual or ADX-based switching).

    Args:
        strategies: List of IStrategy instances to manage.
        mode: StrategyMode controlling how strategies are selected.
        adx_threshold: ADX value used for ADX_SWITCHING mode.
        data_provider: Shared DataProvider instance.
        logger: Shared Logger instance.
    """

    def __init__(
        self,
        strategies: list["IStrategy"],
        mode: StrategyMode,
        adx_threshold: float,
        data_provider: "DataProvider",
        logger: "Logger",
    ) -> None:
        self._strategies = {s.name: s for s in strategies}
        self._mode = mode
        self._adx_threshold = adx_threshold
        self._data_provider = data_provider
        self._logger = logger

    def evaluate(self, symbols: list[str]) -> list["TradeSignal"]:
        """Evaluate all active strategies across all tracked symbols.

        Args:
            symbols: List of instrument names to evaluate.

        Returns:
            List of actionable TradeSignals (Direction != NONE).
        """
        signals: list["TradeSignal"] = []

        for symbol in symbols:
            active = self._select_strategies(symbol)
            for strategy in active:
                try:
                    signal = strategy.evaluate(symbol)
                    if signal.is_actionable():
                        self._logger.debug(f"Signal: {signal}")
                        signals.append(signal)
                except BaseException as exc:
                    self._logger.error(
                        f"Strategy {strategy.name} raised an error for {symbol}", exc
                    )

        return signals

    def check_exits(self, positions) -> list[tuple]:
        """Identify open positions that should be closed per strategy exit rules.

        Matches each position to its originating strategy via the order label
        (e.g. label "ArgoAlgo_TR_EURUSD" → strategy abbrev "TR" → TrendFollowing).

        Args:
            positions: Iterable of cTrader Position objects.

        Returns:
            List of (position, reason) tuples for positions to close.
        """
        # Build abbrev → strategy map (strategy_name[:2].upper())
        abbrev_map = {name[:2].upper(): s for name, s in self._strategies.items()}
        exits = []
        for position in positions:
            try:
                parts = str(position.Label).split("_")
                if len(parts) < 3:
                    continue
                abbrev = parts[1]
                strategy = abbrev_map.get(abbrev)
                if strategy is None:
                    continue
                if strategy.should_close(position):
                    exits.append((position, f"Strategy exit: {strategy.name}"))
            except BaseException as exc:
                self._logger.error(
                    f"check_exits error for position #{getattr(position, 'Id', '?')}",
                    exc,
                )
        return exits

    def _select_strategies(self, symbol: str) -> list["IStrategy"]:
        """Return the strategies that should run for this symbol and mode.

        MANUAL mode: all registered strategies run.
        ADX_SWITCHING mode: TrendFollowing when ADX >= threshold, else
        MeanReversion. BreakoutStrategy always runs alongside either.

        Args:
            symbol: Instrument name.

        Returns:
            List of active IStrategy instances.
        """
        if self._mode == StrategyMode.MANUAL:
            return list(self._strategies.values())

        # ADX_SWITCHING — read ADX for this symbol
        try:
            adx_ind = self._data_provider.get_indicator(symbol, "adx")
            adx_value = float(adx_ind.ADX.Last(0))
        except BaseException:
            # If ADX unavailable, fall back to all strategies
            return list(self._strategies.values())

        selected: list["IStrategy"] = []
        if adx_value >= self._adx_threshold:
            tf = self._strategies.get("TrendFollowing")
            if tf:
                selected.append(tf)
        else:
            mr = self._strategies.get("MeanReversion")
            if mr:
                selected.append(mr)

        # Breakout always runs alongside if registered
        bo = self._strategies.get("Breakout")
        if bo and bo not in selected:
            selected.append(bo)

        return selected

    @property
    def strategy_names(self) -> list[str]:
        """Return the names of all registered strategies."""
        return list(self._strategies.keys())

    def __repr__(self) -> str:
        return (
            f"StrategyEngine("
            f"mode={self._mode.value}, "
            f"strategies={self.strategy_names})"
        )
