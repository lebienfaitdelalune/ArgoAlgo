"""
test_phase2.py
Phase 2 tests: TradingBot full lifecycle, Logger structured methods,
session/day-of-week filtering, halt logic, event handlers.
No cTrader API required — uses a mock API object.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import BotStatus, Direction, DrawdownStatus, LogLevel
from models.trade_signal import TradeSignal
from models.performance import PerformanceSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_api():
    """Fully-mocked cTrader API with server time and account info."""
    api = MagicMock()
    api.Account.Balance = 10_000.0
    api.Account.Equity = 10_050.0
    api.Print = MagicMock()
    api.Positions = MagicMock()
    api.Positions.__iter__ = MagicMock(return_value=iter([]))
    api.PendingOrders = MagicMock()
    # Default: Tuesday 10:00 UTC (within session)
    api.Server.Time.Hour = 10
    api.Server.Time.DayOfWeek.ToString.return_value = "Tuesday"
    return api


@pytest.fixture
def started_bot(mock_api):
    """TradingBot instance that has completed on_start."""
    from main import TradingBot
    bot = TradingBot(api=mock_api)
    bot.on_start()
    mock_api.Print.reset_mock()  # clear startup noise
    return bot


@pytest.fixture
def sample_signal():
    return TradeSignal(
        strategy_name="TrendFollowing",
        symbol="EURUSD",
        direction=Direction.BUY,
        stop_loss_pips=20.0,
        take_profit_pips=40.0,
        entry_price=1.08500,
    )


# ---------------------------------------------------------------------------
# Logger — structured methods
# ---------------------------------------------------------------------------

class TestLoggerStructuredMethods:
    def test_trade_entry_logs_symbol_and_direction(self, mock_api, sample_signal):
        from core.logger import Logger
        from models.trade_instruction import TradeInstruction
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        instr = TradeInstruction(signal=sample_signal, volume_units=5000, validated=True)
        logger.trade_entry(instr, result=None)
        call_arg = mock_api.Print.call_args[0][0]
        assert "EURUSD" in call_arg
        assert "Buy" in call_arg
        assert "vol=5000" in call_arg

    def test_trade_entry_logs_sl_and_tp(self, mock_api, sample_signal):
        from core.logger import Logger
        from models.trade_instruction import TradeInstruction
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        instr = TradeInstruction(signal=sample_signal, volume_units=5000, validated=True)
        logger.trade_entry(instr, result=None)
        call_arg = mock_api.Print.call_args[0][0]
        assert "sl=20.0pips" in call_arg
        assert "tp=40.0pips" in call_arg

    def test_trade_exit_logs_pnl_and_reason(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        pos = MagicMock()
        pos.SymbolName = "GBPUSD"
        pos.NetProfit = 75.50
        logger.trade_exit(pos, reason="SL hit")
        call_arg = mock_api.Print.call_args[0][0]
        assert "GBPUSD" in call_arg
        assert "75.50" in call_arg
        assert "SL hit" in call_arg

    def test_risk_action_prefixes_risk(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        logger.risk_action("Max drawdown approaching")
        call_arg = mock_api.Print.call_args[0][0]
        assert "RISK" in call_arg  # format is [RISK ] with padding
        assert "Max drawdown approaching" in call_arg

    def test_daily_summary_includes_key_fields(self, mock_api):
        from core.logger import Logger
        logger = Logger(mock_api, LogLevel.INFO, False, "ArgoAlgo")
        snap = PerformanceSnapshot(
            timestamp=datetime(2025, 3, 10, 23, 59, 0),
            balance=10_200.0,
            equity=10_250.0,
            open_positions=0,
            daily_pnl=200.0,
            daily_drawdown_pct=0.5,
            total_drawdown_pct=0.5,
            trade_count_today=4,
        )
        logger.daily_summary(snap)
        all_output = " ".join(str(c) for c in mock_api.Print.call_args_list)
        assert "2025-03-10" in all_output
        assert "200" in all_output       # daily_pnl
        assert "10200" in all_output     # balance

    def test_risk_action_always_emitted_even_at_warning_level(self, mock_api):
        from core.logger import Logger
        # risk_action uses _emit directly — always written regardless of level
        logger = Logger(mock_api, LogLevel.WARNING, False, "ArgoAlgo")
        logger.risk_action("Trailing stop moved")
        assert mock_api.Print.called


# ---------------------------------------------------------------------------
# TradingBot on_stop
# ---------------------------------------------------------------------------

class TestOnStop:
    def test_on_stop_logs_stopping(self, started_bot, mock_api):
        started_bot.on_stop()
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("stopping" in c.lower() for c in calls)

    def test_on_stop_logs_stopped(self, started_bot, mock_api):
        started_bot.on_stop()
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("stopped" in c.lower() for c in calls)

    def test_on_stop_sets_ui_to_stopped(self, started_bot):
        started_bot._ui_panel = MagicMock()
        started_bot.on_stop()
        started_bot._ui_panel.set_status.assert_called_with(BotStatus.STOPPED)

    def test_on_stop_logs_daily_summary(self, started_bot, mock_api):
        started_bot.on_stop()
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("DAILY SUMMARY" in c for c in calls)

    def test_on_stop_logs_final_balance(self, started_bot, mock_api):
        started_bot.on_stop()
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("balance" in c.lower() for c in calls)

    def test_on_stop_no_friday_close_when_disabled(self, started_bot):
        mock_executor = MagicMock()
        started_bot._order_executor = mock_executor
        started_bot.FridayCloseEnabled = False
        started_bot.on_stop()
        mock_executor.close_all_positions.assert_not_called()

    def test_on_stop_friday_close_calls_close_all(self, mock_api):
        """Friday close during on_stop triggers close_all_positions."""
        from main import TradingBot
        mock_api.Server.Time.DayOfWeek.ToString.return_value = "Friday"
        mock_api.Server.Time.Hour = 22
        bot = TradingBot(api=mock_api)
        bot.on_start()
        mock_executor = MagicMock()
        bot._order_executor = mock_executor
        bot.FridayCloseEnabled = True
        bot.FridayCloseHourUTC = 20
        bot.EnableXsect = False  # xsect mode skips Friday close by design
        bot.on_stop()
        mock_executor.close_all_positions.assert_called_with("Friday end-of-week close")

    def test_on_stop_no_friday_close_outside_hours(self, mock_api):
        from main import TradingBot
        mock_api.Server.Time.DayOfWeek.ToString.return_value = "Friday"
        mock_api.Server.Time.Hour = 15  # before close hour
        bot = TradingBot(api=mock_api)
        bot.on_start()
        mock_executor = MagicMock()
        bot._order_executor = mock_executor
        bot.FridayCloseEnabled = True
        bot.FridayCloseHourUTC = 20
        bot.on_stop()
        mock_executor.close_all_positions.assert_not_called()

    def test_on_stop_works_without_logger(self, mock_api):
        """on_stop must not raise when called before on_start."""
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        # No on_start — _logger is None
        bot.on_stop()  # Should not raise


# ---------------------------------------------------------------------------
# TradingBot on_error
# ---------------------------------------------------------------------------

class TestOnError:
    def test_on_error_logs_error(self, started_bot, mock_api):
        started_bot.on_error("Connection lost")
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("ERROR" in c for c in calls)

    def test_on_error_sends_push_notification(self, started_bot, mock_api):
        started_bot.on_error("Connection lost")
        mock_api.Notifications.SendPushNotification.assert_called()

    def test_on_error_does_not_raise(self, started_bot, mock_api):
        mock_api.Notifications.SendPushNotification.side_effect = RuntimeError("no network")
        started_bot.on_error("Some error")  # Should not raise

    def test_on_error_works_without_logger(self, mock_api):
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_error("early error")  # Should not raise


# ---------------------------------------------------------------------------
# Halt trading
# ---------------------------------------------------------------------------

class TestHaltTrading:
    def test_halt_sets_flag(self, started_bot):
        started_bot._halt_trading("test")
        assert started_bot._is_halted is True

    def test_halt_logs_risk_action(self, started_bot, mock_api):
        started_bot._halt_trading("drawdown limit")
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("HALTED" in c and "drawdown limit" in c for c in calls)

    def test_halt_sends_push_notification(self, started_bot, mock_api):
        started_bot._halt_trading("drawdown limit")
        mock_api.Notifications.SendPushNotification.assert_called()
        call_arg = mock_api.Notifications.SendPushNotification.call_args[0][0]
        assert "HALTED" in call_arg

    def test_halt_updates_ui_panel(self, started_bot):
        started_bot._ui_panel = MagicMock()
        started_bot._halt_trading("test")
        started_bot._ui_panel.set_status.assert_called_with(BotStatus.HALTED)

    def test_halt_closes_all_positions(self, started_bot):
        started_bot._order_executor = MagicMock()
        started_bot._halt_trading("drawdown limit")
        started_bot._order_executor.close_all_positions.assert_called_once()

    def test_halt_close_all_failure_does_not_raise(self, started_bot):
        started_bot._order_executor = MagicMock()
        started_bot._order_executor.close_all_positions.side_effect = RuntimeError("api down")
        started_bot._halt_trading("test")  # Should not raise
        assert started_bot._is_halted is True

    def test_halt_push_notification_failure_does_not_raise(self, started_bot, mock_api):
        mock_api.Notifications.SendPushNotification.side_effect = RuntimeError("offline")
        started_bot._halt_trading("test")  # Should not raise

    def test_halted_bot_skips_on_bar_closed(self, started_bot):
        # Permanent halt (e.g. total drawdown or panic) skips signal generation
        # but still runs day-rollover and drawdown checks.
        started_bot._is_halted = True
        started_bot._permanent_halt = True
        started_bot._strategy_engine = MagicMock()
        started_bot.on_bar_closed()
        started_bot._strategy_engine.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------

def _set_bar_time(bot, hour: int, day: str) -> None:
    """Configure the bot's data_provider mock to return a specific bar time."""
    mock_bars = MagicMock()
    mock_bars.OpenTimes.LastValue.Hour = hour
    mock_bars.OpenTimes.LastValue.DayOfWeek.ToString.return_value = day
    bot._data_provider.get_bars = MagicMock(return_value=mock_bars)


class TestSessionFilter:
    def test_within_session_returns_true(self, started_bot):
        _set_bar_time(started_bot, 12, "Wednesday")
        assert started_bot._is_session_active() is True

    def test_before_start_hour_returns_false(self, started_bot):
        started_bot.TradingStartHourUTC = 7
        started_bot.TradingEndHourUTC = 20
        _set_bar_time(started_bot, 3, "Wednesday")  # before TradingStartHourUTC=7
        assert started_bot._is_session_active() is False

    def test_after_end_hour_returns_false(self, started_bot):
        started_bot.TradingStartHourUTC = 7
        started_bot.TradingEndHourUTC = 20
        _set_bar_time(started_bot, 21, "Wednesday")  # after TradingEndHourUTC=20
        assert started_bot._is_session_active() is False

    def test_weekend_returns_false(self, started_bot):
        _set_bar_time(started_bot, 12, "Saturday")
        assert started_bot._is_session_active() is False

    def test_friday_close_triggers_halt(self, started_bot):
        # Hour 17 is within trading session (7–20) but past FridayCloseHourUTC=16
        _set_bar_time(started_bot, 17, "Friday")
        started_bot.FridayCloseEnabled = True
        started_bot.FridayCloseHourUTC = 16
        started_bot._ui_panel = MagicMock()  # avoid real UIPanel.set_status
        result = started_bot._is_session_active()
        assert result is False
        assert started_bot._is_halted is False  # Friday close no longer permanently halts

    def test_friday_close_calls_close_all_positions(self, started_bot):
        _set_bar_time(started_bot, 17, "Friday")
        started_bot.FridayCloseEnabled = True
        started_bot.FridayCloseHourUTC = 16
        mock_executor = MagicMock()
        started_bot._order_executor = mock_executor
        started_bot._ui_panel = MagicMock()
        started_bot._is_session_active()
        mock_executor.close_all_positions.assert_called_with("Friday end-of-week close")

    def test_friday_before_close_hour_is_active(self, started_bot):
        # Friday hour 9 is before FridayCloseHourUTC (12) and within session (7-13)
        _set_bar_time(started_bot, 9, "Friday")
        started_bot.FridayCloseEnabled = True
        started_bot.FridayCloseHourUTC = 12
        started_bot.TradingStartHourUTC = 7
        started_bot.TradingEndHourUTC = 13
        assert started_bot._is_session_active() is True
        assert started_bot._is_halted is False

    def test_friday_close_fires_even_when_after_session_end(self, started_bot):
        """Regression: Friday close must run BEFORE the session-hour check.

        Previously the Friday close branch was unreachable because
        is_within_trading_hours returned False first, letting positions
        carry over the weekend. The 10/04–12/04 weekend gap loss was
        caused by this bug.
        """
        _set_bar_time(started_bot, 14, "Friday")  # past 13h session end
        started_bot.FridayCloseEnabled = True
        started_bot.FridayCloseHourUTC = 12
        started_bot.TradingStartHourUTC = 7
        started_bot.TradingEndHourUTC = 13
        mock_executor = MagicMock()
        started_bot._order_executor = mock_executor
        assert started_bot._is_session_active() is False
        mock_executor.close_all_positions.assert_called_with("Friday end-of-week close")

    def test_session_filter_blocks_on_bar_closed(self, started_bot):
        started_bot.TradingStartHourUTC = 7
        started_bot.TradingEndHourUTC = 20
        _set_bar_time(started_bot, 2, "Wednesday")  # outside session
        strategy_engine = MagicMock()
        started_bot._strategy_engine = strategy_engine
        started_bot.on_bar_closed()
        strategy_engine.evaluate.assert_not_called()

    def test_no_api_session_returns_true(self):
        """When api is None (test environment), session is always open."""
        from main import TradingBot
        bot = TradingBot(api=None)
        assert bot._is_session_active() is True

    def test_api_time_failure_returns_true(self, started_bot):
        """If bar time read throws, fail open."""
        started_bot._data_provider.get_bars = MagicMock(side_effect=RuntimeError("no bars"))
        result = started_bot._is_session_active()
        assert result is True


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

class TestEventHandlers:
    def test_position_opened_logs_symbol(self, started_bot, mock_api):
        pos = MagicMock()
        pos.SymbolName = "EURUSD"
        pos.TradeType = "Buy"
        pos.VolumeInUnits = 10000
        pos.EntryPrice = 1.08500
        args = MagicMock()
        args.Position = pos
        started_bot._on_position_opened(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("EURUSD" in c and "Buy" in c for c in calls)

    def test_position_opened_logs_volume_and_entry(self, started_bot, mock_api):
        pos = MagicMock()
        pos.SymbolName = "GBPUSD"
        pos.TradeType = "Sell"
        pos.VolumeInUnits = 5000
        pos.EntryPrice = 1.25500
        args = MagicMock()
        args.Position = pos
        started_bot._on_position_opened(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("5000" in c for c in calls)
        assert any("1.25500" in c for c in calls)

    def test_position_closed_logs_pnl(self, started_bot, mock_api):
        pos = MagicMock()
        pos.SymbolName = "EURUSD"
        pos.NetProfit = -42.50
        pos.Label = "ArgoAlgo_TF_EURUSD"
        args = MagicMock()
        args.Position = pos
        started_bot._on_position_closed(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("-42.50" in c for c in calls)

    def test_position_modified_logs_id(self, mock_api):
        """_on_position_modified logs at DEBUG level — verify with DEBUG logger."""
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        bot._logger._log_level = LogLevel.DEBUG
        mock_api.Print.reset_mock()
        pos = MagicMock()
        pos.Id = 12345
        pos.StopLoss = 1.08000
        pos.TakeProfit = 1.09500
        args = MagicMock()
        args.Position = pos
        bot._on_position_modified(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("12345" in c for c in calls)

    def test_pending_order_created_logs(self, mock_api):
        """_on_pending_order_created logs at DEBUG level — verify with DEBUG logger."""
        from main import TradingBot
        bot = TradingBot(api=mock_api)
        bot.on_start()
        bot._logger._log_level = LogLevel.DEBUG
        mock_api.Print.reset_mock()
        order = MagicMock()
        order.Id = 99
        order.SymbolName = "USDJPY"
        args = MagicMock()
        args.PendingOrder = order
        bot._on_pending_order_created(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("99" in c and "USDJPY" in c for c in calls)

    def test_pending_order_filled_logs(self, started_bot, mock_api):
        order = MagicMock()
        order.Id = 100
        order.SymbolName = "EURUSD"
        args = MagicMock()
        args.PendingOrder = order
        started_bot._on_pending_order_filled(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("100" in c for c in calls)

    def test_pending_order_cancelled_logs(self, started_bot, mock_api):
        order = MagicMock()
        order.Id = 101
        order.SymbolName = "GBPUSD"
        args = MagicMock()
        args.PendingOrder = order
        started_bot._on_pending_order_cancelled(args)
        calls = [str(c) for c in mock_api.Print.call_args_list]
        assert any("101" in c for c in calls)


# ---------------------------------------------------------------------------
# on_tick
# ---------------------------------------------------------------------------

class TestOnTick:
    def test_on_tick_skips_when_halted(self, started_bot):
        started_bot._is_halted = True
        rm = MagicMock()
        started_bot._risk_manager = rm
        started_bot.on_tick()
        rm.update_trailing_stops.assert_not_called()

    def test_on_tick_calls_trailing_stops_when_enabled(self, started_bot):
        started_bot.TrailingStopEnabled = True
        started_bot.EnableXsect = False  # xsect mode disables trailing by design
        rm = MagicMock()
        started_bot._risk_manager = rm
        started_bot.on_tick()
        rm.update_trailing_stops.assert_called_once()

    def test_on_tick_skips_trailing_stops_when_disabled(self, started_bot):
        started_bot.TrailingStopEnabled = False
        rm = MagicMock()
        started_bot._risk_manager = rm
        started_bot.on_tick()
        rm.update_trailing_stops.assert_not_called()


# ---------------------------------------------------------------------------
# Performance snapshot
# ---------------------------------------------------------------------------

class TestBuildPerformanceSnapshot:
    def test_snapshot_uses_account_balance(self, started_bot, mock_api):
        mock_api.Account.Balance = 12_500.0
        mock_api.Account.Equity = 12_600.0
        snap = started_bot._build_performance_snapshot()
        assert snap.balance == 12_500.0
        assert snap.equity == 12_600.0

    def test_snapshot_timestamp_is_recent(self, started_bot):
        before = datetime.utcnow()
        snap = started_bot._build_performance_snapshot()
        after = datetime.utcnow()
        assert before <= snap.timestamp <= after

    def test_snapshot_stub_fields_are_zero(self, started_bot):
        snap = started_bot._build_performance_snapshot()
        assert snap.daily_pnl == 0.0
        assert snap.open_positions == 0
        assert snap.trade_count_today == 0
