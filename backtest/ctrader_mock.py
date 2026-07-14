"""
ctrader_mock.py
Mocks of cTrader's API surface that strategies/*.py depends on.

The strategies access:
  - bars.ClosePrices.Last(n) / OpenPrices / HighPrices / LowPrices / OpenTimes
  - bars.Count
  - indicator.Result.Last(n)         (EMA, ATR, RSI, Donchian via _RollingExtreme)
  - indicator.ADX.Last(n)            (DirectionalMovementSystem)
  - indicator.Top/Main/Bottom.Last(n) (BollingerBands)
  - sym_obj.PipSize / PipValue / VolumeInUnitsMin/Max/Step / Bid / Ask / Name
  - data_provider.get_bars / get_indicator / get_symbol / has_sufficient_history
  - data_provider.primary_timeframe / is_spread_acceptable / get_spread_pips

Each mock keeps a reference to a shared "current bar index" so .Last(n) returns
the value that would have been visible at on_bar_closed for that bar.
"""

from __future__ import annotations

import math
from typing import Sequence


class _Cursor:
    """Mutable index pointer shared between bars and indicators.

    The engine advances this after evaluate() to expose the next bar.
    """
    def __init__(self) -> None:
        self.idx: int = -1


class _SeriesView:
    """Mimics cTrader DataSeries: .Last(n) returns value n bars before current."""
    def __init__(self, arr: Sequence[float], cursor: _Cursor) -> None:
        self._arr = arr
        self._cursor = cursor

    def Last(self, n: int) -> float:
        i = self._cursor.idx - n
        if 0 <= i < len(self._arr):
            v = self._arr[i]
            return v if not (isinstance(v, float) and math.isnan(v)) else float("nan")
        return float("nan")

    @property
    def Count(self) -> int:
        # cursor.idx is the most recent visible bar index; Count = idx + 1
        return max(0, self._cursor.idx + 1)

    def __getitem__(self, i: int) -> float:
        if 0 <= i < len(self._arr):
            return self._arr[i]
        return float("nan")


class _OpenTimes:
    def __init__(self, times, cursor: _Cursor) -> None:
        self._times = times
        self._cursor = cursor

    @property
    def LastValue(self):
        return self._times[self._cursor.idx]

    def Last(self, n: int):
        return self._times[self._cursor.idx - n]


class MockBars:
    """Mimics cTrader Bars object."""
    def __init__(self, opens, highs, lows, closes, times, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.OpenPrices = _SeriesView(opens, cursor)
        self.HighPrices = _SeriesView(highs, cursor)
        self.LowPrices = _SeriesView(lows, cursor)
        self.ClosePrices = _SeriesView(closes, cursor)
        self.OpenTimes = _OpenTimes(times, cursor)

    @property
    def Count(self) -> int:
        return max(0, self._cursor.idx + 1)


class MockIndicator:
    """Generic indicator mock — exposes named series as _SeriesView attributes."""
    def __init__(self, series_dict: dict, cursor: _Cursor) -> None:
        for name, arr in series_dict.items():
            setattr(self, name, _SeriesView(arr, cursor))


class MockSymbol:
    """Mimics cTrader Symbol — values fixed for EURUSD on IC Markets Raw Spread."""
    def __init__(self, name: str = "EURUSD") -> None:
        self.Name = name
        self.PipSize = 0.0001
        self.PipValue = 0.0001  # value of 1 pip per 1 unit of base currency
        self.VolumeInUnitsMin = 1000
        self.VolumeInUnitsMax = 100_000_000
        self.VolumeInUnitsStep = 1000
        self.MinStopLossInPips = 0.0
        self.Bid = 1.0
        self.Ask = 1.0


class MockDataProvider:
    """Mimics core.data_provider.DataProvider for one symbol on one timeframe."""

    def __init__(self, symbol: str, bars: MockBars, indicators: dict,
                 cursor: _Cursor, primary_tf: str = "H1",
                 spread_pips: float = 1.0) -> None:
        self._symbol = symbol
        self._bars = bars
        self._indicators = indicators
        self._symbol_obj = MockSymbol(symbol)
        self._cursor = cursor
        self.primary_timeframe = primary_tf
        self._spread_pips = spread_pips
        self.symbols = [symbol]

    def get_bars(self, symbol: str, timeframe):
        return self._bars

    def get_symbol(self, symbol: str):
        return self._symbol_obj

    def get_indicator(self, symbol: str, key: str):
        return self._indicators[key]

    def has_sufficient_history(self, symbol: str, timeframe, min_bars: int) -> bool:
        return self._bars.Count >= min_bars

    def get_spread_pips(self, symbol: str) -> float:
        return self._spread_pips

    def is_spread_acceptable(self, symbol: str, max_spread_pips: float) -> bool:
        return self._spread_pips <= max_spread_pips


class _NullLogger:
    """No-op logger so strategies can run without printing during a backtest."""
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def trade_entry(self, *a, **kw): pass
    def trade_exit(self, *a, **kw): pass
    def risk_action(self, *a, **kw): pass
    def daily_summary(self, *a, **kw): pass


def make_null_logger():
    return _NullLogger()
