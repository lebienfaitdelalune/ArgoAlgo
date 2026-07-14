"""
backtest_validator.py
Validates a set of backtest KPI metrics against the minimum acceptance criteria
defined in the PRD (§9.3) and PLAN (§8.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# KPI minimum thresholds (from PLAN §8.2)
# ---------------------------------------------------------------------------

#: Minimum acceptable values for each KPI.
#: For ``max_drawdown_pct``, *lower* is better (must be ≤ threshold).
#: For all other metrics, *higher* is better (must be ≥ threshold).
KPI_MINIMUMS: dict[str, float] = {
    "profit_factor":    1.5,
    "sharpe_ratio":     1.0,
    "max_drawdown_pct": 15.0,   # upper bound; lower is better
    "win_rate_pct":     40.0,
    "avg_win_loss_ratio": 1.5,
    "recovery_factor":  3.0,
    "total_trades":     200.0,
}

#: KPIs where lower values are better (checked with <=).
_LOWER_IS_BETTER: frozenset[str] = frozenset({"max_drawdown_pct"})


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class KpiResult:
    """Outcome for a single KPI check.

    Attributes:
        name: KPI identifier (matches a key in KPI_MINIMUMS).
        value: Observed value from the backtest.
        threshold: Minimum (or maximum) acceptable value.
        passed: True if the KPI meets the acceptance criterion.
    """

    name: str
    value: float
    threshold: float
    passed: bool

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"KpiResult({self.name}={self.value:.2f}, "
            f"threshold={self.threshold:.2f}, {status})"
        )


@dataclass
class ValidationReport:
    """Aggregated result of a full KPI validation run.

    Attributes:
        results: Per-KPI check results.
        passed: True only when every KPI passes.
    """

    results: list[KpiResult] = field(default_factory=list)
    passed: bool = False

    def failed_kpis(self) -> list[KpiResult]:
        """Return only the KPI results that did not pass."""
        return [r for r in self.results if not r.passed]

    def __repr__(self) -> str:
        n_pass = sum(1 for r in self.results if r.passed)
        verdict = "PASS" if self.passed else "FAIL"
        return f"ValidationReport({verdict}, {n_pass}/{len(self.results)} KPIs passed)"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class BacktestValidator:
    """Validates backtest KPI metrics against the PRD minimum criteria.

    Metrics are supplied as a plain dict.  Any KPI key absent from the dict
    is treated as 0.0 (i.e., it will fail unless the minimum is also 0).

    Example::

        validator = BacktestValidator()
        report = validator.validate({
            "profit_factor": 1.8,
            "sharpe_ratio": 1.2,
            "max_drawdown_pct": 12.0,
            "win_rate_pct": 45.0,
            "avg_win_loss_ratio": 1.7,
            "recovery_factor": 4.0,
            "total_trades": 350,
        })
        assert report.passed
    """

    def validate(self, metrics: dict[str, float]) -> ValidationReport:
        """Check every KPI against its minimum acceptance threshold.

        Args:
            metrics: Dict mapping KPI names to observed values.

        Returns:
            ValidationReport with per-KPI results and overall pass/fail.
        """
        results: list[KpiResult] = []

        for name, threshold in KPI_MINIMUMS.items():
            value = float(metrics.get(name, 0.0))
            if name in _LOWER_IS_BETTER:
                passed = value <= threshold
            else:
                passed = value >= threshold
            results.append(KpiResult(name=name, value=value, threshold=threshold, passed=passed))

        all_passed = all(r.passed for r in results)
        return ValidationReport(results=results, passed=all_passed)
