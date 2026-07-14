"""
wfa.py
Walk-Forward Analysis utilities (from PLAN §8.4, Step 3).

Walk-Forward Analysis (WFA) divides a historical data range into overlapping
windows, each split into an in-sample (optimisation) period and an
out-of-sample (validation) period.  The efficiency ratio measures how well
parameters optimised on in-sample data transfer to unseen out-of-sample data.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date


# ---------------------------------------------------------------------------
# Window type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WfaWindow:
    """A single Walk-Forward Analysis window.

    Attributes:
        in_sample_start: First date of the in-sample (optimisation) period.
        in_sample_end: Last date of the in-sample period (exclusive).
        out_sample_start: First date of the out-of-sample (validation) period.
        out_sample_end: Last date of the out-of-sample period (exclusive).
    """

    in_sample_start: date
    in_sample_end: date
    out_sample_start: date
    out_sample_end: date

    def __repr__(self) -> str:
        return (
            f"WfaWindow("
            f"IS={self.in_sample_start}→{self.in_sample_end}, "
            f"OOS={self.out_sample_start}→{self.out_sample_end})"
        )


# ---------------------------------------------------------------------------
# Window generation
# ---------------------------------------------------------------------------

def create_wfa_windows(
    start: date,
    end: date,
    in_sample_months: int = 4,
    out_sample_months: int = 2,
    step_months: int = 2,
) -> list[WfaWindow]:
    """Generate a sequence of Walk-Forward Analysis windows.

    Each window has an in-sample period followed immediately by an
    out-of-sample period.  Windows slide forward by ``step_months`` each
    iteration.  Windows whose out-of-sample period would exceed ``end`` are
    discarded.

    Args:
        start: First day of the overall analysis period.
        end: Last day (exclusive) of the overall analysis period.
        in_sample_months: Duration of each in-sample period in calendar months.
        out_sample_months: Duration of each out-of-sample period in calendar months.
        step_months: Number of months to slide the window forward each iteration.

    Returns:
        Ordered list of WfaWindow instances.

    Raises:
        ValueError: If any duration argument is < 1 or if start >= end.
    """
    if in_sample_months < 1 or out_sample_months < 1 or step_months < 1:
        raise ValueError("in_sample_months, out_sample_months, and step_months must be >= 1")
    if start >= end:
        raise ValueError("start must be before end")

    windows: list[WfaWindow] = []
    current = start

    while True:
        in_end = _add_months(current, in_sample_months)
        out_start = in_end
        out_end = _add_months(out_start, out_sample_months)

        if out_end > end:
            break

        windows.append(WfaWindow(
            in_sample_start=current,
            in_sample_end=in_end,
            out_sample_start=out_start,
            out_sample_end=out_end,
        ))
        current = _add_months(current, step_months)

    return windows


# ---------------------------------------------------------------------------
# Efficiency ratio
# ---------------------------------------------------------------------------

def calculate_wfa_efficiency(
    in_sample_pf: list[float],
    out_sample_pf: list[float],
) -> float:
    """Compute the WFA efficiency ratio: avg(OOS PF) / avg(IS PF).

    An efficiency ratio >= 0.70 indicates the strategy generalises well to
    unseen data and is not significantly overfitted to the in-sample period.

    Args:
        in_sample_pf: Profit Factor values from each window's in-sample run.
        out_sample_pf: Profit Factor values from each window's out-of-sample run.

    Returns:
        Efficiency ratio in [0, ∞), or 0.0 if inputs are empty or avg IS PF
        is zero (which would imply division by zero).
    """
    if not in_sample_pf or not out_sample_pf:
        return 0.0

    avg_in = sum(in_sample_pf) / len(in_sample_pf)
    avg_out = sum(out_sample_pf) / len(out_sample_pf)

    if avg_in == 0.0:
        return 0.0

    return avg_out / avg_in


def wfa_passes(efficiency: float, min_efficiency: float = 0.70) -> bool:
    """Return True if the WFA efficiency ratio meets the minimum threshold.

    Args:
        efficiency: Value returned by ``calculate_wfa_efficiency``.
        min_efficiency: Acceptance threshold (default 0.70 per PLAN §8.4).

    Returns:
        True if efficiency >= min_efficiency.
    """
    return efficiency >= min_efficiency


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    """Return the date that is exactly *months* calendar months after *d*.

    The day-of-month is clamped to the last valid day of the target month
    (e.g., Jan 31 + 1 month → Feb 28/29).

    Args:
        d: Starting date.
        months: Number of calendar months to add.

    Returns:
        New date *months* months later.
    """
    total_months = d.month - 1 + months
    year = d.year + total_months // 12
    month = total_months % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
