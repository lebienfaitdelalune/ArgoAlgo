"""
indicators.py
Technical indicator implementations matching cTrader's defaults.

All functions take a list[float] of input prices (typically closes for EMA/RSI/BB,
or full OHLC bars for ADX/ATR) and return a same-length list of indicator values
where insufficient-history positions are float('nan').
"""

from __future__ import annotations

import math
from typing import Sequence


NAN = float("nan")


def ema(values: Sequence[float], period: int) -> list[float]:
    """Standard exponential moving average. cTrader's ExponentialMovingAverage default."""
    out: list[float] = [NAN] * len(values)
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def sma(values: Sequence[float], period: int) -> list[float]:
    """Simple moving average."""
    out: list[float] = [NAN] * len(values)
    if period <= 0 or len(values) < period:
        return out
    rolling = sum(values[:period])
    out[period - 1] = rolling / period
    for i in range(period, len(values)):
        rolling += values[i] - values[i - period]
        out[i] = rolling / period
    return out


def stddev(values: Sequence[float], period: int) -> list[float]:
    """Population standard deviation over a rolling window."""
    out: list[float] = [NAN] * len(values)
    if period <= 0 or len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        out[i] = math.sqrt(var)
    return out


def bollinger(closes: Sequence[float], period: int, dev: float):
    """Bollinger Bands using SMA midline (matches cTrader MovingAverageType.Simple)."""
    mid = sma(closes, period)
    sd = stddev(closes, period)
    upper = [NAN] * len(closes)
    lower = [NAN] * len(closes)
    for i in range(len(closes)):
        if not math.isnan(mid[i]) and not math.isnan(sd[i]):
            upper[i] = mid[i] + dev * sd[i]
            lower[i] = mid[i] - dev * sd[i]
    return upper, mid, lower


def rsi(closes: Sequence[float], period: int) -> list[float]:
    """Wilder's RSI — same as cTrader's RelativeStrengthIndex."""
    out: list[float] = [NAN] * len(closes)
    if period <= 0 or len(closes) <= period:
        return out
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    # Wilder seed = simple average of first `period` gains/losses
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int) -> list[float]:
    """Wilder's ATR — same as cTrader's AverageTrueRange (default smoothing)."""
    n = len(highs)
    out: list[float] = [NAN] * n
    if n < period + 1:
        return out
    trs: list[float] = [NAN]  # bar 0 has no prev close
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # Wilder seed at index `period`: simple average of TR[1..period]
    seed = sum(trs[1: period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int):
    """Wilder's ADX/+DI/-DI — matches cTrader's DirectionalMovementSystem.

    Returns (adx, plus_di, minus_di) lists of len(highs).
    """
    n = len(highs)
    adx_out: list[float] = [NAN] * n
    pdi_out: list[float] = [NAN] * n
    mdi_out: list[float] = [NAN] * n
    if n < 2 * period + 1:
        return adx_out, pdi_out, mdi_out

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # Wilder smoothing — first value is sum of period values, then EMA-style update
    sm_plus = sum(plus_dm[1: period + 1])
    sm_minus = sum(minus_dm[1: period + 1])
    sm_tr = sum(tr[1: period + 1])

    dx_series: list[float] = []  # collected from index `period` onwards
    for i in range(period, n):
        if i > period:
            sm_plus = sm_plus - (sm_plus / period) + plus_dm[i]
            sm_minus = sm_minus - (sm_minus / period) + minus_dm[i]
            sm_tr = sm_tr - (sm_tr / period) + tr[i]
        if sm_tr > 0:
            pdi = 100.0 * sm_plus / sm_tr
            mdi = 100.0 * sm_minus / sm_tr
        else:
            pdi = 0.0
            mdi = 0.0
        pdi_out[i] = pdi
        mdi_out[i] = mdi
        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dx_series.append(dx)

    # ADX = Wilder smoothing of DX over `period`
    if len(dx_series) >= period:
        adx_seed = sum(dx_series[:period]) / period
        adx_out[period + period - 1] = adx_seed
        prev_adx = adx_seed
        for k in range(period, len(dx_series)):
            prev_adx = (prev_adx * (period - 1) + dx_series[k]) / period
            adx_out[period + k] = prev_adx

    return adx_out, pdi_out, mdi_out


def donchian(highs: Sequence[float], lows: Sequence[float], period: int):
    """Donchian channel high/low (rolling max/min of the last `period` bars including current)."""
    n = len(highs)
    up = [NAN] * n
    dn = [NAN] * n
    for i in range(period - 1, n):
        window_h = highs[i - period + 1: i + 1]
        window_l = lows[i - period + 1: i + 1]
        up[i] = max(window_h)
        dn[i] = min(window_l)
    return up, dn
