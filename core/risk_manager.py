"""
risk_manager.py
RiskManager — validates trade signals and enforces all capital protection rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from utils.constants import Defaults, Direction, DrawdownStatus, SLType
    from utils.helpers import calculate_position_volume
except ImportError:
    from constants import Defaults, Direction, DrawdownStatus, SLType  # type: ignore[no-redef]
    from helpers import calculate_position_volume  # type: ignore[no-redef]

if TYPE_CHECKING:
    from core.data_provider import DataProvider
    from core.logger import Logger
    from models.trade_instruction import TradeInstruction
    from models.trade_signal import TradeSignal


@dataclass
class RiskParams:
    """All risk-related cBot parameters in a single container.

    Attributes:
        risk_per_trade_pct: Max risk per trade as % of balance.
        max_daily_drawdown_pct: Max daily loss as % of daily start balance.
        max_total_drawdown_pct: Max total loss as % of high-water mark.
        max_concurrent_positions: Hard cap on total open positions.
        max_positions_per_symbol: Hard cap on positions per instrument.
        max_spread_pips: Maximum allowable spread before skipping trade.
        trailing_stop_enabled: Whether trailing stop management is active.
        trailing_stop_trigger_pips: Pips in profit before trailing activates.
        trailing_stop_distance_pips: Trail distance behind current price.
    """

    risk_per_trade_pct: float
    max_daily_drawdown_pct: float
    max_total_drawdown_pct: float
    max_concurrent_positions: int
    max_positions_per_symbol: int
    max_spread_pips: float
    trailing_stop_enabled: bool
    trailing_stop_trigger_pips: float
    trailing_stop_distance_pips: float


class RiskManager:
    """Validates trade signals and enforces capital protection rules.

    Every TradeSignal must pass through validate() before reaching
    the OrderExecutor. Also manages trailing stops on open positions.

    Args:
        api: The cTrader Algo API object.
        params: RiskParams dataclass with all risk configuration.
        logger: Shared Logger instance.
        data_provider: DataProvider for symbol data and spread checks.
        label_prefix: Prefix used to identify bot-managed positions.
    """

    def __init__(
        self,
        api,
        params: "RiskParams",
        logger: "Logger",
        data_provider: "DataProvider",
        label_prefix: str = Defaults.LABEL_PREFIX,
    ) -> None:
        self._api = api
        self._params = params
        self._logger = logger
        self._data_provider = data_provider
        self._label_prefix = label_prefix

        self._initial_balance: float = 0.0
        self._daily_start_balance: float = 0.0
        self._daily_start_date = None          # cTrader Date object; None until first rollover check
        self._high_water_mark: float = 0.0
        self._trailing_stop_map: dict[int, float] = {}  # position_id -> current trail SL price

        # Trade throttling state
        self._trades_opened_today: int = 0     # Reset on day rollover
        self._last_loss_server_time = None     # cTrader DateTime of last losing close (None = no losses)

        # Tick-throttle state for update_trailing_stops (see _TRAIL_MIN_INTERVAL_SEC).
        self._last_trail_tick_monotonic: float = 0.0

    def initialize(self, initial_balance: float) -> None:
        """Record the starting balances used for drawdown calculations.

        Must be called once during on_start after the account is accessible.

        After recording the boot-time balance, reconstructs throttle state
        (today's trade count, last-loss timestamp, HWM, daily-start balance)
        from the broker's ``History`` collection so that an OOM/restart cycle
        does NOT bypass the daily trade cap, post-loss cooldown, or drawdown
        guards. cTrader Cloud forbids file I/O, so this is the only viable
        persistence channel.

        Args:
            initial_balance: Account balance at bot start.
        """
        self._initial_balance = initial_balance
        self._daily_start_balance = initial_balance
        self._high_water_mark = initial_balance
        self._logger.info(
            f"RiskManager initialized | balance={initial_balance:.2f}"
        )
        try:
            self._reconstruct_state_from_history(initial_balance)
        except BaseException as exc:
            self._logger.error(
                "State reconstruction failed; continuing with fresh counters",
                exc=exc,
            )

    def _reconstruct_state_from_history(self, deploy_balance: float) -> None:
        """Rebuild throttle/DD counters from ``api.History``.

        Reconstructed fields:
            * ``_trades_opened_today``   — count of today's bot-labelled entries.
            * ``_last_loss_server_time`` — close time of the most recent losing
              bot-labelled trade (None if no prior loss). Drives post-loss
              cooldown.
            * ``_high_water_mark``       — running max of (deploy_balance + Σ
              NetProfit) over all bot-labelled history. Conservatively floored
              at current equity so we never report a HWM lower than reality.
            * ``_daily_start_balance``   — current balance minus today's
              realised P&L (so daily DD math anchors at today's open).
            * ``_daily_start_date``      — today's broker date.

        Failures are swallowed; on any exception the caller's fresh-init
        counters remain in place and a single error line is logged.
        """
        if self._api is None:
            return

        try:
            history = list(self._api.History)
        except BaseException:
            history = []

        # Filter to bot-managed trades (label prefix match).
        bot_trades = []
        for tr in history:
            try:
                if str(tr.Label).startswith(self._label_prefix):
                    bot_trades.append(tr)
            except BaseException:
                continue

        if not bot_trades:
            self._logger.info("State reconstruction: no prior bot trades in History")
            return

        # Today's broker date — same source as check_day_rollover so they agree.
        try:
            today = self._api.Server.Time.Date
        except BaseException:
            today = None

        trades_today = 0
        today_pnl = 0.0
        most_recent_loss_time = None

        # Sort by close time so HWM walk is chronological.
        try:
            sorted_trades = sorted(bot_trades, key=lambda t: t.ClosingTime)
        except BaseException:
            sorted_trades = bot_trades

        running_balance = float(deploy_balance)
        hwm_walk = float(deploy_balance)
        for tr in sorted_trades:
            try:
                net_pnl = float(tr.NetProfit)
                running_balance += net_pnl
                if running_balance > hwm_walk:
                    hwm_walk = running_balance
                # Today's tallies — entry day and close day both relevant.
                if today is not None:
                    if tr.EntryTime.Date == today:
                        trades_today += 1
                    if tr.ClosingTime.Date == today:
                        today_pnl += net_pnl
                if net_pnl < 0:
                    if most_recent_loss_time is None or tr.ClosingTime > most_recent_loss_time:
                        most_recent_loss_time = tr.ClosingTime
            except BaseException:
                continue

        self._trades_opened_today = trades_today
        self._last_loss_server_time = most_recent_loss_time

        try:
            current_equity = float(self._api.Account.Equity)
            current_balance = float(self._api.Account.Balance)
        except BaseException:
            current_equity = float(deploy_balance)
            current_balance = float(deploy_balance)

        # HWM: chronological-walk max, but never below current equity (floating winners).
        self._high_water_mark = max(hwm_walk, current_equity)
        # Today's open: current balance minus realised P&L from today's closes.
        self._daily_start_balance = current_balance - today_pnl
        if today is not None:
            self._daily_start_date = today

        cooldown_str = ""
        if most_recent_loss_time is not None:
            try:
                cooldown_str = f" last_loss={most_recent_loss_time}"
            except BaseException:
                cooldown_str = " last_loss=<unprintable>"
        self._logger.info(
            f"State reconstructed from History: "
            f"trades_today={trades_today} hwm={self._high_water_mark:.2f} "
            f"daily_open={self._daily_start_balance:.2f}"
            + cooldown_str
        )

    # ------------------------------------------------------------------
    # Day rollover
    # ------------------------------------------------------------------

    def check_day_rollover(self) -> None:
        """Reset daily tracking state if the UTC date has changed.

        Call at the start of each on_bar_closed event.
        On the first call the date is simply recorded; no reset occurs.
        On subsequent calls a date change triggers a balance snapshot reset.
        """
        if self._api is None:
            return
        try:
            today = self._api.Server.Time.Date
            if self._daily_start_date is None:
                # First call — initialise without resetting balance
                self._daily_start_date = today
                return
            if today != self._daily_start_date:
                balance = self._api.Account.Balance
                self._daily_start_balance = balance
                self._daily_start_date = today
                self._trades_opened_today = 0
                self._logger.info(
                    f"Day rollover: daily balance reset to {balance:.2f}"
                )
        except BaseException as exc:
            self._logger.error("Day rollover check failed", exc=exc)

    # ------------------------------------------------------------------
    # Drawdown monitoring
    # ------------------------------------------------------------------

    def check_drawdown_limits(self) -> DrawdownStatus:
        """Evaluate current equity against daily and total drawdown limits.

        Updates the high-water mark as a side-effect.

        Returns:
            DrawdownStatus.OK if within limits.
            DrawdownStatus.DAILY_LIMIT_BREACHED if daily loss >= threshold.
            DrawdownStatus.TOTAL_LIMIT_BREACHED if total drawdown >= threshold.
        """
        if self._api is None:
            return DrawdownStatus.OK

        try:
            current_equity = self._api.Account.Equity
        except BaseException:
            return DrawdownStatus.OK

        # Update high-water mark
        if current_equity > self._high_water_mark:
            self._high_water_mark = current_equity

        # Daily drawdown
        if self._daily_start_balance > 0:
            daily_loss_pct = (
                (self._daily_start_balance - current_equity)
                / self._daily_start_balance
                * 100.0
            )
            if daily_loss_pct >= self._params.max_daily_drawdown_pct:
                self._logger.risk_action(
                    f"Daily drawdown breached: {daily_loss_pct:.2f}% "
                    f">= {self._params.max_daily_drawdown_pct}%"
                )
                return DrawdownStatus.DAILY_LIMIT_BREACHED

        # Total drawdown from high-water mark
        if self._high_water_mark > 0:
            total_dd_pct = (
                (self._high_water_mark - current_equity)
                / self._high_water_mark
                * 100.0
            )
            if total_dd_pct >= self._params.max_total_drawdown_pct:
                self._logger.risk_action(
                    f"Total drawdown breached: {total_dd_pct:.2f}% "
                    f">= {self._params.max_total_drawdown_pct}%"
                )
                return DrawdownStatus.TOTAL_LIMIT_BREACHED

        return DrawdownStatus.OK

    # ------------------------------------------------------------------
    # Signal validation
    # ------------------------------------------------------------------

    def validate(self, signal: "TradeSignal") -> "TradeInstruction":
        """Run all risk checks on a signal and produce a TradeInstruction.

        Checks are evaluated in order; the first failure causes rejection.
        All rejections are logged at INFO level.

        Checks performed:
            1. Direction — signal must be BUY or SELL (not NONE).
            2. Spread — current spread must be within MaxSpreadPips.
            3. Daily drawdown — daily loss must be below limit.
            4. Total drawdown — total loss from HWM must be below limit.
            5. Max concurrent — open bot positions < MaxConcurrentPositions.
            6. Max per symbol — bot positions for this symbol < MaxPositionsPerSymbol.
            7. Volume — calculated volume must meet symbol minimum.

        Args:
            signal: The TradeSignal produced by a strategy.

        Returns:
            TradeInstruction with validated=True if all checks pass,
            or validated=False with a rejection_reason.
        """
        from models.trade_instruction import TradeInstruction

        def _reject(reason: str) -> TradeInstruction:
            self._logger.info(f"Signal rejected [{signal.symbol}]: {reason}")
            return TradeInstruction(
                signal=signal,
                volume_units=0.0,
                validated=False,
                rejection_reason=reason,
            )

        # 1. Direction
        if signal.direction == Direction.NONE:
            return _reject("No signal direction")

        # 1a. Hard SL cap — reject wide-stop signals so $ loss per trade stays bounded
        if signal.stop_loss_pips > Defaults.MAX_SL_PIPS:
            return _reject(
                f"SL too wide: {signal.stop_loss_pips:.1f} pips "
                f"> cap {Defaults.MAX_SL_PIPS:.0f}"
            )

        # 1b. Daily trade cap
        if self._trades_opened_today >= Defaults.MAX_TRADES_PER_DAY:
            return _reject(
                f"Daily trade cap reached ({self._trades_opened_today}/"
                f"{Defaults.MAX_TRADES_PER_DAY})"
            )

        # 1c. Post-loss cooldown
        if self._is_in_post_loss_cooldown():
            return _reject(
                f"Post-loss cooldown active ({Defaults.POST_LOSS_COOLDOWN_HOURS:.0f}h)"
            )

        # 2. Spread
        try:
            if not self._data_provider.is_spread_acceptable(
                signal.symbol, self._params.max_spread_pips
            ):
                spread = self._data_provider.get_spread_pips(signal.symbol)
                return _reject(f"Spread too wide: {spread:.1f} pips")
        except BaseException as exc:
            self._logger.warning(f"Spread check failed for {signal.symbol}: {exc}")

        # 3. Daily drawdown guard
        daily_status = self._check_daily_drawdown()
        if daily_status == DrawdownStatus.DAILY_LIMIT_BREACHED:
            return _reject("Daily drawdown limit reached")

        # 4. Total drawdown guard
        total_status = self._check_total_drawdown()
        if total_status == DrawdownStatus.TOTAL_LIMIT_BREACHED:
            return _reject("Total drawdown limit reached")

        # 5. Max concurrent positions (bot-managed only)
        open_count = self._count_bot_positions()
        if open_count >= self._params.max_concurrent_positions:
            return _reject(
                f"Max concurrent positions reached ({open_count}/"
                f"{self._params.max_concurrent_positions})"
            )

        # 6. Max positions per symbol
        symbol_count = self._count_bot_positions(symbol=signal.symbol)
        if symbol_count >= self._params.max_positions_per_symbol:
            return _reject(
                f"Max positions for {signal.symbol} reached "
                f"({symbol_count}/{self._params.max_positions_per_symbol})"
            )

        # 7. Volume — reject if calculated volume is below the symbol minimum.
        #    On small accounts, clamping to vol_min silently raises actual risk
        #    per trade above RiskPerTradePercent. With the 20-pip SL cap in check 1a,
        #    the minimum-volume $ loss is already bounded (e.g. 1000 * 20 * 0.10 = $2
        #    on a $200 account = 1%), so clamping here is acceptable — but we log it
        #    so the user can see when the account is too small for their risk setting.
        try:
            balance = self._api.Account.Balance
            sym = self._data_provider.get_symbol(signal.symbol)
            risk_amount = balance * (self._params.risk_per_trade_pct / 100.0)
            if signal.stop_loss_pips > 0:
                raw_vol = risk_amount / (signal.stop_loss_pips * sym.PipValue)
                if raw_vol < sym.VolumeInUnitsMin:
                    actual_risk_pct = (
                        sym.VolumeInUnitsMin * signal.stop_loss_pips * sym.PipValue
                        / balance * 100.0
                    )
                    self._logger.warning(
                        f"Volume clamped to minimum for {signal.symbol}: "
                        f"ideal={raw_vol:.0f} min={sym.VolumeInUnitsMin:.0f} "
                        f"actual_risk={actual_risk_pct:.1f}%"
                    )
        except BaseException:
            pass  # Volume check skipped if data unavailable

        volume = self.calculate_volume(signal.symbol, signal.stop_loss_pips)

        # Final volume sanity check — reject if still below symbol minimum
        # (happens when calculate_volume errors out or returns 0).
        try:
            sym = self._data_provider.get_symbol(signal.symbol)
            vol_min = float(sym.VolumeInUnitsMin)
        except BaseException:
            vol_min = 0.0
        if volume < vol_min or volume <= 0:
            return _reject(
                f"Volume below minimum: {volume:.0f} < {vol_min:.0f}"
            )

        return TradeInstruction(signal=signal, volume_units=volume, validated=True)

    # ------------------------------------------------------------------
    # Trade throttling (daily cap + post-loss cooldown)
    # ------------------------------------------------------------------

    def notify_position_opened(self) -> None:
        """Increment the daily trade counter. Call on every bot-managed open."""
        self._trades_opened_today += 1

    def notify_position_closed(self, net_pnl: float, position_id=None) -> None:
        """Record a losing close so the post-loss cooldown can be enforced.

        Also cleans up the trailing-stop map entry for the closed position so it
        cannot grow unbounded over months of trading.

        Args:
            net_pnl: Realised P&L of the closed position. Losses (<0) trigger cooldown.
            position_id: cTrader Position.Id if available; used to evict the
                trailing-stop map entry. Optional for backwards compatibility —
                callers that don't pass it just leave the entry to be reaped by
                the periodic sweep below.
        """
        # Drop the closed position's trailing-stop entry. Without this, the map
        # accumulates one stale entry per closed trade for the lifetime of the bot.
        if position_id is not None:
            self._trailing_stop_map.pop(position_id, None)

        if net_pnl >= 0:
            return
        try:
            self._last_loss_server_time = self._api.Server.Time
        except BaseException:
            self._last_loss_server_time = None

    def _is_in_post_loss_cooldown(self) -> bool:
        """Return True if we're still within POST_LOSS_COOLDOWN_HOURS of the last loss."""
        if self._last_loss_server_time is None or self._api is None:
            return False
        try:
            now = self._api.Server.Time
            elapsed = now - self._last_loss_server_time
            hours = float(elapsed.TotalHours)
            return hours < Defaults.POST_LOSS_COOLDOWN_HOURS
        except BaseException:
            return False

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_volume(self, symbol: str, stop_loss_pips: float) -> float:
        """Calculate position volume using the fixed fractional risk model.

        Formula: volume = (balance × risk_pct%) / (sl_pips × pip_value)
        Result is clamped to symbol min/max and rounded to volume step.

        Args:
            symbol: Instrument name.
            stop_loss_pips: Stop-loss distance in pips.

        Returns:
            Normalised volume in instrument units. Returns 0.0 on error.
        """
        if stop_loss_pips <= 0:
            self._logger.warning(f"calculate_volume: invalid stop_loss_pips={stop_loss_pips}")
            return 0.0

        try:
            balance = self._api.Account.Balance
            sym = self._data_provider.get_symbol(symbol)
            volume = calculate_position_volume(
                balance=balance,
                risk_pct=self._params.risk_per_trade_pct,
                stop_loss_pips=stop_loss_pips,
                pip_value=sym.PipValue,
                volume_min=sym.VolumeInUnitsMin,
                volume_max=sym.VolumeInUnitsMax,
                volume_step=sym.VolumeInUnitsStep,
            )
            self._logger.debug(
                f"Volume calc: {symbol} sl={stop_loss_pips}pips "
                f"balance={balance:.2f} -> {volume:.0f} units"
            )
            return volume
        except BaseException as exc:
            self._logger.error(f"calculate_volume failed for {symbol}", exc=exc)
            return 0.0

    # ------------------------------------------------------------------
    # SL / TP helpers
    # ------------------------------------------------------------------

    def calculate_sl_pips(
        self,
        symbol: str,
        direction: Direction,
        sl_type: SLType,
        atr_multiplier: float,
        fixed_pips: float = 0.0,
    ) -> float:
        """Calculate stop-loss distance in pips.

        Args:
            symbol: Instrument name.
            direction: Trade direction (unused for ATR but kept for symmetry).
            sl_type: SLType.ATR or SLType.FIXED.
            atr_multiplier: Multiplier applied to ATR value (ATR mode only).
            fixed_pips: Fixed pip distance (Fixed mode only).

        Returns:
            Stop-loss distance in pips. Returns 0.0 on error.
        """
        try:
            sym = self._data_provider.get_symbol(symbol)
            pip_size = sym.PipSize
            min_sl = float(getattr(sym, "MinStopLossInPips", 0.0))

            if sl_type == SLType.ATR:
                atr_indicator = self._data_provider.get_indicator(symbol, "atr")
                atr_value = float(atr_indicator.Result.Last(0))
                sl_pips = (atr_value / pip_size) * atr_multiplier
            else:
                sl_pips = fixed_pips

            return max(sl_pips, min_sl)
        except BaseException as exc:
            self._logger.error(f"calculate_sl_pips failed for {symbol}", exc=exc)
            return 0.0

    def calculate_tp_pips(self, sl_pips: float, rr_ratio: float) -> float:
        """Calculate take-profit distance from a risk/reward ratio.

        Args:
            sl_pips: Stop-loss distance in pips.
            rr_ratio: Desired reward-to-risk ratio (e.g. 2.0 for 2:1).

        Returns:
            Take-profit distance in pips.
        """
        return sl_pips * rr_ratio

    def calculate_tp_to_price(
        self,
        entry_price: float,
        target_price: float,
        direction: Direction,
        pip_size: float,
    ) -> float:
        """Calculate take-profit distance in pips to a specific price target.

        Used by mean-reversion strategies targeting the Bollinger midline.

        Args:
            entry_price: Trade entry price.
            target_price: Desired exit price (e.g. Bollinger middle band).
            direction: BUY or SELL.
            pip_size: Pip size for the instrument.

        Returns:
            Take-profit distance in pips (always positive).
        """
        if direction == Direction.BUY:
            distance = target_price - entry_price
        else:
            distance = entry_price - target_price
        return max(distance / pip_size, 0.0)

    # ------------------------------------------------------------------
    # Trailing stop management
    # ------------------------------------------------------------------

    # Rate-limit the heavy trailing-stop loop. on_tick fires roughly once per
    # second on a liquid EURUSD feed; running the full loop every tick allocates
    # a fresh Python list of Python.NET position wrappers + indicator marshalling
    # calls, which the 23/04 logs suggest was leaking enough memory to OOM the
    # cTrader Cloud sandbox 3× in one day. 10s is plenty for an H1 strategy.
    _TRAIL_MIN_INTERVAL_SEC: float = 10.0

    def update_trailing_stops(self) -> None:
        """Advance trailing stops for all bot-managed open positions.

        Logic per position (when run):
          - If profit >= TrailingStopTriggerPips and not yet active:
              move SL to breakeven (entry price) and mark as active.
          - If already active:
              compute new SL at current_price - trail_distance (buy) or
              current_price + trail_distance (sell); advance only, never retreat.

        Performance guards:
          1. Early-exit if trailing disabled.
          2. Throttle: skip if less than ``_TRAIL_MIN_INTERVAL_SEC`` since the
             last run. Prevents per-tick Python.NET marshalling under load.
          3. Skip if no positions exist (avoids the list() allocation entirely).
        """
        if not self._params.trailing_stop_enabled:
            return

        # Throttle — only run periodically, regardless of tick rate.
        import time
        now = time.monotonic()
        if (now - self._last_trail_tick_monotonic) < self._TRAIL_MIN_INTERVAL_SEC:
            return
        self._last_trail_tick_monotonic = now

        # Cheap pre-check: bail before allocating the position list if we have
        # no map entry AND the broker reports no positions. ``Positions.Count``
        # is a single int read; avoids materialising the wrapper list.
        try:
            count = int(self._api.Positions.Count)
            if count == 0:
                return
        except BaseException:
            pass  # Fall through to the full path if Count isn't available.

        try:
            positions = list(self._api.Positions)
        except BaseException:
            return

        for position in positions:
            try:
                if not str(position.Label).startswith(self._label_prefix):
                    continue
                self._process_trailing_stop(position)
            except BaseException as exc:
                pos_id = getattr(position, "Id", "?")
                self._logger.error(
                    f"Trailing stop error for position #{pos_id}", exc=exc
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_daily_drawdown(self) -> DrawdownStatus:
        """Return DAILY_LIMIT_BREACHED if daily loss >= threshold, else OK."""
        if self._api is None or self._daily_start_balance <= 0:
            return DrawdownStatus.OK
        try:
            equity = self._api.Account.Equity
            loss_pct = (
                (self._daily_start_balance - equity)
                / self._daily_start_balance
                * 100.0
            )
            if loss_pct >= self._params.max_daily_drawdown_pct:
                return DrawdownStatus.DAILY_LIMIT_BREACHED
        except BaseException:
            pass
        return DrawdownStatus.OK

    def _check_total_drawdown(self) -> DrawdownStatus:
        """Return TOTAL_LIMIT_BREACHED if drawdown from HWM >= threshold, else OK."""
        if self._api is None or self._high_water_mark <= 0:
            return DrawdownStatus.OK
        try:
            equity = self._api.Account.Equity
            if equity > self._high_water_mark:
                self._high_water_mark = equity
            dd_pct = (
                (self._high_water_mark - equity)
                / self._high_water_mark
                * 100.0
            )
            if dd_pct >= self._params.max_total_drawdown_pct:
                return DrawdownStatus.TOTAL_LIMIT_BREACHED
        except BaseException:
            pass
        return DrawdownStatus.OK

    def _count_bot_positions(self, symbol: str | None = None) -> int:
        """Count open positions managed by this bot, optionally filtered by symbol."""
        try:
            count = 0
            for pos in self._api.Positions:
                if not str(pos.Label).startswith(self._label_prefix):
                    continue
                if symbol is not None and pos.SymbolName != symbol:
                    continue
                count += 1
            return count
        except BaseException:
            return 0

    def _get_trailing_params(self, label: str, atr_pips: float) -> tuple[float, float]:
        """Resolve strategy-specific trailing trigger and distance in pips.

        Uses ATR-based multipliers per strategy (read from Defaults constants
        to avoid adding fields to RiskParams). Falls back to global fixed-pip
        defaults if the strategy label is unrecognised or ATR is unavailable.

        Args:
            label: Position label (e.g. "ArgoAlgo_TR_EURUSD").
            atr_pips: Current ATR in pips for the position's symbol.

        Returns:
            (trigger_pips, distance_pips) tuple.
        """
        # Extract 2-char strategy code from label: ArgoAlgo_XX_SYMBOL.
        # OrderExecutor.build_label uses strategy_name[:2].upper(), which yields
        # TR (TrendFollowing), ME (MeanReversion), BR (Breakout). The previous
        # check used "TF" — a typo that silently broke per-strategy ATR trails
        # for TrendFollowing positions for the entire 5-week live run.
        parts = str(label).split("_")
        strategy_code = parts[1] if len(parts) >= 3 else ""

        if atr_pips > 0 and strategy_code in ("TR", "ME", "BR"):
            if strategy_code == "TR":
                trigger = atr_pips * Defaults.TF_TRAILING_TRIGGER_ATR
                distance = atr_pips * Defaults.TF_TRAILING_DISTANCE_ATR
            elif strategy_code == "ME":
                trigger = atr_pips * Defaults.MR_TRAILING_TRIGGER_ATR
                distance = atr_pips * Defaults.MR_TRAILING_DISTANCE_ATR
            else:  # BR
                trigger = atr_pips * Defaults.BO_TRAILING_TRIGGER_ATR
                distance = atr_pips * Defaults.BO_TRAILING_DISTANCE_ATR
            return (trigger, distance)

        # Fallback: global fixed-pip defaults
        return (
            self._params.trailing_stop_trigger_pips,
            self._params.trailing_stop_distance_pips,
        )

    def _process_trailing_stop(self, position) -> None:
        """Apply trailing stop logic for one position.

        Uses strategy-specific ATR-based trailing parameters when available,
        falling back to global fixed-pip defaults otherwise.
        """
        profit_pips = float(position.Pips)

        sym = self._data_provider.get_symbol(position.SymbolName)
        pip_size = float(sym.PipSize)
        is_buy = str(position.TradeType) == "Buy"

        # Get ATR in pips for strategy-specific trailing
        atr_pips = 0.0
        try:
            atr_ind = self._data_provider.get_indicator(position.SymbolName, "atr")
            atr_value = float(atr_ind.Result.Last(0))
            atr_pips = atr_value / pip_size
        except BaseException:
            pass  # Fallback to fixed-pip defaults

        trigger, trail_distance_pips = self._get_trailing_params(
            position.Label, atr_pips
        )

        if profit_pips < trigger:
            return  # Not yet in profit enough to activate trailing

        if position.Id not in self._trailing_stop_map:
            # Activate: move SL to breakeven
            breakeven = float(position.EntryPrice)
            position.ModifyStopLossPrice(breakeven)
            self._trailing_stop_map[position.Id] = breakeven
            self._logger.debug(
                f"Trailing stop activated: pos #{position.Id} "
                f"SL moved to breakeven {breakeven:.5f}"
            )
        else:
            # Advance: trail SL behind current price
            trail_distance_price = trail_distance_pips * pip_size
            min_step = Defaults.TRAILING_STOP_MIN_STEP_PIPS * pip_size
            current_sl = self._trailing_stop_map[position.Id]

            if is_buy:
                current_price = float(sym.Bid)
                new_sl = current_price - trail_distance_price
                if new_sl > current_sl + min_step:
                    position.ModifyStopLossPrice(new_sl)
                    self._trailing_stop_map[position.Id] = new_sl
                    self._logger.debug(
                        f"Trailing stop advanced: pos #{position.Id} "
                        f"SL {current_sl:.5f} -> {new_sl:.5f}"
                    )
            else:
                current_price = float(sym.Ask)
                new_sl = current_price + trail_distance_price
                if new_sl < current_sl - min_step:
                    position.ModifyStopLossPrice(new_sl)
                    self._trailing_stop_map[position.Id] = new_sl
                    self._logger.debug(
                        f"Trailing stop advanced: pos #{position.Id} "
                        f"SL {current_sl:.5f} -> {new_sl:.5f}"
                    )

    def __repr__(self) -> str:
        return (
            f"RiskManager("
            f"risk={self._params.risk_per_trade_pct}%, "
            f"max_dd={self._params.max_total_drawdown_pct}%)"
        )
