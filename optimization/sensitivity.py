"""
sensitivity.py
Parameter sensitivity analysis for ArgoAlgo strategies (from PLAN §8.4, Step 4).

For each parameter in a best-fit set, each parameter is perturbed individually
by ±10% and ±20% while all others are held fixed.  A parameter is flagged as
a potential overfit risk if a ±10% perturbation causes a fitness drop > 30%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: A ±10% perturbation causing more than this fitness drop (%) flags overfit.
OVERFIT_THRESHOLD_PCT: float = 30.0

#: Default perturbation percentages applied to each parameter.
DEFAULT_PERTURBATIONS: list[float] = [10.0, -10.0, 20.0, -20.0]

#: Perturbations considered "minor" — used to determine overfit flag.
_MINOR_PERTURBATION_PCT: float = 10.0


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class PerturbationResult:
    """Outcome of a single parameter perturbation.

    Attributes:
        param_name: Name of the perturbed parameter.
        original_value: Original parameter value.
        perturbed_value: Value after perturbation.
        perturbation_pct: Perturbation size in percent (positive = increase).
        original_fitness: Fitness with the original parameter set.
        perturbed_fitness: Fitness after perturbation.
        fitness_drop_pct: Drop as a percentage of original_fitness.
                          Positive = drop; negative = improvement.
        overfitted: True if this is a minor (|pct| <= 10) perturbation AND
                    the fitness drop exceeds OVERFIT_THRESHOLD_PCT.
    """

    param_name: str
    original_value: float
    perturbed_value: float
    perturbation_pct: float
    original_fitness: float
    perturbed_fitness: float
    fitness_drop_pct: float
    overfitted: bool

    def __repr__(self) -> str:
        status = "OVERFIT" if self.overfitted else "OK"
        return (
            f"PerturbationResult("
            f"{self.param_name}, "
            f"{self.perturbation_pct:+.0f}%, "
            f"drop={self.fitness_drop_pct:.1f}%, "
            f"{status})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def perturb_value(value: float, pct: float) -> float:
    """Return *value* adjusted by *pct* percent.

    Args:
        value: Original value.
        pct: Perturbation in percent (e.g. 10.0 → +10%, -10.0 → -10%).

    Returns:
        Perturbed value: value × (1 + pct / 100).
    """
    return value * (1.0 + pct / 100.0)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class SensitivityAnalyzer:
    """Perturbs each parameter individually and records the fitness impact.

    Usage::

        analyzer = SensitivityAnalyzer()
        results = analyzer.analyze(
            params={"adx_threshold": 25.0, "tp_rr": 2.0},
            fitness_fn=lambda p: my_backtest_fitness(p),
        )
        if analyzer.has_overfitting_risk(results):
            print("Warning: potential overfit detected")
    """

    def analyze(
        self,
        params: dict[str, float],
        fitness_fn: Callable[[dict[str, float]], float],
        perturbations: list[float] | None = None,
        flag_threshold_pct: float = OVERFIT_THRESHOLD_PCT,
    ) -> list[PerturbationResult]:
        """Perturb each parameter individually and measure fitness impact.

        Each parameter is varied one at a time while all others remain at
        their original values.

        Args:
            params: Current best-fit parameter set (name → value).
            fitness_fn: Callable that accepts a params dict and returns a
                        fitness score (float).  Must handle any param values
                        without raising.
            perturbations: List of perturbation percentages to apply.
                           Defaults to DEFAULT_PERTURBATIONS (±10%, ±20%).
            flag_threshold_pct: Drop percentage above which a ±10%
                                perturbation is considered overfitting risk.

        Returns:
            List of PerturbationResult (one per parameter × perturbation).
        """
        if perturbations is None:
            perturbations = DEFAULT_PERTURBATIONS

        original_fitness = fitness_fn(params)
        results: list[PerturbationResult] = []

        for name, value in params.items():
            for pct in perturbations:
                perturbed_val = perturb_value(value, pct)
                perturbed_params = dict(params)
                perturbed_params[name] = perturbed_val

                new_fitness = fitness_fn(perturbed_params)

                if original_fitness != 0.0:
                    drop_pct = (original_fitness - new_fitness) / abs(original_fitness) * 100.0
                else:
                    drop_pct = 0.0

                is_minor = abs(pct) <= _MINOR_PERTURBATION_PCT
                overfitted = is_minor and drop_pct > flag_threshold_pct

                results.append(PerturbationResult(
                    param_name=name,
                    original_value=value,
                    perturbed_value=perturbed_val,
                    perturbation_pct=pct,
                    original_fitness=original_fitness,
                    perturbed_fitness=new_fitness,
                    fitness_drop_pct=drop_pct,
                    overfitted=overfitted,
                ))

        return results

    def has_overfitting_risk(self, results: list[PerturbationResult]) -> bool:
        """Return True if any result is flagged as a potential overfit.

        Args:
            results: Output of :meth:`analyze`.

        Returns:
            True if at least one PerturbationResult has ``overfitted=True``.
        """
        return any(r.overfitted for r in results)

    def flagged_params(self, results: list[PerturbationResult]) -> list[str]:
        """Return the names of parameters flagged as potential overfits.

        Args:
            results: Output of :meth:`analyze`.

        Returns:
            Deduplicated list of parameter names that have overfitting risk.
        """
        seen: set[str] = set()
        flagged: list[str] = []
        for r in results:
            if r.overfitted and r.param_name not in seen:
                flagged.append(r.param_name)
                seen.add(r.param_name)
        return flagged
