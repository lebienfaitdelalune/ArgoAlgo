"""
helpers.py
Pure utility functions shared across ArgoAlgo modules.
No dependencies on cTrader API — all functions are testable in isolation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def round_to_step(value: float, step: float) -> float:
    """Round *value* down to the nearest multiple of *step*.

    Args:
        value: The raw value to round.
        step: The granularity step (e.g. 0.01 for 2 decimal places).

    Returns:
        The largest multiple of *step* that is <= *value*.

    Examples:
        >>> round_to_step(1.234, 0.01)
        1.23
        >>> round_to_step(10500, 1000)
        10000.0
    """
    if step <= 0:
        raise ValueError(f"step must be > 0, got {step}")
    return float(int(value / step) * step)


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp *value* to the inclusive range [minimum, maximum].

    Args:
        value: The value to clamp.
        minimum: Lower bound.
        maximum: Upper bound.

    Returns:
        *value* constrained within [minimum, maximum].

    Examples:
        >>> clamp(5.0, 1.0, 10.0)
        5.0
        >>> clamp(-1.0, 0.0, 10.0)
        0.0
        >>> clamp(15.0, 0.0, 10.0)
        10.0
    """
    return max(minimum, min(value, maximum))


def pips_to_price(pips: float, pip_size: float) -> float:
    """Convert a pip distance to a price distance.

    Args:
        pips: Distance in pips.
        pip_size: The pip size for the instrument (e.g. 0.0001 for EUR/USD).

    Returns:
        Price distance equivalent.

    Examples:
        >>> pips_to_price(10.0, 0.0001)
        0.001
    """
    return pips * pip_size


def price_to_pips(price_distance: float, pip_size: float) -> float:
    """Convert a price distance to pips.

    Args:
        price_distance: Distance in price units.
        pip_size: The pip size for the instrument.

    Returns:
        Distance in pips.

    Examples:
        >>> round(price_to_pips(0.001, 0.0001), 2)
        10.0
    """
    if pip_size <= 0:
        raise ValueError(f"pip_size must be > 0, got {pip_size}")
    return price_distance / pip_size


def calculate_position_volume(
    balance: float,
    risk_pct: float,
    stop_loss_pips: float,
    pip_value: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> float:
    """Calculate trade volume using the fixed fractional risk model.

    Formula: volume = (balance * risk_pct / 100) / (stop_loss_pips * pip_value)
    The result is clamped to [volume_min, volume_max] and rounded to volume_step.

    Args:
        balance: Current account balance in account currency.
        risk_pct: Risk per trade as a percentage of balance (e.g. 1.0 for 1%).
        stop_loss_pips: Stop-loss distance in pips.
        pip_value: Value of 1 pip per 1 unit of volume in account currency.
        volume_min: Minimum allowed volume for the instrument.
        volume_max: Maximum allowed volume for the instrument.
        volume_step: Volume granularity step for the instrument.

    Returns:
        Normalised volume in units.

    Raises:
        ValueError: If any input is non-positive where required.

    Examples:
        >>> calculate_position_volume(10000, 1.0, 20.0, 1.0, 1000, 10_000_000, 1000)
        5000.0
    """
    if balance <= 0:
        raise ValueError("balance must be positive")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if stop_loss_pips <= 0:
        raise ValueError("stop_loss_pips must be positive")
    if pip_value <= 0:
        raise ValueError("pip_value must be positive")

    risk_amount = balance * (risk_pct / 100.0)
    raw_volume = risk_amount / (stop_loss_pips * pip_value)
    clamped = clamp(raw_volume, volume_min, volume_max)
    return round_to_step(clamped, volume_step)


def parse_symbols_string(symbols_str: str) -> list[str]:
    """Parse a comma-separated string of symbol names into a list.

    Strips whitespace and removes empty tokens.

    Args:
        symbols_str: e.g. "EURUSD, GBPUSD, USDJPY"

    Returns:
        List of symbol name strings.

    Examples:
        >>> parse_symbols_string("EURUSD,GBPUSD, USDJPY")
        ['EURUSD', 'GBPUSD', 'USDJPY']
        >>> parse_symbols_string("")
        []
    """
    return [s.strip() for s in symbols_str.split(",") if s.strip()]


def parse_days_string(days_str: str) -> list[str]:
    """Parse a comma-separated string of weekday abbreviations.

    Args:
        days_str: e.g. "Mon,Tue,Wed,Thu,Fri"

    Returns:
        List of day abbreviations.

    Examples:
        >>> parse_days_string("Mon,Wed,Fri")
        ['Mon', 'Wed', 'Fri']
    """
    return [d.strip() for d in days_str.split(",") if d.strip()]


def is_within_trading_hours(
    current_hour_utc: int,
    start_hour: int,
    end_hour: int,
) -> bool:
    """Check if the current UTC hour falls within the configured trading window.

    Args:
        current_hour_utc: Current hour in UTC (0–23).
        start_hour: Session start hour (inclusive).
        end_hour: Session end hour (exclusive).

    Returns:
        True if trading is allowed at *current_hour_utc*.

    Examples:
        >>> is_within_trading_hours(9, 7, 20)
        True
        >>> is_within_trading_hours(6, 7, 20)
        False
        >>> is_within_trading_hours(20, 7, 20)
        False
    """
    return start_hour <= current_hour_utc < end_hour


def is_trading_day(weekday_name: str, allowed_days: list[str]) -> bool:
    """Check if *weekday_name* is in the list of allowed trading days.

    Args:
        weekday_name: 3-letter abbreviation, e.g. "Mon", "Fri".
        allowed_days: List of allowed abbreviations.

    Returns:
        True if trading is allowed on this day.

    Examples:
        >>> is_trading_day("Mon", ["Mon", "Tue", "Wed", "Thu", "Fri"])
        True
        >>> is_trading_day("Sat", ["Mon", "Tue", "Wed", "Thu", "Fri"])
        False
    """
    return weekday_name in allowed_days


def format_pnl(pnl: float) -> str:
    """Format a P/L value as a signed currency string.

    Args:
        pnl: Profit or loss value.

    Returns:
        Formatted string, e.g. "+$42.50" or "-$10.00".

    Examples:
        >>> format_pnl(42.5)
        '+$42.50'
        >>> format_pnl(-10.0)
        '-$10.00'
        >>> format_pnl(0.0)
        '+$0.00'
    """
    sign = "+" if pnl >= 0 else "-"
    return f"{sign}${abs(pnl):.2f}"


def format_pct(value: float) -> str:
    """Format a percentage value with two decimal places.

    Args:
        value: Percentage value (e.g. 1.5 means 1.5%).

    Returns:
        Formatted string, e.g. "1.50%".

    Examples:
        >>> format_pct(1.5)
        '1.50%'
    """
    return f"{value:.2f}%"
