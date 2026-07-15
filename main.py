"""
main.py
TradingBot — cBot entry point for ArgoAlgo.

This class inherits from cTrader's Robot base class and implements the
full cBot lifecycle: on_start, on_bar_closed, on_tick, on_stop, on_error.
All trading logic is delegated to specialist modules.
"""

from __future__ import annotations

try:
    from utils.constants import (
        BOT_VERSION,
        BotStatus,
        Defaults,
        Direction,
        DrawdownStatus,
        LogLevel,
        StrategyMode,
    )
    from utils.helpers import parse_days_string, parse_symbols_string
except ImportError:  # cTrader Cloud flat namespace
    from constants import (  # type: ignore[no-redef]
        BOT_VERSION,
        BotStatus,
        Defaults,
        Direction,
        DrawdownStatus,
        LogLevel,
        StrategyMode,
    )
    from helpers import parse_days_string, parse_symbols_string  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# cTrader parameter declarations
# These are read by the cTrader IDE to populate the UI parameter panel.
# When running outside cTrader (e.g. unit tests), defaults are used.
# ---------------------------------------------------------------------------

# NOTE: In a real cTrader cBot, parameters are declared as class attributes
# using cTrader's parameter decorators. Here we use a plain class with
# getattr()-based fallback for portability across test and live environments.

class TradingBot:
    """Main cBot class for ArgoAlgo.

    Orchestrates all modules and responds to cTrader lifecycle events.
    Inherits from cTrader's Robot class in production; standalone in tests.

    Parameter groups (all configurable in cTrader UI):
        Strategy, Risk Management, Trend Following, Mean Reversion,
        Breakout, Filters, Instruments, Logging.
    """

    # ------------------------------------------------------------------
    # Default parameter values (overridden by cTrader UI at runtime)
    # ------------------------------------------------------------------

    # Strategy
    StrategyMode: str = Defaults.STRATEGY_MODE
    EnableTrend: bool = Defaults.ENABLE_TREND
    EnableMeanReversion: bool = Defaults.ENABLE_MEAN_REVERSION
    EnableBreakout: bool = Defaults.ENABLE_BREAKOUT
    # Exclusive mode: when True, bar-signal strategies, session filters,
    # trailing stops and Friday close are all bypassed — the bot only runs
    # the daily 21:00 UTC cross-sectional rebalance (forward test, demo only).
    EnableXsect: bool = Defaults.ENABLE_XSECT

    # Risk Management
    RiskPerTradePercent: float = Defaults.RISK_PER_TRADE_PCT
    MaxDailyDrawdownPercent: float = Defaults.MAX_DAILY_DRAWDOWN_PCT
    MaxTotalDrawdownPercent: float = Defaults.MAX_TOTAL_DRAWDOWN_PCT
    MaxConcurrentPositions: int = Defaults.MAX_CONCURRENT_POSITIONS
    MaxPositionsPerSymbol: int = Defaults.MAX_POSITIONS_PER_SYMBOL
    MaxSpreadPips: float = Defaults.MAX_SPREAD_PIPS
    TrailingStopEnabled: bool = Defaults.TRAILING_STOP_ENABLED
    TrailingStopTriggerPips: float = Defaults.TRAILING_STOP_TRIGGER_PIPS
    TrailingStopDistancePips: float = Defaults.TRAILING_STOP_DISTANCE_PIPS

    # Trend Following
    TF_FastEmaPeriod: int = Defaults.TF_FAST_EMA_PERIOD
    TF_SlowEmaPeriod: int = Defaults.TF_SLOW_EMA_PERIOD
    TF_AdxPeriod: int = Defaults.TF_ADX_PERIOD
    TF_AdxThreshold: float = Defaults.TF_ADX_THRESHOLD
    TF_StopLossAtrMultiplier: float = Defaults.TF_SL_ATR_MULTIPLIER
    TF_TakeProfitRR: float = Defaults.TF_TP_RR

    # Mean Reversion
    MR_BollingerPeriod: int = Defaults.MR_BOLLINGER_PERIOD
    MR_BollingerDeviation: float = Defaults.MR_BOLLINGER_DEVIATION
    MR_RsiPeriod: int = Defaults.MR_RSI_PERIOD
    MR_RsiOversold: float = Defaults.MR_RSI_OVERSOLD
    MR_RsiOverbought: float = Defaults.MR_RSI_OVERBOUGHT
    MR_AdxFilterPeriod: int = Defaults.MR_ADX_FILTER_PERIOD
    MR_AdxFilterThreshold: float = Defaults.MR_ADX_FILTER_THRESHOLD

    # Breakout
    BO_DonchianPeriod: int = Defaults.BO_DONCHIAN_PERIOD
    BO_AtrPeriod: int = Defaults.BO_ATR_PERIOD
    BO_AtrMinThreshold: float = Defaults.BO_ATR_MIN_THRESHOLD
    BO_StopLossAtrMultiplier: float = Defaults.BO_SL_ATR_MULTIPLIER
    BO_TakeProfitRR: float = Defaults.BO_TP_RR

    # Filters
    TradingStartHourUTC: int = Defaults.TRADING_START_HOUR_UTC
    TradingEndHourUTC: int = Defaults.TRADING_END_HOUR_UTC
    TradeDaysOfWeek: str = Defaults.TRADE_DAYS_OF_WEEK
    FridayCloseEnabled: bool = Defaults.FRIDAY_CLOSE_ENABLED
    FridayCloseHourUTC: int = Defaults.FRIDAY_CLOSE_HOUR_UTC

    # Instruments
    TradedSymbols: str = Defaults.TRADED_SYMBOLS

    # Logging
    LogLevel: str = Defaults.LOG_LEVEL
    FileLogging: bool = Defaults.FILE_LOGGING

    # ------------------------------------------------------------------
    # Constructor (used in tests; cTrader injects `api` differently)
    # ------------------------------------------------------------------

    def __init__(self, api=None) -> None:
        """Initialise TradingBot with an optional API object.

        In production, cTrader populates `api` automatically.
        In tests, a mock API object is passed explicitly.

        Args:
            api: The cTrader Algo API object (or a mock for testing).
        """
        self._api = api
        self._is_halted: bool = False
        self._permanent_halt: bool = False   # True = total limit or panic; False = daily limit (recoverable)
        self._symbols: list[str] = []
        self._allowed_days: list[str] = []

        # Module references (populated in on_start)
        self._logger = None
        self._data_provider = None
        self._risk_manager = None
        self._order_executor = None
        self._strategy_engine = None
        self._ui_panel = None
        self._xsect = None

    # ------------------------------------------------------------------
    # cBot lifecycle methods
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Initialise all modules and prepare the bot for trading.

        Called once by cTrader when the cBot instance starts.
        Sequence: Logger → DataProvider → RiskManager → OrderExecutor
                  → StrategyEngine → UIPanel → Event subscriptions.
        """
        try:
            self._bootstrap_logger()
        except BaseException:
            pass  # Logger creation failed — proceed without logging
        try:
            self._logger.info(f"ArgoAlgo v{BOT_VERSION} starting...")
            self._log_startup_banner()
        except BaseException:
            pass

        try:
            self._symbols = parse_symbols_string(self.TradedSymbols)
            self._allowed_days = parse_days_string(self.TradeDaysOfWeek)
        except BaseException:
            pass

        for _name, _fn in [
            ("DataProvider",    self._bootstrap_data_provider),
            ("RiskManager",     self._bootstrap_risk_manager),
            ("OrderExecutor",   self._bootstrap_order_executor),
            ("StrategyEngine",  self._bootstrap_strategy_engine),
            ("XsectForward",    self._bootstrap_xsect),
            ("UIPanel",         self._bootstrap_ui_panel),
            ("Events",          self._subscribe_events),
        ]:
            try:
                _fn()
            except BaseException as exc:
                if self._logger:
                    self._logger.error(f"{_name} bootstrap failed: {type(exc).__name__}")

        if self._logger:
            self._logger.info("Bot started successfully. Awaiting first bar close.")

    def on_bar_closed(self) -> None:
        """Main trading loop — called on every bar close event.

        Checks halted state, drawdown limits, session filters, evaluates
        strategies, validates signals, and executes valid instructions.
        """
        try:
            self._on_bar_closed_impl()
        except BaseException as exc:
            if self._logger:
                self._logger.error(f"Unhandled error in on_bar_closed: {type(exc).__name__}")

    def _on_bar_closed_impl(self) -> None:
        """Internal implementation of on_bar_closed logic."""
        if self._risk_manager is None:
            return

        # Day rollover MUST run even when halted so daily drawdown counters
        # reset on the new day and the bot can resume trading.
        self._logger.debug("BC-1: day rollover check")
        try:
            self._risk_manager.check_day_rollover()
        except BaseException as exc:
            self._logger.error(f"BC-1 day rollover failed: {type(exc).__name__}")

        # Rate counters were never reset before 2026-07-15 — they'd grow
        # until the 500/min cap silently blocked all orders (~8 months in).
        if self._order_executor:
            try:
                self._order_executor.reset_rate_counters()
            except BaseException:
                pass

        self._logger.debug("BC-2: drawdown check")
        try:
            dd_status = self._risk_manager.check_drawdown_limits()
        except BaseException as exc:
            self._logger.error(f"BC-2 drawdown check failed: {type(exc).__name__}")
            dd_status = DrawdownStatus.OK
        if dd_status == DrawdownStatus.DAILY_LIMIT_BREACHED:
            if not self._is_halted:
                self._halt_trading("Daily drawdown limit reached")
            return
        if dd_status == DrawdownStatus.TOTAL_LIMIT_BREACHED:
            self._halt_trading("Total drawdown limit reached", permanent=True)
            return

        # Drawdown is within limits — un-halt if the previous halt was for a daily
        # limit only (not permanent: no total-limit breach, no panic button).
        if self._is_halted and not self._permanent_halt:
            self._is_halted = False
            self._logger.info("Daily drawdown cleared — resuming trading")
            if self._ui_panel:
                self._ui_panel.set_status(BotStatus.RUNNING)

        if self._is_halted:
            return

        # Xsect forward-test mode is exclusive: no session filter (rebalance
        # is at 21 UTC, outside the 8-16 session), no bar-signal strategies,
        # no strategy exits. Drawdown backstops above still apply.
        if self.EnableXsect:
            self._logger.debug("BC-X: xsect rebalance check")
            try:
                self._data_provider.update()
            except BaseException as exc:
                self._logger.error(f"BC-X data update failed: {type(exc).__name__}")
            try:
                if self._xsect:
                    self._xsect.on_bar_closed()
            except BaseException as exc:
                self._logger.error(f"BC-X rebalance failed: {type(exc).__name__}")
            return

        self._logger.debug("BC-3: session filter")
        # Session filter
        try:
            session_active = self._is_session_active()
        except BaseException as exc:
            self._logger.error(f"BC-3 session check failed: {type(exc).__name__}")
            session_active = True
        if not session_active:
            return

        self._logger.debug("BC-4: data update")
        # Update market data state
        try:
            self._data_provider.update()
        except BaseException as exc:
            self._logger.error(f"BC-4 data update failed: {type(exc).__name__}")

        self._logger.debug("BC-5: strategy evaluation")
        # Generate and execute signals
        try:
            signals = self._strategy_engine.evaluate(self._symbols)
        except BaseException as exc:
            self._logger.error(f"BC-5 strategy eval failed: {type(exc).__name__}")
            signals = []
        if signals:
            self._logger.info(
                f"BC-5: {len(signals)} signal(s): "
                f"{[s.symbol+'/'+s.direction.value for s in signals]}"
            )

        for signal in signals:
            try:
                instruction = self._risk_manager.validate(signal)
                if instruction.validated:
                    self._logger.debug("BC-6: executing order")
                    result = self._order_executor.execute(instruction)
                    self._logger.debug(f"BC-6 execution result: {result}")
            except BaseException as exc:
                self._logger.error(f"BC-6 validate/execute failed for {signal.symbol}: {type(exc).__name__}")

        self._logger.debug("BC-7: strategy exits")
        # Strategy-driven exits
        try:
            positions = list(self._api.Positions) if self._api else []
        except BaseException:
            positions = []
        try:
            exits = self._strategy_engine.check_exits(positions)
            for position, reason in exits:
                self._order_executor.close_position(position, reason)
        except BaseException as exc:
            self._logger.error(f"BC-7 exits check failed: {type(exc).__name__}")

    def on_tick(self) -> None:
        """Tick handler — lightweight operations only.

        Manages trailing stops and updates UIPanel equity display.
        """
        if self._is_halted or self._risk_manager is None:
            return

        # Xsect legs have no SL by design — trailing must never touch them.
        if self.TrailingStopEnabled and not self.EnableXsect:
            self._risk_manager.update_trailing_stops()

    def on_stop(self) -> None:
        """Cleanup handler — called when the cBot is stopped.

        Logs a daily summary, optionally closes positions on Friday, and
        records the final account balance before shutdown.
        """
        if self._logger:
            self._logger.info("Bot stopping...")

        if self._ui_panel:
            self._ui_panel.set_status(BotStatus.STOPPED)

        # Emit daily P/L summary
        if self._logger and self._risk_manager:
            try:
                snapshot = self._build_performance_snapshot()
                self._logger.daily_summary(snapshot)
            except BaseException as exc:
                self._logger.error("Failed to generate daily summary on stop", exc=exc)

        # Friday end-of-week position close (xsect holds over weekends by design)
        if self._order_executor and self.FridayCloseEnabled and not self.EnableXsect:
            try:
                now = self._api.Server.Time if self._api else None
                if now and now.DayOfWeek.ToString()[:3] == "Fri" and now.Hour >= self.FridayCloseHourUTC:
                    closed = self._order_executor.close_all_positions("Friday end-of-week close")
                    if self._logger:
                        self._logger.risk_action(f"Friday close: closed {closed} position(s)")
            except BaseException as exc:
                if self._logger:
                    self._logger.error("Friday close error during on_stop", exc=exc)

        # Log final account state
        if self._api and self._logger:
            try:
                balance = self._api.Account.Balance
                equity = self._api.Account.Equity
                self._logger.info(f"Final balance={balance:.2f} equity={equity:.2f}")
            except BaseException:
                pass

        if self._logger:
            self._logger.info("Bot stopped.")

    def on_error(self, error) -> None:
        """Global error handler — called by cTrader on unhandled exceptions.

        Logs the error and sends a push notification for critical failures.
        Does not re-raise — cTrader handles recovery.

        Args:
            error: The cTrader error object.
        """
        # Avoid calling str(error) on .NET objects — can throw uncatchable NullReferenceException
        error_type = type(error).__name__
        if self._logger:
            self._logger.error(f"cTrader error type: {error_type}")
        if self._api:
            try:
                self._api.Notifications.SendPushNotification(f"ArgoAlgo ERROR: {error_type}")
            except BaseException:
                pass

    # ------------------------------------------------------------------
    # Halt / resume
    # ------------------------------------------------------------------

    def _halt_trading(self, reason: str, permanent: bool = False) -> None:
        """Halt all new trading activity.

        Sets the internal halted flag, updates the UI, and sends a
        critical push notification. Does not close existing positions.

        Args:
            reason: Human-readable reason for the halt.
            permanent: If True (total drawdown breach or panic), the halt
                persists across day rollovers. If False (daily limit), the
                bot resumes automatically on the next day's bar close.
        """
        self._is_halted = True
        if permanent:
            self._permanent_halt = True
        if self._logger:
            self._logger.risk_action(f"Trading HALTED: {reason}")
        if self._ui_panel:
            self._ui_panel.set_status(BotStatus.HALTED)
        if self._api:
            try:
                self._api.Notifications.SendPushNotification(f"ArgoAlgo HALTED: {reason}")
            except BaseException:
                pass

    # ------------------------------------------------------------------
    # Session / day-of-week filtering
    # ------------------------------------------------------------------

    def _is_session_active(self) -> bool:
        """Return True if the current UTC time is within the trading session.

        Checks both the hour range and the day-of-week filter.
        """
        try:
            from utils.helpers import is_trading_day, is_within_trading_hours
        except ImportError:
            from helpers import is_trading_day, is_within_trading_hours  # type: ignore[no-redef]

        if self._api is None:
            return True  # Allow in tests without a real API

        try:
            # Use bar open time from market data (always UTC), not Server.Time
            # which returns broker local time (UTC+3 on IC Markets).
            bars = self._data_provider.get_bars(
                self._symbols[0], self._data_provider.primary_timeframe
            )
            now = bars.OpenTimes.LastValue
            hour = now.Hour
            day = now.DayOfWeek.ToString()[:3]  # "Monday" -> "Mon"
        except BaseException:
            return True  # Fail open if time unavailable

        if not is_trading_day(day, self._allowed_days):
            return False

        # Friday close filter — must run BEFORE the session-hour check, otherwise
        # the session filter returns False at end-of-session and this branch is
        # unreachable, leaving open positions to carry over the weekend.
        if self.FridayCloseEnabled and day == "Fri" and hour >= self.FridayCloseHourUTC:
            if self._order_executor:
                self._order_executor.close_all_positions("Friday end-of-week close")
            return False  # Skip new entries; trading resumes Monday automatically

        if not is_within_trading_hours(hour, self.TradingStartHourUTC, self.TradingEndHourUTC):
            return False

        return True

    # ------------------------------------------------------------------
    # Event subscription handlers
    # ------------------------------------------------------------------

    def _subscribe_events(self) -> None:
        """Subscribe to cTrader position and order lifecycle events.

        Each subscription is isolated in its own try/except so that a
        .NET InvalidOperationException on one event does not abort the rest.
        In cTrader Cloud, Python.NET may not wrap all .NET exceptions as
        Python exceptions, so each += must be independently guarded.
        """
        if self._api is None:
            return
        try:
            self._api.Positions.Opened += self._on_position_opened
        except BaseException:
            pass
        try:
            self._api.Positions.Modified += self._on_position_modified
        except BaseException:
            pass
        try:
            self._api.Positions.Closed += self._on_position_closed
        except BaseException:
            pass
        try:
            self._api.PendingOrders.Created += self._on_pending_order_created
        except BaseException:
            pass
        try:
            self._api.PendingOrders.Filled += self._on_pending_order_filled
        except BaseException:
            pass
        try:
            self._api.PendingOrders.Cancelled += self._on_pending_order_cancelled
        except BaseException:
            pass

    def _on_position_opened(self, args) -> None:
        try:
            pos = args.Position
            self._logger.info(
                f"Position opened: {pos.SymbolName} {pos.TradeType} "
                f"vol={pos.VolumeInUnits:.0f} entry={pos.EntryPrice:.5f}"
            )
        except BaseException:
            pass
        # Notify RiskManager for daily trade-count tracking (bot positions only)
        try:
            pos = args.Position
            if self._risk_manager and str(pos.Label).startswith(Defaults.LABEL_PREFIX):
                self._risk_manager.notify_position_opened()
        except BaseException:
            pass

    def _on_position_modified(self, args) -> None:
        try:
            pos = args.Position
            self._logger.debug(
                f"Position modified: #{pos.Id} "
                f"sl={pos.StopLoss} tp={pos.TakeProfit}"
            )
        except BaseException:
            pass

    def _on_position_closed(self, args) -> None:
        try:
            pos = args.Position
            self._logger.info(
                f"Position closed: {pos.SymbolName} "
                f"pnl={pos.NetProfit:+.2f} label={pos.Label}"
            )
        except BaseException:
            pass
        # Notify RiskManager for post-loss cooldown tracking (bot positions only).
        # Pass position_id so the trailing-stop map entry is evicted (prevents
        # unbounded growth over thousands of closed trades).
        try:
            pos = args.Position
            if self._risk_manager and str(pos.Label).startswith(Defaults.LABEL_PREFIX):
                self._risk_manager.notify_position_closed(
                    float(pos.NetProfit),
                    position_id=getattr(pos, "Id", None),
                )
        except BaseException:
            pass
        if self._strategy_engine:
            try:
                self._strategy_engine.check_exits([])
            except BaseException:
                pass

    def _on_pending_order_created(self, args) -> None:
        try:
            order = args.PendingOrder
            self._logger.debug(f"Pending order created: #{order.Id} {order.SymbolName}")
        except BaseException:
            pass

    def _on_pending_order_filled(self, args) -> None:
        try:
            order = args.PendingOrder
            self._logger.info(f"Pending order filled: #{order.Id} {order.SymbolName}")
        except BaseException:
            pass

    def _on_pending_order_cancelled(self, args) -> None:
        try:
            order = args.PendingOrder
            self._logger.info(f"Pending order cancelled: #{order.Id} {order.SymbolName}")
        except BaseException:
            pass

    # ------------------------------------------------------------------
    # Module bootstrapping (called from on_start)
    # ------------------------------------------------------------------

    def _bootstrap_logger(self) -> None:
        try:
            from core.logger import Logger
        except ImportError:
            from logger import Logger  # type: ignore[no-redef]

        log_level = LogLevel[self.LogLevel] if isinstance(self.LogLevel, str) else self.LogLevel
        self._logger = Logger(
            api=self._api,
            log_level=log_level,
            file_logging=self.FileLogging,
            label_prefix=Defaults.LABEL_PREFIX,
        )

    def _bootstrap_data_provider(self) -> None:
        try:
            from core.data_provider import DataProvider
        except ImportError:
            from data_provider import DataProvider  # type: ignore[no-redef]

        indicator_params = {
            "ema_fast_period": self.TF_FastEmaPeriod,
            "ema_slow_period": self.TF_SlowEmaPeriod,
            "adx_period": self.TF_AdxPeriod,
            "bollinger_period": self.MR_BollingerPeriod,
            "bollinger_deviation": self.MR_BollingerDeviation,
            "rsi_period": self.MR_RsiPeriod,
            "adx_filter_period": self.MR_AdxFilterPeriod,
            "donchian_period": self.BO_DonchianPeriod,
            "atr_bo_period": self.BO_AtrPeriod,
        }
        self._data_provider = DataProvider(
            api=self._api,
            symbols=self._symbols,
            logger=self._logger,
            indicator_params=indicator_params,
            init_indicators=not self.EnableXsect,
        )

        # Primary TF must match the chart TF the cBot is deployed on (H1).
        # Use the chart's TimeFrame OBJECT: api.TimeFrame doesn't exist
        # (TimeFrame is a standalone enum, same gotcha as TradeType) and the
        # "H1" string fallback matches no MarketData.GetBars overload.
        try:
            primary_tf = self._api.Bars.TimeFrame
        except BaseException:
            primary_tf = "H1"

        self._data_provider.initialize([primary_tf])

    def _bootstrap_risk_manager(self) -> None:
        try:
            from core.risk_manager import RiskManager, RiskParams
        except ImportError:
            from risk_manager import RiskManager, RiskParams  # type: ignore[no-redef]

        # Xsect forward test (demo): its no-halt backtest maxDD exceeds the
        # default 5%/10% limits at this account size — normal variance would
        # halt the test. Use the wider demo-only backstops instead.
        daily_dd = (Defaults.XS_MAX_DAILY_DD_PCT if self.EnableXsect
                    else self.MaxDailyDrawdownPercent)
        total_dd = (Defaults.XS_MAX_TOTAL_DD_PCT if self.EnableXsect
                    else self.MaxTotalDrawdownPercent)
        params = RiskParams(
            risk_per_trade_pct=self.RiskPerTradePercent,
            max_daily_drawdown_pct=daily_dd,
            max_total_drawdown_pct=total_dd,
            max_concurrent_positions=self.MaxConcurrentPositions,
            max_positions_per_symbol=self.MaxPositionsPerSymbol,
            max_spread_pips=self.MaxSpreadPips,
            trailing_stop_enabled=self.TrailingStopEnabled,
            trailing_stop_trigger_pips=self.TrailingStopTriggerPips,
            trailing_stop_distance_pips=self.TrailingStopDistancePips,
        )
        self._risk_manager = RiskManager(
            api=self._api,
            params=params,
            logger=self._logger,
            data_provider=self._data_provider,
            label_prefix=Defaults.LABEL_PREFIX,
        )
        try:
            initial_balance = self._api.Account.Balance if self._api else 10_000.0
        except BaseException:
            initial_balance = 10_000.0
        self._risk_manager.initialize(initial_balance)

    def _bootstrap_order_executor(self) -> None:
        try:
            from core.order_executor import OrderExecutor
        except ImportError:
            from order_executor import OrderExecutor  # type: ignore[no-redef]

        self._order_executor = OrderExecutor(
            api=self._api,
            logger=self._logger,
            label_prefix=Defaults.LABEL_PREFIX,
        )

    def _bootstrap_strategy_engine(self) -> None:
        try:
            from core.strategy_engine import StrategyEngine
            from strategies.breakout import BreakoutStrategy
            from strategies.mean_reversion import MeanReversionStrategy
            from strategies.trend_following import TrendFollowingStrategy
        except ImportError:
            from strategy_engine import StrategyEngine  # type: ignore[no-redef]
            from breakout import BreakoutStrategy  # type: ignore[no-redef]
            from mean_reversion import MeanReversionStrategy  # type: ignore[no-redef]
            from trend_following import TrendFollowingStrategy  # type: ignore[no-redef]

        strategies = []
        common = dict(
            api=self._api,
            data_provider=self._data_provider,
            logger=self._logger,
        )

        if self.EnableTrend:
            strategies.append(TrendFollowingStrategy(
                params={
                    "fast_ema_period": self.TF_FastEmaPeriod,
                    "slow_ema_period": self.TF_SlowEmaPeriod,
                    "adx_period": self.TF_AdxPeriod,
                    "adx_threshold": self.TF_AdxThreshold,
                    "sl_atr_multiplier": self.TF_StopLossAtrMultiplier,
                    "tp_rr": self.TF_TakeProfitRR,
                },
                **common,
            ))

        if self.EnableMeanReversion:
            strategies.append(MeanReversionStrategy(
                params={
                    "bollinger_period": self.MR_BollingerPeriod,
                    "bollinger_deviation": self.MR_BollingerDeviation,
                    "rsi_period": self.MR_RsiPeriod,
                    "rsi_oversold": self.MR_RsiOversold,
                    "rsi_overbought": self.MR_RsiOverbought,
                    "adx_filter_period": self.MR_AdxFilterPeriod,
                    "adx_filter_threshold": self.MR_AdxFilterThreshold,
                },
                **common,
            ))

        if self.EnableBreakout:
            strategies.append(BreakoutStrategy(
                params={
                    "donchian_period": self.BO_DonchianPeriod,
                    "atr_period": self.BO_AtrPeriod,
                    "atr_min_threshold": self.BO_AtrMinThreshold,
                    "sl_atr_multiplier": self.BO_StopLossAtrMultiplier,
                    "tp_rr": self.BO_TakeProfitRR,
                },
                **common,
            ))

        mode = StrategyMode(self.StrategyMode)
        self._strategy_engine = StrategyEngine(
            strategies=strategies,
            mode=mode,
            adx_threshold=self.TF_AdxThreshold,
            data_provider=self._data_provider,
            logger=self._logger,
        )
        self._logger.info(
            f"StrategyEngine: {self._strategy_engine.strategy_names} | mode={mode.value}"
        )

    def _bootstrap_xsect(self) -> None:
        if not self.EnableXsect:
            return
        try:
            from core.xsect_forward import XsectForward
        except ImportError:
            from xsect_forward import XsectForward  # type: ignore[no-redef]

        self._xsect = XsectForward(
            api=self._api,
            data_provider=self._data_provider,
            order_executor=self._order_executor,
            logger=self._logger,
            symbols=self._symbols,
            units_per_leg=Defaults.XS_UNITS_PER_LEG,
        )
        self._xsect.initialize()
        self._logger.info(
            f"XSect forward test active: {self._symbols} "
            f"@{Defaults.XS_REBAL_HOUR_UTC}:00 UTC, "
            f"{Defaults.XS_UNITS_PER_LEG} units/leg, no SL"
        )

    def _bootstrap_ui_panel(self) -> None:
        try:
            from ui.panel import UIPanel
        except ImportError:
            from panel import UIPanel  # type: ignore[no-redef]

        self._ui_panel = UIPanel(
            api=self._api,
            logger=self._logger,
            order_executor=self._order_executor,
            on_halt_callback=lambda reason: self._halt_trading(reason, permanent=True),
        )
        self._ui_panel.initialize()

    # ------------------------------------------------------------------
    # Performance snapshot
    # ------------------------------------------------------------------

    def _build_performance_snapshot(self) -> "PerformanceSnapshot":
        """Build a PerformanceSnapshot from current account state.

        Values from RiskManager (daily P/L, drawdown, trade count) are
        populated in Phase 4 when the full risk pipeline is implemented.

        Returns:
            A PerformanceSnapshot with current balance and equity filled in.
        """
        from datetime import datetime

        try:
            from models.performance import PerformanceSnapshot
        except ImportError:
            from performance import PerformanceSnapshot  # type: ignore[no-redef]

        balance = self._api.Account.Balance if self._api else 0.0
        equity = self._api.Account.Equity if self._api else 0.0
        return PerformanceSnapshot(
            timestamp=datetime.utcnow(),
            balance=balance,
            equity=equity,
            open_positions=0,       # Implemented in Phase 4
            daily_pnl=0.0,          # Implemented in Phase 4
            daily_drawdown_pct=0.0, # Implemented in Phase 4
            total_drawdown_pct=0.0, # Implemented in Phase 4
            trade_count_today=0,    # Implemented in Phase 4
        )

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log_startup_banner(self) -> None:
        """Log a structured startup banner with account and config info."""
        self._logger.info("=" * 60)
        self._logger.info(f"  ArgoAlgo v{BOT_VERSION}")
        self._logger.info(f"  Symbols  : {self.TradedSymbols}")
        self._logger.info(f"  Strategy : {self.StrategyMode}")
        self._logger.info(f"  Risk     : {self.RiskPerTradePercent}% per trade")
        self._logger.info(f"  Max DD   : {self.MaxTotalDrawdownPercent}% total")
        self._logger.info(f"  Session  : {self.TradingStartHourUTC}–{self.TradingEndHourUTC} UTC")
        self._logger.info("=" * 60)

    def __repr__(self) -> str:
        return f"TradingBot(v{BOT_VERSION}, halted={self._is_halted})"
