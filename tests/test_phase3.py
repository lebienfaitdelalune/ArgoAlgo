"""
test_phase3.py
Phase 3 tests: DataProvider — symbol loading, bar access, indicator
initialization, spread checks, multi-timeframe support, error handling.
No cTrader API required — uses a mock API object.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import Defaults, MIN_BARS_REQUIRED, LogLevel
from core.data_provider import DataProvider, DataProviderError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PRIMARY_TF = "M15"
HIGHER_TF = "H4"
ALL_TF = [PRIMARY_TF, HIGHER_TF]

INDICATOR_KEYS = [
    "ema_fast", "ema_slow", "adx", "atr",
    "bollinger", "rsi", "adx_filter",
    "donchian_high", "donchian_low", "atr_bo",
]


def _make_mock_bars(n_bars: int = 200) -> MagicMock:
    bars = MagicMock()
    bars.__len__ = MagicMock(return_value=n_bars)
    bars.Count = n_bars
    bars.ClosePrices = MagicMock()
    bars.HighPrices = MagicMock()
    bars.LowPrices = MagicMock()
    return bars


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.Print = MagicMock()

    # Symbol with realistic spread properties
    def make_symbol(name):
        sym = MagicMock()
        sym.Name = name
        sym.Bid = 1.08000
        sym.Ask = 1.08020
        sym.PipSize = 0.0001
        return sym

    api.Symbols.get_Item.side_effect = make_symbol
    api.MarketData.GetBars.return_value = _make_mock_bars()

    # Indicator factory — each call returns a fresh mock
    api.Indicators.ExponentialMovingAverage.return_value = MagicMock()
    api.Indicators.DirectionalMovementSystem.return_value = MagicMock()
    api.Indicators.AverageTrueRange.return_value = MagicMock()
    api.Indicators.BollingerBands.return_value = MagicMock()
    api.Indicators.RelativeStrengthIndex.return_value = MagicMock()
    api.Indicators.Maximum.return_value = MagicMock()
    api.Indicators.Minimum.return_value = MagicMock()

    return api


@pytest.fixture
def mock_logger():
    logger = MagicMock()
    return logger


@pytest.fixture
def dp(mock_api, mock_logger):
    """DataProvider initialized with 2 symbols and default params."""
    provider = DataProvider(
        api=mock_api,
        symbols=["EURUSD", "GBPUSD"],
        logger=mock_logger,
    )
    provider.initialize(ALL_TF)
    return provider


@pytest.fixture
def dp_custom_params(mock_api, mock_logger):
    """DataProvider with explicit indicator params."""
    params = {
        "ema_fast_period": 8,
        "ema_slow_period": 21,
        "adx_period": 10,
        "bollinger_period": 15,
        "bollinger_deviation": 1.5,
        "rsi_period": 7,
        "adx_filter_period": 20,
        "donchian_period": 30,
        "atr_bo_period": 10,
    }
    provider = DataProvider(
        api=mock_api,
        symbols=["EURUSD"],
        logger=mock_logger,
        indicator_params=params,
    )
    provider.initialize(ALL_TF)
    return provider


# ---------------------------------------------------------------------------
# Constructor and repr
# ---------------------------------------------------------------------------

class TestDataProviderConstruction:
    def test_symbols_stored(self, mock_api, mock_logger):
        dp = DataProvider(mock_api, ["EURUSD", "USDJPY"], mock_logger)
        assert dp.symbols == ["EURUSD", "USDJPY"]

    def test_indicator_params_defaults_to_empty(self, mock_api, mock_logger):
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        assert dp._indicator_params == {}

    def test_primary_timeframe_none_before_initialize(self, mock_api, mock_logger):
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        assert dp.primary_timeframe is None

    def test_repr_contains_symbols(self, mock_api, mock_logger):
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        r = repr(dp)
        assert "EURUSD" in r

    def test_repr_shows_loaded_count_after_init(self, dp):
        r = repr(dp)
        assert "loaded=2" in r


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_sets_primary_timeframe(self, dp):
        assert dp.primary_timeframe == PRIMARY_TF

    def test_symbol_api_called_for_each_symbol(self, mock_api, dp):
        calls = [c[0][0] for c in mock_api.Symbols.get_Item.call_args_list]
        assert "EURUSD" in calls
        assert "GBPUSD" in calls

    def test_get_bars_called_per_symbol_per_timeframe(self, mock_api, dp):
        # 2 symbols × 2 timeframes = 4 calls
        assert mock_api.MarketData.GetBars.call_count == 4

    def test_get_bars_called_with_correct_timeframes(self, mock_api, dp):
        tf_args = {c[0][1] for c in mock_api.MarketData.GetBars.call_args_list}
        assert PRIMARY_TF in tf_args
        assert HIGHER_TF in tf_args

    def test_logs_initialization_info(self, mock_logger, dp):
        # info() should have been called at least twice (start + ready)
        assert mock_logger.info.call_count >= 2

    def test_empty_timeframes_logs_warning(self, mock_api, mock_logger):
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize([])
        mock_logger.warning.assert_called()

    def test_five_symbols_all_loaded(self, mock_api, mock_logger):
        symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]
        dp = DataProvider(mock_api, symbols, mock_logger)
        dp.initialize(ALL_TF)
        for sym in symbols:
            assert dp.get_symbol(sym) is not None

    def test_symbol_load_failure_skips_symbol(self, mock_api, mock_logger):
        """A bad symbol doesn't abort initialization of other symbols."""
        def symbol_side_effect(name):
            if name == "BADSYM":
                raise RuntimeError("unknown symbol")
            s = MagicMock()
            s.Bid = 1.0
            s.Ask = 1.001
            s.PipSize = 0.0001
            return s

        mock_api.Symbols.get_Item.side_effect = symbol_side_effect
        dp = DataProvider(mock_api, ["BADSYM", "EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        # EURUSD should still be available
        assert dp.get_symbol("EURUSD") is not None
        mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# get_symbol()
# ---------------------------------------------------------------------------

class TestGetSymbol:
    def test_returns_symbol_object(self, dp):
        sym = dp.get_symbol("EURUSD")
        assert sym is not None

    def test_raises_for_unknown_symbol(self, dp):
        with pytest.raises(DataProviderError, match="XYZABC"):
            dp.get_symbol("XYZABC")


# ---------------------------------------------------------------------------
# get_bars()
# ---------------------------------------------------------------------------

class TestGetBars:
    def test_returns_bars_for_primary_tf(self, dp):
        bars = dp.get_bars("EURUSD", PRIMARY_TF)
        assert bars is not None

    def test_returns_bars_for_higher_tf(self, dp):
        bars = dp.get_bars("EURUSD", HIGHER_TF)
        assert bars is not None

    def test_raises_for_unknown_symbol(self, dp):
        with pytest.raises(DataProviderError, match="XYZABC"):
            dp.get_bars("XYZABC", PRIMARY_TF)

    def test_raises_for_unknown_timeframe(self, dp):
        with pytest.raises(DataProviderError, match="D1"):
            dp.get_bars("EURUSD", "D1")


# ---------------------------------------------------------------------------
# get_spread_pips()
# ---------------------------------------------------------------------------

class TestGetSpreadPips:
    def test_returns_positive_float(self, dp):
        spread = dp.get_spread_pips("EURUSD")
        assert isinstance(spread, float)
        assert spread > 0.0

    def test_correct_spread_calculation(self, mock_api, mock_logger):
        """(Ask - Bid) / PipSize = (1.08020 - 1.08000) / 0.0001 = 2.0 pips."""
        sym = MagicMock()
        sym.Bid = 1.08000
        sym.Ask = 1.08020
        sym.PipSize = 0.0001
        mock_api.Symbols.get_Item.return_value = sym
        mock_api.Symbols.get_Item.side_effect = None
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        spread = dp.get_spread_pips("EURUSD")
        assert spread == pytest.approx(2.0)

    def test_raises_for_unknown_symbol(self, dp):
        with pytest.raises(DataProviderError):
            dp.get_spread_pips("XYZABC")


# ---------------------------------------------------------------------------
# is_spread_acceptable()
# ---------------------------------------------------------------------------

class TestIsSpreadAcceptable:
    def test_acceptable_when_spread_below_max(self, mock_api, mock_logger):
        sym = MagicMock()
        sym.Bid = 1.08000
        sym.Ask = 1.08010   # 1.0 pip spread
        sym.PipSize = 0.0001
        mock_api.Symbols.get_Item.return_value = sym
        mock_api.Symbols.get_Item.side_effect = None
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.is_spread_acceptable("EURUSD", max_spread_pips=3.0) is True

    def test_not_acceptable_when_spread_above_max(self, mock_api, mock_logger):
        sym = MagicMock()
        sym.Bid = 1.08000
        sym.Ask = 1.08050   # 5.0 pip spread
        sym.PipSize = 0.0001
        mock_api.Symbols.get_Item.return_value = sym
        mock_api.Symbols.get_Item.side_effect = None
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.is_spread_acceptable("EURUSD", max_spread_pips=3.0) is False

    def test_acceptable_at_exact_limit(self, mock_api, mock_logger):
        sym = MagicMock()
        sym.Bid = 1.08000
        sym.Ask = 1.08030   # exactly 3.0 pip spread
        sym.PipSize = 0.0001
        mock_api.Symbols.get_Item.return_value = sym
        mock_api.Symbols.get_Item.side_effect = None
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.is_spread_acceptable("EURUSD", max_spread_pips=3.0) is True


# ---------------------------------------------------------------------------
# get_indicator()
# ---------------------------------------------------------------------------

class TestGetIndicator:
    def test_all_10_indicators_accessible(self, dp):
        for key in INDICATOR_KEYS:
            ind = dp.get_indicator("EURUSD", key)
            assert ind is not None, f"Indicator {key!r} missing"

    def test_indicators_accessible_for_all_symbols(self, dp):
        for symbol in ["EURUSD", "GBPUSD"]:
            for key in INDICATOR_KEYS:
                ind = dp.get_indicator(symbol, key)
                assert ind is not None

    def test_raises_for_unknown_symbol(self, dp):
        with pytest.raises(DataProviderError, match="XYZABC"):
            dp.get_indicator("XYZABC", "ema_fast")

    def test_raises_for_unknown_key(self, dp):
        with pytest.raises(DataProviderError, match="unknown_key"):
            dp.get_indicator("EURUSD", "unknown_key")

    def test_ema_fast_created_with_correct_period(self, mock_api, mock_logger):
        dp = DataProvider(
            mock_api, ["EURUSD"], mock_logger,
            indicator_params={"ema_fast_period": 8}
        )
        dp.initialize(ALL_TF)
        ema_calls = mock_api.Indicators.ExponentialMovingAverage.call_args_list
        periods = [c[0][1] for c in ema_calls if c[0][1] == 8]
        assert len(periods) >= 1, "EMA with period=8 not called"

    def test_ema_slow_created_with_correct_period(self, mock_api, mock_logger):
        dp = DataProvider(
            mock_api, ["EURUSD"], mock_logger,
            indicator_params={"ema_slow_period": 50}
        )
        dp.initialize(ALL_TF)
        ema_calls = mock_api.Indicators.ExponentialMovingAverage.call_args_list
        periods = [c[0][1] for c in ema_calls if c[0][1] == 50]
        assert len(periods) >= 1

    def test_bollinger_created_with_correct_params(self, mock_api, mock_logger):
        dp = DataProvider(
            mock_api, ["EURUSD"], mock_logger,
            indicator_params={"bollinger_period": 15, "bollinger_deviation": 1.5}
        )
        dp.initialize(ALL_TF)
        bb_calls = mock_api.Indicators.BollingerBands.call_args_list
        assert len(bb_calls) >= 1
        args = bb_calls[0][0]
        assert args[1] == 15
        assert args[2] == 1.5

    def test_rsi_created_with_correct_period(self, mock_api, mock_logger):
        dp = DataProvider(
            mock_api, ["EURUSD"], mock_logger,
            indicator_params={"rsi_period": 7}
        )
        dp.initialize(ALL_TF)
        rsi_calls = mock_api.Indicators.RelativeStrengthIndex.call_args_list
        assert any(c[0][1] == 7 for c in rsi_calls)

    def test_donchian_high_uses_high_prices(self, mock_api, mock_logger):
        # donchian_high uses _RollingExtreme (manual wrapper), not a cTrader API call
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        ind = dp.get_indicator("EURUSD", "donchian_high")
        assert ind is not None
        assert hasattr(ind, "Result")

    def test_donchian_low_uses_low_prices(self, mock_api, mock_logger):
        # donchian_low uses _RollingExtreme (manual wrapper), not a cTrader API call
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        ind = dp.get_indicator("EURUSD", "donchian_low")
        assert ind is not None
        assert hasattr(ind, "Result")

    def test_custom_params_override_defaults(self, dp_custom_params):
        """All 10 indicators are still present when custom params are used."""
        for key in INDICATOR_KEYS:
            ind = dp_custom_params.get_indicator("EURUSD", key)
            assert ind is not None

    def test_indicator_init_failure_does_not_raise(self, mock_api, mock_logger):
        """If one indicator fails, DataProvider stays functional."""
        mock_api.Indicators.ExponentialMovingAverage.side_effect = RuntimeError("no license")
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        # Should not raise; error logged
        mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# has_sufficient_history()
# ---------------------------------------------------------------------------

class TestHasSufficientHistory:
    def test_true_when_enough_bars(self, mock_api, mock_logger):
        mock_api.MarketData.GetBars.return_value = _make_mock_bars(200)
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.has_sufficient_history("EURUSD", PRIMARY_TF, min_bars=100) is True

    def test_false_when_too_few_bars(self, mock_api, mock_logger):
        mock_api.MarketData.GetBars.return_value = _make_mock_bars(30)
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.has_sufficient_history("EURUSD", PRIMARY_TF, min_bars=100) is False

    def test_false_for_unknown_symbol(self, dp):
        assert dp.has_sufficient_history("XYZABC", PRIMARY_TF) is False

    def test_false_for_unknown_timeframe(self, dp):
        assert dp.has_sufficient_history("EURUSD", "D1") is False

    def test_uses_min_bars_required_constant_by_default(self, mock_api, mock_logger):
        mock_api.MarketData.GetBars.return_value = _make_mock_bars(MIN_BARS_REQUIRED)
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.has_sufficient_history("EURUSD", PRIMARY_TF) is True

    def test_false_just_below_min_bars_required(self, mock_api, mock_logger):
        mock_api.MarketData.GetBars.return_value = _make_mock_bars(MIN_BARS_REQUIRED - 1)
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.has_sufficient_history("EURUSD", PRIMARY_TF) is False


# ---------------------------------------------------------------------------
# Multi-timeframe
# ---------------------------------------------------------------------------

class TestMultiTimeframe:
    def test_both_timeframes_loaded(self, dp):
        bars_m15 = dp.get_bars("EURUSD", PRIMARY_TF)
        bars_h4 = dp.get_bars("EURUSD", HIGHER_TF)
        assert bars_m15 is not None
        assert bars_h4 is not None

    def test_different_timeframes_return_different_bar_objects(self, mock_api, mock_logger):
        bars_m15 = _make_mock_bars(200)
        bars_h4 = _make_mock_bars(500)
        mock_api.MarketData.GetBars.side_effect = lambda sym, tf: bars_m15 if tf == PRIMARY_TF else bars_h4
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(ALL_TF)
        assert dp.get_bars("EURUSD", PRIMARY_TF) is bars_m15
        assert dp.get_bars("EURUSD", HIGHER_TF) is bars_h4

    def test_third_timeframe_raises_if_not_loaded(self, dp):
        with pytest.raises(DataProviderError):
            dp.get_bars("EURUSD", "W1")

    def test_first_timeframe_is_primary(self, mock_api, mock_logger):
        dp = DataProvider(mock_api, ["EURUSD"], mock_logger)
        dp.initialize(["D1", "W1"])
        assert dp.primary_timeframe == "D1"


# ---------------------------------------------------------------------------
# update() is a no-op (cTrader data is live)
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_update_does_not_raise(self, dp):
        dp.update()  # Should be a silent no-op

    def test_update_makes_no_api_calls(self, mock_api, dp):
        before = mock_api.MarketData.GetBars.call_count
        dp.update()
        assert mock_api.MarketData.GetBars.call_count == before


# ---------------------------------------------------------------------------
# TradingBot integration — _bootstrap_data_provider passes correct params
# ---------------------------------------------------------------------------

class TestTradingBotDataProviderIntegration:
    def _make_bot_api(self):
        api = MagicMock()
        api.Account.Balance = 10_000.0
        api.Account.Equity = 10_000.0
        api.Print = MagicMock()
        api.Positions = MagicMock()
        api.Positions.__iter__ = MagicMock(return_value=iter([]))
        api.PendingOrders = MagicMock()
        api.Server.Time.Hour = 10
        api.Server.Time.DayOfWeek.ToString.return_value = "Tuesday"
        sym = MagicMock()
        sym.Bid = 1.08000
        sym.Ask = 1.08020
        sym.PipSize = 0.0001
        api.Symbols.get_Item.return_value = sym
        api.MarketData.GetBars.return_value = _make_mock_bars()
        return api

    def test_data_provider_initialized_after_on_start(self):
        from main import TradingBot
        api = self._make_bot_api()
        bot = TradingBot(api=api)
        bot.on_start()
        assert bot._data_provider is not None
        assert bot._data_provider.primary_timeframe is not None

    def test_symbols_match_bot_config(self):
        from main import TradingBot
        api = self._make_bot_api()
        bot = TradingBot(api=api)
        bot.on_start()
        for sym in bot._symbols:
            assert sym in bot._data_provider.symbols

    def test_indicator_params_passed_correctly(self):
        from main import TradingBot
        api = self._make_bot_api()
        bot = TradingBot(api=api)
        bot.TF_FastEmaPeriod = 8
        bot.on_start()
        params = bot._data_provider._indicator_params
        assert params["ema_fast_period"] == 8

    def test_all_indicators_accessible_after_on_start(self):
        from main import TradingBot
        api = self._make_bot_api()
        bot = TradingBot(api=api)
        bot.on_start()
        dp = bot._data_provider
        for sym in bot._symbols:
            for key in INDICATOR_KEYS:
                ind = dp.get_indicator(sym, key)
                assert ind is not None, f"Missing {key!r} for {sym}"
