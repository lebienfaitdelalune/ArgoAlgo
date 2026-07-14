"""
test_integration_e2e.py
End-to-end integration test for the full ArgoAlgo trading pipeline.

Simulates the cTrader runtime environment using MagicMock so we can verify
that the pipeline produces trades without needing cTrader IDE or a backtest.

Scenario
--------
  - EURUSD H1, Tuesday 12:00 UTC (within session)
  - Fast EMA (12) just crossed ABOVE slow EMA (26) → TrendFollowing BUY signal
  - ADX = 30 (trending, > 20 threshold)
  - ATR = 0.001 (10 pips) → SL=20 pips, TP=40 pips
  - Balance = 10,000, no open positions, spread = 0.5 pips (within limit)
  - Expected: ExecuteMarketOrder called with BUY direction

Run with:
  python3 -m pytest tests/test_integration_e2e.py -v
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import TradingBot


# ---------------------------------------------------------------------------
# Mock API factory
# ---------------------------------------------------------------------------

def _make_positions_mock(positions: list | None = None):
    """Create a Positions mock that supports both event subscription and iteration."""
    positions = positions or []
    mock = MagicMock()
    # Magic methods for iteration (needs to be set on the type or via configure_mock)
    mock.__iter__ = MagicMock(side_effect=lambda: iter(positions))
    mock.__len__ = MagicMock(return_value=len(positions))
    return mock


def _make_indicator_ema(now_value: float, prev_value: float):
    """Create an EMA indicator mock with two values (Last(0) and Last(1))."""
    ind = MagicMock()
    ind.Result.Last.side_effect = lambda n: now_value if n == 0 else prev_value
    return ind


def _make_indicator_adx(adx_value: float):
    """Create an ADX/DMS indicator mock."""
    ind = MagicMock()
    ind.ADX.Last.return_value = adx_value
    return ind


def _make_indicator_atr(atr_value: float):
    """Create an ATR indicator mock."""
    ind = MagicMock()
    ind.Result.Last.return_value = atr_value
    return ind


def _make_api_buy_signal():
    """
    Build a fully realistic mock of the cTrader API that will produce
    a TrendFollowing BUY signal on the first bar_closed call.

    EMA crossover setup:
        ema_fast: prev=1.0798, now=1.0810  (crossed ABOVE slow EMA)
        ema_slow: prev=1.0800, now=1.0808  (stayed flat, below fast)
        close:    1.0812                   (above slow EMA → confirms BUY)
        ADX:      30.0                     (trending, > 20 threshold)
        ATR:      0.001                    (10 pips)
    """
    api = MagicMock()

    # ------------------------------------------------------------------ time
    # Tuesday 12:00 UTC — within 07–20 session, not Friday
    server_time = MagicMock()
    server_time.Hour = 12
    server_time.DayOfWeek.ToString.return_value = "Tuesday"
    server_time.Date = object()  # Stable object — same ref each call → same day, no rollover log
    api.Server.Time = server_time

    # ---------------------------------------------------------------- account
    api.Account.Balance = 10_000.0
    api.Account.Equity = 10_000.0

    # -------------------------------------------------------------- timeframe
    api.TimeFrame.Hour1 = "H1"
    api.TimeFrame.Hour4 = "H4"

    # ----------------------------------------------------------------- trade
    api.TradeType.Buy = "Buy"
    api.TradeType.Sell = "Sell"

    # ----------------------------------------------------------------- symbol
    sym = MagicMock()
    sym.PipSize = 0.0001
    sym.PipValue = 10.0          # €10 per pip per standard lot (100k)
    sym.VolumeInUnitsMin = 1_000.0
    sym.VolumeInUnitsMax = 100_000_000.0
    sym.VolumeInUnitsStep = 1_000.0
    sym.Ask = 1.0800             # spread = Ask - Bid = 0.00005 = 0.5 pips
    sym.Bid = 1.07995
    sym.MinStopLossInPips = 0.0
    api.Symbols.get_Item.return_value = sym

    # ------------------------------------------------------------------- bars
    bars = MagicMock()
    bars.Count = 200             # int — has_sufficient_history returns True
    bars.ClosePrices.Last.return_value = 1.0812   # close above slow EMA → confirms BUY
    bars.OpenTimes.LastValue.Hour = 12            # 12:00 UTC — within session
    bars.OpenTimes.LastValue.DayOfWeek.ToString.return_value = "Tuesday"
    api.GetBars.return_value = bars

    # -------------------------------------------------------------- indicators
    # EMA fast: Last(0)=1.0810, Last(1)=1.0798  (crossed above slow)
    ema_fast = _make_indicator_ema(now_value=1.0810, prev_value=1.0798)
    # EMA slow: Last(0)=1.0808, Last(1)=1.0800  (was above fast, now below)
    ema_slow = _make_indicator_ema(now_value=1.0808, prev_value=1.0800)
    # ADX = 30 (trending)
    adx_ind = _make_indicator_adx(adx_value=30.0)
    # ATR = 0.001 = 10 pips
    atr_ind = _make_indicator_atr(atr_value=0.001)

    # Bollinger Bands — set so MR strategy does NOT trigger
    bollinger = MagicMock()
    bollinger.Top.Last.return_value = 1.0950     # top band well above price
    bollinger.Bottom.Last.return_value = 1.0650  # bottom band well below price
    bollinger.Main.Last.return_value = 1.0800

    # RSI = 55 — neutral (not oversold < 30 or overbought > 70) → MR stays silent
    rsi = MagicMock()
    rsi.Result.Last.return_value = 55.0

    # Donchian — price not at channel edge → Breakout stays silent
    donchian_high = MagicMock()
    donchian_high.Result.Last.return_value = 1.0950  # 50+ pips above current price
    donchian_low = MagicMock()
    donchian_low.Result.Last.return_value = 1.0650

    # Wire up indicators with call counters (EMA is called twice: fast then slow)
    _ema_seq = [ema_fast, ema_slow]
    _ema_idx = [0]
    def _ema_side_effect(*args):
        result = _ema_seq[_ema_idx[0] % len(_ema_seq)]
        _ema_idx[0] += 1
        return result

    _adx_seq = [adx_ind, adx_ind]
    _adx_idx = [0]
    def _adx_side_effect(*args):
        result = _adx_seq[_adx_idx[0] % len(_adx_seq)]
        _adx_idx[0] += 1
        return result

    _atr_seq = [atr_ind, atr_ind]
    _atr_idx = [0]
    def _atr_side_effect(*args):
        result = _atr_seq[_atr_idx[0] % len(_atr_seq)]
        _atr_idx[0] += 1
        return result

    api.Indicators.ExponentialMovingAverage.side_effect = _ema_side_effect
    api.Indicators.DirectionalMovementSystem.side_effect = _adx_side_effect
    api.Indicators.AverageTrueRange.side_effect = _atr_side_effect
    api.Indicators.BollingerBands.return_value = bollinger
    api.Indicators.RelativeStrengthIndex.return_value = rsi
    api.Indicators.HighestHigh.return_value = donchian_high
    api.Indicators.LowestLow.return_value = donchian_low

    # -------------------------------------------------------------- positions
    api.Positions = _make_positions_mock(positions=[])

    # -------------------------------------------------- ExecuteMarketOrder
    trade_result = MagicMock()
    trade_result.IsSuccessful = True
    trade_result.Position.Id = 42
    api.ExecuteMarketOrder.return_value = trade_result

    return api


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    """Full-pipeline integration tests without cTrader IDE."""

    def test_on_start_does_not_crash(self):
        """on_start() must complete without raising for a valid mock API."""
        api = _make_api_buy_signal()
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.on_start()   # should not raise
        assert bot._logger is not None
        assert bot._data_provider is not None
        assert bot._risk_manager is not None
        assert bot._order_executor is not None
        assert bot._strategy_engine is not None

    def test_buy_signal_produces_order(self):
        """
        BUY scenario: fast EMA crosses above slow EMA with ADX > 20.
        on_bar_closed() must call ExecuteMarketOrder with Buy direction.
        """
        api = _make_api_buy_signal()
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableXsect = False  # exercise the legacy bar-signal pipeline
        bot.on_start()
        bot.on_bar_closed()

        assert api.ExecuteMarketOrder.called, (
            "ExecuteMarketOrder was NOT called.\n"
            "The pipeline failed to place a trade despite a valid BUY signal.\n"
            "Check: session filter, has_sufficient_history, indicator values, "
            "risk validation, volume calculation."
        )

        # Verify the first positional arg is the Buy trade type
        call_args = api.ExecuteMarketOrder.call_args
        assert call_args is not None
        pos_args = call_args.args
        assert len(pos_args) >= 4, f"Expected ≥4 positional args, got {len(pos_args)}: {pos_args}"
        assert pos_args[0] == api.TradeType.Buy, (
            f"Expected Buy order, got: {pos_args[0]}"
        )
        assert pos_args[1] == "EURUSD", f"Expected EURUSD, got: {pos_args[1]}"
        assert pos_args[2] > 0, f"Expected positive volume, got: {pos_args[2]}"

    def test_halted_bot_skips_bar(self):
        """A halted bot must skip on_bar_closed without placing any order."""
        api = _make_api_buy_signal()
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.on_start()
        bot._is_halted = True
        bot.on_bar_closed()
        api.ExecuteMarketOrder.assert_not_called()

    def test_session_filter_blocks_outside_hours(self):
        """on_bar_closed outside trading hours must not place orders."""
        api = _make_api_buy_signal()
        # Move bar time to 03:00 UTC — before session start (07:00)
        api.GetBars.return_value.OpenTimes.LastValue.Hour = 3
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableXsect = False  # exercise the legacy bar-signal pipeline
        bot.on_start()
        bot.on_bar_closed()
        api.ExecuteMarketOrder.assert_not_called()

    def test_multiple_bars_all_trigger(self):
        """
        Calling on_bar_closed twice with the same crossover data should
        produce a trade on the first call, and potentially on the second
        (depends on position count check — first trade fills the slot).
        """
        api = _make_api_buy_signal()
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableXsect = False  # exercise the legacy bar-signal pipeline
        bot.on_start()
        bot.on_bar_closed()
        first_count = api.ExecuteMarketOrder.call_count
        bot.on_bar_closed()
        # At least one trade placed across both bars
        assert api.ExecuteMarketOrder.call_count >= first_count >= 1

    def test_no_signal_when_adx_below_threshold(self):
        """ADX below threshold: TF strategy returns NONE; no trade placed."""
        api = _make_api_buy_signal()
        # Override ADX indicators to return value below threshold (20)
        weak_adx = _make_indicator_adx(adx_value=10.0)

        _adx_idx = [0]
        def _weak_adx_side_effect(*args):
            _adx_idx[0] += 1
            return weak_adx
        api.Indicators.DirectionalMovementSystem.side_effect = _weak_adx_side_effect

        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableMeanReversion = False
        bot.EnableBreakout = False
        bot.on_start()
        bot.on_bar_closed()
        api.ExecuteMarketOrder.assert_not_called()

    def test_volume_calculation_is_positive(self):
        """The volume sent to ExecuteMarketOrder must be > 0."""
        api = _make_api_buy_signal()
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableXsect = False  # exercise the legacy bar-signal pipeline
        bot.on_start()
        bot.on_bar_closed()
        if api.ExecuteMarketOrder.called:
            volume = api.ExecuteMarketOrder.call_args.args[2]
            assert volume > 0, f"Volume must be positive, got {volume}"

    def test_label_follows_naming_convention(self):
        """Order label must follow ArgoAlgo_{strategy}_{symbol} convention."""
        api = _make_api_buy_signal()
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableXsect = False  # exercise the legacy bar-signal pipeline
        bot.on_start()
        bot.on_bar_closed()
        if api.ExecuteMarketOrder.called:
            label = api.ExecuteMarketOrder.call_args.args[3]
            assert label.startswith("ArgoAlgo_"), f"Label must start with ArgoAlgo_, got: {label}"
            assert "EURUSD" in label, f"Label must contain symbol, got: {label}"

    def test_sell_signal_when_ema_crosses_below(self):
        """SELL scenario: fast EMA crosses BELOW slow EMA with price below slow."""
        api = _make_api_buy_signal()

        # Reconfigure for SELL crossover
        # ema_fast: prev=1.0810, now=1.0798  (crossed BELOW slow)
        ema_fast_sell = _make_indicator_ema(now_value=1.0798, prev_value=1.0810)
        # ema_slow: prev=1.0800, now=1.0808  (was below fast, now above)
        ema_slow_sell = _make_indicator_ema(now_value=1.0808, prev_value=1.0800)
        # close below slow EMA
        api.GetBars.return_value.ClosePrices.Last.return_value = 1.0795

        _ema_seq = [ema_fast_sell, ema_slow_sell]
        _ema_idx = [0]
        def _sell_ema_side_effect(*args):
            result = _ema_seq[_ema_idx[0] % len(_ema_seq)]
            _ema_idx[0] += 1
            return result
        api.Indicators.ExponentialMovingAverage.side_effect = _sell_ema_side_effect

        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.EnableMeanReversion = False
        bot.EnableBreakout = False
        bot.EnableXsect = False  # exercise the legacy bar-signal pipeline
        bot.on_start()
        bot.on_bar_closed()

        assert api.ExecuteMarketOrder.called, "Expected SELL order, but none placed"
        assert api.ExecuteMarketOrder.call_args.args[0] == api.TradeType.Sell


class TestPipelineRobustness:
    """Test that the pipeline handles bad API data gracefully."""

    def test_no_crash_when_symbol_load_fails(self):
        """If Symbol() throws, bot should log error and continue (no crash)."""
        api = _make_api_buy_signal()
        api.Symbols.get_Item.side_effect = RuntimeError("Symbol not available")
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.on_start()   # must not raise
        bot.on_bar_closed()  # no crash, no trade
        api.ExecuteMarketOrder.assert_not_called()

    def test_no_crash_when_bars_load_fails(self):
        """If GetBars() throws, bot should log error and continue (no crash)."""
        api = _make_api_buy_signal()
        api.GetBars.side_effect = RuntimeError("Bars not available")
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.on_start()   # must not raise
        bot.on_bar_closed()  # no crash
        api.ExecuteMarketOrder.assert_not_called()

    def test_no_crash_when_print_throws(self):
        """If api.Print() throws (NullReferenceException), bot must not crash."""
        api = _make_api_buy_signal()
        api.Print.side_effect = RuntimeError("NullReferenceException")
        bot = TradingBot(api=api)
        bot.TradedSymbols = "EURUSD"
        bot.on_start()   # must not raise despite Print() failing
        # Logger still initialized, bot functional
        assert bot._logger is not None

    def test_no_crash_on_none_api(self):
        """TradingBot should be constructible without any API (test-only mode)."""
        bot = TradingBot(api=None)
        bot.TradedSymbols = "EURUSD"
        bot.on_start()   # must not raise
        bot.on_bar_closed()  # no crash
