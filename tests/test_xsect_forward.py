"""Tests for core.xsect_forward — cross-sectional reversal forward test."""

from unittest.mock import MagicMock

import pytest

from core.xsect_forward import XsectForward
from utils.constants import Defaults, Direction

SYMBOLS = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
LOOKBACK = 5  # patched over Defaults.XS_LOOKBACK_BARS in all tests


class FakeTime:
    def __init__(self, hour: int, day: int = 1, dow: str = "Monday") -> None:
        self.Hour = hour
        self.Year, self.Month, self.Day = 2026, 7, day
        self.DayOfWeek = MagicMock()
        self.DayOfWeek.ToString.return_value = dow

    def __repr__(self) -> str:
        return f"FakeTime(h={self.Hour}, d={self.Day})"


class FakeSeries:
    """Last(0) = newest element."""

    def __init__(self, values: list) -> None:
        self._values = values

    def Last(self, n: int):
        return self._values[-1 - n]


class FakeBars:
    def __init__(self, closes: list[float], open_hour: int = 21,
                 day: int = 1, dow: str = "Monday") -> None:
        self.ClosePrices = FakeSeries(closes)
        self.OpenTimes = FakeSeries(
            [FakeTime(0)] * (len(closes) - 1) + [FakeTime(open_hour, day, dow)])
        self.Count = len(closes)
        self.LoadMoreHistory = MagicMock(return_value=0)


def make_bot(closes_by_symbol, open_hour=21, day=1, dow="Monday"):
    """XsectForward with mocked collaborators; returns (xsect, executor, api)."""
    dp = MagicMock()
    dp.primary_timeframe = "H1"
    bars = {s: FakeBars(c, open_hour, day, dow) for s, c in closes_by_symbol.items()}
    dp.get_bars.side_effect = lambda s, tf: bars[s]

    executor = MagicMock()
    executor.build_label.side_effect = lambda name, sym: f"ArgoAlgo_XS_{sym}"

    api = MagicMock()
    api.Positions = []

    xsect = XsectForward(api=api, data_provider=dp, order_executor=executor,
                         logger=MagicMock(), symbols=SYMBOLS, units_per_leg=1000)
    return xsect, executor, api


def make_position(symbol: str, trade_type: str):
    pos = MagicMock()
    pos.Label = f"ArgoAlgo_XS_{symbol}"
    pos.SymbolName = symbol
    pos.TradeType = trade_type
    return pos


# Returns over LOOKBACK bars: EURUSD +2% (strongest), NZDUSD -2% (weakest)
CLOSES = {
    "EURUSD": [1.00] * 2 + [1.02] * LOOKBACK,   # +2%  -> short
    "GBPUSD": [1.30] * 2 + [1.313] * LOOKBACK,  # +1%
    "AUDUSD": [0.65] * 2 + [0.6468] * LOOKBACK, # -0.5%
    "NZDUSD": [0.60] * 2 + [0.588] * LOOKBACK,  # -2%  -> long
}


@pytest.fixture(autouse=True)
def small_lookback(monkeypatch):
    monkeypatch.setattr(Defaults, "XS_LOOKBACK_BARS", LOOKBACK)


def test_longs_weakest_shorts_strongest():
    xsect, executor, _ = make_bot(CLOSES)
    xsect.on_bar_closed()
    calls = {c.args[0]: c.args[1] for c in executor.execute_market_simple.call_args_list}
    assert calls == {"NZDUSD": Direction.BUY, "EURUSD": Direction.SELL}
    executor.close_position.assert_not_called()


def test_no_churn_when_positions_match():
    xsect, executor, api = make_bot(CLOSES)
    api.Positions = [make_position("NZDUSD", "Buy"), make_position("EURUSD", "Sell")]
    xsect.on_bar_closed()
    executor.execute_market_simple.assert_not_called()
    executor.close_position.assert_not_called()


def test_flips_leg_on_rank_change():
    xsect, executor, api = make_bot(CLOSES)
    # Stale legs from a previous ranking: GBPUSD short, NZDUSD short
    api.Positions = [make_position("GBPUSD", "Sell"), make_position("NZDUSD", "Sell")]
    xsect.on_bar_closed()
    assert executor.close_position.call_count == 2
    calls = {c.args[0]: c.args[1] for c in executor.execute_market_simple.call_args_list}
    assert calls == {"NZDUSD": Direction.BUY, "EURUSD": Direction.SELL}


def test_ignores_non_xsect_positions():
    xsect, executor, api = make_bot(CLOSES)
    foreign = make_position("GBPUSD", "Sell")
    foreign.Label = "ArgoAlgo_ME_GBPUSD"
    api.Positions = [foreign]
    xsect.on_bar_closed()
    executor.close_position.assert_not_called()


def test_skips_outside_rebal_hour():
    xsect, executor, _ = make_bot(CLOSES, open_hour=14)
    xsect.on_bar_closed()
    executor.execute_market_simple.assert_not_called()


def test_skips_weekend_bar():
    xsect, executor, _ = make_bot(CLOSES, dow="Sunday")
    xsect.on_bar_closed()
    executor.execute_market_simple.assert_not_called()


def test_skips_insufficient_history():
    closes = dict(CLOSES)
    closes["AUDUSD"] = [0.65] * 3  # fewer than LOOKBACK+2 bars
    xsect, executor, _ = make_bot(closes)
    xsect.on_bar_closed()
    executor.execute_market_simple.assert_not_called()


def test_rebalances_once_per_day():
    xsect, executor, _ = make_bot(CLOSES)
    xsect.on_bar_closed()
    xsect.on_bar_closed()
    assert executor.execute_market_simple.call_count == 2  # 2 legs, day one only
