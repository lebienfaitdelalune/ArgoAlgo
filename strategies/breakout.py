"""
breakout.py
BreakoutStrategy — Donchian Channel breakout with ATR volatility filter.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

try:
    from strategies.base_strategy import IStrategy
    from utils.constants import Direction, StrategyName
except ImportError:
    from base_strategy import IStrategy  # type: ignore[no-redef]
    from constants import Direction, StrategyName  # type: ignore[no-redef]

if TYPE_CHECKING:
    from models.trade_signal import TradeSignal


class BreakoutStrategy(IStrategy):
    """Breakout strategy using Donchian Channels and ATR validation.

    Entry: Price closes above/below the previous bar's Donchian Channel
           with ATR confirming sufficient volatility.
    Exit:  Price retraces to the middle Donchian Channel.

    Required param keys (all have Defaults fallbacks):
        atr_min_threshold, sl_atr_multiplier, tp_rr, donchian_period
    """

    name: str = StrategyName.BREAKOUT.value

    def evaluate(self, symbol: str) -> "TradeSignal":
        """Evaluate Donchian breakout and ATR conditions.

        Args:
            symbol: Instrument name.

        Returns:
            TradeSignal with BUY, SELL, or NONE direction.
        """
        dp = self._data_provider
        primary_tf = dp.primary_timeframe

        donchian_period = self._params.get("donchian_period", 20)
        min_bars = donchian_period + 5
        if not dp.has_sufficient_history(symbol, primary_tf, min_bars):
            return self._no_signal(symbol)

        try:
            bars          = dp.get_bars(symbol, primary_tf)
            donchian_high = dp.get_indicator(symbol, "donchian_high")
            donchian_low  = dp.get_indicator(symbol, "donchian_low")
            atr_ind       = dp.get_indicator(symbol, "atr_bo")
            ema_slow      = dp.get_indicator(symbol, "ema_slow")   # trend direction filter
            adx_ind       = dp.get_indicator(symbol, "adx")        # momentum filter
            sym_obj       = dp.get_symbol(symbol)

            # Use previous bar's channel to avoid lookahead bias
            upper_channel  = float(donchian_high.Result.Last(1))
            lower_channel  = float(donchian_low.Result.Last(1))
            atr_value      = float(atr_ind.Result.Last(0))
            close_price    = float(bars.ClosePrices.Last(0))
            open_price     = float(bars.OpenPrices.Last(0))
            ema_slow_now   = float(ema_slow.Result.Last(0))
            adx_value      = float(adx_ind.ADX.Last(0))
            pip_size       = float(sym_obj.PipSize)
        except BaseException as exc:
            self._logger.error(f"Breakout data access failed for {symbol}: {type(exc).__name__}")
            return self._no_signal(symbol)

        if any(math.isnan(v) for v in [upper_channel, lower_channel, atr_value,
                                        open_price, ema_slow_now, adx_value]):
            return self._no_signal(symbol)

        atr_min_threshold = self._params.get("atr_min_threshold", 0.0005)
        sl_multiplier     = self._params.get("sl_atr_multiplier", 2.0)
        tp_rr             = self._params.get("tp_rr", 3.0)
        adx_threshold     = self._params.get("adx_threshold", 15.0)
        min_sl_pips       = self._params.get("min_sl_pips", 20.0)
        max_atr_pips      = self._params.get("max_atr_pips", 150.0)

        atr_pips = atr_value / pip_size

        # ATR volatility filter — skip if market is too quiet
        if atr_value < atr_min_threshold:
            self._logger.debug(f"BO {symbol}: no signal — ATR too low ({atr_value:.5f})")
            return self._no_signal(symbol)

        # ATR ceiling — skip during extreme volatility (news shocks, flash crashes)
        if atr_pips > max_atr_pips:
            self._logger.debug(f"BO {symbol}: no signal — ATR={atr_pips:.0f} pips > max {max_atr_pips:.0f}")
            return self._no_signal(symbol)

        # ADX momentum filter — skip flat/choppy markets (same threshold as TF)
        if adx_value < adx_threshold:
            self._logger.debug(f"BO {symbol}: no signal — ADX={adx_value:.1f} < {adx_threshold}")
            return self._no_signal(symbol)

        # Bar-body confirmation — reject wick-only breakouts (false breaks)
        bar_body = abs(close_price - open_price)
        if atr_value > 0 and bar_body < atr_value * 0.5:
            self._logger.debug(
                f"BO {symbol}: no signal — weak bar body "
                f"{bar_body:.5f} < 50% ATR {atr_value:.5f}"
            )
            return self._no_signal(symbol)

        sl_pips = max(atr_pips * sl_multiplier, min_sl_pips)
        tp_pips = sl_pips * tp_rr
        middle_channel = (upper_channel + lower_channel) / 2.0
        metadata = {
            "upper_channel": upper_channel,
            "lower_channel": lower_channel,
            "atr": atr_value,
            "adx": adx_value,
            "ema_slow": ema_slow_now,
        }

        # BUY: close above upper channel AND in uptrend (EMA direction filter)
        # Prevents buying at range tops when price is above the trend EMA
        # but the broader structure is topping out.
        if close_price > upper_channel and close_price > ema_slow_now:
            return self._make_signal(symbol, Direction.BUY, sl_pips, tp_pips, close_price, metadata)

        # SELL: close below lower channel AND in downtrend (EMA direction filter)
        # Prevents selling into recoveries / at range bottoms in an uptrend.
        if close_price < lower_channel and close_price < ema_slow_now:
            return self._make_signal(symbol, Direction.SELL, sl_pips, tp_pips, close_price, metadata)

        self._logger.debug(
            f"BO {symbol}: no signal — close={close_price:.5f} "
            f"channel=[{lower_channel:.5f},{upper_channel:.5f}] "
            f"ema_slow={ema_slow_now:.5f} ADX={adx_value:.1f}"
        )
        return self._no_signal(symbol)

    def should_close(self, position) -> bool:
        """Let the hard TP/SL handle all exits for breakout trades.

        A middle-channel exit closes winners too early (5–25 pips) while SL
        is 2× ATR away, producing an inverted R:R in practice. Hard TP at
        3:1 is the correct exit for this strategy.
        """
        return False

    def _make_signal(self, symbol, direction, sl_pips, tp_pips, entry_price, metadata):
        from datetime import datetime
        from models.trade_signal import TradeSignal
        return TradeSignal(
            strategy_name=self.name, symbol=symbol, direction=direction,
            stop_loss_pips=sl_pips, take_profit_pips=tp_pips,
            entry_price=entry_price, timestamp=datetime.utcnow(), metadata=metadata,
        )

    def __repr__(self) -> str:
        return "BreakoutStrategy()"
