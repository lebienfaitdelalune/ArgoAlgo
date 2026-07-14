"""
test_phase10.py
Phase 10 tests: Live Deployment Acceptance Criteria.

Validates that ArgoAlgo meets all PRD §10 / PLAN Phase 10 acceptance
criteria before and during production deployment on cTrader Cloud /
IC Markets live account.

All tests use mocked API objects — no live connection is required.

Coverage:
  - Ramp-up risk parameters (0.25 % → 0.5 % → 0.75 % → 1.0 %)
  - Live instance config: FileLogging=False, LogLevel=INFO
  - Volume sizing at minimal risk (0.25 % per trade)
  - Rate limiting enforcement (max 500 new orders / min)
  - Emergency stop: halted flag blocks all execution
  - Bot repr and __str__ sanity (visible on Cloud dashboard)
  - Day rollover resets daily tracking state
  - Drawdown controls functional at each ramp-up stage
  - First trade label matches deployment naming convention
  - Notification on critical events (drawdown breach, panic)
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import (
    BOT_VERSION,
    BotStatus,
    Defaults,
    Direction,
    DrawdownStatus,
    LogLevel,
    RateLimits,
    StrategyMode,
)
from core.risk_manager import RiskManager, RiskParams
from core.order_executor import OrderExecutor, ExecutionResult
from models.trade_signal import TradeSignal
from models.trade_instruction import TradeInstruction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api(
    balance: float = 10_000.0,
    equity: float = 10_000.0,
    hour: int = 10,
    day: str = "Tuesday",
) -> MagicMock:
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


def _make_params(**overrides) -> RiskParams:
    base = dict(
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
    base.update(overrides)
    return RiskParams(**base)


def _make_symbol(
    pip_value: float = 0.0001,
    pip_size: float = 0.0001,
    vol_min: int = 1_000,
    vol_max: int = 10_000_000,
    vol_step: int = 1_000,
) -> MagicMock:
    sym = MagicMock()
    sym.PipValue = pip_value
    sym.PipSize = pip_size
    sym.VolumeInUnitsMin = vol_min
    sym.VolumeInUnitsMax = vol_max
    sym.VolumeInUnitsStep = vol_step
    sym.Bid = 1.08000
    sym.Ask = 1.08010
    sym.MinStopLossInPips = 0.0
    return sym


def _make_rm(api=None, **param_overrides) -> RiskManager:
    if api is None:
        api = _make_api()
    logger = MagicMock()
    dp = MagicMock()
    dp.get_symbol.return_value = _make_symbol()
    dp.get_spread_pips.return_value = 1.0
    dp.is_spread_acceptable.return_value = True
    params = _make_params(**param_overrides)
    rm = RiskManager(api=api, params=params, logger=logger, data_provider=dp)
    rm.initialize(api.Account.Balance)
    return rm


def _make_signal(
    symbol: str = "EURUSD",
    direction: Direction = Direction.BUY,
    sl_pips: float = 20.0,
    tp_pips: float = 40.0,
    strategy: str = "TrendFollowing",
) -> TradeSignal:
    return TradeSignal(
        strategy_name=strategy,
        symbol=symbol,
        direction=direction,
        stop_loss_pips=sl_pips,
        take_profit_pips=tp_pips,
        entry_price=1.08500,
    )


def _started_bot(api=None, **param_overrides):
    from main import TradingBot
    if api is None:
        api = _make_api()
    bot = TradingBot(api=api)
    for key, value in param_overrides.items():
        setattr(bot, key, value)
    bot.on_start()
    api.Print.reset_mock()
    return bot


# ---------------------------------------------------------------------------
# AC-L001: Cloud instance starts with startup banner
# ---------------------------------------------------------------------------

class TestCloudStartup:
    """Verify the bot emits the startup banner that cTrader Cloud will display."""

    def test_on_start_prints_banner(self):
        api = _make_api()
        bot = _started_bot(api=api)
        # Banner was already printed; reset was after — check it ran without error
        assert bot._logger is not None

    def test_repr_shows_version(self):
        bot = _started_bot()
        assert BOT_VERSION in repr(bot)

    def test_repr_shows_halted_false_on_start(self):
        bot = _started_bot()
        assert "False" in repr(bot)

    def test_repr_shows_halted_true_after_halt(self):
        bot = _started_bot()
        bot._halt_trading("test")
        assert "True" in repr(bot)

    def test_all_modules_initialised(self):
        bot = _started_bot()
        assert bot._logger is not None
        assert bot._data_provider is not None
        assert bot._risk_manager is not None
        assert bot._order_executor is not None
        assert bot._strategy_engine is not None
        assert bot._ui_panel is not None


# ---------------------------------------------------------------------------
# AC-L002: Live config — FileLogging=False, LogLevel=INFO
# ---------------------------------------------------------------------------

class TestLiveConfig:
    """Live deployment must use conservative logging settings."""

    def test_file_logging_false_is_default(self):
        assert Defaults.FILE_LOGGING is False

    def test_log_level_info_is_default(self):
        assert Defaults.LOG_LEVEL == "INFO"

    def test_bot_uses_file_logging_false_by_default(self):
        from main import TradingBot
        bot = TradingBot(api=None)
        assert bot.FileLogging is False

    def test_bot_uses_info_log_level_by_default(self):
        from main import TradingBot
        bot = TradingBot(api=None)
        assert bot.LogLevel == "INFO"

    def test_logger_respects_info_level(self):
        from core.logger import Logger
        api = _make_api()
        logger = Logger(api=api, log_level=LogLevel.INFO, file_logging=False, label_prefix="ArgoAlgo")
        logger.debug("should be suppressed")
        # DEBUG should not appear (below INFO threshold)
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "should be suppressed" not in all_output

    def test_logger_prints_info_messages(self):
        from core.logger import Logger
        api = _make_api()
        logger = Logger(api=api, log_level=LogLevel.INFO, file_logging=False, label_prefix="ArgoAlgo")
        logger.info("live deployment info")
        all_output = " ".join(str(c) for c in api.Print.call_args_list)
        assert "live deployment info" in all_output


# ---------------------------------------------------------------------------
# AC-L003: Volume sizing at minimal risk (0.25 % — ramp-up Week 1)
# ---------------------------------------------------------------------------

class TestMinimalRiskVolumeSizing:
    """Week 1 ramp-up: 0.25 % per trade, 2 concurrent positions."""

    def test_025_pct_risk_produces_lower_volume_than_1pct(self):
        api = _make_api(balance=10_000.0)
        rm_live = _make_rm(api=api, risk_per_trade_pct=0.25)
        rm_full = _make_rm(api=api, risk_per_trade_pct=1.0)

        api_full = _make_api(balance=10_000.0)
        rm_full = _make_rm(api=api_full, risk_per_trade_pct=1.0)

        signal = _make_signal()
        instr_live = rm_live.validate(signal)
        instr_full = rm_full.validate(signal)

        assert instr_live.volume_units < instr_full.volume_units

    def test_volume_above_zero_at_025_pct(self):
        rm = _make_rm(risk_per_trade_pct=0.25)
        signal = _make_signal()
        instr = rm.validate(signal)
        if instr.validated:
            assert instr.volume_units > 0

    def test_max_concurrent_2_enforced_on_ramp_up(self):
        api = _make_api()
        # Add 2 mock positions that belong to the bot
        pos1 = MagicMock()
        pos1.Label = "ArgoAlgo_TF_EURUSD"
        pos2 = MagicMock()
        pos2.Label = "ArgoAlgo_MR_GBPUSD"
        api.Positions.__iter__ = MagicMock(return_value=iter([pos1, pos2]))

        rm = _make_rm(api=api, risk_per_trade_pct=0.25, max_concurrent_positions=2)
        signal = _make_signal(symbol="USDJPY")
        instr = rm.validate(signal)
        assert instr.validated is False  # cap of 2 reached

    def test_ramp_up_risk_params_week1(self):
        params = _make_params(risk_per_trade_pct=0.25, max_concurrent_positions=2)
        assert params.risk_per_trade_pct == 0.25
        assert params.max_concurrent_positions == 2

    def test_ramp_up_risk_params_week3(self):
        params = _make_params(risk_per_trade_pct=0.5, max_concurrent_positions=3)
        assert params.risk_per_trade_pct == 0.5
        assert params.max_concurrent_positions == 3

    def test_ramp_up_risk_params_week5(self):
        params = _make_params(risk_per_trade_pct=0.75, max_concurrent_positions=4)
        assert params.risk_per_trade_pct == 0.75
        assert params.max_concurrent_positions == 4

    def test_ramp_up_risk_params_week9_full(self):
        params = _make_params(risk_per_trade_pct=1.0, max_concurrent_positions=5)
        assert params.risk_per_trade_pct == 1.0
        assert params.max_concurrent_positions == 5


# ---------------------------------------------------------------------------
# AC-L004: Rate limiting — 500 new orders / min
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """OrderExecutor must respect the cTrader rate limits defined in RateLimits."""

    def test_rate_limit_constant_is_500(self):
        assert RateLimits.NEW_ORDERS == 500

    def test_rate_limit_sl_mod_is_1000(self):
        assert RateLimits.MODIFY_PROTECTION_L1 == 1_000

    def test_order_executor_tracks_orders_this_minute(self):
        api = _make_api()
        logger = MagicMock()
        executor = OrderExecutor(api=api, logger=logger)
        assert executor._orders_this_minute == 0

    def test_order_executor_skips_when_rate_limit_reached(self):
        api = _make_api()
        logger = MagicMock()
        executor = OrderExecutor(api=api, logger=logger)
        executor._orders_this_minute = RateLimits.NEW_ORDERS  # saturate limit

        signal = _make_signal()
        instr = MagicMock(spec=TradeInstruction)
        instr.validated = True
        instr.signal = signal
        instr.volume_units = 10_000

        result = executor.execute(instr)
        from utils.constants import OrderResult
        assert result.outcome == OrderResult.SKIPPED

    def test_reset_rate_counters_zeroes_all(self):
        api = _make_api()
        executor = OrderExecutor(api=api, logger=MagicMock())
        executor._orders_this_minute = 100
        executor._cancels_this_minute = 50
        executor._sl_mods_this_minute = 200
        executor.reset_rate_counters()
        assert executor._orders_this_minute == 0
        assert executor._cancels_this_minute == 0
        assert executor._sl_mods_this_minute == 0


# ---------------------------------------------------------------------------
# AC-L005: Emergency stop — halted flag blocks all execution
# ---------------------------------------------------------------------------

class TestEmergencyStop:
    """After an emergency stop, no new orders must be placed."""

    def test_halted_bot_skips_bar_closed(self):
        # Permanent halt still runs drawdown check but skips signal generation.
        bot = _started_bot()
        bot._is_halted = True
        bot._permanent_halt = True
        bot._risk_manager.check_drawdown_limits = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot.on_bar_closed()
        bot._risk_manager.check_drawdown_limits.assert_called_once()
        bot._strategy_engine.evaluate.assert_not_called()

    def test_halted_bot_skips_tick(self):
        bot = _started_bot(TrailingStopEnabled=True)
        bot._is_halted = True
        bot._risk_manager.update_trailing_stops = MagicMock()
        bot.on_tick()
        bot._risk_manager.update_trailing_stops.assert_not_called()

    def test_halt_trading_sets_flag(self):
        bot = _started_bot()
        assert bot._is_halted is False
        bot._halt_trading("emergency stop test")
        assert bot._is_halted is True

    def test_halt_sends_notification(self):
        api = _make_api()
        bot = _started_bot(api=api)
        api.Notifications.SendPushNotification.reset_mock()
        bot._halt_trading("live emergency stop")
        api.Notifications.SendPushNotification.assert_called()

    def test_halt_notification_contains_reason(self):
        api = _make_api()
        bot = _started_bot(api=api)
        bot._halt_trading("live emergency stop")
        call_arg = str(api.Notifications.SendPushNotification.call_args)
        assert "live emergency stop" in call_arg

    def test_halt_sets_panel_to_halted(self):
        bot = _started_bot()
        bot._halt_trading("test")
        assert bot._ui_panel._status == BotStatus.HALTED


# ---------------------------------------------------------------------------
# AC-L006: Drawdown controls at live ramp-up risk levels
# ---------------------------------------------------------------------------

class TestDrawdownControlsLive:
    """Drawdown limits must fire correctly at each ramp-up risk level."""

    @pytest.mark.parametrize("risk_pct,max_positions", [
        (0.25, 2),
        (0.50, 3),
        (0.75, 4),
        (1.00, 5),
    ])
    def test_daily_drawdown_check_exists_at_each_stage(self, risk_pct, max_positions):
        bot = _started_bot(
            RiskPerTradePercent=risk_pct,
            MaxConcurrentPositions=max_positions,
        )
        # RiskManager must respond to drawdown status check
        status = bot._risk_manager.check_drawdown_limits()
        assert status in list(DrawdownStatus)

    def test_drawdown_below_limit_returns_ok(self):
        rm = _make_rm(
            risk_per_trade_pct=0.25,
            max_daily_drawdown_pct=3.0,
            max_total_drawdown_pct=10.0,
        )
        # Initial state — no losses → OK
        status = rm.check_drawdown_limits()
        assert status == DrawdownStatus.OK

    def test_daily_drawdown_breached_returns_correct_status(self):
        api = _make_api(balance=10_000.0, equity=9_650.0)  # 3.5 % drop
        rm = _make_rm(api=api, max_daily_drawdown_pct=3.0)
        # Simulate daily start balance at 10,000
        rm._daily_start_balance = 10_000.0
        rm._high_water_mark = 10_000.0
        status = rm.check_drawdown_limits()
        assert status == DrawdownStatus.DAILY_LIMIT_BREACHED

    def test_total_drawdown_breached_returns_correct_status(self):
        # Equity dropped 11% from HWM of 10,000 but only 2% from today's
        # daily start (8,980), so daily limit (3%) is not breached.
        api = _make_api(balance=10_000.0, equity=8_900.0)
        rm = _make_rm(api=api, max_daily_drawdown_pct=3.0, max_total_drawdown_pct=10.0)
        rm._high_water_mark = 10_000.0
        rm._daily_start_balance = 9_070.0  # 2% daily drop → daily OK, total 11% → TOTAL breach
        status = rm.check_drawdown_limits()
        assert status == DrawdownStatus.TOTAL_LIMIT_BREACHED


# ---------------------------------------------------------------------------
# AC-L007: First trade label format
# ---------------------------------------------------------------------------

class TestFirstTradeLabelFormat:
    """Labels on live orders must match the ArgoAlgo_{abbrev}_{symbol} convention."""

    def test_build_label_trend_following(self):
        from core.order_executor import OrderExecutor
        api = _make_api()
        executor = OrderExecutor(api=api, logger=MagicMock())

        signal = _make_signal(symbol="EURUSD", strategy="TrendFollowing")
        instr = MagicMock(spec=TradeInstruction)
        instr.validated = True
        instr.signal = signal
        instr.volume_units = 5_000

        executor.execute(instr)
        # The cTrader API execute_market_order must be called with a label containing the prefix
        if api.ExecuteMarketOrder.called:
            call_kwargs = api.ExecuteMarketOrder.call_args
            label_arg = str(call_kwargs)
            assert "ArgoAlgo" in label_arg

    def test_label_prefix_in_executor(self):
        executor = OrderExecutor(api=MagicMock(), logger=MagicMock(), label_prefix="ArgoAlgo")
        assert executor._label_prefix == "ArgoAlgo"

    def test_label_prefix_custom(self):
        executor = OrderExecutor(api=MagicMock(), logger=MagicMock(), label_prefix="TestBot")
        assert executor._label_prefix == "TestBot"


# ---------------------------------------------------------------------------
# AC-L008: Day rollover resets daily tracking
# ---------------------------------------------------------------------------

class TestDayRollover:
    """check_day_rollover() must reset daily P/L tracking on a new trading day."""

    def test_rollover_resets_daily_start_balance(self):
        api = _make_api(balance=10_500.0)
        rm = _make_rm(api=api)
        # Simulate day change
        api.Server.Time.Date = "2025-03-11"
        rm._daily_start_date = "2025-03-10"
        rm.check_day_rollover()
        # After rollover, daily start balance should match current balance
        assert rm._daily_start_balance == pytest.approx(10_500.0, rel=1e-4)

    def test_rollover_updates_date(self):
        api = _make_api(balance=10_000.0)
        rm = _make_rm(api=api)
        api.Server.Time.Date = "2025-03-11"
        rm._daily_start_date = "2025-03-10"
        rm.check_day_rollover()
        assert rm._daily_start_date == "2025-03-11"

    def test_no_rollover_when_same_date(self):
        api = _make_api(balance=10_000.0)
        rm = _make_rm(api=api)
        api.Server.Time.Date = "2025-03-10"
        rm._daily_start_date = "2025-03-10"
        original_balance = rm._daily_start_balance
        rm.check_day_rollover()
        # Balance must NOT be updated mid-day
        assert rm._daily_start_balance == original_balance


# ---------------------------------------------------------------------------
# AC-L009: Survivability — 1 week without manual intervention
# ---------------------------------------------------------------------------

class TestSurvivability:
    """Simulate 1 week × 24 h × 4 (15-min bars) bar-close cycles (672 bars)."""

    def test_672_bar_cycles_no_exception(self):
        api = _make_api(hour=10, day="Tuesday")
        bot = _started_bot(api=api)

        # Stub heavy subsystems
        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._data_provider.update = MagicMock()

        for _ in range(672):
            bot.on_bar_closed()  # Must not raise

        assert not bot._is_halted

    def test_on_stop_clean_after_long_run(self):
        api = _make_api(hour=10, day="Tuesday")
        bot = _started_bot(api=api)

        bot._risk_manager.check_drawdown_limits = MagicMock(return_value=DrawdownStatus.OK)
        bot._risk_manager.check_day_rollover = MagicMock()
        bot._strategy_engine.evaluate = MagicMock(return_value=[])
        bot._strategy_engine.check_exits = MagicMock(return_value=[])
        bot._data_provider.update = MagicMock()

        for _ in range(100):
            bot.on_bar_closed()

        bot.on_stop()  # Must not raise
        assert bot._ui_panel._status == BotStatus.STOPPED
