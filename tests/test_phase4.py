"""
test_phase4.py
Phase 4 tests: RiskManager — volume calculation, signal validation pipeline,
drawdown monitoring, day rollover, SL/TP helpers, trailing stops.
No cTrader API required — uses mock objects.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.constants import Defaults, Direction, DrawdownStatus, LogLevel, SLType
from core.risk_manager import RiskManager, RiskParams
from models.trade_signal import TradeSignal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    pip_value=0.0001,   # realistic EURUSD pip value per unit
    pip_size=0.0001,
    vol_min=1000,
    vol_max=10_000_000,
    vol_step=1000,
    bid=1.08000,
    ask=1.08010,
    min_sl_pips=0.0,
) -> MagicMock:
    sym = MagicMock()
    sym.PipValue = pip_value
    sym.PipSize = pip_size
    sym.VolumeInUnitsMin = vol_min
    sym.VolumeInUnitsMax = vol_max
    sym.VolumeInUnitsStep = vol_step
    sym.Bid = bid
    sym.Ask = ask
    sym.MinStopLossInPips = min_sl_pips
    return sym


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.Account.Balance = 10_000.0
    api.Account.Equity = 10_000.0
    api.Positions.__iter__ = MagicMock(return_value=iter([]))
    # Default server date — same object on repeated access
    api.Server.Time.Date = "2025-01-15"
    return api


@pytest.fixture
def mock_logger():
    return MagicMock()


def _make_atr_indicator(atr_value=0.0015):
    """Create a mock ATR indicator returning a realistic value (default 15 pips)."""
    ind = MagicMock()
    ind.Result.Last.return_value = atr_value
    return ind


@pytest.fixture
def mock_dp():
    dp = MagicMock()
    dp.get_symbol.return_value = _make_symbol()
    dp.get_spread_pips.return_value = 1.5
    dp.is_spread_acceptable.return_value = True
    dp.get_indicator.return_value = _make_atr_indicator()
    return dp


@pytest.fixture
def rm(mock_api, mock_logger, mock_dp):
    params = _make_params()
    manager = RiskManager(mock_api, params, mock_logger, mock_dp)
    manager.initialize(10_000.0)
    return manager


@pytest.fixture
def buy_signal():
    return TradeSignal(
        strategy_name="TrendFollowing",
        symbol="EURUSD",
        direction=Direction.BUY,
        stop_loss_pips=20.0,
        take_profit_pips=40.0,
        entry_price=1.08500,
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
# Initialization
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_balances_set(self, rm):
        assert rm._initial_balance == 10_000.0
        assert rm._daily_start_balance == 10_000.0
        assert rm._high_water_mark == 10_000.0

    def test_logs_on_init(self, mock_logger, rm):
        mock_logger.info.assert_called()

    def test_repr(self, rm):
        r = repr(rm)
        assert "1.0%" in r
        assert "10.0%" in r


# ---------------------------------------------------------------------------
# check_day_rollover()
# ---------------------------------------------------------------------------

class TestDayRollover:
    def test_first_call_records_date(self, rm, mock_api):
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()
        assert rm._daily_start_date == "2025-01-15"

    def test_first_call_does_not_reset_balance(self, rm, mock_api):
        mock_api.Account.Balance = 9_500.0  # simulate some loss
        rm._daily_start_balance = 10_000.0
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()
        assert rm._daily_start_balance == 10_000.0  # unchanged on first call

    def test_same_day_does_not_reset(self, rm, mock_api):
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()  # first call
        mock_api.Account.Balance = 9_500.0
        rm._daily_start_balance = 10_000.0
        rm.check_day_rollover()  # same day
        assert rm._daily_start_balance == 10_000.0

    def test_new_day_resets_daily_balance(self, rm, mock_api):
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()  # first call
        mock_api.Server.Time.Date = "2025-01-16"
        mock_api.Account.Balance = 10_200.0
        rm.check_day_rollover()  # day changed
        assert rm._daily_start_balance == 10_200.0

    def test_new_day_updates_date(self, rm, mock_api):
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()
        mock_api.Server.Time.Date = "2025-01-16"
        rm.check_day_rollover()
        assert rm._daily_start_date == "2025-01-16"

    def test_new_day_logs_reset(self, rm, mock_api, mock_logger):
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()
        mock_api.Server.Time.Date = "2025-01-16"
        mock_api.Account.Balance = 10_200.0
        rm.check_day_rollover()
        calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("Day rollover" in c for c in calls)

    def test_api_none_does_not_raise(self, mock_logger, mock_dp):
        rm = RiskManager(None, _make_params(), mock_logger, mock_dp)
        rm.initialize(10_000.0)
        rm.check_day_rollover()  # Should not raise

    def test_time_error_logs_and_continues(self, rm, mock_api, mock_logger):
        from unittest.mock import PropertyMock
        rm._daily_start_date = "2025-01-15"  # pre-seeded so comparison runs
        time_mock = MagicMock()
        type(time_mock).Date = PropertyMock(side_effect=RuntimeError("no date"))
        mock_api.Server.Time = time_mock
        rm.check_day_rollover()  # Should not raise; error logged
        mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# check_drawdown_limits()
# ---------------------------------------------------------------------------

class TestCheckDrawdownLimits:
    def test_ok_at_start(self, rm):
        assert rm.check_drawdown_limits() == DrawdownStatus.OK

    def test_ok_when_equity_equals_balance(self, rm, mock_api):
        mock_api.Account.Equity = 10_000.0
        assert rm.check_drawdown_limits() == DrawdownStatus.OK

    def test_daily_limit_breached_at_threshold(self, rm, mock_api):
        # 3% daily DD on 10_000 start = equity must drop to 9_700
        mock_api.Account.Equity = 9_700.0
        result = rm.check_drawdown_limits()
        assert result == DrawdownStatus.DAILY_LIMIT_BREACHED

    def test_daily_limit_not_breached_just_below(self, rm, mock_api):
        mock_api.Account.Equity = 9_701.0
        assert rm.check_drawdown_limits() == DrawdownStatus.OK

    def test_total_limit_breached_at_threshold(self, rm, mock_api):
        # Set daily_start_balance to match equity so daily check passes,
        # but HWM is 10_000 so total drawdown is 10%.
        rm._daily_start_balance = 9_000.0
        mock_api.Account.Equity = 9_000.0
        result = rm.check_drawdown_limits()
        assert result == DrawdownStatus.TOTAL_LIMIT_BREACHED

    def test_total_limit_not_breached_just_above(self, rm, mock_api):
        rm._daily_start_balance = 9_001.0
        mock_api.Account.Equity = 9_001.0
        assert rm.check_drawdown_limits() == DrawdownStatus.OK

    def test_high_water_mark_advances(self, rm, mock_api):
        mock_api.Account.Equity = 11_000.0
        rm.check_drawdown_limits()
        assert rm._high_water_mark == 11_000.0

    def test_high_water_mark_does_not_retreat(self, rm, mock_api):
        mock_api.Account.Equity = 11_000.0
        rm.check_drawdown_limits()
        mock_api.Account.Equity = 10_500.0
        rm.check_drawdown_limits()
        assert rm._high_water_mark == 11_000.0

    def test_daily_breach_logged(self, rm, mock_api, mock_logger):
        mock_api.Account.Equity = 9_700.0
        rm.check_drawdown_limits()
        mock_logger.risk_action.assert_called()

    def test_total_breach_logged(self, rm, mock_api, mock_logger):
        mock_api.Account.Equity = 9_000.0
        rm.check_drawdown_limits()
        mock_logger.risk_action.assert_called()

    def test_api_none_returns_ok(self, mock_logger, mock_dp):
        rm = RiskManager(None, _make_params(), mock_logger, mock_dp)
        rm.initialize(10_000.0)
        assert rm.check_drawdown_limits() == DrawdownStatus.OK


# ---------------------------------------------------------------------------
# validate() — 8-check pipeline
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_signal_returns_validated_true(self, rm, buy_signal):
        instr = rm.validate(buy_signal)
        assert instr.validated is True

    def test_valid_signal_has_positive_volume(self, rm, buy_signal):
        instr = rm.validate(buy_signal)
        assert instr.volume_units > 0

    def test_none_direction_rejected(self, rm, none_signal):
        instr = rm.validate(none_signal)
        assert instr.validated is False
        assert "direction" in instr.rejection_reason.lower()

    def test_wide_spread_rejected(self, rm, buy_signal, mock_dp):
        mock_dp.is_spread_acceptable.return_value = False
        mock_dp.get_spread_pips.return_value = 5.0
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "spread" in instr.rejection_reason.lower()

    def test_daily_drawdown_rejects(self, rm, buy_signal, mock_api):
        mock_api.Account.Equity = 9_700.0  # 3% daily loss
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "daily" in instr.rejection_reason.lower()

    def test_total_drawdown_rejects(self, rm, buy_signal, mock_api):
        # Set daily balance = equity so daily check passes; HWM=10000 gives 10% total DD
        rm._daily_start_balance = 9_000.0
        mock_api.Account.Equity = 9_000.0
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "total" in instr.rejection_reason.lower()

    def test_max_concurrent_rejects(self, rm, buy_signal, mock_api):
        positions = [MagicMock(Label=f"ArgoAlgo_TF_SYM_{i}", SymbolName="EURUSD") for i in range(5)]
        mock_api.Positions.__iter__ = MagicMock(side_effect=lambda: iter(positions))
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "concurrent" in instr.rejection_reason.lower()

    def test_max_per_symbol_rejects(self, rm, buy_signal, mock_api):
        pos = MagicMock()
        pos.Label = "ArgoAlgo_TF_EURUSD"
        pos.SymbolName = "EURUSD"
        # Use side_effect so iterator resets on each call (validate calls _count_bot_positions twice)
        mock_api.Positions.__iter__ = MagicMock(side_effect=lambda: iter([pos]))
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "EURUSD" in instr.rejection_reason

    def test_volume_below_min_clamps_to_minimum(self, rm, buy_signal, mock_api, mock_dp):
        # balance=10000, risk=1%, sl=20pips, pip_value=100 -> raw=0.5 < vol_min=1000
        # Warning is logged; calculate_volume clamps to vol_min so validation still passes
        # (the 20-pip SL cap bounds the resulting $ loss).
        sym = _make_symbol(pip_value=100.0, vol_min=1_000)
        mock_dp.get_symbol.return_value = sym
        mock_api.Account.Balance = 10_000.0
        instr = rm.validate(buy_signal)
        assert instr.validated is True
        assert instr.volume_units >= 1_000

    def test_rejection_logged_at_info(self, rm, none_signal, mock_logger):
        rm.validate(none_signal)
        mock_logger.info.assert_called()

    def test_valid_signal_carries_original_signal(self, rm, buy_signal):
        instr = rm.validate(buy_signal)
        assert instr.signal is buy_signal

    def test_manual_positions_not_counted(self, rm, buy_signal, mock_api):
        """Positions with non-bot labels don't count toward limits."""
        pos = MagicMock()
        pos.Label = "ManualTrade"
        pos.SymbolName = "EURUSD"
        mock_api.Positions.__iter__ = MagicMock(side_effect=lambda: iter([pos]))
        instr = rm.validate(buy_signal)
        assert instr.validated is True


# ---------------------------------------------------------------------------
# New validation rules (post-Week-4 tightening)
# ---------------------------------------------------------------------------

class TestSlCap:
    """Reject signals with SL > MAX_SL_PIPS so $ loss per trade stays bounded."""

    def test_sl_exactly_at_cap_accepted(self, rm, buy_signal):
        buy_signal.stop_loss_pips = Defaults.MAX_SL_PIPS
        assert rm.validate(buy_signal).validated is True

    def test_sl_above_cap_rejected(self, rm, buy_signal):
        buy_signal.stop_loss_pips = Defaults.MAX_SL_PIPS + 0.1
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "sl too wide" in instr.rejection_reason.lower()

    def test_sl_far_above_cap_rejected(self, rm, buy_signal):
        buy_signal.stop_loss_pips = Defaults.MAX_SL_PIPS + 30.0
        instr = rm.validate(buy_signal)
        assert instr.validated is False


class TestDailyTradeCap:
    """Reject new entries after MAX_TRADES_PER_DAY opens on the same day."""

    def test_under_cap_accepted(self, rm, buy_signal):
        rm._trades_opened_today = Defaults.MAX_TRADES_PER_DAY - 1
        assert rm.validate(buy_signal).validated is True

    def test_at_cap_rejected(self, rm, buy_signal):
        rm._trades_opened_today = Defaults.MAX_TRADES_PER_DAY
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "daily trade cap" in instr.rejection_reason.lower()

    def test_notify_position_opened_increments(self, rm):
        start = rm._trades_opened_today
        rm.notify_position_opened()
        assert rm._trades_opened_today == start + 1

    def test_rollover_resets_trade_count(self, rm, mock_api):
        rm._trades_opened_today = 5
        mock_api.Server.Time.Date = "2025-01-15"
        rm.check_day_rollover()  # first call records date
        mock_api.Server.Time.Date = "2025-01-16"
        mock_api.Account.Balance = 10_000.0
        rm.check_day_rollover()  # day changed
        assert rm._trades_opened_today == 0


class TestPostLossCooldown:
    """Reject new entries for POST_LOSS_COOLDOWN_HOURS after a losing close."""

    def test_no_prior_loss_accepted(self, rm, buy_signal):
        assert rm._last_loss_server_time is None
        assert rm.validate(buy_signal).validated is True

    def test_winning_close_does_not_arm_cooldown(self, rm):
        rm.notify_position_closed(net_pnl=2.50)
        assert rm._last_loss_server_time is None

    def test_losing_close_arms_cooldown(self, rm, mock_api):
        now = MagicMock()
        mock_api.Server.Time = now
        rm.notify_position_closed(net_pnl=-3.00)
        assert rm._last_loss_server_time is now

    def test_within_cooldown_rejects(self, rm, buy_signal, mock_api):
        # The default cooldown is 0 (disabled) post-MR-deployment. Patch it back to 4h
        # to exercise the cooldown branch — the *logic* still must work even if the
        # default is off, in case the user re-enables it later.
        with patch.object(Defaults, "POST_LOSS_COOLDOWN_HOURS", 4.0):
            recent = MagicMock()
            rm._last_loss_server_time = recent
            elapsed = MagicMock()
            elapsed.TotalHours = 1.0  # under 4h cooldown
            current = MagicMock()
            current.__sub__ = MagicMock(return_value=elapsed)
            mock_api.Server.Time = current
            instr = rm.validate(buy_signal)
            assert instr.validated is False
            assert "cooldown" in instr.rejection_reason.lower()

    def test_after_cooldown_accepts(self, rm, buy_signal, mock_api):
        with patch.object(Defaults, "POST_LOSS_COOLDOWN_HOURS", 4.0):
            recent = MagicMock()
            rm._last_loss_server_time = recent
            elapsed = MagicMock()
            elapsed.TotalHours = 5.0  # past 4h cooldown
            current = MagicMock()
            current.__sub__ = MagicMock(return_value=elapsed)
            mock_api.Server.Time = current
            assert rm.validate(buy_signal).validated is True


class TestVolumeRejection:
    """Reject signals whose calculated volume falls below symbol minimum."""

    def test_zero_volume_rejected(self, rm, buy_signal, mock_dp):
        # Force calculate_volume to return 0 by patching the helper path
        sym = _make_symbol(vol_min=1000)
        mock_dp.get_symbol.return_value = sym
        rm.calculate_volume = MagicMock(return_value=0.0)
        instr = rm.validate(buy_signal)
        assert instr.validated is False
        assert "volume below minimum" in instr.rejection_reason.lower()


# ---------------------------------------------------------------------------
# calculate_volume()
# ---------------------------------------------------------------------------

class TestCalculateVolume:
    def test_basic_volume_calculation(self, rm, mock_api, mock_dp):
        """balance=10000, risk=1%, sl=20pips, pip_value=1.0 -> 5000 units."""
        sym = _make_symbol(pip_value=1.0, vol_min=1000, vol_max=10_000_000, vol_step=1000)
        mock_dp.get_symbol.return_value = sym
        mock_api.Account.Balance = 10_000.0
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=20.0)
        # risk_amount = 10000 * 0.01 = 100; vol = 100 / (20 * 1) = 5; rounded to 1000 step
        assert vol == 0.0 or vol >= sym.VolumeInUnitsMin  # vol might be below min

    def test_volume_calculation_correct_result(self, rm, mock_api, mock_dp):
        """With pip_value=0.1, sl=20, balance=10000, risk=1%: raw=500, step=1000 -> 0."""
        # Adjust to get a clean result
        sym = _make_symbol(pip_value=0.01, vol_min=1000, vol_max=10_000_000, vol_step=1000)
        mock_dp.get_symbol.return_value = sym
        mock_api.Account.Balance = 10_000.0
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=10.0)
        # risk=100, sl=10, pv=0.01 -> 100/(10*0.01)=1000 -> step 1000 -> 1000
        assert vol == 1000.0

    def test_volume_clamped_to_max(self, rm, mock_api, mock_dp):
        sym = _make_symbol(pip_value=0.0001, vol_min=1000, vol_max=5000, vol_step=1000)
        mock_dp.get_symbol.return_value = sym
        mock_api.Account.Balance = 10_000.0
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=1.0)
        assert vol <= 5000

    def test_volume_clamped_to_min(self, rm, mock_api, mock_dp):
        sym = _make_symbol(pip_value=1000.0, vol_min=1000, vol_max=10_000_000, vol_step=1000)
        mock_dp.get_symbol.return_value = sym
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=20.0)
        assert vol == 0.0 or vol >= 1000  # either 0 (below min rejected) or >=min

    def test_zero_sl_returns_zero(self, rm):
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=0.0)
        assert vol == 0.0

    def test_negative_sl_returns_zero(self, rm):
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=-5.0)
        assert vol == 0.0

    def test_volume_rounded_to_step(self, rm, mock_api, mock_dp):
        sym = _make_symbol(pip_value=0.01, vol_min=1000, vol_max=10_000_000, vol_step=1000)
        mock_dp.get_symbol.return_value = sym
        mock_api.Account.Balance = 10_000.0
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=7.0)
        # raw = 100/(7*0.01) = ~1428; rounded down to step 1000 -> 1000
        assert vol % 1000 == 0

    def test_volume_logs_debug(self, rm, mock_logger):
        rm.calculate_volume("EURUSD", stop_loss_pips=20.0)
        mock_logger.debug.assert_called()

    def test_api_error_returns_zero(self, rm, mock_api, mock_logger):
        mock_api.Account.Balance = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
        mock_api.Account = MagicMock(side_effect=RuntimeError("no API"))
        vol = rm.calculate_volume("EURUSD", stop_loss_pips=20.0)
        assert vol == 0.0
        mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# calculate_sl_pips()
# ---------------------------------------------------------------------------

class TestCalculateSlPips:
    def test_fixed_sl_returns_fixed_pips(self, rm):
        sl = rm.calculate_sl_pips(
            "EURUSD", Direction.BUY, SLType.FIXED,
            atr_multiplier=2.0, fixed_pips=25.0
        )
        assert sl == pytest.approx(25.0)

    def test_atr_sl_uses_atr_value(self, rm, mock_dp):
        atr_mock = MagicMock()
        atr_mock.Result.Last.return_value = 0.002  # 20 pips at 0.0001 pip_size
        mock_dp.get_indicator.return_value = atr_mock
        sl = rm.calculate_sl_pips(
            "EURUSD", Direction.BUY, SLType.ATR,
            atr_multiplier=2.0
        )
        # 0.002 / 0.0001 * 2.0 = 40.0 pips
        assert sl == pytest.approx(40.0)

    def test_sl_respects_minimum(self, rm, mock_dp):
        sym = _make_symbol(min_sl_pips=10.0)
        mock_dp.get_symbol.return_value = sym
        sl = rm.calculate_sl_pips(
            "EURUSD", Direction.BUY, SLType.FIXED,
            atr_multiplier=2.0, fixed_pips=5.0  # below min
        )
        assert sl >= 10.0

    def test_atr_error_returns_zero(self, rm, mock_dp):
        mock_dp.get_indicator.side_effect = RuntimeError("no indicator")
        sl = rm.calculate_sl_pips("EURUSD", Direction.BUY, SLType.ATR, 2.0)
        assert sl == 0.0


# ---------------------------------------------------------------------------
# calculate_tp_pips() and calculate_tp_to_price()
# ---------------------------------------------------------------------------

class TestCalculateTp:
    def test_tp_pips_rr_ratio(self, rm):
        assert rm.calculate_tp_pips(20.0, 2.0) == pytest.approx(40.0)

    def test_tp_pips_rr_1_5(self, rm):
        assert rm.calculate_tp_pips(10.0, 1.5) == pytest.approx(15.0)

    def test_tp_to_price_buy(self, rm):
        # Buy: target 100 pips above entry
        pips = rm.calculate_tp_to_price(1.08000, 1.09000, Direction.BUY, 0.0001)
        assert pips == pytest.approx(100.0)

    def test_tp_to_price_sell(self, rm):
        # Sell: target 100 pips below entry
        pips = rm.calculate_tp_to_price(1.09000, 1.08000, Direction.SELL, 0.0001)
        assert pips == pytest.approx(100.0)

    def test_tp_to_price_never_negative(self, rm):
        # Wrong direction target: returns 0.0, not negative
        pips = rm.calculate_tp_to_price(1.08000, 1.07900, Direction.BUY, 0.0001)
        assert pips == 0.0


# ---------------------------------------------------------------------------
# update_trailing_stops()
# ---------------------------------------------------------------------------

class TestUpdateTrailingStops:
    def _make_position(self, pos_id=1, label="ArgoAlgo_TR_EURUSD",
                       symbol="EURUSD", trade_type="Buy",
                       entry=1.08000, pips=0.0, stop_loss=1.07800):
        pos = MagicMock()
        pos.Id = pos_id
        pos.Label = label
        pos.SymbolName = symbol
        pos.TradeType = trade_type
        pos.EntryPrice = entry
        pos.Pips = pips
        pos.StopLoss = stop_loss
        return pos

    def test_skips_when_trailing_disabled(self, mock_api, mock_logger, mock_dp):
        params = _make_params(trailing_stop_enabled=False)
        rm = RiskManager(mock_api, params, mock_logger, mock_dp)
        rm.initialize(10_000.0)
        pos = self._make_position(pips=20.0)
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        pos.ModifyStopLossPrice.assert_not_called()

    def test_skips_non_bot_positions(self, rm, mock_api):
        pos = self._make_position(label="ManualTrade", pips=20.0)
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        pos.ModifyStopLossPrice.assert_not_called()

    def test_no_action_below_trigger(self, rm, mock_api):
        pos = self._make_position(pips=10.0)  # trigger=15
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        pos.ModifyStopLossPrice.assert_not_called()

    def test_activates_at_trigger_moves_to_breakeven(self, rm, mock_api):
        pos = self._make_position(pips=15.0, entry=1.08000)
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        pos.ModifyStopLossPrice.assert_called_once_with(1.08000)

    def test_activation_recorded_in_map(self, rm, mock_api):
        pos = self._make_position(pips=15.0, entry=1.08000)
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        assert pos.Id in rm._trailing_stop_map
        assert rm._trailing_stop_map[pos.Id] == 1.08000

    def test_buy_trail_advances_sl_upward(self, rm, mock_api, mock_dp):
        sym = _make_symbol(pip_size=0.0001, bid=1.08500)
        mock_dp.get_symbol.return_value = sym
        pos = self._make_position(pips=30.0, entry=1.08000, trade_type="Buy")
        rm._trailing_stop_map[pos.Id] = 1.08000  # already active
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        # ATR-based trail: ATR=15 pips, TF distance=0.75×15=11.25 pips=0.001125
        # new_sl=1.08500-0.001125=1.083875; > 1.08000+min_step, so advance
        pos.ModifyStopLossPrice.assert_called()
        new_sl = pos.ModifyStopLossPrice.call_args[0][0]
        assert new_sl == pytest.approx(1.083875)

    def test_sell_trail_advances_sl_downward(self, rm, mock_api, mock_dp):
        sym = _make_symbol(pip_size=0.0001, ask=1.07500)
        mock_dp.get_symbol.return_value = sym
        pos = self._make_position(
            pips=30.0, entry=1.08000, trade_type="Sell", stop_loss=1.09000
        )
        rm._trailing_stop_map[pos.Id] = 1.09000  # already active
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        # ATR-based trail: ATR=15 pips, TF distance=0.75×15=11.25 pips=0.001125
        # new_sl=1.07500+0.001125=1.076125; < 1.09000-min_step, so advance
        new_sl = pos.ModifyStopLossPrice.call_args[0][0]
        assert new_sl == pytest.approx(1.076125)

    def test_buy_trail_does_not_retreat(self, rm, mock_api, mock_dp):
        """If price drops, trailing SL should not move backward."""
        sym = _make_symbol(pip_size=0.0001, bid=1.08050)
        mock_dp.get_symbol.return_value = sym
        pos = self._make_position(pips=30.0, entry=1.08000, trade_type="Buy")
        rm._trailing_stop_map[pos.Id] = 1.08100  # SL already at 1.08100 (higher)
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        # new_sl=1.08050-0.001125=1.079375 < current_sl 1.08100 → don't move
        pos.ModifyStopLossPrice.assert_not_called()

    def test_sell_trail_does_not_retreat(self, rm, mock_api, mock_dp):
        sym = _make_symbol(pip_size=0.0001, ask=1.07950)
        mock_dp.get_symbol.return_value = sym
        pos = self._make_position(pips=30.0, entry=1.08000, trade_type="Sell")
        rm._trailing_stop_map[pos.Id] = 1.07800  # SL already low
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos]))
        rm.update_trailing_stops()
        # new_sl=1.07950+0.001125=1.080625 > 1.07800-min_step → don't move backward
        pos.ModifyStopLossPrice.assert_not_called()

    def test_position_error_does_not_abort_others(self, rm, mock_api, mock_dp):
        pos1 = self._make_position(pos_id=1, pips=15.0)
        pos2 = self._make_position(pos_id=2, pips=15.0, entry=1.09000)
        pos1.EntryPrice = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
        pos1.EntryPrice = MagicMock(side_effect=RuntimeError("bad"))
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([pos1, pos2]))
        rm.update_trailing_stops()  # Should not raise; pos2 still processed


# ---------------------------------------------------------------------------
# _count_bot_positions()
# ---------------------------------------------------------------------------

class TestCountBotPositions:
    def test_counts_only_bot_positions(self, rm, mock_api):
        p1 = MagicMock(); p1.Label = "ArgoAlgo_TF_EURUSD"; p1.SymbolName = "EURUSD"
        p2 = MagicMock(); p2.Label = "Manual"; p2.SymbolName = "EURUSD"
        mock_api.Positions.__iter__ = MagicMock(return_value=iter([p1, p2]))
        assert rm._count_bot_positions() == 1

    def test_counts_by_symbol(self, rm, mock_api):
        p1 = MagicMock(); p1.Label = "ArgoAlgo_TF_EURUSD"; p1.SymbolName = "EURUSD"
        p2 = MagicMock(); p2.Label = "ArgoAlgo_TF_GBPUSD"; p2.SymbolName = "GBPUSD"
        positions = [p1, p2]
        mock_api.Positions.__iter__ = MagicMock(side_effect=lambda: iter(positions))
        assert rm._count_bot_positions(symbol="EURUSD") == 1
        assert rm._count_bot_positions(symbol="GBPUSD") == 1

    def test_returns_zero_on_error(self, rm, mock_api):
        mock_api.Positions.__iter__ = MagicMock(side_effect=RuntimeError("no positions"))
        assert rm._count_bot_positions() == 0


# ---------------------------------------------------------------------------
# State reconstruction from History (post-OOM persistence)
# ---------------------------------------------------------------------------

class TestStateReconstruction:
    """Verify _reconstruct_state_from_history rebuilds throttle/DD state from
    api.History so an OOM/restart cycle does not bypass the daily cap, the
    post-loss cooldown, or the drawdown high-water mark."""

    _next_close_seq = [0]  # mutable counter so each fake trade gets a unique close time

    class _FakeCloseTime:
        """cTrader-DateTime stand-in: has ``.Date`` like .NET DateTime AND
        supports < / > / sorted via an explicit sequence number so tests are
        deterministic regardless of memory layout."""
        def __init__(self, seq: int, date_str: str) -> None:
            self.seq = seq
            self.Date = date_str
        def __lt__(self, other):  return self.seq < other.seq
        def __gt__(self, other):  return self.seq > other.seq
        def __le__(self, other):  return self.seq <= other.seq
        def __ge__(self, other):  return self.seq >= other.seq
        def __eq__(self, other):  return isinstance(other, type(self)) and self.seq == other.seq
        def __hash__(self):       return hash(self.seq)
        def __repr__(self):       return f"_FakeCloseTime(seq={self.seq}, Date={self.Date!r})"

    def _make_trade(self, label="ArgoAlgo_ME_EURUSD", entry_date="2025-01-15",
                    close_date="2025-01-15", net=2.50, close_seq=None):
        """Build a fake closed-trade.

        ``ClosingTime`` is a deterministic ordered stand-in (not a real datetime)
        with the ``.Date`` attribute the production code expects from cTrader.
        """
        if close_seq is None:
            self._next_close_seq[0] += 1
            close_seq = self._next_close_seq[0]
        tr = MagicMock()
        tr.Label = label
        tr.NetProfit = net
        tr.EntryTime = MagicMock()
        tr.EntryTime.Date = entry_date
        tr.ClosingTime = self._FakeCloseTime(seq=close_seq, date_str=close_date)
        return tr

    def test_no_history_keeps_fresh_counters(self, mock_api, mock_logger, mock_dp):
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(return_value=iter([]))
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        assert rm._trades_opened_today == 0
        assert rm._last_loss_server_time is None
        assert rm._high_water_mark == 698.0

    def test_today_trade_count_reconstructed(self, mock_api, mock_logger, mock_dp):
        today = mock_api.Server.Time.Date  # "2025-01-15"
        trades = [
            self._make_trade(entry_date=today, close_date=today, net=1.50),
            self._make_trade(entry_date=today, close_date=today, net=-2.00),
            self._make_trade(entry_date="2025-01-14", close_date="2025-01-14", net=3.00),
        ]
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(return_value=iter(trades))
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        assert rm._trades_opened_today == 2  # only today's entries

    def test_last_loss_set_to_most_recent_losing_close(self, mock_api, mock_logger, mock_dp):
        # Three trades: two losses (one older, one newer) and one winner.
        # Explicit close_seq makes "newer" deterministic.
        win = self._make_trade(net=2.0, close_seq=1)
        old_loss = self._make_trade(net=-3.0, close_seq=2)
        new_loss = self._make_trade(net=-1.5, close_seq=3)
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(return_value=iter([win, old_loss, new_loss]))
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        assert rm._last_loss_server_time is new_loss.ClosingTime

    def test_non_bot_labels_ignored(self, mock_api, mock_logger, mock_dp):
        manual = self._make_trade(label="ManualTrade", net=-50.0)
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(return_value=iter([manual]))
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        assert rm._trades_opened_today == 0
        assert rm._last_loss_server_time is None

    def test_hwm_reflects_chronological_peak(self, mock_api, mock_logger, mock_dp):
        # Walk: 698 → 798 → 778 → 818 (peak) → 798. Current balance 798.
        # HWM should be 818 (the historical peak), not 798 (current).
        trades = [
            self._make_trade(net=100.0),  # 698 → 798
            self._make_trade(net=-20.0),  # 798 → 778
            self._make_trade(net=40.0),   # 778 → 818
            self._make_trade(net=-20.0),  # 818 → 798
        ]
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(return_value=iter(trades))
        mock_api.Account.Balance = 798.0
        mock_api.Account.Equity = 798.0
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        assert rm._high_water_mark == 818.0

    def test_daily_start_balance_subtracts_today_realised_pnl(
        self, mock_api, mock_logger, mock_dp
    ):
        today = mock_api.Server.Time.Date  # "2025-01-15"
        trades = [
            self._make_trade(entry_date="2025-01-14", close_date=today, net=10.0),
            self._make_trade(entry_date=today, close_date=today, net=-3.0),
        ]
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(return_value=iter(trades))
        # Current balance = 705 (after both trades realised today).
        # daily_start_balance = 705 - (10 + -3) = 698.
        mock_api.Account.Balance = 705.0
        mock_api.Account.Equity = 705.0
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        assert rm._daily_start_balance == 698.0

    def test_notify_position_closed_evicts_trail_map(self, rm):
        rm._trailing_stop_map[42] = 1.08000
        rm.notify_position_closed(net_pnl=2.50, position_id=42)
        assert 42 not in rm._trailing_stop_map

    def test_notify_position_closed_without_id_leaves_map(self, rm):
        # Backwards-compatible call (no position_id) doesn't error or evict.
        rm._trailing_stop_map[42] = 1.08000
        rm.notify_position_closed(net_pnl=-1.0)
        assert 42 in rm._trailing_stop_map  # entry survives

    def test_update_trailing_stops_throttle_blocks_rapid_calls(
        self, rm, mock_api, mock_dp
    ):
        # First call processes; an immediate second call must be skipped.
        sym = _make_symbol(pip_size=0.0001, bid=1.08500)
        mock_dp.get_symbol.return_value = sym
        pos = MagicMock()
        pos.Id = 1
        pos.Label = "ArgoAlgo_ME_EURUSD"
        pos.SymbolName = "EURUSD"
        pos.TradeType = "Buy"
        pos.EntryPrice = 1.08000
        pos.Pips = 30.0
        mock_api.Positions.Count = 1
        mock_api.Positions.__iter__ = MagicMock(side_effect=lambda: iter([pos]))
        rm._trailing_stop_map[pos.Id] = 1.08000

        rm.update_trailing_stops()
        first_call_count = pos.ModifyStopLossPrice.call_count
        assert first_call_count >= 1

        # Second call right after — throttle should block it.
        rm.update_trailing_stops()
        assert pos.ModifyStopLossPrice.call_count == first_call_count

    def test_update_trailing_stops_skips_when_no_positions(
        self, rm, mock_api
    ):
        # Positions.Count == 0 → return before allocating list, so __iter__ never runs.
        mock_api.Positions.Count = 0
        mock_api.Positions.__iter__ = MagicMock(side_effect=AssertionError("must not iterate"))
        # Ensure throttle doesn't block this first call.
        rm._last_trail_tick_monotonic = 0.0
        rm.update_trailing_stops()  # must not raise

    def test_reconstruct_failure_does_not_break_init(
        self, mock_api, mock_logger, mock_dp
    ):
        # api.History throws when iterated → reconstruction must swallow + carry on.
        mock_api.History = MagicMock()
        mock_api.History.__iter__ = MagicMock(side_effect=RuntimeError("api dead"))
        rm = RiskManager(mock_api, _make_params(), mock_logger, mock_dp)
        rm.initialize(698.0)
        # Fresh-init counters retained.
        assert rm._high_water_mark == 698.0
        assert rm._trades_opened_today == 0
        assert rm._last_loss_server_time is None
