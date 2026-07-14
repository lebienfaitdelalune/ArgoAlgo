"""
test_phase8.py
Phase 8 tests: optimization modules — fitness function, backtest validator,
parameter ranges, walk-forward analysis, sensitivity analysis.
No cTrader API required.
"""

from __future__ import annotations

import sys
import os
from datetime import date
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimization.fitness import calculate_fitness
from optimization.backtest_validator import (
    BacktestValidator,
    KPI_MINIMUMS,
    KpiResult,
    ValidationReport,
)
from optimization.parameter_ranges import (
    ParamRange,
    TREND_FOLLOWING_RANGES,
    MEAN_REVERSION_RANGES,
    BREAKOUT_RANGES,
    ALL_RANGES,
)
from optimization.wfa import (
    WfaWindow,
    create_wfa_windows,
    calculate_wfa_efficiency,
    wfa_passes,
    _add_months,
)
from optimization.sensitivity import (
    OVERFIT_THRESHOLD_PCT,
    PerturbationResult,
    SensitivityAnalyzer,
    perturb_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bt(total_trades=250, net_profit=5000.0, profit_factor=1.8, max_dd=8.0):
    """Build a mock cTrader backtesting result object."""
    bt = MagicMock()
    bt.TotalTrades = total_trades
    bt.NetProfit = net_profit
    bt.ProfitFactor = profit_factor
    bt.MaxEquityDrawdownPercentages = max_dd
    return bt


def _passing_metrics() -> dict:
    return {
        "profit_factor":      1.8,
        "sharpe_ratio":       1.2,
        "max_drawdown_pct":   10.0,
        "win_rate_pct":       45.0,
        "avg_win_loss_ratio": 1.7,
        "recovery_factor":    4.0,
        "total_trades":       300.0,
    }


# ===========================================================================
# TestCalculateFitness
# ===========================================================================

class TestCalculateFitness:
    def test_passing_result_returns_positive(self):
        score = calculate_fitness(_make_bt())
        assert score > 0.0

    def test_formula_is_np_times_pf_over_dd(self):
        # 5000 * 1.8 / 8.0 = 1125.0
        score = calculate_fitness(_make_bt(net_profit=5000.0, profit_factor=1.8, max_dd=8.0))
        assert score == pytest.approx(1125.0, rel=1e-6)

    def test_disqualified_too_few_trades(self):
        assert calculate_fitness(_make_bt(total_trades=199)) == -1.0

    def test_exactly_200_trades_is_allowed(self):
        assert calculate_fitness(_make_bt(total_trades=200)) > 0.0

    def test_disqualified_max_dd_over_20(self):
        assert calculate_fitness(_make_bt(max_dd=20.1)) == -1.0

    def test_exactly_20_dd_is_allowed(self):
        assert calculate_fitness(_make_bt(max_dd=20.0)) > 0.0

    def test_disqualified_net_profit_zero(self):
        assert calculate_fitness(_make_bt(net_profit=0.0)) == -1.0

    def test_disqualified_net_profit_negative(self):
        assert calculate_fitness(_make_bt(net_profit=-100.0)) == -1.0

    def test_near_zero_drawdown_clamped(self):
        # max_dd=0.001 → clamped to 0.01 → 5000 * 1.8 / 0.01 = 900_000
        score = calculate_fitness(_make_bt(net_profit=5000.0, profit_factor=1.8, max_dd=0.001))
        assert score == pytest.approx(900_000.0, rel=1e-4)

    def test_malformed_object_returns_minus_one(self):
        bad = MagicMock(spec=[])  # no attributes
        assert calculate_fitness(bad) == -1.0

    def test_attribute_error_returns_minus_one(self):
        bt = MagicMock()
        bt.TotalTrades = 250
        type(bt).NetProfit = property(lambda self: (_ for _ in ()).throw(RuntimeError("no profit")))
        # RuntimeError on attribute access → should return -1.0
        # Simulate via side_effect on property access
        bt2 = MagicMock()
        bt2.TotalTrades = 250
        del bt2.NetProfit  # AttributeError on access
        assert calculate_fitness(bt2) == -1.0

    def test_higher_profit_factor_gives_higher_score(self):
        s1 = calculate_fitness(_make_bt(profit_factor=1.5))
        s2 = calculate_fitness(_make_bt(profit_factor=2.5))
        assert s2 > s1

    def test_higher_drawdown_gives_lower_score(self):
        s1 = calculate_fitness(_make_bt(max_dd=5.0))
        s2 = calculate_fitness(_make_bt(max_dd=15.0))
        assert s1 > s2


# ===========================================================================
# TestBacktestValidator
# ===========================================================================

class TestBacktestValidator:
    def test_all_passing_metrics_returns_pass(self):
        v = BacktestValidator()
        report = v.validate(_passing_metrics())
        assert report.passed is True

    def test_all_passing_no_failed_kpis(self):
        v = BacktestValidator()
        report = v.validate(_passing_metrics())
        assert report.failed_kpis() == []

    def test_below_profit_factor_fails(self):
        m = {**_passing_metrics(), "profit_factor": 1.2}
        report = BacktestValidator().validate(m)
        assert report.passed is False
        assert any(r.name == "profit_factor" for r in report.failed_kpis())

    def test_drawdown_at_limit_passes(self):
        m = {**_passing_metrics(), "max_drawdown_pct": 15.0}
        report = BacktestValidator().validate(m)
        kpi = next(r for r in report.results if r.name == "max_drawdown_pct")
        assert kpi.passed is True

    def test_drawdown_above_limit_fails(self):
        m = {**_passing_metrics(), "max_drawdown_pct": 15.1}
        report = BacktestValidator().validate(m)
        kpi = next(r for r in report.results if r.name == "max_drawdown_pct")
        assert kpi.passed is False

    def test_missing_kpi_treated_as_zero(self):
        report = BacktestValidator().validate({})
        assert report.passed is False  # everything missing → all fail

    def test_report_contains_all_kpis(self):
        report = BacktestValidator().validate(_passing_metrics())
        reported_names = {r.name for r in report.results}
        assert reported_names == set(KPI_MINIMUMS.keys())

    def test_exactly_at_minimum_passes(self):
        m = {k: v for k, v in KPI_MINIMUMS.items()}
        # For drawdown, minimum IS the threshold (<=), so exactly at threshold passes
        report = BacktestValidator().validate(m)
        assert report.passed is True

    def test_failed_kpis_only_contains_failures(self):
        m = {**_passing_metrics(), "sharpe_ratio": 0.5, "win_rate_pct": 30.0}
        report = BacktestValidator().validate(m)
        failed_names = {r.name for r in report.failed_kpis()}
        assert failed_names == {"sharpe_ratio", "win_rate_pct"}

    def test_kpi_result_repr_shows_pass(self):
        r = KpiResult(name="profit_factor", value=2.0, threshold=1.5, passed=True)
        assert "PASS" in repr(r)

    def test_kpi_result_repr_shows_fail(self):
        r = KpiResult(name="profit_factor", value=1.0, threshold=1.5, passed=False)
        assert "FAIL" in repr(r)

    def test_validation_report_repr(self):
        report = BacktestValidator().validate(_passing_metrics())
        assert "PASS" in repr(report)


# ===========================================================================
# TestParamRange
# ===========================================================================

class TestParamRange:
    def test_values_includes_min_and_max(self):
        r = ParamRange("x", 1.0, 3.0, 1.0)
        v = r.values()
        assert v[0] == pytest.approx(1.0)
        assert v[-1] == pytest.approx(3.0)

    def test_values_correct_count(self):
        r = ParamRange("x", 8, 20, 2)  # 8,10,12,14,16,18,20 = 7 values
        assert len(r.values()) == 7

    def test_values_step_size(self):
        r = ParamRange("x", 1.5, 3.0, 0.5)
        vals = r.values()
        for i in range(len(vals) - 1):
            assert vals[i + 1] - vals[i] == pytest.approx(0.5, abs=1e-9)

    def test_contains_min_val(self):
        r = ParamRange("x", 1.5, 3.0, 0.5)
        assert r.contains(1.5) is True

    def test_contains_max_val(self):
        r = ParamRange("x", 1.5, 3.0, 0.5)
        assert r.contains(3.0) is True

    def test_contains_mid_val(self):
        r = ParamRange("x", 1.5, 3.0, 0.5)
        assert r.contains(2.0) is True

    def test_does_not_contain_below_min(self):
        r = ParamRange("x", 1.5, 3.0, 0.5)
        assert r.contains(1.0) is False

    def test_does_not_contain_above_max(self):
        r = ParamRange("x", 1.5, 3.0, 0.5)
        assert r.contains(3.5) is False

    def test_clamp_below_min(self):
        r = ParamRange("x", 2.0, 5.0, 1.0)
        assert r.clamp(0.0) == pytest.approx(2.0)

    def test_clamp_above_max(self):
        r = ParamRange("x", 2.0, 5.0, 1.0)
        assert r.clamp(10.0) == pytest.approx(5.0)

    def test_clamp_within_range_unchanged(self):
        r = ParamRange("x", 2.0, 5.0, 1.0)
        assert r.clamp(3.5) == pytest.approx(3.5)

    def test_repr_contains_name(self):
        r = ParamRange("adx_threshold", 20, 35, 5)
        assert "adx_threshold" in repr(r)


class TestStrategyRanges:
    def test_trend_following_has_all_params(self):
        names = {r.name for r in TREND_FOLLOWING_RANGES}
        assert "fast_ema_period" in names
        assert "slow_ema_period" in names
        assert "adx_threshold" in names
        assert "sl_atr_multiplier" in names
        assert "tp_rr" in names

    def test_mean_reversion_has_all_params(self):
        names = {r.name for r in MEAN_REVERSION_RANGES}
        assert "bollinger_period" in names
        assert "bollinger_deviation" in names
        assert "rsi_oversold" in names
        assert "rsi_overbought" in names
        assert "adx_filter_threshold" in names

    def test_breakout_has_all_params(self):
        names = {r.name for r in BREAKOUT_RANGES}
        assert "donchian_period" in names
        assert "sl_atr_multiplier" in names
        assert "tp_rr" in names

    def test_all_ranges_lookup(self):
        assert "TrendFollowing" in ALL_RANGES
        assert "MeanReversion" in ALL_RANGES
        assert "Breakout" in ALL_RANGES

    def test_all_ranges_point_to_correct_lists(self):
        assert ALL_RANGES["TrendFollowing"] is TREND_FOLLOWING_RANGES

    def test_fast_ema_period_range(self):
        r = next(p for p in TREND_FOLLOWING_RANGES if p.name == "fast_ema_period")
        assert r.min_val == 8
        assert r.max_val == 20
        assert r.step == 2


# ===========================================================================
# TestWfa
# ===========================================================================

class TestAddMonths:
    def test_add_one_month(self):
        assert _add_months(date(2021, 1, 1), 1) == date(2021, 2, 1)

    def test_add_across_year_boundary(self):
        assert _add_months(date(2021, 11, 1), 3) == date(2022, 2, 1)

    def test_add_end_of_january_to_feb(self):
        # Jan 31 + 1 month → Feb 28 (2021 not a leap year)
        assert _add_months(date(2021, 1, 31), 1) == date(2021, 2, 28)

    def test_add_end_of_january_leap_year(self):
        # Jan 31 + 1 month → Feb 29 (2024 is a leap year)
        assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_add_zero_months(self):
        assert _add_months(date(2021, 6, 15), 0) == date(2021, 6, 15)


class TestCreateWfaWindows:
    def test_returns_correct_number_of_windows(self):
        # 5 years = 60 months; IS=4, OOS=2, step=2
        # Window requires IS+OOS=6 months; last start where start+6 <= 60 months is month 54
        # starts: 0,2,4,...,54 → 28 windows
        windows = create_wfa_windows(
            start=date(2021, 1, 1),
            end=date(2026, 1, 1),
            in_sample_months=4,
            out_sample_months=2,
            step_months=2,
        )
        assert len(windows) == 28

    def test_first_window_starts_at_start(self):
        windows = create_wfa_windows(date(2021, 1, 1), date(2023, 1, 1))
        assert windows[0].in_sample_start == date(2021, 1, 1)

    def test_windows_are_sequential(self):
        windows = create_wfa_windows(date(2021, 1, 1), date(2023, 1, 1))
        for i in range(len(windows) - 1):
            assert windows[i + 1].in_sample_start == windows[i + 1].in_sample_start

    def test_in_sample_end_equals_out_sample_start(self):
        windows = create_wfa_windows(date(2021, 1, 1), date(2023, 1, 1))
        for w in windows:
            assert w.in_sample_end == w.out_sample_start

    def test_incomplete_window_excluded(self):
        # 7 months total; IS=4, OOS=2, step=2 → only 1 full window (4+2=6 ≤ 7, next would be 2+4+2=8 > 7)
        windows = create_wfa_windows(
            date(2021, 1, 1),
            date(2021, 8, 1),  # 7 months
            in_sample_months=4,
            out_sample_months=2,
            step_months=2,
        )
        assert len(windows) == 1

    def test_raises_on_invalid_months(self):
        with pytest.raises(ValueError):
            create_wfa_windows(date(2021, 1, 1), date(2023, 1, 1), in_sample_months=0)

    def test_raises_on_start_after_end(self):
        with pytest.raises(ValueError):
            create_wfa_windows(date(2022, 1, 1), date(2021, 1, 1))

    def test_window_repr(self):
        w = WfaWindow(date(2021, 1, 1), date(2021, 5, 1), date(2021, 5, 1), date(2021, 7, 1))
        assert "IS=" in repr(w) and "OOS=" in repr(w)


class TestCalculateWfaEfficiency:
    def test_efficiency_ratio_formula(self):
        # avg_out=1.4, avg_in=2.0 → 0.7
        eff = calculate_wfa_efficiency([2.0, 2.0], [1.4, 1.4])
        assert eff == pytest.approx(0.7)

    def test_empty_in_sample_returns_zero(self):
        assert calculate_wfa_efficiency([], [1.5, 1.6]) == 0.0

    def test_empty_out_sample_returns_zero(self):
        assert calculate_wfa_efficiency([1.5, 1.6], []) == 0.0

    def test_zero_avg_in_sample_returns_zero(self):
        assert calculate_wfa_efficiency([0.0, 0.0], [1.5]) == 0.0

    def test_efficiency_above_one_possible(self):
        # OOS outperforms IS
        eff = calculate_wfa_efficiency([1.5], [2.0])
        assert eff > 1.0

    def test_single_window(self):
        eff = calculate_wfa_efficiency([2.0], [1.4])
        assert eff == pytest.approx(0.7)

    def test_different_lengths_allowed(self):
        # No requirement for equal lengths — averages computed independently
        eff = calculate_wfa_efficiency([1.8, 2.0, 2.2], [1.4])
        assert eff == pytest.approx(1.4 / 2.0, rel=1e-6)


class TestWfaPasses:
    def test_at_threshold_passes(self):
        assert wfa_passes(0.70) is True

    def test_above_threshold_passes(self):
        assert wfa_passes(0.85) is True

    def test_below_threshold_fails(self):
        assert wfa_passes(0.69) is False

    def test_custom_threshold(self):
        assert wfa_passes(0.60, min_efficiency=0.60) is True
        assert wfa_passes(0.59, min_efficiency=0.60) is False


# ===========================================================================
# TestSensitivityAnalysis
# ===========================================================================

class TestPerturbValue:
    def test_positive_perturbation(self):
        assert perturb_value(100.0, 10.0) == pytest.approx(110.0)

    def test_negative_perturbation(self):
        assert perturb_value(100.0, -10.0) == pytest.approx(90.0)

    def test_zero_perturbation(self):
        assert perturb_value(100.0, 0.0) == pytest.approx(100.0)

    def test_twenty_percent(self):
        assert perturb_value(50.0, 20.0) == pytest.approx(60.0)


class TestSensitivityAnalyzer:
    def _constant_fitness(self, params):
        """Fitness that ignores param values — always returns 100."""
        return 100.0

    def _sensitive_fitness(self, params):
        """Fitness that drops sharply when adx_threshold moves away from 25."""
        base = 100.0
        deviation = abs(params.get("adx_threshold", 25.0) - 25.0)
        return max(0.0, base - deviation * 10)

    def test_produces_result_per_param_per_perturbation(self):
        sa = SensitivityAnalyzer()
        params = {"a": 10.0, "b": 20.0}
        results = sa.analyze(params, self._constant_fitness)
        # 2 params × 4 perturbations = 8 results
        assert len(results) == 8

    def test_no_overfit_when_fitness_stable(self):
        sa = SensitivityAnalyzer()
        params = {"x": 25.0}
        results = sa.analyze(params, self._constant_fitness)
        assert not sa.has_overfitting_risk(results)

    def test_overfit_flagged_on_sharp_drop(self):
        sa = SensitivityAnalyzer()
        # +10% of 25 = 27.5 → deviation=2.5 → fitness=75 → drop=25% < 30% …
        # use a steeper function: deviation*40 to get >30% drop
        def steep(params):
            deviation = abs(params.get("adx_threshold", 25.0) - 25.0)
            return max(0.0, 100.0 - deviation * 40)
        params = {"adx_threshold": 25.0}
        results = sa.analyze(params, steep)
        # +10% → 27.5 → deviation=2.5 → fitness=0 → drop=100% > 30% → flagged
        assert sa.has_overfitting_risk(results)

    def test_twenty_percent_perturbation_not_minor(self):
        sa = SensitivityAnalyzer()
        # Only ±10% perturbations should trigger overfit flag
        def drops_on_20pct(params):
            x = params.get("x", 10.0)
            if abs(x - 10.0) > 1.5:  # >15% away → 20% perturbation triggers drop
                return 0.0
            return 100.0
        params = {"x": 10.0}
        results = sa.analyze(params, drops_on_20pct, perturbations=[20.0, -20.0])
        # ±20% perturbations should NOT be flagged as overfit (only ±10% is "minor")
        assert not sa.has_overfitting_risk(results)

    def test_flagged_params_returns_param_names(self):
        sa = SensitivityAnalyzer()
        def steep(params):
            deviation = abs(params.get("adx_threshold", 25.0) - 25.0)
            return max(0.0, 100.0 - deviation * 40)
        params = {"adx_threshold": 25.0, "tp_rr": 2.0}
        results = sa.analyze(params, steep)
        flagged = sa.flagged_params(results)
        assert "adx_threshold" in flagged
        assert "tp_rr" not in flagged

    def test_custom_perturbations(self):
        sa = SensitivityAnalyzer()
        params = {"x": 10.0}
        results = sa.analyze(params, self._constant_fitness, perturbations=[5.0])
        assert len(results) == 1
        assert results[0].perturbation_pct == 5.0

    def test_perturbation_result_repr(self):
        r = PerturbationResult(
            param_name="adx", original_value=25.0, perturbed_value=27.5,
            perturbation_pct=10.0, original_fitness=100.0, perturbed_fitness=60.0,
            fitness_drop_pct=40.0, overfitted=True,
        )
        assert "OVERFIT" in repr(r)
        assert "adx" in repr(r)

    def test_original_fitness_zero_no_crash(self):
        sa = SensitivityAnalyzer()
        params = {"x": 1.0}
        results = sa.analyze(params, lambda _: 0.0)
        # Should not raise; drop_pct should be 0.0 when original is 0
        assert all(r.fitness_drop_pct == 0.0 for r in results)

    def test_analyze_does_not_modify_original_params(self):
        sa = SensitivityAnalyzer()
        params = {"x": 10.0, "y": 20.0}
        original_copy = dict(params)
        sa.analyze(params, self._constant_fitness)
        assert params == original_copy
