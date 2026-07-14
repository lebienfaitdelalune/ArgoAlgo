"""
test_helpers.py
Unit tests for utils/helpers.py — all pure functions, no cTrader API required.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.helpers import (
    calculate_position_volume,
    clamp,
    format_pnl,
    format_pct,
    is_trading_day,
    is_within_trading_hours,
    parse_days_string,
    parse_symbols_string,
    pips_to_price,
    price_to_pips,
    round_to_step,
)


# ---------------------------------------------------------------------------
# round_to_step
# ---------------------------------------------------------------------------

class TestRoundToStep:
    def test_exact_multiple(self):
        assert round_to_step(1.0, 0.01) == pytest.approx(1.0)

    def test_rounds_down(self):
        assert round_to_step(1.239, 0.01) == pytest.approx(1.23)

    def test_large_step(self):
        assert round_to_step(10500, 1000) == pytest.approx(10000.0)

    def test_volume_step(self):
        # 1234 units with step 100 -> 1200
        assert round_to_step(1234, 100) == pytest.approx(1200.0)

    def test_raises_on_zero_step(self):
        with pytest.raises(ValueError):
            round_to_step(1.0, 0)

    def test_raises_on_negative_step(self):
        with pytest.raises(ValueError):
            round_to_step(1.0, -0.01)


# ---------------------------------------------------------------------------
# clamp
# ---------------------------------------------------------------------------

class TestClamp:
    def test_within_range(self):
        assert clamp(5.0, 1.0, 10.0) == 5.0

    def test_below_minimum(self):
        assert clamp(-1.0, 0.0, 10.0) == 0.0

    def test_above_maximum(self):
        assert clamp(15.0, 0.0, 10.0) == 10.0

    def test_at_minimum(self):
        assert clamp(0.0, 0.0, 10.0) == 0.0

    def test_at_maximum(self):
        assert clamp(10.0, 0.0, 10.0) == 10.0


# ---------------------------------------------------------------------------
# pips_to_price / price_to_pips
# ---------------------------------------------------------------------------

class TestPipConversions:
    def test_pips_to_price_eurusd(self):
        assert pips_to_price(10.0, 0.0001) == pytest.approx(0.001)

    def test_price_to_pips_eurusd(self):
        assert price_to_pips(0.001, 0.0001) == pytest.approx(10.0)

    def test_pips_to_price_usdjpy(self):
        # JPY pairs: pip_size = 0.01
        assert pips_to_price(10.0, 0.01) == pytest.approx(0.1)

    def test_price_to_pips_usdjpy(self):
        assert price_to_pips(0.1, 0.01) == pytest.approx(10.0)

    def test_roundtrip(self):
        pips = 25.0
        pip_size = 0.0001
        assert price_to_pips(pips_to_price(pips, pip_size), pip_size) == pytest.approx(pips)

    def test_price_to_pips_raises_zero_pip_size(self):
        with pytest.raises(ValueError):
            price_to_pips(0.001, 0.0)


# ---------------------------------------------------------------------------
# calculate_position_volume
# ---------------------------------------------------------------------------

class TestCalculatePositionVolume:
    def test_basic_calculation(self):
        # $10,000 * 1% / (20 pips * $0.001/pip/unit) = $100 / $0.02 = 5,000 units
        vol = calculate_position_volume(
            balance=10_000,
            risk_pct=1.0,
            stop_loss_pips=20.0,
            pip_value=0.001,
            volume_min=1_000,
            volume_max=10_000_000,
            volume_step=1_000,
        )
        assert vol == pytest.approx(5_000.0)

    def test_clamps_to_minimum(self):
        # Very large SL should push volume below minimum
        vol = calculate_position_volume(
            balance=1_000,
            risk_pct=0.1,
            stop_loss_pips=1000.0,
            pip_value=1.0,
            volume_min=1_000,
            volume_max=10_000_000,
            volume_step=1_000,
        )
        assert vol == pytest.approx(1_000.0)

    def test_clamps_to_maximum(self):
        # Very small SL + large balance should push volume above maximum
        vol = calculate_position_volume(
            balance=1_000_000,
            risk_pct=5.0,
            stop_loss_pips=1.0,
            pip_value=0.01,
            volume_min=1_000,
            volume_max=10_000,
            volume_step=1_000,
        )
        assert vol == pytest.approx(10_000.0)

    def test_rounds_to_step(self):
        # Result should be rounded down to nearest step
        vol = calculate_position_volume(
            balance=10_000,
            risk_pct=1.0,
            stop_loss_pips=23.0,   # produces ~4347 -> rounded to 4000
            pip_value=1.0,
            volume_min=1_000,
            volume_max=10_000_000,
            volume_step=1_000,
        )
        assert vol % 1_000 == 0

    def test_raises_on_zero_balance(self):
        with pytest.raises(ValueError):
            calculate_position_volume(0, 1.0, 20.0, 1.0, 1000, 1_000_000, 1000)

    def test_raises_on_zero_risk(self):
        with pytest.raises(ValueError):
            calculate_position_volume(10_000, 0.0, 20.0, 1.0, 1000, 1_000_000, 1000)

    def test_raises_on_zero_sl(self):
        with pytest.raises(ValueError):
            calculate_position_volume(10_000, 1.0, 0.0, 1.0, 1000, 1_000_000, 1000)

    def test_raises_on_zero_pip_value(self):
        with pytest.raises(ValueError):
            calculate_position_volume(10_000, 1.0, 20.0, 0.0, 1000, 1_000_000, 1000)


# ---------------------------------------------------------------------------
# parse_symbols_string
# ---------------------------------------------------------------------------

class TestParseSymbolsString:
    def test_standard(self):
        assert parse_symbols_string("EURUSD,GBPUSD,USDJPY") == ["EURUSD", "GBPUSD", "USDJPY"]

    def test_with_spaces(self):
        assert parse_symbols_string("EURUSD, GBPUSD, USDJPY") == ["EURUSD", "GBPUSD", "USDJPY"]

    def test_empty_string(self):
        assert parse_symbols_string("") == []

    def test_single_symbol(self):
        assert parse_symbols_string("EURUSD") == ["EURUSD"]

    def test_trailing_comma(self):
        result = parse_symbols_string("EURUSD,GBPUSD,")
        assert result == ["EURUSD", "GBPUSD"]


# ---------------------------------------------------------------------------
# parse_days_string
# ---------------------------------------------------------------------------

class TestParseDaysString:
    def test_full_week(self):
        result = parse_days_string("Mon,Tue,Wed,Thu,Fri")
        assert result == ["Mon", "Tue", "Wed", "Thu", "Fri"]

    def test_subset(self):
        result = parse_days_string("Mon,Wed,Fri")
        assert result == ["Mon", "Wed", "Fri"]

    def test_with_spaces(self):
        result = parse_days_string("Mon, Tue, Wed")
        assert result == ["Mon", "Tue", "Wed"]


# ---------------------------------------------------------------------------
# is_within_trading_hours
# ---------------------------------------------------------------------------

class TestIsWithinTradingHours:
    def test_inside_session(self):
        assert is_within_trading_hours(9, 7, 20) is True

    def test_before_session(self):
        assert is_within_trading_hours(6, 7, 20) is False

    def test_at_start(self):
        assert is_within_trading_hours(7, 7, 20) is True

    def test_at_end_exclusive(self):
        # End hour is exclusive
        assert is_within_trading_hours(20, 7, 20) is False

    def test_just_before_end(self):
        assert is_within_trading_hours(19, 7, 20) is True

    def test_midnight(self):
        assert is_within_trading_hours(0, 7, 20) is False


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------

class TestIsTradingDay:
    WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

    def test_weekday_allowed(self):
        assert is_trading_day("Mon", self.WEEKDAYS) is True

    def test_friday_allowed(self):
        assert is_trading_day("Fri", self.WEEKDAYS) is True

    def test_saturday_not_allowed(self):
        assert is_trading_day("Sat", self.WEEKDAYS) is False

    def test_sunday_not_allowed(self):
        assert is_trading_day("Sun", self.WEEKDAYS) is False

    def test_empty_allowed_list(self):
        assert is_trading_day("Mon", []) is False


# ---------------------------------------------------------------------------
# format_pnl / format_pct
# ---------------------------------------------------------------------------

class TestFormatters:
    def test_positive_pnl(self):
        assert format_pnl(42.5) == "+$42.50"

    def test_negative_pnl(self):
        assert format_pnl(-10.0) == "-$10.00"

    def test_zero_pnl(self):
        assert format_pnl(0.0) == "+$0.00"

    def test_format_pct(self):
        assert format_pct(1.5) == "1.50%"

    def test_format_pct_zero(self):
        assert format_pct(0.0) == "0.00%"
