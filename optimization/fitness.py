"""
fitness.py
Custom fitness function for cTrader's genetic optimizer.
"""

from __future__ import annotations


def calculate_fitness(backtesting) -> float:
    """Custom optimizer fitness: rewards profit and efficiency, penalises drawdown.

    Formula: Net Profit × Profit Factor / Max Drawdown %

    The ``backtesting`` argument is the cTrader backtesting result object
    passed automatically by the optimizer. In unit tests, pass any object
    (or MagicMock) that exposes the four attributes below.

    Disqualifiers (return -1.0):
        - Fewer than 200 trades (insufficient statistical sample).
        - Max equity drawdown > 20 %.
        - Net profit <= 0.
        - Any attribute access failure (malformed result object).

    Args:
        backtesting: cTrader backtesting result object with attributes:
            ``TotalTrades`` (int),
            ``NetProfit`` (float),
            ``ProfitFactor`` (float),
            ``MaxEquityDrawdownPercentages`` (float).

    Returns:
        Fitness score ≥ 0.0 (higher is better), or -1.0 if disqualified.
    """
    try:
        total_trades = backtesting.TotalTrades
        net_profit = float(backtesting.NetProfit)
        profit_factor = float(backtesting.ProfitFactor)
        max_drawdown = float(backtesting.MaxEquityDrawdownPercentages)
    except Exception:
        return -1.0

    if total_trades < 200:
        return -1.0
    if max_drawdown > 20.0:
        return -1.0
    if net_profit <= 0.0:
        return -1.0

    # Clamp denominator away from zero to avoid division-by-zero on
    # (theoretically impossible but logically possible) 0% drawdown result.
    return net_profit * profit_factor / max(max_drawdown, 0.01)
