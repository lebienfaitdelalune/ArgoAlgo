"""
test_phase1.py
Phase 1 integration tests: data models, constants, module imports,
TradingBot initialization, and parameter defaults.
No cTrader API required — uses a mock API object.
"""

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import (
    BOT_VERSION,
    BotStatus,
    Defaults,
    Direction,
    DrawdownStatus,
    LogLevel,
    NotificationLevel,
    OrderResult,
    RateLimits,
    SLType,
    StrategyMode,
    StrategyName,
)
from models.trade_signal import TradeSignal
from models.trade_instruction import TradeInstruction
from models.performance import PerformanceSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_api():
    """Minimal mock of the cTrader API object."""
    api = MagicMock()
    api.Account.Balance = 10_000.0
    api.Account.Equity = 10_050.0
    api.Print = MagicMock()
    api.Positions = MagicMock()
    api.Positions.__iter__ = MagicMock(return_value=iter([]))
    api.PendingOrders = MagicMock()
    return api


@pytest.fixture
def sample_signal():
    return TradeSignal(
        strategy_name="TrendFollowing",
        symbol="EURUSD",
        direction=Direction.BUY,
        stop_loss_pips=20.0,
        take_profit_pips=40.0,
        entry_price=1.08500,
        timestamp=datetime(2025, 1, 15, 10, 30, 0),
        metadata={"adx": 28.5},
    )


@pytest.fixture
def none_signal():
    return TradeSignal(
        strategy_name="TrendFollowing",
        symbol="EURUSD",
        direction=Direction.NONE,
        stop_loss_pips=0.0,
        take_profit_pips=0.0,
        entry_price=0.0,
    )


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_direction_values(self):
        assert Direction.BUY.value == "Buy"
        assert Direction.SELL.value == "Sell"
        assert Direction.NONE.value == "None"

    def test_strategy_mode_values(self):
        assert StrategyMode.MANUAL.value == "Manual"
        assert StrategyMode.ADX_SWITCHING.value == "AdxSwitching"

    def test_strategy_names(self):
        assert StrategyName.TREND_FOLLOWING.value == "TrendFollowing"
        assert StrategyName.MEAN_REVERSION.value == "MeanReversion"
        assert StrategyName.BREAKOUT.value == "Breakout"

    def test_bot_status(self):
        assert BotStatus.RUNNING.value == "RUNNING"
        assert BotStatus.HALTED.value == "HALTED"

    def test_drawdown_status_distinct(self):
        statuses = {DrawdownStatus.OK, DrawdownStatus.DAILY_LIMIT_BREACHED, DrawdownStatus.TOTAL_LIMIT_BREACHED}
        assert len(statuses) == 3

    def test_log_level_ordering(self):
        assert LogLevel.DEBUG.value < LogLevel.INFO.value
        assert LogLevel.INFO.value < LogLevel.WARNING.value
        assert LogLevel.WARNING.value < LogLevel.ERROR.value

    def test_rate_limits_positive(self):
        assert RateLimits.NEW_ORDERS == 500
        assert RateLimits.CANCEL_ORDERS == 100
        assert RateLimits.MODIFY_PROTECTION_L1 == 1000


# ---------------------------------------------------------------------------
# Defaults tests
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_risk_per_trade(self):
        assert Defaults.RISK_PER_TRADE_PCT == 1.0

    def test_max_drawdowns(self):
        assert Defaults.MAX_DAILY_DRAWDOWN_PCT == 5.0
        assert Defaults.MAX_TOTAL_DRAWDOWN_PCT == 10.0

    def test_session_hours(self):
        assert 0 <= Defaults.TRADING_START_HOUR_UTC <= 23
        assert 1 <= Defaults.TRADING_END_HOUR_UTC <= 24

    def test_traded_symbols_parseable(self):
        from utils.helpers import parse_symbols_string
        symbols = parse_symbols_string(Defaults.TRADED_SYMBOLS)
        assert len(symbols) >= 1
        assert "EURUSD" in symbols

    def test_label_prefix(self):
        assert Defaults.LABEL_PREFIX == "ArgoAlgo"

    def test_bot_version(self):
        assert BOT_VERSION == "1.0.0"


# ---------------------------------------------------------------------------
# TradeSignal tests
# ---------------------------------------------------------------------------

class TestTradeSignal:
    def test_instantiation(self, sample_signal):
        assert sample_signal.symbol == "EURUSD"
        assert sample_signal.direction == Direction.BUY
        assert sample_signal.stop_loss_pips == 20.0
        assert sample_signal.take_profit_pips == 40.0

    def test_is_actionable_buy(self, sample_signal):
        assert sample_signal.is_actionable() is True

    def test_is_actionable_sell(self):
        sig = TradeSignal("MR", "GBPUSD", Direction.SELL, 15.0, 30.0, 1.25000)
        assert sig.is_actionable() is True

    def test_is_actionable_none(self, none_signal):
        assert none_signal.is_actionable() is False

    def test_metadata_default_empty(self):
        sig = TradeSignal("TF", "EURUSD", Direction.BUY, 20.0, 40.0, 1.085)
        assert sig.metadata == {}

    def test_metadata_stored(self, sample_signal):
        assert sample_signal.metadata["adx"] == 28.5

    def test_repr_contains_key_info(self, sample_signal):
        r = repr(sample_signal)
        assert "EURUSD" in r
        assert "Buy" in r
        assert "TrendFollowing" in r

    def test_timestamp_default_set(self):
        before = datetime.utcnow()
        sig = TradeSignal("TF", "EURUSD", Direction.BUY, 20.0, 40.0, 1.085)
        after = datetime.utcnow()
        assert before <= sig.timestamp <= after


# ---------------------------------------------------------------------------
# TradeInstruction tests
# ---------------------------------------------------------------------------

class TestTradeInstruction:
    def test_validated_instruction(self, sample_signal):
        instr = TradeInstruction(signal=sample_signal, volume_units=5000, validated=True)
        assert instr.validated is True
        assert instr.volume_units == 5000
        assert instr.rejection_reason is None

    def test_rejected_instruction(self, sample_signal):
        instr = TradeInstruction(
            signal=sample_signal,
            volume_units=0,
            validated=False,
            rejection_reason="Spread too wide",
        )
        assert instr.validated is False
        assert instr.rejection_reason == "Spread too wide"

    def test_repr_validated(self, sample_signal):
        instr = TradeInstruction(signal=sample_signal, volume_units=5000, validated=True)
        r = repr(instr)
        assert "EURUSD" in r
        assert "validated=True" in r

    def test_repr_rejected(self, sample_signal):
        instr = TradeInstruction(
            signal=sample_signal, volume_units=0, validated=False,
            rejection_reason="Max positions reached"
        )
        r = repr(instr)
        assert "validated=False" in r
        assert "Max positions reached" in r


# ---------------------------------------------------------------------------
# PerformanceSnapshot tests
# ---------------------------------------------------------------------------

class TestPerformanceSnapshot:
    def test_instantiation(self):
        snap = PerformanceSnapshot(
            timestamp=datetime(2025, 1, 15, 12, 0, 0),
            balance=10_000.0,
            equity=10_050.0,
            open_positions=2,
            daily_pnl=50.0,
            daily_drawdown_pct=0.5,
            total_drawdown_pct=0.5,
            trade_count_today=3,
        )
        assert snap.balance == 10_000.0
        assert snap.equity == 10_050.0
        assert snap.open_positions == 2

    def test_repr(self):
        snap = PerformanceSnapshot(
            timestamp=datetime.utcnow(),
            balance=10_000.0,
            equity=9_800.0,
            open_positions=1,
            daily_pnl=-200.0,
            daily_drawdown_pct=2.0,
            total_drawdown_pct=2.0,
            trade_count_today=5,
        )
        r = repr(snap)
        assert "9800" in r
        assert "daily_dd=2.00%" in r


# ---------------------------------------------------------------------------
# Module import tests
# ---------------------------------------------------------------------------

class TestModuleImports:
    def test_import_logger(self):
        from core.logger import Logger
        assert Logger is not None

    def test_import_data_provider(self):
        from core.data_provider import DataProvider, DataProviderError
        assert DataProvider is not None
        assert DataProviderError is not None

    def test_import_risk_manager(self):
        from core.risk_manager import RiskManager, RiskParams
        assert RiskManager is not None
        assert RiskParams is not None

    def test_import_order_executor(self):
        from core.order_executor import OrderExecutor, ExecutionResult
        assert OrderExecutor is not None
        assert ExecutionResult is not None

    def test_import_strategy_engine(self):
        from core.strategy_engine import StrategyEngine
        assert StrategyEngine is not None

    def test_import_base_strategy(self):
        from strategies.base_strategy import IStrategy
        assert IStrategy is not None

    def test_import_strategies(self):
        from strategies.trend_following import TrendFollowingStrategy
        from strategies.mean_reversion import MeanReversionStrategy
        from strategies.breakout import BreakoutStrategy
        assert TrendFollowingStrategy is not None
        assert MeanReversionStrategy is not None
        assert BreakoutStrategy is not None

    def test_import_ui_panel(self):
        from ui.panel import UIPanel
        assert UIPanel is not None

    def test_import_main(self):
        from main import TradingBot
        assert TradingBot is not None

    def test_import_fitness(self):
        from optimization.fitness import calculate_fitness
        assert callable(calculate_fitness)


# ---------------------------------------------------------------------------
# TradingBot initialisation tests
# ---------------------------------------------------------------------------

class TestTradingBotInit:
    def test_default_parameters(self):
        from main import TradingBot
        bot = TradingBot()
        assert bot.RiskPerTradePercent == Defaults.RISK_PER_TRADE_PCT
        assert bot.MaxTotalDrawdownPercent == Defaults.MAX_TOTAL_DRAWDOWN_PCT
        # 2026-07 config: all bar-signal strategies disabled (edges falsified);
        # only the xsect forward test trades.
        assert bot.EnableTrend is False
        assert bot.EnableMeanReversion is False
        assert bot.EnableBreakout is False
        assert bot.EnableXsect is True

    def test_starts_not_halted(self):
        from main import TradingBot
        bot = TradingBot()
        assert bot._is_halted is False

    def test_modules_none_before_on_start(self):
        from main import TradingBot
        bot = TradingBot()
        assert bot._logger is None
        assert bot._risk_manager is None
        assert bot._order_executor is None
        assert bot._strategy_engine is None

    def test_on_start_initialises_all_modules(self, mock_api):
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        assert bot._logger is not None
        assert bot._data_provider is not None
        assert bot._risk_manager is not None
        assert bot._order_executor is not None
        assert bot._strategy_engine is not None
        assert bot._ui_panel is not None

    def test_on_start_logs_banner(self, mock_api):
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        assert mock_api.Print.called
        # Banner should include the version
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any(BOT_VERSION in c for c in calls)

    def test_on_start_parses_symbols(self, mock_api):
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        assert "EURUSD" in bot._symbols
        assert len(bot._symbols) >= 1

    def test_halt_trading(self, mock_api):
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        bot._halt_trading("test reason")
        assert bot._is_halted is True

    def test_on_bar_closed_returns_early_when_halted(self, mock_api):
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        bot._is_halted = True
        # Should not raise; should return without calling strategy engine
        bot.on_bar_closed()

    def test_repr(self):
        from main import TradingBot
        bot = TradingBot()
        r = repr(bot)
        assert BOT_VERSION in r
        assert "halted=False" in r


# ---------------------------------------------------------------------------
# Logger tests (with mock API)
# ---------------------------------------------------------------------------

class TestLogger:
    def test_info_calls_print(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        logger.info("hello world")
        assert mock_api.Print.called
        call_arg = mock_api.Print.call_args[0][0]
        assert "hello world" in call_arg
        assert "[INFO " in call_arg

    def test_debug_suppressed_at_info_level(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        logger.debug("debug message")
        assert not mock_api.Print.called

    def test_debug_emitted_at_debug_level(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.DEBUG, False, "ArgoAlgo")
        logger.debug("debug message")
        assert mock_api.Print.called

    def test_error_always_emitted(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.ERROR, False, "ArgoAlgo")
        logger.error("something broke")
        assert mock_api.Print.called
        call_arg = mock_api.Print.call_args[0][0]
        assert "[ERROR" in call_arg

    def test_warning_emitted_at_warning_level(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.WARNING, False, "ArgoAlgo")
        logger.warning("watch out")
        assert mock_api.Print.called

    def test_warning_suppressed_above_warning(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.ERROR, False, "ArgoAlgo")
        logger.warning("watch out")
        assert not mock_api.Print.called

    def test_log_format_contains_utc(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        logger.info("test")
        call_arg = mock_api.Print.call_args[0][0]
        assert "UTC" in call_arg

    def test_error_with_exception(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.ERROR, False, "ArgoAlgo")
        try:
            raise ValueError("test exception")
        except ValueError as e:
            logger.error("caught error", exc=e)
        call_arg = mock_api.Print.call_args[0][0]
        assert "ValueError" in call_arg
        assert "test exception" in call_arg

    def test_repr(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        assert "INFO" in repr(logger)

    def test_api_print_failure_does_not_raise(self):
        from core.logger import Logger
        bad_api = MagicMock()
        bad_api.Print.side_effect = RuntimeError("API unavailable")
        logger = Logger(bad_api, LogLevel.INFO, False, "ArgoAlgo")
        # Should not raise even if Print fails
        logger.info("should not crash")


# ---------------------------------------------------------------------------
# OrderExecutor — build_label and is_bot_position
# ---------------------------------------------------------------------------

class TestOrderExecutor:
    def test_build_label_trend_following(self, mock_api):
        from core.logger import Logger
        from core.order_executor import OrderExecutor
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        executor = OrderExecutor(mock_api, logger, "ArgoAlgo")
        label = executor.build_label("TrendFollowing", "EURUSD")
        assert label == "ArgoAlgo_TR_EURUSD"

    def test_build_label_mean_reversion(self, mock_api):
        from core.logger import Logger
        from core.order_executor import OrderExecutor
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        executor = OrderExecutor(mock_api, logger, "ArgoAlgo")
        label = executor.build_label("MeanReversion", "GBPUSD")
        assert label == "ArgoAlgo_ME_GBPUSD"

    def test_is_bot_position_true(self, mock_api):
        from core.logger import Logger
        from core.order_executor import OrderExecutor
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        executor = OrderExecutor(mock_api, logger, "ArgoAlgo")
        pos = MagicMock()
        pos.Label = "ArgoAlgo_TF_EURUSD"
        assert executor.is_bot_position(pos) is True

    def test_is_bot_position_false(self, mock_api):
        from core.logger import Logger
        from core.order_executor import OrderExecutor
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        executor = OrderExecutor(mock_api, logger, "ArgoAlgo")
        pos = MagicMock()
        pos.Label = "ManualTrade"
        assert executor.is_bot_position(pos) is False

    def test_repr(self, mock_api):
        from core.logger import Logger
        from core.order_executor import OrderExecutor
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        executor = OrderExecutor(mock_api, logger)
        assert "ArgoAlgo" in repr(executor)


# ---------------------------------------------------------------------------
# Strategy stubs — no_signal helper
# ---------------------------------------------------------------------------

class TestStrategyStubs:
    def test_trend_following_returns_none_signal(self, mock_api):
        from core.data_provider import DataProvider
        from core.logger import Logger
        from strategies.trend_following import TrendFollowingStrategy
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        dp = DataProvider(mock_api, ["EURUSD"], logger)
        strategy = TrendFollowingStrategy(mock_api, dp, logger, {})
        signal = strategy.evaluate("EURUSD")
        assert signal.direction == Direction.NONE
        assert signal.symbol == "EURUSD"

    def test_mean_reversion_returns_none_signal(self, mock_api):
        from core.data_provider import DataProvider
        from core.logger import Logger
        from strategies.mean_reversion import MeanReversionStrategy
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        dp = DataProvider(mock_api, ["EURUSD"], logger)
        strategy = MeanReversionStrategy(mock_api, dp, logger, {})
        signal = strategy.evaluate("EURUSD")
        assert signal.direction == Direction.NONE

    def test_breakout_returns_none_signal(self, mock_api):
        from core.data_provider import DataProvider
        from core.logger import Logger
        from strategies.breakout import BreakoutStrategy
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        dp = DataProvider(mock_api, ["EURUSD"], logger)
        strategy = BreakoutStrategy(mock_api, dp, logger, {})
        signal = strategy.evaluate("EURUSD")
        assert signal.direction == Direction.NONE

    def test_strategy_name_attributes(self):
        from strategies.breakout import BreakoutStrategy
        from strategies.mean_reversion import MeanReversionStrategy
        from strategies.trend_following import TrendFollowingStrategy
        assert TrendFollowingStrategy.name == "TrendFollowing"
        assert MeanReversionStrategy.name == "MeanReversion"
        assert BreakoutStrategy.name == "Breakout"


# ---------------------------------------------------------------------------
# RiskManager — validate stub returns unimplemented
# ---------------------------------------------------------------------------

class TestRiskManagerStub:
    def test_validate_returns_instruction(self, mock_api, sample_signal):
        from core.data_provider import DataProvider
        from core.logger import Logger
        from core.risk_manager import RiskManager, RiskParams
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        dp = DataProvider(mock_api, ["EURUSD"], logger)
        params = RiskParams(
            risk_per_trade_pct=1.0,
            max_daily_drawdown_pct=3.0,
            max_total_drawdown_pct=10.0,
            max_concurrent_positions=5,
            max_positions_per_symbol=1,
            max_spread_pips=3.0,
            trailing_stop_enabled=True,
            trailing_stop_trigger_pips=15.0,
            trailing_stop_distance_pips=10.0,
        )
        rm = RiskManager(mock_api, params, logger, dp)
        rm.initialize(10_000.0)
        instr = rm.validate(sample_signal)
        assert instr is not None
        assert instr.signal is sample_signal

    def test_calculate_tp_pips(self, mock_api):
        from core.data_provider import DataProvider
        from core.logger import Logger
        from core.risk_manager import RiskManager, RiskParams
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        dp = DataProvider(mock_api, ["EURUSD"], logger)
        params = RiskParams(1.0, 3.0, 10.0, 5, 1, 3.0, True, 15.0, 10.0)
        rm = RiskManager(mock_api, params, logger, dp)
        tp = rm.calculate_tp_pips(sl_pips=20.0, rr_ratio=2.0)
        assert tp == pytest.approx(40.0)

    def test_drawdown_ok_by_default(self, mock_api):
        from core.data_provider import DataProvider
        from core.logger import Logger
        from core.risk_manager import RiskManager, RiskParams
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        dp = DataProvider(mock_api, ["EURUSD"], logger)
        params = RiskParams(1.0, 3.0, 10.0, 5, 1, 3.0, True, 15.0, 10.0)
        rm = RiskManager(mock_api, params, logger, dp)
        rm.initialize(10_000.0)
        assert rm.check_drawdown_limits() == DrawdownStatus.OK
