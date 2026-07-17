"""
test_phase7.py
Phase 7 tests: UIPanel — initialize, update, set_status, set_active_strategy,
panic button, panel text formatting, graceful chart API degradation.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.panel import UIPanel, _PANEL_ID, _PANIC_BTN_ID
from utils.constants import BotStatus
from models.performance import PerformanceSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_panel(api=None, halt_cb=None):
    """Return a UIPanel with a mock API, logger, executor, and halt callback."""
    if api is None:
        api = MagicMock()
    logger = MagicMock()
    executor = MagicMock()
    if halt_cb is None:
        halt_cb = MagicMock()
    panel = UIPanel(api=api, logger=logger, order_executor=executor, on_halt_callback=halt_cb)
    return panel, api, logger, executor, halt_cb


def _make_snapshot(
    equity=10_500.0,
    balance=10_000.0,
    daily_pnl=500.0,
    daily_dd=1.5,
    total_dd=2.0,
    positions=2,
    trades=5,
):
    return PerformanceSnapshot(
        timestamp=datetime.utcnow(),
        balance=balance,
        equity=equity,
        open_positions=positions,
        daily_pnl=daily_pnl,
        daily_drawdown_pct=daily_dd,
        total_drawdown_pct=total_dd,
        trade_count_today=trades,
    )


# ---------------------------------------------------------------------------
# TestInitialize
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_logs_initialized_message(self):
        panel, _, logger, _, _ = _make_panel()
        panel.initialize()
        logger.info.assert_called()
        calls_text = " ".join(str(c) for c in logger.info.call_args_list)
        assert "initialized" in calls_text.lower()

    def test_calls_draw_static_text(self):
        panel, api, _, _, _ = _make_panel()
        panel.initialize()
        api.Chart.DrawStaticText.assert_called()

    def test_calls_draw_button(self):
        panel, api, _, _, _ = _make_panel()
        panel.initialize()
        api.Chart.DrawButton.assert_called()

    def test_panic_button_click_event_wired(self):
        panel, api, _, _, _ = _make_panel()
        # Capture the Click mock BEFORE initialize() reassigns it via +=
        btn = api.Chart.DrawButton.return_value
        original_click = btn.Click
        panel.initialize()
        # btn.Click += handler calls original_click.__iadd__(handler)
        original_click.__iadd__.assert_called_once_with(panel._on_panic_clicked)

    def test_chart_api_failure_does_not_raise(self):
        api = MagicMock()
        api.Chart.DrawStaticText.side_effect = RuntimeError("no chart")
        panel, _, _, _, _ = _make_panel(api=api)
        panel.initialize()  # Must not raise

    def test_button_api_failure_does_not_raise(self):
        api = MagicMock()
        api.Chart.DrawButton.side_effect = AttributeError("no button")
        panel, _, _, _, _ = _make_panel(api=api)
        panel.initialize()  # Must not raise

    def test_initial_status_is_running(self):
        panel, _, _, _, _ = _make_panel()
        assert panel._status == BotStatus.RUNNING


# ---------------------------------------------------------------------------
# TestUpdate
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_stores_snapshot(self):
        panel, _, _, _, _ = _make_panel()
        snap = _make_snapshot()
        panel.update(snap)
        assert panel._last_snapshot is snap

    def test_calls_draw_static_text(self):
        panel, api, _, _, _ = _make_panel()
        panel.update(_make_snapshot())
        api.Chart.DrawStaticText.assert_called()

    def test_panel_text_contains_equity(self):
        panel, api, _, _, _ = _make_panel()
        panel.update(_make_snapshot(equity=12345.67))
        call_args = api.Chart.DrawStaticText.call_args
        text_arg = call_args.args[1] if call_args.args else call_args.kwargs.get("text", "")
        assert "12345.67" in text_arg

    def test_panel_text_contains_daily_pnl(self):
        panel, api, _, _, _ = _make_panel()
        panel.update(_make_snapshot(daily_pnl=250.0))
        call_args = api.Chart.DrawStaticText.call_args
        text_arg = call_args.args[1] if call_args.args else call_args.kwargs.get("text", "")
        assert "+250" in text_arg or "250" in text_arg

    def test_panel_text_contains_positions(self):
        panel, api, _, _, _ = _make_panel()
        panel.update(_make_snapshot(positions=3))
        call_args = api.Chart.DrawStaticText.call_args
        text_arg = call_args.args[1] if call_args.args else call_args.kwargs.get("text", "")
        assert "3" in text_arg

    def test_chart_api_failure_does_not_raise(self):
        api = MagicMock()
        api.Chart.DrawStaticText.side_effect = RuntimeError("chart error")
        panel, _, _, _, _ = _make_panel(api=api)
        panel.update(_make_snapshot())  # Must not raise

    def test_multiple_updates_overwrite_snapshot(self):
        panel, _, _, _, _ = _make_panel()
        snap1 = _make_snapshot(equity=10_000.0)
        snap2 = _make_snapshot(equity=11_000.0)
        panel.update(snap1)
        panel.update(snap2)
        assert panel._last_snapshot is snap2


# ---------------------------------------------------------------------------
# TestSetStatus
# ---------------------------------------------------------------------------

class TestSetStatus:
    def test_updates_internal_status(self):
        panel, _, _, _, _ = _make_panel()
        panel.set_status(BotStatus.HALTED)
        assert panel._status == BotStatus.HALTED

    def test_logs_debug_message(self):
        panel, _, logger, _, _ = _make_panel()
        panel.set_status(BotStatus.PAUSED)
        logger.debug.assert_called()

    def test_debug_message_contains_status_value(self):
        panel, _, logger, _, _ = _make_panel()
        panel.set_status(BotStatus.HALTED)
        calls_text = " ".join(str(c) for c in logger.debug.call_args_list)
        assert "HALTED" in calls_text

    def test_calls_draw_static_text(self):
        panel, api, _, _, _ = _make_panel()
        panel.set_status(BotStatus.STOPPED)
        api.Chart.DrawStaticText.assert_called()

    def test_chart_failure_does_not_raise(self):
        api = MagicMock()
        api.Chart.DrawStaticText.side_effect = RuntimeError("chart error")
        panel, _, _, _, _ = _make_panel(api=api)
        panel.set_status(BotStatus.HALTED)  # Must not raise

    @pytest.mark.parametrize("status", list(BotStatus))
    def test_all_statuses_accepted(self, status):
        panel, _, _, _, _ = _make_panel()
        panel.set_status(status)
        assert panel._status == status


# ---------------------------------------------------------------------------
# TestSetActiveStrategy
# ---------------------------------------------------------------------------

class TestSetActiveStrategy:
    def test_stores_strategy_name(self):
        panel, _, _, _, _ = _make_panel()
        panel.set_active_strategy("TrendFollowing")
        assert panel._active_strategy == "TrendFollowing"

    def test_panel_text_contains_strategy_after_update(self):
        panel, api, _, _, _ = _make_panel()
        panel.set_active_strategy("MeanReversion")
        panel.update(_make_snapshot())
        call_args = api.Chart.DrawStaticText.call_args
        text_arg = call_args.args[1] if call_args.args else call_args.kwargs.get("text", "")
        assert "MeanReversion" in text_arg


# ---------------------------------------------------------------------------
# TestPanicButton
# ---------------------------------------------------------------------------

class TestPanicButton:
    def test_panic_calls_halt_callback(self):
        panel, _, _, executor, halt_cb = _make_panel()
        executor.close_all_positions.return_value = 0
        panel._on_panic_clicked()
        halt_cb.assert_called_once_with("Panic button pressed")

    def test_panic_sets_status_to_halted(self):
        panel, _, _, executor, _ = _make_panel()
        executor.close_all_positions.return_value = 0
        panel._on_panic_clicked()
        assert panel._status == BotStatus.HALTED

    def test_panic_logs_warning(self):
        panel, _, logger, executor, _ = _make_panel()
        executor.close_all_positions.return_value = 1
        panel._on_panic_clicked()
        logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestFormatPanelText
# ---------------------------------------------------------------------------

class TestFormatPanelText:
    def test_contains_status(self):
        panel, _, _, _, _ = _make_panel()
        panel._status = BotStatus.RUNNING
        text = panel._format_panel_text()
        assert "RUNNING" in text

    def test_contains_strategy_name(self):
        panel, _, _, _, _ = _make_panel()
        panel._active_strategy = "Breakout"
        text = panel._format_panel_text()
        assert "Breakout" in text

    def test_no_snapshot_omits_metrics(self):
        panel, _, _, _, _ = _make_panel()
        panel._last_snapshot = None
        text = panel._format_panel_text()
        assert "Equity" not in text

    def test_with_snapshot_includes_equity(self):
        panel, _, _, _, _ = _make_panel()
        panel._last_snapshot = _make_snapshot(equity=9_876.54)
        text = panel._format_panel_text()
        assert "9876.54" in text

    def test_with_snapshot_includes_drawdown(self):
        panel, _, _, _, _ = _make_panel()
        panel._last_snapshot = _make_snapshot(daily_dd=2.5)
        text = panel._format_panel_text()
        assert "2.5" in text

    def test_with_snapshot_includes_trade_count(self):
        panel, _, _, _, _ = _make_panel()
        panel._last_snapshot = _make_snapshot(trades=7)
        text = panel._format_panel_text()
        assert "7" in text


# ---------------------------------------------------------------------------
# TestRepr
# ---------------------------------------------------------------------------

class TestRepr:
    def test_repr_contains_status(self):
        panel, _, _, _, _ = _make_panel()
        panel._status = BotStatus.HALTED
        assert "HALTED" in repr(panel)

    def test_repr_running_initially(self):
        panel, _, _, _, _ = _make_panel()
        assert "RUNNING" in repr(panel)
