"""
trend_following.py
TrendFollowingStrategy — EMA crossover with ADX trend-strength filter.
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


class TrendFollowingStrategy(IStrategy):
    """Trend-following strategy using EMA crossover and ADX filter.

    Entry: Fast EMA crosses above/below slow EMA while ADX confirms a
           trending market and price is on the correct side of the slow EMA.
    Exit:  Fast EMA crosses back in the opposite direction of the trade.

    Required param keys (all have Defaults fallbacks):
        adx_threshold, sl_atr_multiplier, tp_rr
    """

    name: str = StrategyName.TREND_FOLLOWING.value

    def evaluate(self, symbol: str) -> "TradeSignal":
        """Evaluate EMA crossover and ADX conditions.

        Args:
            symbol: Instrument name.

        Returns:
            TradeSignal with BUY, SELL, or NONE direction.
        """
        dp = self._data_provider
        primary_tf = dp.primary_timeframe

        slow_period = self._params.get("slow_ema_period", 26)
        adx_period = self._params.get("adx_period", 14)
        min_bars = max(slow_period, adx_period) + 5
        if not dp.has_sufficient_history(symbol, primary_tf, min_bars):
            return self._no_signal(symbol)

        try:
            bars = dp.get_bars(symbol, primary_tf)
            ema_fast = dp.get_indicator(symbol, "ema_fast")
            ema_slow = dp.get_indicator(symbol, "ema_slow")
            adx_ind = dp.get_indicator(symbol, "adx")
            atr_ind = dp.get_indicator(symbol, "atr")
            sym_obj = dp.get_symbol(symbol)

            ema_fast_now  = float(ema_fast.Result.Last(0))
            ema_fast_prev = float(ema_fast.Result.Last(1))
            ema_slow_now  = float(ema_slow.Result.Last(0))
            ema_slow_prev = float(ema_slow.Result.Last(1))
            adx_value     = float(adx_ind.ADX.Last(0))
            atr_value     = float(atr_ind.Result.Last(0))
            close_price   = float(bars.ClosePrices.Last(0))
            pip_size      = float(sym_obj.PipSize)
        except BaseException as exc:
            self._logger.error(f"TrendFollowing data access failed for {symbol}: {type(exc).__name__}")
            return self._no_signal(symbol)

        if any(math.isnan(v) for v in
               [ema_fast_now, ema_fast_prev, ema_slow_now, ema_slow_prev, adx_value, atr_value]):
            return self._no_signal(symbol)

        adx_threshold = self._params.get("adx_threshold", 25.0)
        sl_multiplier = self._params.get("sl_atr_multiplier", 2.0)
        tp_rr         = self._params.get("tp_rr", 2.0)
        max_atr_pips  = self._params.get("max_atr_pips", 150.0)

        # ATR ceiling — skip during extreme volatility events (BoJ shocks, flash crashes)
        atr_pips = atr_value / pip_size
        if atr_pips > max_atr_pips:
            self._logger.debug(f"TF {symbol}: no signal — ATR={atr_pips:.0f} pips > max {max_atr_pips:.0f}")
            return self._no_signal(symbol)

        if adx_value < adx_threshold:
            self._logger.debug(
                f"TF {symbol}: no signal — ADX={adx_value:.1f} < {adx_threshold}"
            )
            return self._no_signal(symbol)

        min_sl_pips = self._params.get("min_sl_pips", 20.0)
        sl_pips = max((atr_value / pip_size) * sl_multiplier, min_sl_pips)
        tp_pips = sl_pips * tp_rr
        metadata = {"adx": adx_value, "ema_fast": ema_fast_now, "ema_slow": ema_slow_now}

        # BUY: fast crosses above slow, price above slow EMA
        if (ema_fast_prev <= ema_slow_prev
                and ema_fast_now > ema_slow_now
                and close_price > ema_slow_now):
            return self._make_signal(symbol, Direction.BUY, sl_pips, tp_pips, close_price, metadata)

        # SELL: fast crosses below slow, price below slow EMA
        if (ema_fast_prev >= ema_slow_prev
                and ema_fast_now < ema_slow_now
                and close_price < ema_slow_now):
            return self._make_signal(symbol, Direction.SELL, sl_pips, tp_pips, close_price, metadata)

        self._logger.debug(
            f"TF {symbol}: no crossover — ema_fast={ema_fast_now:.5f} "
            f"ema_slow={ema_slow_now:.5f} ADX={adx_value:.1f}"
        )
        return self._no_signal(symbol)

    def should_close(self, position) -> bool:
        """Close when fast EMA crosses back against the trade direction."""
        try:
            symbol = position.SymbolName
            ema_fast = self._data_provider.get_indicator(symbol, "ema_fast")
            ema_slow = self._data_provider.get_indicator(symbol, "ema_slow")
            ema_fast_now = float(ema_fast.Result.Last(0))
            ema_slow_now = float(ema_slow.Result.Last(0))
            if math.isnan(ema_fast_now) or math.isnan(ema_slow_now):
                return False
            is_buy = str(position.TradeType) == "Buy"
            return ema_fast_now < ema_slow_now if is_buy else ema_fast_now > ema_slow_now
        except BaseException:
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
        return "TrendFollowingStrategy()"
