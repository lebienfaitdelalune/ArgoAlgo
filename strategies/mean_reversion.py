"""
mean_reversion.py
MeanReversionStrategy — Bollinger Bands + RSI overbought/oversold signals.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

try:
    from strategies.base_strategy import IStrategy
    from utils.constants import Defaults, Direction, StrategyName
except ImportError:
    from base_strategy import IStrategy  # type: ignore[no-redef]
    from constants import Defaults, Direction, StrategyName  # type: ignore[no-redef]

if TYPE_CHECKING:
    from models.trade_signal import TradeSignal


class MeanReversionStrategy(IStrategy):
    """Mean-reversion strategy using Bollinger Bands and RSI.

    Entry: Price outside the Bollinger Band with RSI confirming extreme
           reading and ADX confirming a ranging (not trending) market.
    Exit:  Price returns to the Bollinger middle band.

    Required param keys (all have Defaults fallbacks):
        rsi_oversold, rsi_overbought, adx_filter_threshold,
        sl_atr_multiplier, bollinger_period
    """

    name: str = StrategyName.MEAN_REVERSION.value

    def evaluate(self, symbol: str) -> "TradeSignal":
        """Evaluate Bollinger/RSI conditions.

        Args:
            symbol: Instrument name.

        Returns:
            TradeSignal with BUY, SELL, or NONE direction.
        """
        dp = self._data_provider
        primary_tf = dp.primary_timeframe

        bollinger_period = self._params.get("bollinger_period", 20)
        min_bars = bollinger_period + 5
        if not dp.has_sufficient_history(symbol, primary_tf, min_bars):
            return self._no_signal(symbol)

        try:
            bars       = dp.get_bars(symbol, primary_tf)
            bollinger  = dp.get_indicator(symbol, "bollinger")
            rsi_ind    = dp.get_indicator(symbol, "rsi")
            adx_filter = dp.get_indicator(symbol, "adx_filter")
            atr_ind    = dp.get_indicator(symbol, "atr")
            sym_obj    = dp.get_symbol(symbol)

            upper_band  = float(bollinger.Top.Last(0))
            lower_band  = float(bollinger.Bottom.Last(0))
            middle_band = float(bollinger.Main.Last(0))
            rsi_value   = float(rsi_ind.Result.Last(0))
            adx_value   = float(adx_filter.ADX.Last(0))
            atr_value   = float(atr_ind.Result.Last(0))
            close_price = float(bars.ClosePrices.Last(0))
            pip_size    = float(sym_obj.PipSize)
        except BaseException as exc:
            self._logger.error(f"MeanReversion data access failed for {symbol}: {type(exc).__name__}")
            return self._no_signal(symbol)

        if any(math.isnan(v) for v in
               [upper_band, lower_band, middle_band, rsi_value, adx_value, atr_value]):
            return self._no_signal(symbol)

        # Fallbacks MUST come from Defaults — hardcoded fallbacks here silently
        # overrode the sweep-validated values for 2 months (main.py doesn't pass
        # sl_atr_multiplier/min_sl_pips/max_atr_pips in the MR params dict).
        rsi_oversold        = self._params.get("rsi_oversold", Defaults.MR_RSI_OVERSOLD)
        rsi_overbought      = self._params.get("rsi_overbought", Defaults.MR_RSI_OVERBOUGHT)
        adx_filter_thresh   = self._params.get("adx_filter_threshold", Defaults.MR_ADX_FILTER_THRESHOLD)
        sl_multiplier       = self._params.get("sl_atr_multiplier", Defaults.MR_SL_ATR_MULTIPLIER)
        min_sl_pips         = self._params.get("min_sl_pips", Defaults.MIN_SL_PIPS)
        max_atr_pips        = self._params.get("max_atr_pips", Defaults.MAX_ATR_PIPS)

        # ATR ceiling — skip during extreme volatility (not a ranging market anyway)
        atr_pips = atr_value / pip_size
        if atr_pips > max_atr_pips:
            self._logger.debug(f"MR {symbol}: no signal — ATR={atr_pips:.0f} pips > max {max_atr_pips:.0f}")
            return self._no_signal(symbol)

        # ADX must confirm ranging market
        if adx_value >= adx_filter_thresh:
            self._logger.debug(
                f"MR {symbol}: no signal — ADX={adx_value:.1f} >= {adx_filter_thresh} (trending)"
            )
            return self._no_signal(symbol)

        sl_pips = max((atr_value / pip_size) * sl_multiplier, min_sl_pips)

        # BUY: price below lower band, RSI oversold
        if close_price < lower_band and rsi_value < rsi_oversold:
            tp_pips = max((middle_band - close_price) / pip_size, 0.0)
            return self._make_signal(
                symbol, Direction.BUY, sl_pips, tp_pips, close_price,
                {"rsi": rsi_value, "adx": adx_value, "middle_band": middle_band},
            )

        # SELL: price above upper band, RSI overbought
        if close_price > upper_band and rsi_value > rsi_overbought:
            tp_pips = max((close_price - middle_band) / pip_size, 0.0)
            return self._make_signal(
                symbol, Direction.SELL, sl_pips, tp_pips, close_price,
                {"rsi": rsi_value, "adx": adx_value, "middle_band": middle_band},
            )

        self._logger.debug(
            f"MR {symbol}: no signal — close={close_price:.5f} "
            f"BB=[{lower_band:.5f},{upper_band:.5f}] RSI={rsi_value:.1f} ADX={adx_value:.1f}"
        )
        return self._no_signal(symbol)

    def should_close(self, position) -> bool:
        """Close when price reaches the middle Bollinger Band."""
        try:
            symbol = position.SymbolName
            bollinger = self._data_provider.get_indicator(symbol, "bollinger")
            bars = self._data_provider.get_bars(
                symbol, self._data_provider.primary_timeframe
            )
            middle_band = float(bollinger.Main.Last(0))
            close_price = float(bars.ClosePrices.Last(0))
            if math.isnan(middle_band) or math.isnan(close_price):
                return False
            is_buy = str(position.TradeType) == "Buy"
            return close_price >= middle_band if is_buy else close_price <= middle_band
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
        return "MeanReversionStrategy()"
