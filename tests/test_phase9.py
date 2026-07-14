"""
test_phase9.py
Phase 9 tests: Integration & Demo Account Validation.

Exercises the full pipeline — TradingBot.on_start → on_bar_closed → on_stop —
with mocked cTrader API objects to simulate realistic demo-account scenarios.
No real cTrader API is required.

Coverage:
  - AC-001  All three strategies registered and enabled
  - AC-002  Risk enforcement (daily / total drawdown halt)
  - AC-003  Session / day-of-week / Friday filters
  - AC-004  Startup banner logged on on_start
  - AC-005  Panic button: closes positions and halts bot
  - AC-006  Spread spike: high spread suppresses signal execution
  - AC-007  ADX-switching mode routes to correct strategy family
  - AC-008  Full pipeline survives 10 consecutive bar-close cycles
            without raising an exception (crash-free run)
  - Misc    Multiple symbols processed per bar; label format validation;
            trailing-stop tick handler; graceful error recovery.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import (
    BOT_VERSION,
    BotStatus,
    Defaults,
    Direction,
    DrawdownStatus,
    LogLevel,
    StrategyMode,
)
from models.trade_signal import TradeSignal
from models.trade_instruction import TradeInstruction


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_api(
    balance: float = 10_000.0,
    equity: float = 10_000.0,
    hour: int = 10,
    day: str = "Tuesday",
) -> MagicMock:
    """Return a fully-mocked cTrader API suitable for integration tests."""
    api = MagicMock()
    api.Account.Balance = balance
    api.Account.Equity = equity
    api.Print = MagicMock()
    api.Positions = MagicMock()
    api.Positions.__iter__ = MagicMock(return_value=iter([]))
    api.Positions.Count = 0
    api.PendingOrders = MagicMock()
    api.Server.Time.Hour = hour
    api.Server.Time.Date = "2025-03-10"
    api.Server.Time.DayOfWeek.ToString.return_value = day
    api.TimeFrame.Minute15 = "M15"
    api.TimeFrame.Hour4 = "H4"
    api.Chart = MagicMock()
    api.Notifications = MagicMock()
    return api


def _make_bot(api=None, **param_overrides):
    """Construct a TradingBot with optional parameter overrides, ready for on_start."""
    from main import TradingBot
    if api is None:
        api = _make_api()
    bot = TradingBot(api=api)
    # These tests exercise the legacy bar-signal pipeline; xsect mode bypasses it.
    param_overrides.setdefault("EnableXsect", False)
    for key, value in param_overrides.items():
        setattr(bot, key, value)
    return bot


def _started_bot(api=None, **param_overrides):
    """Return a TradingBot that has already completed on_start."""
    bot = _make_bot(api=api, **param_overrides)
    bot.on_start()
    if api:
        api.Print.reset_mock()  # suppress startup noise
    return bot


def _no_signal(symbol: str = "EURUSD") -> TradeSignal:
    return TradeSignal(
        strategy_name="TrendFollowing",
        symbol=symbol,
        direction=Direction.NONE,
        stop_loss_pips=0.0,
        take_profit_pips=0.0,
        entry_price=0.0,
    )


def _set_bar_time(bot, hour: int, day: str) -> None:
    """Mock the data_provider to return a specific UTC bar time for session filtering."""
    mock_bars = MagicMock()
    mock_bars.OpenTimes.LastValue.Hour = hour
    mock_bars.OpenTimes.LastValue.DayOfWeek.ToString.return_value = day
    bot._data_provider.get_bars = MagicMock(return_value=mock_bars)


def _buy_signal(symbol: str = "EURUSD", strategy: str = "TrendFollowing") -> TradeSignal:
    return TradeSignal(
        strategy_name=strategy,
        symbol=symbol,
        direction=Direction.BUY,
        stop_loss_pips=20.0,
        take_profit_pips=40.0,
        entry_price=1.08500,
    )


# ---------------------------------------------------------------------------
# AC-004: Startup banner
# ---------------------------------------------------------------------------

class TestStartupBanner:
    """AC-004: on_start must emit a recognisable startup banner."""

    def test_banner_logged_on_start(self):
        api = _make_api()
        bot = _make_bot(api=api)
        bot.on_start()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "ArgoAlgo" in all_output

    def test_banner_contains_version(self):
        api = _make_api()
        bot = _make_bot(api=api)
        bot.on_start()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert BOT_VERSION in all_output

    def test_banner_contains_risk_percent(self):
        api = _make_api()
        bot = _make_bot(api=api, RiskPerTradePercent=1.0)
        bot.on_start()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "1.0" in all_output

    def test_banner_contains_symbols(self):
        api = _make_api()
        bot = _make_bot(api=api, TradedSymbols="EURUSD,GBPUSD")
        bot.on_start()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "EURUSD" in all_output

    def test_bot_started_message_after_banner(self):
        api = _make_api()
        bot = _make_bot(api=api)
        bot.on_start()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "started" in all_output.lower()


# ---------------------------------------------------------------------------
# AC-001: All three strategies registered
# ---------------------------------------------------------------------------

class TestAllStrategiesRegistered:
    """AC-001: TrendFollowing, MeanReversion, and Breakout must all be available."""

    def test_all_three_strategies_in_engine(self):
        bot = _started_bot(EnableTrend=True, EnableMeanReversion=True, EnableBreakout=True)
        names = bot._strategy_engine.strategy_names
        assert "TrendFollowing" in names
        assert "MeanReversion" in names
        assert "Breakout" in names

    def test_only_trend_when_others_disabled(self):
        bot = _started_bot(EnableTrend=True, EnableMeanReversion=False, EnableBreakout=False)
        names = bot._strategy_engine.strategy_names
        assert "TrendFollowing" in names
        assert "MeanReversion" not in names
        assert "Breakout" not in names

    def test_strategy_engine_is_not_none_after_start(self):
        bot = _started_bot()
        assert bot._strategy_engine is not None

    def test_risk_manager_initialized_after_start(self):
        bot = _started_bot()
        assert bot._risk_manager is not None

    def test_order_executor_initialized_after_start(self):
        bot = _started_bot()
        assert bot._order_executor is not None

    def test_data_provider_initialized_after_start(self):
        bot = _started_bot()
        assert bot._data_provider is not None

    def test_ui_panel_initialized_after_start(self):
        bot = _started_bot()
        assert bot._ui_panel is not None


# ---------------------------------------------------------------------------
# AC-002: Risk enforcement — daily drawdown halt
# ---------------------------------------------------------------------------

class TestDailyDrawdownHalt:
    """AC-002: Bot halts trading when daily drawdown limit is breached."""

    def test_bot_halts_on_daily_drawdown(self):
        bot = _started_bot()
        bot._risk_manager.check_drawdown_limits = MagicMock(
            return_value=DrawdownStatus.DAILY_LIMIT_BREACHED
        )
        bot.on_bar_closed()
        assert bot._is_halted is True

    def test_halt_sets_ui_status(self):
        bot = _started_bot()
        bot._risk_manager.check_drawdown_limits = MagicMock(
            return_value=DrawdownStatus.DAILY_LIMIT_BREACHED
        )
        bot.on_bar_closed()
        assert bot._ui_panel._status == BotStatus.HALTED

    def test_halt_sends_push_notification(self):
        api = _make_api()
        bot = _started_bot(api=api)
        bot._risk_manager.check_drawdown_limits = MagicMock(
            return_value=DrawdownStatus.DAILY_LIMIT_BREACHED
        )
        bot.on_bar_closed()
        api.Notifications.SendPushNotification.assert_called()

    def test_no_signals_evaluated_after_halt(self):
        bot = _started_bot()
        bot._risk_manager.check_drawdown_limits = MagicMock(
            return_value=DrawdownStatus.DAILY_LIMIT_BREACHED
        )
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot.on_bar_closed()
        bot._strategy_engine.evaluate.assert_not_called()

    def test_subsequent_bar_skipped_when_halted(self):
        # Day-rollover and drawdown checks run even when halted (needed to un-halt).
        # Signal generation is skipped for a permanent halt.
        bot = _started_bot()
        bot._is_halted = True
        bot._permanent_halt = True
        bot._risk_manager.check_drawdown_limits = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot.on_bar_closed()
        bot._risk_manager.check_drawdown_limits.assert_called_once()
        bot._strategy_engine.evaluate.assert_not_called()

    def test_total_drawdown_also_halts(self):
        bot = _started_bot()
        bot._risk_manager.check_drawdown_limits = MagicMock(
            return_value=DrawdownStatus.TOTAL_LIMIT_BREACHED
        )
        bot.on_bar_closed()
        assert bot._is_halted is True

    def test_ok_drawdown_does_not_halt(self):
        bot = _started_bot()
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._data_provider.update = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot.on_bar_closed()
        assert bot._is_halted is False


# ---------------------------------------------------------------------------
# AC-003: Session filters
# ---------------------------------------------------------------------------

class TestSessionFilters:
    """AC-003: Session / day-of-week / Friday close filters prevent off-hours trading."""

    def test_outside_session_hours_skips_evaluate(self):
        bot = _started_bot(TradingStartHourUTC=7, TradingEndHourUTC=20)
        _set_bar_time(bot, 3, "Wednesday")  # 03:00 UTC — before session start
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot.on_bar_closed()
        bot._strategy_engine.evaluate.assert_not_called()

    def test_within_session_hours_runs_evaluate(self):
        bot = _started_bot(TradingStartHourUTC=7, TradingEndHourUTC=20)
        _set_bar_time(bot, 10, "Wednesday")  # 10:00 UTC — within session
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._data_provider.update = MagicMock()
        bot.on_bar_closed()
        bot._strategy_engine.evaluate.assert_called_once()

    def test_friday_close_halts_and_closes_positions(self):
        bot = _started_bot(
            FridayCloseEnabled=True,
            FridayCloseHourUTC=20,
            TradingStartHourUTC=7,
            TradingEndHourUTC=23,
        )
        _set_bar_time(bot, 21, "Friday")  # 21:00 UTC Friday — past close hour
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._order_executor.close_all_positions = MagicMock(return_value=0)
        bot._data_provider.update = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot.on_bar_closed()
        # Friday close should call close_all_positions but NOT permanently halt
        bot._order_executor.close_all_positions.assert_called()
        assert bot._is_halted is False  # Trading resumes Monday automatically

    def test_friday_before_close_hour_does_not_halt(self):
        bot = _started_bot(
            FridayCloseEnabled=True,
            FridayCloseHourUTC=20,
            TradingStartHourUTC=7,
            TradingEndHourUTC=23,
        )
        _set_bar_time(bot, 12, "Friday")  # 12:00 UTC — before Friday close
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._data_provider.update = MagicMock()
        bot.on_bar_closed()
        assert bot._is_halted is False

    def test_saturday_is_not_a_trading_day(self):
        bot = _started_bot(TradeDaysOfWeek="Mon,Tue,Wed,Thu,Fri")
        _set_bar_time(bot, 10, "Saturday")
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot.on_bar_closed()
        bot._strategy_engine.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# AC-005: Panic button integration
# ---------------------------------------------------------------------------

class TestPanicButton:
    """AC-005: Panic button closes all positions and halts the bot."""

    def test_panic_halts_bot(self):
        bot = _started_bot()
        bot._order_executor.close_all_positions = MagicMock(return_value=2)
        bot._ui_panel._on_panic_clicked()
        assert bot._is_halted is True

    def test_panic_closes_positions(self):
        bot = _started_bot()
        bot._order_executor.close_all_positions = MagicMock(return_value=2)
        bot._ui_panel._on_panic_clicked()
        bot._order_executor.close_all_positions.assert_called_once_with("PANIC")

    def test_panic_sets_panel_status_to_halted(self):
        bot = _started_bot()
        bot._order_executor.close_all_positions = MagicMock(return_value=0)
        bot._ui_panel._on_panic_clicked()
        assert bot._ui_panel._status == BotStatus.HALTED

    def test_panic_logs_warning(self):
        api = _make_api()
        bot = _started_bot(api=api)
        bot._order_executor.close_all_positions = MagicMock(return_value=1)
        api.Print.reset_mock()
        bot._ui_panel._on_panic_clicked()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        # Warning must be printed (WARN or WARNING in log output)
        assert any(word in all_output.upper() for word in ("WARN", "PANIC"))

    def test_on_bar_closed_skipped_after_panic(self):
        # Panic button triggers a permanent halt (no auto-resume next day).
        bot = _started_bot()
        bot._order_executor.close_all_positions = MagicMock(return_value=0)
        bot._ui_panel._on_panic_clicked()
        assert bot._permanent_halt is True
        bot._risk_manager.check_drawdown_limits = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot.on_bar_closed()
        # Drawdown check still runs, but signal generation is skipped
        bot._risk_manager.check_drawdown_limits.assert_called_once()
        bot._strategy_engine.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# AC-006: Spread spike — high spread suppresses execution
# ---------------------------------------------------------------------------

class TestSpreadSpike:
    """AC-006: When spread exceeds max_spread_pips, validate() must reject the signal."""

    def _rm_with_wide_spread(self, bot):
        """Inject a wide spread into the bot's DataProvider."""
        bot._data_provider.get_spread_pips = MagicMock(return_value=10.0)
        bot._data_provider.is_spread_acceptable = MagicMock(return_value=False)

    def test_wide_spread_rejects_validate(self):
        bot = _started_bot()
        self._rm_with_wide_spread(bot)
        signal = _buy_signal()
        instruction = bot._risk_manager.validate(signal)
        assert instruction.validated is False

    def test_normal_spread_accepts_validate(self):
        bot = _started_bot()
        bot._data_provider.get_spread_pips = MagicMock(return_value=1.0)
        bot._data_provider.is_spread_acceptable = MagicMock(return_value=True)
        # Patch position count so other checks pass
        bot._api.Positions.__iter__ = MagicMock(return_value=iter([]))
        bot._api.Account.Balance = 10_000.0
        bot._api.Account.Equity = 10_000.0
        signal = _buy_signal()
        sym = MagicMock()
        sym.PipValue = 0.0001
        sym.PipSize = 0.0001
        sym.VolumeInUnitsMin = 1000
        sym.VolumeInUnitsMax = 10_000_000
        sym.VolumeInUnitsStep = 1000
        sym.Bid = 1.08000
        sym.Ask = 1.08010
        sym.MinStopLossInPips = 0.0
        bot._data_provider.get_symbol = MagicMock(return_value=sym)
        instruction = bot._risk_manager.validate(signal)
        assert instruction.validated is True

    def test_spread_check_logs_warning(self):
        api = _make_api()
        bot = _started_bot(api=api)
        self._rm_with_wide_spread(bot)
        api.Print.reset_mock()
        signal = _buy_signal()
        bot._risk_manager.validate(signal)
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert any(word in all_output.upper() for word in ("SPREAD", "WARN"))


# ---------------------------------------------------------------------------
# AC-007: ADX-switching mode
# ---------------------------------------------------------------------------

class TestAdxSwitching:
    """AC-007: ADX-switching mode routes signals to the correct strategy family."""

    def test_adx_mode_set_correctly(self):
        bot = _started_bot(
            StrategyMode=StrategyMode.ADX_SWITCHING.value,
            EnableTrend=True,
            EnableMeanReversion=True,
        )
        assert bot._strategy_engine._mode == StrategyMode.ADX_SWITCHING

    def test_manual_mode_set_correctly(self):
        bot = _started_bot(StrategyMode=StrategyMode.MANUAL.value)
        assert bot._strategy_engine._mode == StrategyMode.MANUAL

    def test_adx_threshold_stored_on_engine(self):
        bot = _started_bot(TF_AdxThreshold=30.0)
        assert bot._strategy_engine._adx_threshold == 30.0

    def test_engine_evaluate_called_per_symbol(self):
        bot = _started_bot(TradedSymbols="EURUSD,GBPUSD")
        _set_bar_time(bot, hour=10, day="Tuesday")
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._data_provider.update = MagicMock()
        bot.on_bar_closed()
        # evaluate is called once with the full symbol list
        bot._strategy_engine.evaluate.assert_called_once()
        call_args = bot._strategy_engine.evaluate.call_args[0][0]
        assert "EURUSD" in call_args
        assert "GBPUSD" in call_args


# ---------------------------------------------------------------------------
# AC-008: Crash-free run — 10 consecutive bar closes
# ---------------------------------------------------------------------------

class TestCrashFreeRun:
    """AC-008: Full pipeline must complete 10 bar-close cycles without raising."""

    def _setup_bot_for_run(self, bar_count: int = 10):
        api = _make_api(hour=10, day="Tuesday")
        bot = _started_bot(api=api)
        _set_bar_time(bot, hour=10, day="Tuesday")
        # Stub out heavier subsystems so the test runs fast
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._data_provider.update = MagicMock()
        return bot

    def test_ten_bar_closes_no_exception(self):
        bot = self._setup_bot_for_run()
        for _ in range(10):
            bot.on_bar_closed()  # Must not raise

    def test_ten_ticks_no_exception(self):
        bot = self._setup_bot_for_run()
        bot._risk_manager.update_trailing_stops = MagicMock()
        for _ in range(10):
            bot.on_tick()  # Must not raise

    def test_on_stop_no_exception_after_run(self):
        bot = self._setup_bot_for_run()
        for _ in range(5):
            bot.on_bar_closed()
        bot.on_stop()  # Must not raise

    def test_evaluate_called_each_bar(self):
        bot = self._setup_bot_for_run()
        for _ in range(10):
            bot.on_bar_closed()
        assert bot._strategy_engine.evaluate.call_count == 10

    def test_data_provider_updated_each_bar(self):
        bot = self._setup_bot_for_run()
        for _ in range(10):
            bot.on_bar_closed()
        assert bot._data_provider.update.call_count == 10

    def test_signal_validated_and_executed(self):
        """When a valid signal is returned, it passes through validate → execute."""
        api = _make_api(hour=10, day="Tuesday")
        bot = _started_bot(api=api)
        _set_bar_time(bot, hour=10, day="Tuesday")
        signal = _buy_signal()
        instruction = MagicMock(spec=TradeInstruction)
        instruction.validated = True
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._risk_manager.validate = MagicMock(return_value=instruction)
        bot._strategy_engine.evaluate = MagicMock(return_value=[signal])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._order_executor.execute = MagicMock()
        bot._data_provider.update = MagicMock()
        bot.on_bar_closed()
        bot._order_executor.execute.assert_called_once_with(instruction)

    def test_invalid_instruction_not_executed(self):
        """When validate returns validated=False, execute must NOT be called."""
        api = _make_api(hour=10, day="Tuesday")
        bot = _started_bot(api=api)
        signal = _buy_signal()
        instruction = MagicMock(spec=TradeInstruction)
        instruction.validated = False
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._risk_manager.validate = MagicMock(return_value=instruction)
        bot._strategy_engine.evaluate = MagicMock(return_value=[signal])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._order_executor.execute = MagicMock()
        bot._data_provider.update = MagicMock()
        bot.on_bar_closed()
        bot._order_executor.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Label format
# ---------------------------------------------------------------------------

class TestLabelFormat:
    """Order labels must match ArgoAlgo_{abbrev}_{symbol} pattern."""

    def test_label_prefix_default(self):
        assert Defaults.LABEL_PREFIX == "ArgoAlgo"

    def test_order_executor_uses_label_prefix(self):
        bot = _started_bot()
        assert bot._order_executor._label_prefix == Defaults.LABEL_PREFIX

    def test_tf_label(self):
        label = f"{Defaults.LABEL_PREFIX}_TF_EURUSD"
        assert label == "ArgoAlgo_TF_EURUSD"

    def test_mr_label(self):
        label = f"{Defaults.LABEL_PREFIX}_MR_GBPUSD"
        assert label == "ArgoAlgo_MR_GBPUSD"

    def test_bo_label(self):
        label = f"{Defaults.LABEL_PREFIX}_BO_USDJPY"
        assert label == "ArgoAlgo_BO_USDJPY"


# ---------------------------------------------------------------------------
# Trailing stop tick handler
# ---------------------------------------------------------------------------

class TestTrailingStopTick:
    """on_tick must delegate trailing stop management to RiskManager."""

    def test_trailing_stop_called_when_enabled(self):
        bot = _started_bot(TrailingStopEnabled=True)
        bot._risk_manager.update_trailing_stops = MagicMock()
        bot.on_tick()
        bot._risk_manager.update_trailing_stops.assert_called_once()

    def test_trailing_stop_not_called_when_disabled(self):
        bot = _started_bot(TrailingStopEnabled=False)
        bot._risk_manager.update_trailing_stops = MagicMock()
        bot.on_tick()
        bot._risk_manager.update_trailing_stops.assert_not_called()

    def test_on_tick_skipped_when_halted(self):
        bot = _started_bot(TrailingStopEnabled=True)
        bot._is_halted = True
        bot._risk_manager.update_trailing_stops = MagicMock()
        bot.on_tick()
        bot._risk_manager.update_trailing_stops.assert_not_called()


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestErrorResilience:
    """on_error must log and send a push notification without re-raising."""

    def test_on_error_does_not_raise(self):
        api = _make_api()
        bot = _started_bot(api=api)
        bot.on_error(RuntimeError("network timeout"))  # Must not raise

    def test_on_error_sends_push_notification(self):
        api = _make_api()
        bot = _started_bot(api=api)
        api.Notifications.SendPushNotification.reset_mock()
        bot.on_error("test error")
        api.Notifications.SendPushNotification.assert_called()

    def test_on_error_logs_message(self):
        api = _make_api()
        bot = _started_bot(api=api)
        api.Print.reset_mock()
        bot.on_error("test error")
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "error" in all_output.lower()


# ---------------------------------------------------------------------------
# on_stop
# ---------------------------------------------------------------------------

class TestOnStop:
    """on_stop must set panel to STOPPED and log a daily summary."""

    def test_on_stop_sets_panel_stopped(self):
        bot = _started_bot()
        bot.on_stop()
        assert bot._ui_panel._status == BotStatus.STOPPED

    def test_on_stop_logs_stopped_message(self):
        api = _make_api()
        bot = _started_bot(api=api)
        api.Print.reset_mock()
        bot.on_stop()
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "stop" in all_output.lower()

    def test_on_stop_does_not_raise_without_api(self):
        from main import TradingBot
        bot = TradingBot(api=None)
        bot.on_stop()  # No modules initialised — must not crash

    def test_repr_contains_version(self):
        bot = _started_bot()
        assert BOT_VERSION in repr(bot)


# ---------------------------------------------------------------------------
# Performance baseline comparison helpers
# ---------------------------------------------------------------------------

class TestPerformanceBaseline:
    """KPI comparison helpers used during demo monitoring (PRD §10.3)."""

    def _within_tolerance(self, actual: float, baseline: float, tolerance_pct: float) -> bool:
        """Return True if actual is within ±tolerance_pct of baseline."""
        if baseline == 0.0:
            return actual == 0.0
        deviation = abs(actual - baseline) / abs(baseline) * 100.0
        return deviation <= tolerance_pct

    def test_within_15pct_tolerance_passes(self):
        assert self._within_tolerance(actual=55.0, baseline=50.0, tolerance_pct=15.0)

    def test_outside_15pct_tolerance_fails(self):
        assert not self._within_tolerance(actual=70.0, baseline=50.0, tolerance_pct=15.0)

    def test_exact_match_passes(self):
        assert self._within_tolerance(actual=50.0, baseline=50.0, tolerance_pct=15.0)

    def test_zero_baseline_exact_zero_passes(self):
        assert self._within_tolerance(actual=0.0, baseline=0.0, tolerance_pct=15.0)

    def test_zero_baseline_nonzero_actual_fails(self):
        assert not self._within_tolerance(actual=1.0, baseline=0.0, tolerance_pct=15.0)

    def test_negative_kpi_within_tolerance(self):
        # win rate 42 vs baseline 50 → 16% off → outside 15%
        assert not self._within_tolerance(actual=42.0, baseline=50.0, tolerance_pct=15.0)

    def test_boundary_exactly_15pct(self):
        # 57.5 is exactly 15% above 50
        assert self._within_tolerance(actual=57.5, baseline=50.0, tolerance_pct=15.0)
