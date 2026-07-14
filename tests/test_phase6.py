"""
test_phase6.py
Phase 6 tests: OrderExecutor — execute, close_position, close_all_positions,
modify_sl, build_label, is_bot_position, rate limiting, reset_rate_counters.
No cTrader API required — uses MagicMock.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.order_executor import ExecutionResult, OrderExecutor
from models.trade_signal import TradeSignal
from models.trade_instruction import TradeInstruction
from utils.constants import Direction, OrderResult, RateLimits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor(label_prefix="ArgoAlgo"):
    api = MagicMock()
    logger = MagicMock()
    return OrderExecutor(api=api, logger=logger, label_prefix=label_prefix), api, logger


def _make_signal(
    strategy_name="TrendFollowing",
    symbol="EURUSD",
    direction=Direction.BUY,
    sl=20.0,
    tp=40.0,
    entry=1.0800,
):
    return TradeSignal(
        strategy_name=strategy_name,
        symbol=symbol,
        direction=direction,
        stop_loss_pips=sl,
        take_profit_pips=tp,
        entry_price=entry,
        timestamp=datetime.utcnow(),
    )


def _make_instruction(validated=True, rejection_reason=None, volume=10_000.0, **signal_kwargs):
    sig = _make_signal(**signal_kwargs)
    return TradeInstruction(
        signal=sig,
        volume_units=volume,
        validated=validated,
        rejection_reason=rejection_reason,
    )


def _make_position(symbol="EURUSD", trade_type="Buy", label="ArgoAlgo_TF_EURUSD", pos_id=1):
    pos = MagicMock()
    pos.SymbolName = symbol
    pos.TradeType = trade_type
    pos.Label = label
    pos.Id = pos_id
    pos.NetProfit = 50.0
    return pos


def _success_result(pos_id=42):
    """Fake cTrader TradeResult for a successful order."""
    r = MagicMock()
    r.IsSuccessful = True
    r.Position.Id = pos_id
    return r


def _failed_result(error="TradeError"):
    """Fake cTrader TradeResult for a failed order."""
    r = MagicMock()
    r.IsSuccessful = False
    r.Error = error
    return r


# ---------------------------------------------------------------------------
# TestExecuteSuccess
# ---------------------------------------------------------------------------

class TestExecuteSuccess:
    def test_returns_success_outcome(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result(pos_id=7)
        result = ex.execute(_make_instruction())
        assert result.outcome == OrderResult.SUCCESS

    def test_returns_position_id_on_success(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result(pos_id=99)
        result = ex.execute(_make_instruction())
        assert result.position_id == 99

    def test_calls_execute_market_order_with_correct_args(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        instr = _make_instruction(symbol="GBPUSD", sl=15.0, tp=30.0, volume=5_000.0)
        ex.execute(instr)
        # Positional arg order: tradeType, symbolName, volumeInUnits, label, stopLossPips, takeProfitPips
        api.ExecuteMarketOrder.assert_called_once_with(
            api.TradeType.Buy,
            "GBPUSD",
            5_000.0,
            "ArgoAlgo_TR_GBPUSD",
            15.0,
            30.0,
        )

    def test_buy_direction_uses_buy_trade_type(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        ex.execute(_make_instruction(direction=Direction.BUY))
        args = api.ExecuteMarketOrder.call_args.args
        assert args[0] == api.TradeType.Buy

    def test_sell_direction_uses_sell_trade_type(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        ex.execute(_make_instruction(direction=Direction.SELL))
        args = api.ExecuteMarketOrder.call_args.args
        assert args[0] == api.TradeType.Sell

    def test_zero_tp_passed_as_none(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        ex.execute(_make_instruction(tp=0.0))
        # When tp=0, no-tp overload is used: only 5 positional args (no takeProfitPips)
        args = api.ExecuteMarketOrder.call_args.args
        assert len(args) == 5

    def test_nonzero_tp_passed_as_float(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        ex.execute(_make_instruction(tp=50.0))
        args = api.ExecuteMarketOrder.call_args.args
        assert args[5] == 50.0

    def test_logs_trade_entry_on_success(self):
        ex, api, logger = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        instr = _make_instruction()
        ex.execute(instr)
        logger.trade_entry.assert_called_once()

    def test_increments_order_counter_on_api_call(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _success_result()
        ex.execute(_make_instruction())
        assert ex._orders_this_minute == 1

    def test_counter_increments_even_on_api_failure(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _failed_result()
        ex.execute(_make_instruction())
        assert ex._orders_this_minute == 1


# ---------------------------------------------------------------------------
# TestExecuteFailure
# ---------------------------------------------------------------------------

class TestExecuteFailure:
    def test_returns_failed_when_api_reports_failure(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _failed_result("InsufficientFunds")
        result = ex.execute(_make_instruction())
        assert result.outcome == OrderResult.FAILED

    def test_error_message_contains_api_error(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.return_value = _failed_result("BadVolume")
        result = ex.execute(_make_instruction())
        assert "BadVolume" in result.error_message

    def test_returns_failed_on_api_exception(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.side_effect = RuntimeError("network error")
        result = ex.execute(_make_instruction())
        assert result.outcome == OrderResult.FAILED

    def test_exception_message_in_error_message(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.side_effect = RuntimeError("boom")
        result = ex.execute(_make_instruction())
        assert "boom" in result.error_message

    def test_logs_error_on_api_failure(self):
        ex, api, logger = _make_executor()
        api.ExecuteMarketOrder.return_value = _failed_result("Err")
        ex.execute(_make_instruction())
        logger.error.assert_called()

    def test_logs_error_on_exception(self):
        ex, api, logger = _make_executor()
        api.ExecuteMarketOrder.side_effect = RuntimeError("crash")
        ex.execute(_make_instruction())
        logger.error.assert_called()

    def test_does_not_increment_counter_on_exception(self):
        ex, api, _ = _make_executor()
        api.ExecuteMarketOrder.side_effect = RuntimeError("crash")
        ex.execute(_make_instruction())
        # counter is only incremented after API call succeeds/fails
        # but if ExecuteMarketOrder raises, we never reach the increment
        assert ex._orders_this_minute == 0


# ---------------------------------------------------------------------------
# TestExecuteSkipped
# ---------------------------------------------------------------------------

class TestExecuteSkipped:
    def test_skips_unvalidated_instruction(self):
        ex, api, _ = _make_executor()
        instr = _make_instruction(validated=False, rejection_reason="spread too wide")
        result = ex.execute(instr)
        assert result.outcome == OrderResult.SKIPPED

    def test_unvalidated_does_not_call_api(self):
        ex, api, _ = _make_executor()
        ex.execute(_make_instruction(validated=False, rejection_reason="x"))
        api.ExecuteMarketOrder.assert_not_called()

    def test_rejection_reason_in_error_message(self):
        ex, api, _ = _make_executor()
        result = ex.execute(_make_instruction(validated=False, rejection_reason="daily DD"))
        assert "daily DD" in result.error_message

    def test_rate_limit_skips_order(self):
        ex, api, _ = _make_executor()
        ex._orders_this_minute = RateLimits.NEW_ORDERS
        result = ex.execute(_make_instruction())
        assert result.outcome == OrderResult.SKIPPED

    def test_rate_limit_does_not_call_api(self):
        ex, api, _ = _make_executor()
        ex._orders_this_minute = RateLimits.NEW_ORDERS
        ex.execute(_make_instruction())
        api.ExecuteMarketOrder.assert_not_called()

    def test_rate_limit_logs_warning(self):
        ex, api, logger = _make_executor()
        ex._orders_this_minute = RateLimits.NEW_ORDERS
        ex.execute(_make_instruction())
        logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestClosePosition
# ---------------------------------------------------------------------------

class TestClosePosition:
    def test_returns_true_on_success(self):
        ex, api, _ = _make_executor()
        api.ClosePosition.return_value = _success_result()
        pos = _make_position()
        assert ex.close_position(pos, "strategy exit") is True

    def test_calls_close_position_api(self):
        ex, api, _ = _make_executor()
        api.ClosePosition.return_value = _success_result()
        pos = _make_position()
        ex.close_position(pos, "test reason")
        api.ClosePosition.assert_called_once_with(pos)

    def test_logs_trade_exit_on_success(self):
        ex, api, logger = _make_executor()
        api.ClosePosition.return_value = _success_result()
        pos = _make_position()
        ex.close_position(pos, "reason")
        logger.trade_exit.assert_called_once_with(pos, "reason")

    def test_returns_false_on_api_failure(self):
        ex, api, _ = _make_executor()
        api.ClosePosition.return_value = _failed_result()
        pos = _make_position()
        assert ex.close_position(pos, "reason") is False

    def test_logs_error_on_api_failure(self):
        ex, api, logger = _make_executor()
        api.ClosePosition.return_value = _failed_result("CloseError")
        ex.close_position(_make_position(), "reason")
        logger.error.assert_called()

    def test_returns_false_on_exception(self):
        ex, api, _ = _make_executor()
        api.ClosePosition.side_effect = RuntimeError("close failed")
        assert ex.close_position(_make_position(), "reason") is False

    def test_logs_error_on_exception(self):
        ex, api, logger = _make_executor()
        api.ClosePosition.side_effect = RuntimeError("crash")
        ex.close_position(_make_position(), "reason")
        logger.error.assert_called()

    def test_does_not_log_exit_on_failure(self):
        ex, api, logger = _make_executor()
        api.ClosePosition.return_value = _failed_result()
        ex.close_position(_make_position(), "reason")
        logger.trade_exit.assert_not_called()


# ---------------------------------------------------------------------------
# TestCloseAllPositions
# ---------------------------------------------------------------------------

class TestCloseAllPositions:
    def test_closes_bot_positions_only(self):
        ex, api, _ = _make_executor()
        bot_pos = _make_position(label="ArgoAlgo_TF_EURUSD", pos_id=1)
        other_pos = _make_position(label="Manual_EUR", pos_id=2)
        api.Positions.__iter__ = MagicMock(return_value=iter([bot_pos, other_pos]))
        api.ClosePosition.return_value = _success_result()
        ex.close_all_positions("Friday close")
        api.ClosePosition.assert_called_once_with(bot_pos)

    def test_returns_count_of_closed_positions(self):
        ex, api, _ = _make_executor()
        p1 = _make_position(label="ArgoAlgo_TF_EURUSD", pos_id=1)
        p2 = _make_position(label="ArgoAlgo_ME_GBPUSD", pos_id=2)
        api.Positions.__iter__ = MagicMock(return_value=iter([p1, p2]))
        api.ClosePosition.return_value = _success_result()
        assert ex.close_all_positions("reason") == 2

    def test_returns_zero_when_no_positions(self):
        ex, api, _ = _make_executor()
        api.Positions.__iter__ = MagicMock(return_value=iter([]))
        assert ex.close_all_positions("reason") == 0

    def test_partial_failure_counts_only_successes(self):
        ex, api, _ = _make_executor()
        p1 = _make_position(label="ArgoAlgo_TF_EURUSD", pos_id=1)
        p2 = _make_position(label="ArgoAlgo_ME_GBPUSD", pos_id=2)
        api.Positions.__iter__ = MagicMock(return_value=iter([p1, p2]))
        api.ClosePosition.side_effect = [_success_result(), _failed_result()]
        assert ex.close_all_positions("reason") == 1

    def test_logs_risk_action_when_positions_closed(self):
        ex, api, logger = _make_executor()
        p = _make_position(label="ArgoAlgo_TF_EURUSD")
        api.Positions.__iter__ = MagicMock(return_value=iter([p]))
        api.ClosePosition.return_value = _success_result()
        ex.close_all_positions("test")
        logger.risk_action.assert_called()

    def test_no_risk_action_logged_when_nothing_closed(self):
        ex, api, logger = _make_executor()
        other = _make_position(label="OtherBot_TF_EUR")
        api.Positions.__iter__ = MagicMock(return_value=iter([other]))
        ex.close_all_positions("test")
        logger.risk_action.assert_not_called()

    def test_returns_zero_on_positions_exception(self):
        ex, api, _ = _make_executor()
        api.Positions.__iter__ = MagicMock(side_effect=RuntimeError("no positions"))
        assert ex.close_all_positions("reason") == 0

    def test_logs_error_on_positions_exception(self):
        ex, api, logger = _make_executor()
        api.Positions.__iter__ = MagicMock(side_effect=RuntimeError("err"))
        ex.close_all_positions("reason")
        logger.error.assert_called()


# ---------------------------------------------------------------------------
# TestModifySl
# ---------------------------------------------------------------------------

class TestModifySl:
    def test_returns_true_on_success(self):
        ex, _, _ = _make_executor()
        pos = _make_position()
        assert ex.modify_sl(pos, 1.0790) is True

    def test_calls_modify_stop_loss_price(self):
        ex, _, _ = _make_executor()
        pos = _make_position()
        ex.modify_sl(pos, 1.0750)
        pos.ModifyStopLossPrice.assert_called_once_with(1.0750)

    def test_increments_sl_mod_counter(self):
        ex, _, _ = _make_executor()
        ex.modify_sl(_make_position(), 1.0750)
        assert ex._sl_mods_this_minute == 1

    def test_returns_false_on_exception(self):
        ex, _, _ = _make_executor()
        pos = _make_position()
        pos.ModifyStopLossPrice.side_effect = RuntimeError("api error")
        assert ex.modify_sl(pos, 1.0750) is False

    def test_logs_error_on_exception(self):
        ex, _, logger = _make_executor()
        pos = _make_position()
        pos.ModifyStopLossPrice.side_effect = RuntimeError("crash")
        ex.modify_sl(pos, 1.0750)
        logger.error.assert_called()

    def test_does_not_increment_counter_on_exception(self):
        ex, _, _ = _make_executor()
        pos = _make_position()
        pos.ModifyStopLossPrice.side_effect = RuntimeError("crash")
        ex.modify_sl(pos, 1.0750)
        assert ex._sl_mods_this_minute == 0

    def test_rate_limit_returns_false(self):
        ex, _, _ = _make_executor()
        ex._sl_mods_this_minute = RateLimits.MODIFY_PROTECTION_L1
        assert ex.modify_sl(_make_position(), 1.0750) is False

    def test_rate_limit_does_not_call_modify(self):
        ex, _, _ = _make_executor()
        pos = _make_position()
        ex._sl_mods_this_minute = RateLimits.MODIFY_PROTECTION_L1
        ex.modify_sl(pos, 1.0750)
        pos.ModifyStopLossPrice.assert_not_called()

    def test_rate_limit_logs_warning(self):
        ex, _, logger = _make_executor()
        ex._sl_mods_this_minute = RateLimits.MODIFY_PROTECTION_L1
        ex.modify_sl(_make_position(), 1.0750)
        logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestBuildLabel
# ---------------------------------------------------------------------------

class TestBuildLabel:
    def test_standard_label_format(self):
        ex, _, _ = _make_executor()
        assert ex.build_label("TrendFollowing", "EURUSD") == "ArgoAlgo_TR_EURUSD"

    def test_mean_reversion_abbrev(self):
        ex, _, _ = _make_executor()
        assert ex.build_label("MeanReversion", "GBPUSD") == "ArgoAlgo_ME_GBPUSD"

    def test_breakout_abbrev(self):
        ex, _, _ = _make_executor()
        assert ex.build_label("Breakout", "USDJPY") == "ArgoAlgo_BR_USDJPY"

    def test_custom_prefix(self):
        ex, _, _ = _make_executor(label_prefix="MyBot")
        assert ex.build_label("TrendFollowing", "EURUSD") == "MyBot_TR_EURUSD"

    def test_abbrev_is_uppercase(self):
        ex, _, _ = _make_executor()
        label = ex.build_label("trend", "EURUSD")
        assert label.split("_")[1] == "TR"


# ---------------------------------------------------------------------------
# TestIsBotPosition
# ---------------------------------------------------------------------------

class TestIsBotPosition:
    def test_returns_true_for_bot_label(self):
        ex, _, _ = _make_executor()
        pos = _make_position(label="ArgoAlgo_TF_EURUSD")
        assert ex.is_bot_position(pos) is True

    def test_returns_false_for_other_label(self):
        ex, _, _ = _make_executor()
        pos = _make_position(label="Manual_TF_EURUSD")
        assert ex.is_bot_position(pos) is False

    def test_returns_false_for_empty_label(self):
        ex, _, _ = _make_executor()
        pos = _make_position(label="")
        assert ex.is_bot_position(pos) is False

    def test_custom_prefix_matching(self):
        ex, _, _ = _make_executor(label_prefix="TestBot")
        pos = _make_position(label="TestBot_TF_EURUSD")
        assert ex.is_bot_position(pos) is True


# ---------------------------------------------------------------------------
# TestResetRateCounters
# ---------------------------------------------------------------------------

class TestResetRateCounters:
    def test_resets_orders_counter(self):
        ex, _, _ = _make_executor()
        ex._orders_this_minute = 50
        ex.reset_rate_counters()
        assert ex._orders_this_minute == 0

    def test_resets_cancels_counter(self):
        ex, _, _ = _make_executor()
        ex._cancels_this_minute = 10
        ex.reset_rate_counters()
        assert ex._cancels_this_minute == 0

    def test_resets_sl_mods_counter(self):
        ex, _, _ = _make_executor()
        ex._sl_mods_this_minute = 200
        ex.reset_rate_counters()
        assert ex._sl_mods_this_minute == 0

    def test_after_reset_new_order_allowed(self):
        ex, api, _ = _make_executor()
        ex._orders_this_minute = RateLimits.NEW_ORDERS
        ex.reset_rate_counters()
        api.ExecuteMarketOrder.return_value = _success_result()
        result = ex.execute(_make_instruction())
        assert result.outcome == OrderResult.SUCCESS


# ---------------------------------------------------------------------------
# TestRepr
# ---------------------------------------------------------------------------

class TestRepr:
    def test_repr_contains_prefix(self):
        ex, _, _ = _make_executor(label_prefix="ArgoAlgo")
        assert "ArgoAlgo" in repr(ex)

    def test_execution_result_success_repr(self):
        r = ExecutionResult(outcome=OrderResult.SUCCESS, position_id=5)
        assert "SUCCESS" in repr(r)
        assert "5" in repr(r)

    def test_execution_result_failed_repr(self):
        r = ExecutionResult(outcome=OrderResult.FAILED, error_message="err")
        assert "FAILED" in repr(r)
