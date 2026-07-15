"""
data_provider.py
DataProvider — clean interface for multi-symbol, multi-timeframe market data and indicators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from utils.constants import Defaults, MIN_BARS_REQUIRED
except ImportError:
    from constants import Defaults, MIN_BARS_REQUIRED  # type: ignore[no-redef]

# MovingAverageType is a cTrader Cloud enum; not available in unit tests.
try:
    from cAlgo.API import MovingAverageType as _MovingAverageType  # type: ignore[import]
    _MA_SIMPLE = _MovingAverageType.Simple
    _MA_EXPONENTIAL = _MovingAverageType.Exponential
except ImportError:
    _MA_SIMPLE = None
    _MA_EXPONENTIAL = None

if TYPE_CHECKING:
    from core.logger import Logger


class DataProviderError(Exception):
    """Raised when DataProvider cannot fulfil a data request."""


class _RollingExtreme:
    """Lightweight drop-in for Donchian high/low when cTrader has no built-in.

    Provides the same ``.Result.Last(n)`` interface as a cTrader indicator so
    strategy code needs no changes.  Reads directly from a cTrader DataSeries.
    """

    class _ResultSeries:
        def __init__(self, prices, period: int, fn):
            self._prices = prices
            self._period = period
            self._fn = fn  # builtin max or min

        def Last(self, n: int) -> float:
            import math
            try:
                count = int(self._prices.Count)
                end_idx = count - n          # exclusive upper bound (most-recent = count-1)
                start_idx = max(0, end_idx - self._period)
                vals = [float(self._prices[i]) for i in range(start_idx, end_idx)]
                return self._fn(vals) if vals else math.nan
            except BaseException:
                import math
                return math.nan

    def __init__(self, prices, period: int, fn):
        self.Result = self._ResultSeries(prices, period, fn)


class _ManualATR:
    """Simple-average ATR when cTrader's built-in throws on all known signatures.

    Provides the same ``.Result.Last(n)`` interface as a cTrader ATR indicator.
    True Range = max(H-L, |H-prevC|, |L-prevC|); averaged over *period* bars.
    """

    class _ResultSeries:
        def __init__(self, bars, period: int):
            self._bars = bars
            self._period = period

        def Last(self, n: int) -> float:
            import math
            try:
                count = int(self._bars.Count)
                end_idx = count - n        # exclusive upper bound
                start_idx = max(1, end_idx - self._period)
                tr_vals = []
                for i in range(start_idx, end_idx):
                    h = float(self._bars.HighPrices[i])
                    l = float(self._bars.LowPrices[i])
                    pc = float(self._bars.ClosePrices[i - 1])
                    tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))
                return sum(tr_vals) / len(tr_vals) if tr_vals else math.nan
            except BaseException:
                import math
                return math.nan

    def __init__(self, bars, period: int):
        self.Result = self._ResultSeries(bars, period)


class DataProvider:
    """Manages market data access and indicator initialization.

    Provides a unified interface so strategy classes can query prices
    and indicator values without directly calling the cTrader API.

    Args:
        api: The cTrader Algo API object.
        symbols: List of instrument names to track.
        logger: Shared Logger instance.
        indicator_params: Dict of indicator period/param overrides.
                          Keys match those documented in _init_indicators().
                          Missing keys fall back to Defaults values.
    """

    def __init__(
        self,
        api,
        symbols: list[str],
        logger: "Logger",
        indicator_params: dict | None = None,
    ) -> None:
        self._api = api
        self._symbols = symbols
        self._logger = logger
        self._indicator_params: dict = indicator_params or {}

        self._symbol_objects: dict = {}           # {symbol_name: Symbol}
        self._bars: dict = {}                     # {symbol_name: {timeframe: Bars}}
        self._indicators: dict = {}               # {symbol_name: {key: Indicator}}
        self._primary_timeframe = None            # set in initialize()

    def initialize(self, timeframes: list) -> None:
        """Load all symbol objects, bars, and indicators.

        Must be called once during on_start before any other method.
        The first timeframe in the list is used as the primary timeframe
        for indicator initialization.

        Args:
            timeframes: List of cTrader TimeFrame values to load bars for.
                        Element 0 is the primary (signal) timeframe;
                        subsequent elements are higher timeframes for context.
        """
        if not timeframes:
            self._logger.warning("DataProvider.initialize() called with empty timeframes list")
            return

        self._primary_timeframe = timeframes[0]
        self._logger.info(
            f"DataProvider initializing {len(self._symbols)} symbols "
            f"across {len(timeframes)} timeframe(s)..."
        )
        self._log_available_symbols()

        for symbol_name in self._symbols:
            try:
                self._load_symbol(symbol_name, timeframes)
            except BaseException as exc:
                self._logger.error(
                    f"Unexpected error loading {symbol_name!r}: {type(exc).__name__}"
                )

        self._logger.info(
            f"DataProvider ready: {len(self._symbol_objects)} symbol(s) loaded."
        )

    def update(self) -> None:
        """Refresh derived state after a bar closes.

        Most cTrader data objects are live references, so this is
        primarily a hook for any custom derived state.
        """
        pass  # cTrader Bars/Indicators update automatically

    def get_bars(self, symbol: str, timeframe) -> object:
        """Return the Bars collection for a symbol and timeframe.

        Args:
            symbol: Instrument name (e.g. "EURUSD").
            timeframe: cTrader TimeFrame value.

        Returns:
            cTrader Bars object.

        Raises:
            DataProviderError: If symbol or timeframe is not loaded.
        """
        if symbol not in self._bars:
            raise DataProviderError(f"Symbol not loaded: {symbol!r}")
        if timeframe not in self._bars[symbol]:
            raise DataProviderError(
                f"Timeframe {timeframe!r} not loaded for {symbol!r}"
            )
        return self._bars[symbol][timeframe]

    def get_symbol(self, symbol: str) -> object:
        """Return the cTrader Symbol object for an instrument.

        Args:
            symbol: Instrument name.

        Returns:
            cTrader Symbol object (contains PipSize, PipValue, VolumeInUnitsMin, etc.).

        Raises:
            DataProviderError: If symbol is not loaded.
        """
        if symbol not in self._symbol_objects:
            raise DataProviderError(f"Symbol not loaded: {symbol!r}")
        return self._symbol_objects[symbol]

    def get_spread_pips(self, symbol: str) -> float:
        """Return the current bid/ask spread in pips for a symbol.

        Args:
            symbol: Instrument name.

        Returns:
            Spread in pips as a float.
        """
        sym = self.get_symbol(symbol)
        spread_price = sym.Ask - sym.Bid
        return spread_price / sym.PipSize

    def is_spread_acceptable(self, symbol: str, max_spread_pips: float) -> bool:
        """Check whether the current spread is within the configured limit.

        Args:
            symbol: Instrument name.
            max_spread_pips: Maximum allowable spread in pips.

        Returns:
            True if spread <= max_spread_pips.
        """
        return self.get_spread_pips(symbol) <= max_spread_pips

    def get_indicator(self, symbol: str, indicator_key: str) -> object:
        """Return a pre-initialized indicator instance.

        Args:
            symbol: Instrument name.
            indicator_key: One of: ema_fast, ema_slow, adx, atr, bollinger,
                           rsi, adx_filter, donchian_high, donchian_low, atr_bo.

        Returns:
            cTrader Indicator object.

        Raises:
            DataProviderError: If key or symbol is not found.
        """
        if symbol not in self._indicators:
            raise DataProviderError(f"Symbol not loaded: {symbol!r}")
        if indicator_key not in self._indicators[symbol]:
            raise DataProviderError(
                f"Indicator {indicator_key!r} not found for {symbol!r}"
            )
        return self._indicators[symbol][indicator_key]

    def has_sufficient_history(self, symbol: str, timeframe, min_bars: int = MIN_BARS_REQUIRED) -> bool:
        """Return True if the bars series has at least min_bars candles.

        Strategies should call this before reading indicator values to
        avoid index-out-of-range errors on freshly-loaded symbols.

        Args:
            symbol: Instrument name.
            timeframe: cTrader TimeFrame value.
            min_bars: Minimum number of closed bars required.

        Returns:
            True if enough history is available, False otherwise.
        """
        try:
            bars = self.get_bars(symbol, timeframe)
            if bars is None:
                return False
            return int(bars.Count) >= min_bars
        except BaseException:
            return False

    @property
    def symbols(self) -> list[str]:
        """Return the list of tracked symbol names."""
        return list(self._symbols)

    @property
    def primary_timeframe(self):
        """Return the primary (signal) timeframe, set during initialize()."""
        return self._primary_timeframe

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log_available_symbols(self) -> None:
        """One-time diagnostic: what does api.Symbols actually contain?

        cTrader's Symbols collection enumerates symbol NAMES (strings);
        GetSymbol(name) returns null for unknown names, so knowing the
        exact names on this account is the fastest way to debug lookups.
        """
        try:
            count = int(self._api.Symbols.Count)
            wanted = [n for n in self._symbols]
            matches: list[str] = []
            for i in range(count):
                try:
                    name = str(self._api.Symbols[i])
                except BaseException:
                    break
                if any(w[:3] in name or w[3:] in name for w in wanted):
                    matches.append(name)
            self._logger.info(
                f"api.Symbols: count={count}, "
                f"names matching basket currencies: {matches[:30]}")
        except BaseException as exc:
            self._logger.warning(
                f"Symbol enumeration failed: {type(exc).__name__}: {str(exc)[:120]}")

    def _load_symbol(self, symbol_name: str, timeframes: list) -> None:
        """Load one symbol: Symbol object, bars for all timeframes, and indicators."""
        try:
            # Symbol lookup attempts, in order. Each failure is logged with
            # the underlying exception so Cloud-only API quirks are visible
            # in the journal instead of a bare DataProviderError downstream.
            def _chart_symbol():
                chart_sym = self._api.Symbol
                if chart_sym is not None and str(chart_sym.Name) == symbol_name:
                    return chart_sym
                return None

            attempts = [
                ("Symbols.GetSymbol", lambda: self._api.Symbols.GetSymbol(symbol_name)),
                ("Symbols[]", lambda: self._api.Symbols[symbol_name]),
                ("Symbols.get_Item", lambda: self._api.Symbols.get_Item(symbol_name)),
                ("api.Symbol", _chart_symbol),
            ]
            sym = None
            for attempt_name, fn in attempts:
                try:
                    sym = fn()
                    if sym is None:
                        self._logger.warning(
                            f"{attempt_name}({symbol_name!r}) returned null")
                except BaseException as exc:
                    self._logger.warning(
                        f"{attempt_name}({symbol_name!r}) failed: "
                        f"{type(exc).__name__}: {str(exc)[:120]}")
                    sym = None
                if sym is not None:
                    break
            if sym is None:
                raise RuntimeError(f"Could not obtain Symbol object for {symbol_name!r}")
            self._symbol_objects[symbol_name] = sym
        except BaseException as exc:
            self._logger.error(
                f"Failed to load symbol {symbol_name!r}: {type(exc).__name__}"
            )
            return

        self._bars[symbol_name] = {}
        primary_bars = None
        for tf in timeframes:
            try:
                # Try API calls in order:
                #   1. MarketData.GetBars(tf, name) — documented signature is
                #      (TimeFrame, symbolName); the reversed order below never
                #      worked and caused the historic H4/multi-symbol failures
                #   2. MarketData.GetBars(name, tf) — legacy reversed fallback
                #   3. api.Bars — chart bars (only valid for chart symbol + chart TF)
                bars = None
                bar_attempts = [
                    ("GetBars(tf,name)", lambda t=tf: self._api.MarketData.GetBars(t, symbol_name)),
                    ("GetBars(name,tf)", lambda t=tf: self._api.MarketData.GetBars(symbol_name, t)),
                ]
                if tf == self._primary_timeframe:
                    def _chart_bars():
                        chart_bars = self._api.Bars
                        if str(self._api.Symbol.Name) == symbol_name:
                            return chart_bars
                        return None
                    bar_attempts.append(("api.Bars", _chart_bars))
                for attempt_name, fn in bar_attempts:
                    try:
                        bars = fn()
                    except BaseException as exc:
                        self._logger.warning(
                            f"{attempt_name} {symbol_name!r} tf={tf!r} failed: "
                            f"{type(exc).__name__}: {str(exc)[:120]}")
                        bars = None
                    if bars is not None:
                        break
                if bars is None:
                    raise RuntimeError("No bars source available")
                self._bars[symbol_name][tf] = bars
                if tf == self._primary_timeframe and primary_bars is None:
                    primary_bars = bars
            except BaseException as exc:
                self._logger.error(
                    f"Failed to load bars for {symbol_name!r} tf={tf!r}: {type(exc).__name__}"
                )

        self._indicators[symbol_name] = {}
        if primary_bars is not None:
            self._init_indicators(symbol_name, primary_bars)
        else:
            self._logger.warning(
                f"No primary timeframe bars for {symbol_name!r} — indicators skipped"
            )

        self._logger.debug(f"Symbol loaded: {symbol_name}")

    def _init_indicators(self, symbol_name: str, bars) -> None:
        """Initialize all 10 indicator instances for one symbol.

        All indicators are attached to the primary timeframe bars so they
        update automatically when new bars close.

        Indicator keys and cTrader API method names:
            ema_fast       ExponentialMovingAverage  — close prices, fast EMA period
            ema_slow       ExponentialMovingAverage  — close prices, slow EMA period
            adx            DirectionalMovementSystem — bars, ADX period
            atr            AverageTrueRange          — bars, shared ATR period (14)
            bollinger      BollingerBands            — close prices, period, std devs
            rsi            RelativeStrengthIndex     — close prices, RSI period
            adx_filter     DirectionalMovementSystem — bars, ADX filter period
            donchian_high  HighestHigh               — high prices, Donchian period
            donchian_low   LowestLow                 — low prices, Donchian period
            atr_bo         AverageTrueRange          — bars, breakout ATR period

        Args:
            symbol_name: Instrument name (used for logging and storage).
            bars: Primary timeframe Bars object to attach indicators to.
        """
        p = self._indicator_params
        ind = self._indicators[symbol_name]

        def _get(key, default):
            return p.get(key, default)

        def _safe_msg(exc):
            try:
                return str(exc)
            except BaseException:
                return "(no message)"

        def _init(key, fn):
            try:
                ind[key] = fn()
            except BaseException as exc:
                self._logger.error(
                    f"Indicator '{key}' failed for {symbol_name!r}: "
                    f"{type(exc).__name__}: {_safe_msg(exc)}"
                )

        _init("ema_fast", lambda: self._api.Indicators.ExponentialMovingAverage(
            bars.ClosePrices, int(_get("ema_fast_period", Defaults.TF_FAST_EMA_PERIOD))))
        _init("ema_slow", lambda: self._api.Indicators.ExponentialMovingAverage(
            bars.ClosePrices, int(_get("ema_slow_period", Defaults.TF_SLOW_EMA_PERIOD))))
        _init("adx", lambda: self._api.Indicators.DirectionalMovementSystem(
            bars, int(_get("adx_period", Defaults.TF_ADX_PERIOD))))

        # atr: try cTrader built-in first; fall back to manual wrapper if it throws
        _atr_period = int(_get("adx_period", Defaults.TF_ADX_PERIOD))
        try:
            ind["atr"] = self._api.Indicators.AverageTrueRange(
                bars.HighPrices, bars.LowPrices, bars.ClosePrices, _atr_period)
        except BaseException:
            ind["atr"] = _ManualATR(bars, _atr_period)

        _init("bollinger", lambda: self._api.Indicators.BollingerBands(
            bars.ClosePrices,
            int(_get("bollinger_period", Defaults.MR_BOLLINGER_PERIOD)),
            float(_get("bollinger_deviation", Defaults.MR_BOLLINGER_DEVIATION)),
            _MA_SIMPLE))
        _init("rsi", lambda: self._api.Indicators.RelativeStrengthIndex(
            bars.ClosePrices, int(_get("rsi_period", Defaults.MR_RSI_PERIOD))))
        _init("adx_filter", lambda: self._api.Indicators.DirectionalMovementSystem(
            bars, int(_get("adx_filter_period", Defaults.MR_ADX_FILTER_PERIOD))))

        # donchian_high / donchian_low: try cTrader built-ins; fall back to manual wrappers
        _donchian_p = int(_get("donchian_period", Defaults.BO_DONCHIAN_PERIOD))
        try:
            ind["donchian_high"] = self._api.Indicators.Maximum(bars.HighPrices, _donchian_p)
        except BaseException:
            ind["donchian_high"] = _RollingExtreme(bars.HighPrices, _donchian_p, max)
        try:
            ind["donchian_low"] = self._api.Indicators.Minimum(bars.LowPrices, _donchian_p)
        except BaseException:
            ind["donchian_low"] = _RollingExtreme(bars.LowPrices, _donchian_p, min)

        # atr_bo: same try/fallback approach
        _atr_bo_period = int(_get("atr_bo_period", Defaults.BO_ATR_PERIOD))
        try:
            ind["atr_bo"] = self._api.Indicators.AverageTrueRange(
                bars.HighPrices, bars.LowPrices, bars.ClosePrices, _atr_bo_period)
        except BaseException:
            ind["atr_bo"] = _ManualATR(bars, _atr_bo_period)

        self._logger.info(
            f"Indicators initialized for {symbol_name} ({len(ind)}/10)"
        )

    def __repr__(self) -> str:
        return (
            f"DataProvider("
            f"symbols={self._symbols}, "
            f"loaded={len(self._symbol_objects)})"
        )
