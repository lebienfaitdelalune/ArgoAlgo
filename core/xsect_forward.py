"""
xsect_forward.py
XsectForward — cross-sectional reversal FORWARD TEST (demo validation only).

Live implementation of backtest/research_xsect.py config `rev_lb60_rb1_nosl`:
  - Daily at 21:00 UTC (after NY close): rank the basket by 1440-H1-bar
    (~60 trading day) return.
  - Long the weakest pair, short the strongest, equal units per leg, NO SL/TP.
  - Re-trade a leg only when its wanted direction changes (avoids paying
    spread to re-establish an identical position). Holds over weekends.

Status: passed IS 2015-21 gates, FAILED OOS 2022-25 (PF 1.05). This module
exists to gather forward evidence on demo — it must NOT trade real money.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from utils.constants import Defaults, Direction
except ImportError:  # cTrader Cloud flat namespace
    from constants import Defaults, Direction  # type: ignore[no-redef]

if TYPE_CHECKING:
    from core.data_provider import DataProvider
    from core.logger import Logger
    from core.order_executor import OrderExecutor

STRATEGY_NAME = "XSectReversal"  # -> label abbrev "XS"


class XsectForward:
    """Daily cross-sectional reversal rebalancer.

    Args:
        api: cTrader Algo API object (or mock).
        data_provider: Shared DataProvider (bars access for all basket pairs).
        order_executor: Shared OrderExecutor (orders, closes, labels).
        logger: Shared Logger.
        symbols: Basket symbols (all must be USD-quote).
        units_per_leg: Fixed position size in units for each leg.
    """

    def __init__(self, api, data_provider: "DataProvider",
                 order_executor: "OrderExecutor", logger: "Logger",
                 symbols: list[str],
                 units_per_leg: int = Defaults.XS_UNITS_PER_LEG) -> None:
        self._api = api
        self._data_provider = data_provider
        self._order_executor = order_executor
        self._logger = logger
        self._symbols = list(symbols)
        self._units_per_leg = units_per_leg
        self._last_rebal_key: str | None = None  # date of last rebalance (dedupe)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Extend bar history so the 60-day lookback is available.

        cTrader loads a limited window by default; LoadMoreHistory() is
        called until Count exceeds the lookback (or attempts run out —
        rebalances then skip with a warning until history suffices).
        """
        needed = Defaults.XS_LOOKBACK_BARS + 2
        for symbol in self._symbols:
            try:
                bars = self._data_provider.get_bars(
                    symbol, self._data_provider.primary_timeframe)
                attempts = 0
                while bars.Count < needed and attempts < 40:
                    loaded = bars.LoadMoreHistory()
                    attempts += 1
                    if not loaded:  # 0 = no more history available
                        break
                self._logger.info(
                    f"XSect history {symbol}: {bars.Count} bars "
                    f"(need {needed})")
            except BaseException as exc:
                self._logger.error(
                    f"XSect history load failed for {symbol}: {type(exc).__name__}")

    # ------------------------------------------------------------------
    # Main entry — called from TradingBot.on_bar_closed
    # ------------------------------------------------------------------

    def on_bar_closed(self) -> None:
        """Rebalance if the 21:00 UTC bar has just closed (once per day)."""
        try:
            chart_bars = self._data_provider.get_bars(
                self._symbols[0], self._data_provider.primary_timeframe)
        except BaseException as exc:
            self._logger.error(f"XSect bars unavailable: {type(exc).__name__}")
            return

        offset = self._rebal_offset(chart_bars)
        if offset is None:
            return

        rebal_time = chart_bars.OpenTimes.Last(offset)
        day = rebal_time.DayOfWeek.ToString()[:3]
        if day in ("Sat", "Sun"):
            return
        key = f"{rebal_time.Year}-{rebal_time.Month}-{rebal_time.Day}"
        if key == self._last_rebal_key:
            return

        # 60-trading-day returns per pair, measured on the just-closed bar
        returns: dict[str, float] = {}
        for symbol in self._symbols:
            r = self._lookback_return(symbol)
            if r is None:
                return  # warning already logged; retry next bar close
            returns[symbol] = r

        self._last_rebal_key = key
        ranked = sorted(returns, key=returns.get)
        # Reversal: long the weakest, short the strongest
        want = {ranked[0]: Direction.BUY, ranked[-1]: Direction.SELL}
        self._logger.info(
            "XSect rebalance "
            + " ".join(f"{s}:{returns[s]*100:+.2f}%" for s in ranked)
            + f" -> long {ranked[0]}, short {ranked[-1]}")
        self._apply(want)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rebal_offset(self, bars) -> int | None:
        """Index (0 or 1) of the just-closed 21:00 UTC bar, or None.

        Checked at both offsets because cTrader bar-closed semantics differ
        by wiring: LastValue may be the closed bar or the newly opened one.
        """
        try:
            for offset in (0, 1):
                if bars.OpenTimes.Last(offset).Hour == Defaults.XS_REBAL_HOUR_UTC:
                    return offset
        except BaseException:
            pass
        return None

    def _lookback_return(self, symbol: str) -> float | None:
        """Return over XS_LOOKBACK_BARS H1 bars ending at the closed 21:00 bar."""
        try:
            bars = self._data_provider.get_bars(
                symbol, self._data_provider.primary_timeframe)
            offset = self._rebal_offset(bars)
            if offset is None:
                self._logger.warning(f"XSect: {symbol} bars not aligned to rebal hour")
                return None
            if bars.Count <= offset + Defaults.XS_LOOKBACK_BARS + 1:
                self._logger.warning(
                    f"XSect: insufficient history for {symbol} "
                    f"({bars.Count} bars)")
                return None
            now = bars.ClosePrices.Last(offset)
            past = bars.ClosePrices.Last(offset + Defaults.XS_LOOKBACK_BARS)
            if not past or past <= 0:
                return None
            return now / past - 1.0
        except BaseException as exc:
            self._logger.error(
                f"XSect return calc failed for {symbol}: {type(exc).__name__}")
            return None

    def _apply(self, want: dict[str, Direction]) -> None:
        """Close legs whose wanted direction changed; open missing legs."""
        held: set[str] = set()
        try:
            positions = list(self._api.Positions)
        except BaseException:
            positions = []

        for pos in positions:
            try:
                label = str(pos.Label)
                if not label.startswith(f"{Defaults.LABEL_PREFIX}_XS_"):
                    continue
                symbol = str(pos.SymbolName)
                current = (Direction.BUY if str(pos.TradeType) == "Buy"
                           else Direction.SELL)
                if want.get(symbol) == current:
                    held.add(symbol)
                else:
                    self._order_executor.close_position(pos, "XSect rebalance")
            except BaseException as exc:
                self._logger.error(f"XSect leg check failed: {type(exc).__name__}")

        for symbol, direction in want.items():
            if symbol in held:
                continue
            label = self._order_executor.build_label(STRATEGY_NAME, symbol)
            self._order_executor.execute_market_simple(
                symbol, direction, self._units_per_leg, label)

    def __repr__(self) -> str:
        return (f"XsectForward(symbols={self._symbols}, "
                f"units={self._units_per_leg})")
