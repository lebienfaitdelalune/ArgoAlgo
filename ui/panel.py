"""
panel.py
UIPanel — on-chart control panel displaying real-time bot status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from utils.constants import BotStatus
except ImportError:
    from constants import BotStatus  # type: ignore[no-redef]

if TYPE_CHECKING:
    from core.logger import Logger
    from core.order_executor import OrderExecutor
    from models.performance import PerformanceSnapshot


# Chart element IDs (constants, used across initialize/update)
_PANEL_ID = "ArgoAlgo_Panel"
_PANIC_BTN_ID = "ArgoAlgo_PanicBtn"

# Status → color string mapping for chart display
_STATUS_COLORS = {
    BotStatus.RUNNING: "LimeGreen",
    BotStatus.PAUSED:  "Yellow",
    BotStatus.HALTED:  "Red",
    BotStatus.STOPPED: "Gray",
}


class UIPanel:
    """Manages the on-chart status panel and Panic Button.

    Renders real-time performance metrics directly on the cTrader chart
    and provides a Panic Button to immediately halt all trading.

    Chart elements are created in ``initialize()`` and refreshed on each
    ``update()`` call. All chart API calls are wrapped in try/except so that
    unavailability (e.g., during backtests or unit tests) degrades gracefully.

    Args:
        api: The cTrader Algo API object.
        logger: Shared Logger instance.
        order_executor: OrderExecutor instance for the Panic Button.
        on_halt_callback: Callable invoked when the Panic Button is pressed.
    """

    def __init__(
        self,
        api,
        logger: "Logger",
        order_executor: "OrderExecutor",
        on_halt_callback,
    ) -> None:
        self._api = api
        self._logger = logger
        self._order_executor = order_executor
        self._on_halt_callback = on_halt_callback
        self._status = BotStatus.RUNNING
        self._active_strategy = "None"
        self._last_snapshot: "PerformanceSnapshot | None" = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create and position the panel on the chart.

        Attempts to draw a static text panel and register a Panic Button.
        Failures are caught and logged so the bot continues without a UI.
        Called once during on_start after all modules are ready.
        """
        self._draw_panel(self._format_panel_text())
        self._register_panic_button()
        self._logger.info("UIPanel initialized.")

    # ------------------------------------------------------------------
    # Update / refresh
    # ------------------------------------------------------------------

    def update(self, snapshot: "PerformanceSnapshot") -> None:
        """Refresh all displayed values with the latest performance data.

        Stores the snapshot for later reference and redraws the panel text.

        Args:
            snapshot: Current PerformanceSnapshot.
        """
        self._last_snapshot = snapshot
        self._draw_panel(self._format_panel_text())

    def set_status(self, status: BotStatus) -> None:
        """Update the status indicator and redraw the panel.

        Args:
            status: New BotStatus value.
        """
        self._status = status
        self._logger.debug(f"UIPanel status -> {status.value}")
        self._draw_panel(self._format_panel_text())

    def set_active_strategy(self, strategy_name: str) -> None:
        """Update the displayed active strategy name.

        Args:
            strategy_name: Human-readable strategy name.
        """
        self._active_strategy = strategy_name

    # ------------------------------------------------------------------
    # Panic Button
    # ------------------------------------------------------------------

    def _on_panic_clicked(self) -> None:
        """Handle Panic Button click: close all positions and halt trading."""
        self._logger.warning("PANIC BUTTON activated!")
        closed = self._order_executor.close_all_positions("PANIC")
        self._logger.warning(f"Panic: closed {closed} positions.")
        self._on_halt_callback("Panic button pressed")
        self.set_status(BotStatus.HALTED)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_panel_text(self) -> str:
        """Build the multi-line panel text from current state.

        Returns:
            Formatted string ready for chart display.
        """
        snap = self._last_snapshot
        lines = [
            "═══ ArgoAlgo ═══",
            f"Status   : {self._status.value}",
            f"Strategy : {self._active_strategy}",
        ]
        if snap is not None:
            lines += [
                f"Equity   : {snap.equity:.2f}",
                f"Daily P/L: {snap.daily_pnl:+.2f}",
                f"Daily DD : {snap.daily_drawdown_pct:.2f}%",
                f"Total DD : {snap.total_drawdown_pct:.2f}%",
                f"Positions: {snap.open_positions}",
                f"Trades   : {snap.trade_count_today}",
            ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Chart API wrappers (graceful degradation)
    # ------------------------------------------------------------------

    def _draw_panel(self, text: str) -> None:
        """Attempt to draw/update the panel text on the chart.

        No-ops silently if the chart API is unavailable (backtests / tests).

        Args:
            text: Formatted panel text to display.
        """
        try:
            color_name = _STATUS_COLORS.get(self._status, "White")
            color = getattr(self._api.Chart.Colors, color_name, None)
            self._api.Chart.DrawStaticText(
                _PANEL_ID,
                text,
                self._api.Chart.HorizontalAlignment.Left,
                self._api.Chart.VerticalAlignment.Top,
                color,
            )
        except BaseException:
            pass  # Chart API unavailable — degrade silently

    def _register_panic_button(self) -> None:
        """Attempt to add the Panic Button to the chart.

        Attaches the _on_panic_clicked handler to the button's Click event.
        Fails silently if the chart button API is unavailable.
        """
        try:
            btn = self._api.Chart.DrawButton(
                _PANIC_BTN_ID,
                "PANIC",
                self._api.Chart.HorizontalAlignment.Right,
                self._api.Chart.VerticalAlignment.Top,
            )
            btn.Click += self._on_panic_clicked
        except BaseException:
            pass  # Button API unavailable — degrade silently

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"UIPanel(status={self._status.value})"
