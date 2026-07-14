"""
parameter_ranges.py
Optimization parameter ranges for each strategy (from PLAN §8.4).

Used to:
  1. Document the search space passed to cTrader's genetic optimizer.
  2. Drive sensitivity analysis (perturb parameters within their valid range).
  3. Validate that a chosen parameter set lies within the optimized range.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParamRange:
    """Defines the optimization search range for a single strategy parameter.

    Attributes:
        name: Parameter name (matches the key used in strategy params dict).
        min_val: Minimum value (inclusive).
        max_val: Maximum value (inclusive).
        step: Increment between candidate values.
    """

    name: str
    min_val: float
    max_val: float
    step: float

    def values(self) -> list[float]:
        """Return every discrete candidate value in [min_val, max_val].

        Values are rounded to 6 decimal places to avoid float accumulation
        drift over many steps.

        Returns:
            Ordered list of candidate values.
        """
        result: list[float] = []
        v = self.min_val
        while v <= self.max_val + 1e-9:
            result.append(round(v, 6))
            v += self.step
        return result

    def contains(self, value: float) -> bool:
        """Return True if *value* lies within [min_val, max_val].

        Args:
            value: The parameter value to check.

        Returns:
            True if min_val ≤ value ≤ max_val.
        """
        return self.min_val <= value <= self.max_val

    def clamp(self, value: float) -> float:
        """Return *value* clamped to [min_val, max_val].

        Args:
            value: The value to clamp.

        Returns:
            The clamped value.
        """
        return max(self.min_val, min(self.max_val, value))

    def __repr__(self) -> str:
        return (
            f"ParamRange({self.name!r}, "
            f"[{self.min_val}–{self.max_val}], step={self.step})"
        )


# ---------------------------------------------------------------------------
# Trend Following
# ---------------------------------------------------------------------------

TREND_FOLLOWING_RANGES: list[ParamRange] = [
    ParamRange("fast_ema_period",    8,    20,  2),
    ParamRange("slow_ema_period",    20,   50,  5),
    ParamRange("adx_threshold",      20,   35,  5),
    ParamRange("sl_atr_multiplier",  1.5,  3.0, 0.5),
    ParamRange("tp_rr",              1.5,  3.5, 0.5),
]

# ---------------------------------------------------------------------------
# Mean Reversion
# ---------------------------------------------------------------------------

MEAN_REVERSION_RANGES: list[ParamRange] = [
    ParamRange("bollinger_period",       15,  30,  5),
    ParamRange("bollinger_deviation",    1.5, 3.0, 0.5),
    ParamRange("rsi_oversold",           25,  35,  5),
    ParamRange("rsi_overbought",         65,  75,  5),
    ParamRange("adx_filter_threshold",   20,  30,  5),
]

# ---------------------------------------------------------------------------
# Breakout
# ---------------------------------------------------------------------------

BREAKOUT_RANGES: list[ParamRange] = [
    ParamRange("donchian_period",    10,  30,  5),
    ParamRange("sl_atr_multiplier",  1.0, 2.5, 0.5),
    ParamRange("tp_rr",              1.5, 3.5, 0.5),
]

# ---------------------------------------------------------------------------
# Convenience lookup
# ---------------------------------------------------------------------------

#: Maps strategy name → its parameter ranges list.
ALL_RANGES: dict[str, list[ParamRange]] = {
    "TrendFollowing": TREND_FOLLOWING_RANGES,
    "MeanReversion":  MEAN_REVERSION_RANGES,
    "Breakout":       BREAKOUT_RANGES,
}
