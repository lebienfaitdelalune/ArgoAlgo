"""
test_phase5.py
Phase 5 tests: TrendFollowingStrategy, MeanReversionStrategy, BreakoutStrategy,
and StrategyEngine (ADX switching, check_exits).
No cTrader API required — uses mock objects.
"""

from __future__ import annotations

import math
import sys
import os
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import Direction, LogLevel, StrategyMode
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakout import BreakoutStrategy
from core.strategy_engine import StrategyEngine


PRIMARY_TF = "M15"
NAN = float("nan")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_logger():
    return MagicMock()


def _make_dp(
    n_bars=200,
    pip_size=0.0001,
    # TF indicators
    ema_fast_now=1.0830, ema_fast_prev=1.0820,
    ema_slow_now=1.0825, ema_slow_prev=1.0828,
    adx_value=30.0,
    atr_value=0.002,
    # MR indicators
    upper_band=1.0900, lower_band=1.0700, middle_band=1.0800,
    rsi_value=25.0, adx_filter_value=15.0,
    # BO indicators
    donchian_high_prev=1.0850, donchian_low_prev=1.0750,
    donchian_high_now=1.0860, donchian_low_now=1.0740,
    atr_bo_value=0.002,
    close_price=1.0860,
) -> MagicMock:
    """Build a fully-configured DataProvider mock."""
    dp = MagicMock()
    dp.primary_timeframe = PRIMARY_TF
    dp.has_sufficient_history.return_value = True

    # Symbol
    sym = MagicMock()
    sym.PipSize = pip_size
    dp.get_symbol.return_value = sym

    # Bars
    bars = MagicMock()
    bars.ClosePrices.Last.return_value = close_price
    bars.__len__ = MagicMock(return_value=n_bars)
    dp.get_bars.return_value = bars

    # Build indicator factory
    def make_ind(**values):
        ind = MagicMock()
        for attr, val in values.items():
            parts = attr.split(".")
            obj = ind
            for p in parts[:-1]:
                obj = getattr(obj, p)
            mock_attr = getattr(obj, parts[-1])
            if callable(val) and not isinstance(val, MagicMock):
                mock_attr.side_effect = val
            else:
                mock_attr.return_value = val
        return ind

    def get_indicator(symbol, key):
        mapping = {
            "ema_fast":      make_ind(**{"Result.Last": _seq(ema_fast_now, ema_fast_prev)}),
            "ema_slow":      make_ind(**{"Result.Last": _seq(ema_slow_now, ema_slow_prev)}),
            "adx":           _make_adx(adx_value),
            "atr":           make_ind(**{"Result.Last": _const(atr_value)}),
            "bollinger":     _make_bb(upper_band, lower_band, middle_band),
            "rsi":           make_ind(**{"Result.Last": _const(rsi_value)}),
            "adx_filter":    _make_adx(adx_filter_value),
            "donchian_high": _make_donchian(donchian_high_now, donchian_high_prev),
            "donchian_low":  _make_donchian(donchian_low_now, donchian_low_prev),
            "atr_bo":        make_ind(**{"Result.Last": _const(atr_bo_value)}),
        }
        return mapping[key]

    dp.get_indicator.side_effect = get_indicator
    return dp


def _seq(now, prev):
    """Return a callable that returns *now* for Last(0) and *prev* for Last(1)."""
    def _last(n):
        return now if n == 0 else prev
    return _last


def _const(val):
    """Return a callable that always returns *val*."""
    return lambda n: val


def _make_adx(value):
    ind = MagicMock()
    ind.ADX.Last.return_value = value
    return ind


def _make_bb(upper, lower, middle):
    ind = MagicMock()
    ind.Top.Last.return_value = upper
    ind.Bottom.Last.return_value = lower
    ind.Main.Last.return_value = middle
    return ind


def _make_donchian(now_val, prev_val):
    ind = MagicMock()
    def _last(n):
        return now_val if n == 0 else prev_val
    ind.Result.Last.side_effect = _last
    return ind


def _make_strategy(cls, params=None, dp=None, logger=None):
    return cls(
        api=MagicMock(),
        data_provider=dp or _make_dp(),
        logger=logger or _mock_logger(),
        params=params or {},
    )


def _make_position(symbol="EURUSD", trade_type="Buy", label="ArgoAlgo_TR_EURUSD"):
    pos = MagicMock()
    pos.SymbolName = symbol
    pos.TradeType = trade_type
    pos.Label = label
    return pos


# ---------------------------------------------------------------------------
# TrendFollowingStrategy
# ---------------------------------------------------------------------------

class TestTrendFollowing:
    def _strat(self, **dp_kwargs):
        return _make_strategy(TrendFollowingStrategy, dp=_make_dp(**dp_kwargs))

    def test_buy_on_crossover_up(self):
        # fast crosses above slow; ADX = 30 > 25 threshold; close > slow
        s = _make_strategy(TrendFollowingStrategy, dp=_make_dp(
            ema_fast_now=1.0830, ema_fast_prev=1.0820,
            ema_slow_now=1.0825, ema_slow_prev=1.0828,  # prev: fast < slow; now: fast > slow
            adx_value=30.0,
            close_price=1.0830,  # above slow
        ))
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.BUY
        assert sig.symbol == "EURUSD"
        assert sig.strategy_name == "TrendFollowing"

    def test_sell_on_crossover_down(self):
        s = _make_strategy(TrendFollowingStrategy, dp=_make_dp(
            ema_fast_now=1.0820, ema_fast_prev=1.0830,
            ema_slow_now=1.0825, ema_slow_prev=1.0822,  # prev: fast > slow; now: fast < slow
            adx_value=30.0,
            close_price=1.0820,  # below slow
        ))
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.SELL

    def test_none_when_adx_below_threshold(self):
        s = _make_strategy(TrendFollowingStrategy,
                           params={"adx_threshold": 25.0},
                           dp=_make_dp(adx_value=20.0))
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.NONE

    def test_none_when_no_crossover(self):
        # fast always above slow (no crossover event)
        s = _make_strategy(TrendFollowingStrategy, dp=_make_dp(
            ema_fast_now=1.0840, ema_fast_prev=1.0835,
            ema_slow_now=1.0825, ema_slow_prev=1.0820,
            adx_value=30.0,
        ))
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_insufficient_history(self):
        dp = _make_dp()
        dp.has_sufficient_history.return_value = False
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_adx_is_nan(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = lambda sym, key: (
            _make_adx(NAN) if key == "adx" else MagicMock()
        )
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_signal_has_sl_and_tp(self):
        s = _make_strategy(TrendFollowingStrategy, dp=_make_dp(
            ema_fast_now=1.0830, ema_fast_prev=1.0820,
            ema_slow_now=1.0825, ema_slow_prev=1.0828,
            adx_value=30.0, atr_value=0.002,
            close_price=1.0830,
        ), params={"sl_atr_multiplier": 2.0, "tp_rr": 2.0})
        sig = s.evaluate("EURUSD")
        # atr=0.002, pip_size=0.0001 → sl = 0.002/0.0001*2 = 40 pips; tp = 80 pips
        assert sig.stop_loss_pips == pytest.approx(40.0)
        assert sig.take_profit_pips == pytest.approx(80.0)

    def test_signal_metadata_contains_adx(self):
        s = _make_strategy(TrendFollowingStrategy, dp=_make_dp(
            ema_fast_now=1.0830, ema_fast_prev=1.0820,
            ema_slow_now=1.0825, ema_slow_prev=1.0828,
            adx_value=30.0, close_price=1.0830,
        ))
        sig = s.evaluate("EURUSD")
        assert "adx" in sig.metadata
        assert sig.metadata["adx"] == pytest.approx(30.0)

    def test_data_error_returns_none_signal(self):
        dp = _make_dp()
        dp.get_bars.side_effect = RuntimeError("no bars")
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    # should_close
    def test_should_close_buy_when_fast_below_slow(self):
        dp = _make_dp(ema_fast_now=1.0820, ema_slow_now=1.0825)
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        pos = _make_position(trade_type="Buy")
        assert s.should_close(pos) is True

    def test_should_not_close_buy_when_fast_above_slow(self):
        dp = _make_dp(ema_fast_now=1.0830, ema_slow_now=1.0825)
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        pos = _make_position(trade_type="Buy")
        assert s.should_close(pos) is False

    def test_should_close_sell_when_fast_above_slow(self):
        dp = _make_dp(ema_fast_now=1.0830, ema_slow_now=1.0825)
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        pos = _make_position(trade_type="Sell")
        assert s.should_close(pos) is True

    def test_should_close_returns_false_on_error(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = RuntimeError("no indicator")
        s = _make_strategy(TrendFollowingStrategy, dp=dp)
        assert s.should_close(_make_position()) is False


# ---------------------------------------------------------------------------
# MeanReversionStrategy
# ---------------------------------------------------------------------------

class TestMeanReversion:
    def test_buy_when_price_below_lower_band_rsi_oversold(self):
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0695,    # below lower_band=1.0700
            lower_band=1.0700,
            upper_band=1.0900,
            middle_band=1.0800,
            rsi_value=25.0,        # < rsi_oversold=30
            adx_filter_value=15.0, # < adx_filter_threshold=25
        ), params={"rsi_oversold": 30.0, "rsi_overbought": 70.0, "adx_filter_threshold": 25.0})
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.BUY

    def test_sell_when_price_above_upper_band_rsi_overbought(self):
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0910,
            upper_band=1.0900,
            lower_band=1.0700,
            middle_band=1.0800,
            rsi_value=75.0,
            adx_filter_value=15.0,
        ), params={"rsi_oversold": 30.0, "rsi_overbought": 70.0, "adx_filter_threshold": 25.0})
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.SELL

    def test_none_when_adx_too_high(self):
        # Market is trending — MR should not fire
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0695,
            rsi_value=25.0,
            adx_filter_value=30.0,  # >= threshold of 25
        ), params={"adx_filter_threshold": 25.0})
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_rsi_not_extreme_on_buy(self):
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0695,
            lower_band=1.0700,
            rsi_value=40.0,  # not oversold
            adx_filter_value=15.0,
        ), params={"rsi_oversold": 30.0, "adx_filter_threshold": 25.0})
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_price_inside_bands(self):
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0800,  # inside bands
            upper_band=1.0900,
            lower_band=1.0700,
            rsi_value=25.0,
            adx_filter_value=15.0,
        ))
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_insufficient_history(self):
        dp = _make_dp()
        dp.has_sufficient_history.return_value = False
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_rsi_nan(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = lambda sym, key: (
            _make_bb(1.09, 1.07, 1.08) if key == "bollinger"
            else _make_adx(15.0) if key == "adx_filter"
            else MagicMock(Result=MagicMock(Last=MagicMock(return_value=NAN)))
        )
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_tp_targets_middle_band(self):
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0695,
            lower_band=1.0700,
            middle_band=1.0800,
            rsi_value=25.0,
            adx_filter_value=15.0,
            pip_size=0.0001,
        ), params={"rsi_oversold": 30.0, "adx_filter_threshold": 25.0})
        sig = s.evaluate("EURUSD")
        # tp = (middle_band - close) / pip_size = (1.0800 - 1.0695) / 0.0001 = 105 pips
        assert sig.take_profit_pips == pytest.approx(105.0, rel=0.01)

    def test_signal_metadata_contains_rsi(self):
        s = _make_strategy(MeanReversionStrategy, dp=_make_dp(
            close_price=1.0695, lower_band=1.0700, rsi_value=25.0, adx_filter_value=15.0,
        ), params={"rsi_oversold": 30.0, "adx_filter_threshold": 25.0})
        sig = s.evaluate("EURUSD")
        assert "rsi" in sig.metadata

    # should_close
    def test_should_close_buy_when_price_at_middle_band(self):
        dp = _make_dp(close_price=1.0800, middle_band=1.0800)
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        pos = _make_position(trade_type="Buy")
        assert s.should_close(pos) is True

    def test_should_close_buy_when_price_above_middle_band(self):
        dp = _make_dp(close_price=1.0810, middle_band=1.0800)
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        pos = _make_position(trade_type="Buy")
        assert s.should_close(pos) is True

    def test_should_not_close_buy_below_middle_band(self):
        dp = _make_dp(close_price=1.0750, middle_band=1.0800)
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        pos = _make_position(trade_type="Buy")
        assert s.should_close(pos) is False

    def test_should_close_sell_when_price_at_middle_band(self):
        dp = _make_dp(close_price=1.0800, middle_band=1.0800)
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        pos = _make_position(trade_type="Sell")
        assert s.should_close(pos) is True

    def test_should_close_returns_false_on_error(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = RuntimeError("no indicator")
        s = _make_strategy(MeanReversionStrategy, dp=dp)
        assert s.should_close(_make_position()) is False


# ---------------------------------------------------------------------------
# BreakoutStrategy
# ---------------------------------------------------------------------------

class TestBreakout:
    def test_buy_when_close_above_upper_channel(self):
        s = _make_strategy(BreakoutStrategy, dp=_make_dp(
            close_price=1.0860,
            donchian_high_prev=1.0850,  # previous bar's high
            donchian_low_prev=1.0750,
            atr_bo_value=0.002,         # above min threshold
        ), params={"atr_min_threshold": 0.0005, "sl_atr_multiplier": 1.5, "tp_rr": 2.0})
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.BUY

    def test_sell_when_close_below_lower_channel(self):
        s = _make_strategy(BreakoutStrategy, dp=_make_dp(
            close_price=1.0740,
            donchian_high_prev=1.0850,
            donchian_low_prev=1.0750,
            atr_bo_value=0.002,
        ), params={"atr_min_threshold": 0.0005})
        sig = s.evaluate("EURUSD")
        assert sig.direction == Direction.SELL

    def test_none_when_atr_below_min_threshold(self):
        s = _make_strategy(BreakoutStrategy, dp=_make_dp(
            close_price=1.0860,
            donchian_high_prev=1.0850,
            atr_bo_value=0.0001,   # very low volatility
        ), params={"atr_min_threshold": 0.0005})
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_price_inside_channel(self):
        s = _make_strategy(BreakoutStrategy, dp=_make_dp(
            close_price=1.0800,        # inside channel
            donchian_high_prev=1.0850,
            donchian_low_prev=1.0750,
            atr_bo_value=0.002,
        ), params={"atr_min_threshold": 0.0005})
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_insufficient_history(self):
        dp = _make_dp()
        dp.has_sufficient_history.return_value = False
        s = _make_strategy(BreakoutStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_none_when_atr_is_nan(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = lambda sym, key: (
            MagicMock(Result=MagicMock(Last=MagicMock(return_value=NAN)))
            if key == "atr_bo" else _make_donchian(1.0860, 1.0850)
        )
        s = _make_strategy(BreakoutStrategy, dp=dp)
        assert s.evaluate("EURUSD").direction == Direction.NONE

    def test_uses_previous_bar_channel(self):
        """Breakout uses Last(1) (previous bar) not Last(0) to avoid lookahead."""
        calls = []
        orig_donchian = _make_donchian(1.0860, 1.0850)
        def track(n):
            calls.append(n)
            return 1.0850 if n == 1 else 1.0860
        orig_donchian.Result.Last.side_effect = track
        dp = _make_dp()
        dp.get_indicator.side_effect = lambda sym, key: (
            orig_donchian if key in ("donchian_high", "donchian_low")
            else _make_adx(15.0) if key == "adx"
            else MagicMock(Result=MagicMock(Last=MagicMock(return_value=0.002)))
        )
        s = _make_strategy(BreakoutStrategy, dp=dp)
        s.evaluate("EURUSD")
        assert 1 in calls, "Expected Last(1) to be called for channel values"

    def test_sl_and_tp_calculated(self):
        s = _make_strategy(BreakoutStrategy, dp=_make_dp(
            close_price=1.0860,
            donchian_high_prev=1.0850,
            atr_bo_value=0.0015,
            pip_size=0.0001,
        ), params={"atr_min_threshold": 0.0005, "sl_atr_multiplier": 1.5, "tp_rr": 2.0})
        sig = s.evaluate("EURUSD")
        # sl = 0.0015/0.0001 * 1.5 = 22.5 pips; tp = 45 pips
        assert sig.stop_loss_pips == pytest.approx(22.5)
        assert sig.take_profit_pips == pytest.approx(45.0)

    def test_metadata_has_channels(self):
        s = _make_strategy(BreakoutStrategy, dp=_make_dp(
            close_price=1.0860, donchian_high_prev=1.0850, atr_bo_value=0.002,
        ), params={"atr_min_threshold": 0.0005})
        sig = s.evaluate("EURUSD")
        assert "upper_channel" in sig.metadata
        assert "lower_channel" in sig.metadata

    # should_close — always False; hard TP/SL handle all exits
    def test_should_close_always_false_for_buy(self):
        dp = _make_dp(
            close_price=1.0800,
            donchian_high_now=1.0850, donchian_low_now=1.0750,
        )
        s = _make_strategy(BreakoutStrategy, dp=dp)
        pos = _make_position(trade_type="Buy")
        assert s.should_close(pos) is False

    def test_should_close_always_false_for_sell(self):
        dp = _make_dp(
            close_price=1.0800,
            donchian_high_now=1.0850, donchian_low_now=1.0750,
        )
        s = _make_strategy(BreakoutStrategy, dp=dp)
        pos = _make_position(trade_type="Sell")
        assert s.should_close(pos) is False

    def test_should_close_returns_false_on_error(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = RuntimeError("no data")
        s = _make_strategy(BreakoutStrategy, dp=dp)
        assert s.should_close(_make_position()) is False


# ---------------------------------------------------------------------------
# StrategyEngine
# ---------------------------------------------------------------------------

def _make_engine(strategies, mode=StrategyMode.MANUAL, adx_threshold=25.0, dp=None):
    return StrategyEngine(
        strategies=strategies,
        mode=mode,
        adx_threshold=adx_threshold,
        data_provider=dp or _make_dp(),
        logger=_mock_logger(),
    )


class TestStrategyEngineManual:
    def test_evaluate_returns_signals_from_all_strategies(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        from models.trade_signal import TradeSignal
        sig = TradeSignal("TF", "EURUSD", Direction.BUY, 20.0, 40.0, 1.085)
        tf.evaluate.return_value = sig
        mr.evaluate.return_value = sig
        engine = _make_engine([tf, mr])
        results = engine.evaluate(["EURUSD"])
        assert len(results) == 2

    def test_none_signals_excluded(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        from models.trade_signal import TradeSignal
        no_sig = TradeSignal("TF", "EURUSD", Direction.NONE, 0, 0, 0)
        tf.evaluate.return_value = no_sig
        engine = _make_engine([tf])
        assert engine.evaluate(["EURUSD"]) == []

    def test_strategy_exception_does_not_abort(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        from models.trade_signal import TradeSignal
        tf.evaluate.side_effect = RuntimeError("crash")
        mr.evaluate.return_value = TradeSignal("MR", "EURUSD", Direction.SELL, 10, 20, 1.09)
        engine = _make_engine([tf, mr])
        results = engine.evaluate(["EURUSD"])
        assert len(results) == 1

    def test_evaluate_over_multiple_symbols(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        from models.trade_signal import TradeSignal
        tf.evaluate.side_effect = lambda sym: TradeSignal("TF", sym, Direction.BUY, 20, 40, 1.085)
        engine = _make_engine([tf])
        results = engine.evaluate(["EURUSD", "GBPUSD"])
        assert len(results) == 2
        symbols = {r.symbol for r in results}
        assert symbols == {"EURUSD", "GBPUSD"}

    def test_strategy_names_property(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        engine = _make_engine([tf, mr])
        assert "TrendFollowing" in engine.strategy_names
        assert "MeanReversion" in engine.strategy_names


class TestStrategyEngineAdxSwitching:
    def test_selects_trend_when_adx_above_threshold(self):
        dp = _make_dp(adx_value=30.0)
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        engine = _make_engine([tf, mr], mode=StrategyMode.ADX_SWITCHING,
                              adx_threshold=25.0, dp=dp)
        selected = engine._select_strategies("EURUSD")
        names = [s.name for s in selected]
        assert "TrendFollowing" in names
        assert "MeanReversion" not in names

    def test_selects_mean_reversion_when_adx_below_threshold(self):
        dp = _make_dp(adx_value=20.0)
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        engine = _make_engine([tf, mr], mode=StrategyMode.ADX_SWITCHING,
                              adx_threshold=25.0, dp=dp)
        selected = engine._select_strategies("EURUSD")
        names = [s.name for s in selected]
        assert "MeanReversion" in names
        assert "TrendFollowing" not in names

    def test_breakout_always_included_in_adx_switching(self):
        dp = _make_dp(adx_value=30.0)
        tf = MagicMock(); tf.name = "TrendFollowing"
        bo = MagicMock(); bo.name = "Breakout"
        engine = _make_engine([tf, bo], mode=StrategyMode.ADX_SWITCHING,
                              adx_threshold=25.0, dp=dp)
        selected = engine._select_strategies("EURUSD")
        names = [s.name for s in selected]
        assert "Breakout" in names
        assert "TrendFollowing" in names

    def test_adx_at_threshold_uses_trend(self):
        dp = _make_dp(adx_value=25.0)  # exactly at threshold
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        engine = _make_engine([tf, mr], mode=StrategyMode.ADX_SWITCHING,
                              adx_threshold=25.0, dp=dp)
        selected = engine._select_strategies("EURUSD")
        assert any(s.name == "TrendFollowing" for s in selected)

    def test_adx_unavailable_falls_back_to_all_strategies(self):
        dp = _make_dp()
        dp.get_indicator.side_effect = RuntimeError("no ADX")
        tf = MagicMock(); tf.name = "TrendFollowing"
        mr = MagicMock(); mr.name = "MeanReversion"
        engine = _make_engine([tf, mr], mode=StrategyMode.ADX_SWITCHING, dp=dp)
        selected = engine._select_strategies("EURUSD")
        assert len(selected) == 2


class TestStrategyEngineCheckExits:
    def _make_exit_engine(self, strategy):
        return _make_engine([strategy])

    def test_returns_position_when_should_close(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        tf.should_close.return_value = True
        engine = _make_engine([tf])
        pos = _make_position(label="ArgoAlgo_TR_EURUSD")
        exits = engine.check_exits([pos])
        assert len(exits) == 1
        assert exits[0][0] is pos
        assert "TrendFollowing" in exits[0][1]

    def test_does_not_return_position_when_should_not_close(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        tf.should_close.return_value = False
        engine = _make_engine([tf])
        pos = _make_position(label="ArgoAlgo_TR_EURUSD")
        exits = engine.check_exits([pos])
        assert exits == []

    def test_matches_mean_reversion_by_abbrev(self):
        mr = MagicMock(); mr.name = "MeanReversion"
        mr.should_close.return_value = True
        engine = _make_engine([mr])
        pos = _make_position(label="ArgoAlgo_ME_GBPUSD")
        exits = engine.check_exits([pos])
        assert len(exits) == 1

    def test_matches_breakout_by_abbrev(self):
        bo = MagicMock(); bo.name = "Breakout"
        bo.should_close.return_value = True
        engine = _make_engine([bo])
        pos = _make_position(label="ArgoAlgo_BR_USDJPY")
        exits = engine.check_exits([pos])
        assert len(exits) == 1

    def test_ignores_unrecognised_label(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        engine = _make_engine([tf])
        pos = _make_position(label="ManualTrade")
        exits = engine.check_exits([pos])
        assert exits == []

    def test_ignores_wrong_strategy_abbrev(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        tf.should_close.return_value = True
        engine = _make_engine([tf])
        pos = _make_position(label="ArgoAlgo_ME_EURUSD")  # ME not registered
        exits = engine.check_exits([pos])
        assert exits == []

    def test_empty_positions_returns_empty(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        engine = _make_engine([tf])
        assert engine.check_exits([]) == []

    def test_position_error_does_not_abort_others(self):
        tf = MagicMock(); tf.name = "TrendFollowing"
        tf.should_close.side_effect = [RuntimeError("crash"), True]
        engine = _make_engine([tf])
        pos1 = _make_position(label="ArgoAlgo_TR_EURUSD")
        pos2 = _make_position(label="ArgoAlgo_TR_GBPUSD")
        # pos1 raises, pos2 should still be processed
        exits = engine.check_exits([pos1, pos2])
        assert any(e[0] is pos2 for e in exits)
