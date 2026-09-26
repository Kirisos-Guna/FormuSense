"""Trial-fitted surrogate: learning the gap between the model and the plant.

The first-principles engine in :mod:`app.core.engine` is a *prior*: it is
internally consistent but it is not this plant, with this equipment, on this day.
Every physical trial measures that gap, and the gap is the most valuable data the
project has, because it is systematic - the same dryer runs 8% less efficiently
every time, the same acid loses a fifth of its dose in an open kettle.

This module fits a small, heavily regularised ridge regression per KPI that
predicts the *residual* (measured minus first-principles) from the formulation
and process vector. Two decisions matter:

* **Model the residual, not the response.** A surrogate that predicts absolute
  values from eight trials will happily contradict mass balance and call a
  formulation with 4% protein "high protein". Fitting the *difference* keeps the
  physics as the backbone and lets the data move it, which is how a real
  development team uses pilot-plant history.
* **Regularise hard and shrink by sample size.** With 5-20 trials and 10-25
  features, ordinary least squares interpolates noise. Ridge with a standardised
  design, plus a blend factor of ``n / (n + 3)`` towards the physics prediction,
  means one trial nudges the prediction and ten trials genuinely move it.

Everything is reported: leave-one-out error per KPI, the residual bias, and the
standardised sensitivity of each KPI to each formulation/process feature. That
report is what the diagnosis and the reformulation plan cite.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import engine, kb, kpi as kpi_registry, nutrition
from .types import Brief, Formulation, Item

RIDGE_LAMBDA = 1.5
MIN_TRIALS = 2
BLEND_HALF_SATURATION = 3.0
# Below this many trials the gap is treated as a single plant offset rather than
# a function of the formulation. Estimating twenty coefficients from two trials
# is not regression, it is memorisation, and it produces confident nonsense.
BIAS_ONLY_TRIALS = 4


def _gauss_solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """Solve a small dense linear system by Gaussian elimination with pivoting."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            continue
        a[col], a[pivot] = a[pivot], a[col]
        pivot_value = a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] / pivot_value
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = a[row][n]
        for c in range(row + 1, n):
            total -= a[row][c] * solution[c]
        solution[row] = total / a[row][row] if abs(a[row][row]) > 1e-12 else 0.0
    return solution


@dataclass
class RidgeModel:
    """One ridge fit on standardised features."""

    kpi: str
    intercept: float
    coefficients: List[float]
    feature_names: List[str]
    means: List[float]
    scales: List[float]
    samples: int
    r2: float = 0.0
    loo_rmse: float = 0.0
    residual_bias: float = 0.0

    def predict_vector(self, x: Sequence[float]) -> float:
        total = self.intercept
        for value, mean, scale, coef in zip(x, self.means, self.scales, self.coefficients):
            if scale <= 1e-12:
                continue
            total += coef * ((value - mean) / scale)
        return total

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kpi": self.kpi,
            "samples": self.samples,
            "r2": round(self.r2, 3),
            "loo_rmse": round(self.loo_rmse, 4),
            "residual_bias": round(self.residual_bias, 4),
            "top_features": sorted(
                (
                    {"feature": name, "coefficient": round(coef, 4)}
                    for name, coef in zip(self.feature_names, self.coefficients)
                ),
                key=lambda row: -abs(row["coefficient"]),
            )[:5],
        }


@dataclass
class SurrogateSet:
    """A fitted set of residual models plus its own report card."""

    category: str
    ingredient_ids: List[str] = field(default_factory=list)
    param_ids: List[str] = field(default_factory=list)
    models: Dict[str, RidgeModel] = field(default_factory=dict)
    samples: int = 0
    is_fitted: bool = False
    blend: float = 0.0
    bias_only: bool = False
    notes: List[str] = field(default_factory=list)

    # ---------------------------------------------------------------- feature #
    def features(self, formulation: Formulation) -> List[float]:
        vector: List[float] = []
        for ingredient_id in self.ingredient_ids:
            vector.append(formulation.pct_of(ingredient_id))
        category = formulation.category
        defaults = kb.category(category).default_params()
        for param_id in self.param_ids:
            vector.append(float(formulation.params.get(param_id, defaults.get(param_id, 0.0))))
        return vector

    @property
    def feature_names(self) -> List[str]:
        table = kb.ingredients()
        names = [(table[i].name if i in table else i) for i in self.ingredient_ids]
        names += [f"param:{p}" for p in self.param_ids]
        return names

    # ------------------------------------------------------------------ fit #
    def fit(
        self,
        samples: Sequence[Tuple[Formulation, Dict[str, float]]],
        brief: Optional[Brief] = None,
        min_ingredient_pct: float = 0.5,
    ) -> "SurrogateSet":
        """Fit residual models from (formulation, measured KPI) pairs."""
        self.notes = []
        if len(samples) < MIN_TRIALS:
            self.notes.append(
                f"{len(samples)} trial(s) available: too few to fit a surrogate, so "
                "predictions remain first-principles only."
            )
            self.is_fitted = False
            return self

        # Feature set: ingredients that actually appear in the trial set, plus the
        # category's process parameters. A feature that is zero in every trial
        # carries no information and would only add noise.
        counts: Dict[str, int] = {}
        for formulation, _ in samples:
            for item in formulation.items:
                if item.pct >= min_ingredient_pct:
                    counts[item.ingredient_id] = counts.get(item.ingredient_id, 0) + 1
        self.ingredient_ids = sorted(
            [i for i, c in counts.items() if c >= max(1, len(samples) // 2)],
            key=lambda i: -sum(f.pct_of(i) for f, _ in samples),
        )
        self.category = samples[0][0].category
        self.param_ids = [p.id for p in kb.category(self.category).parameters if p.id != "water_added_pct"]

        x_rows = [self.features(f) for f, _ in samples]
        feature_count = len(x_rows[0]) if x_rows else 0
        if feature_count == 0:
            self.notes.append("No usable formulation features in the trial set.")
            self.is_fitted = False
            return self

        # KPI set: everything measured in at least half the trials and known to the
        # registry, so the surrogate cannot invent a new product characteristic.
        measured: Dict[str, int] = {}
        for _, values in samples:
            for kpi_id in values:
                measured[kpi_id] = measured.get(kpi_id, 0) + 1
        kpi_ids = [
            k
            for k, c in measured.items()
            if c >= max(2, len(samples) // 2) and k in kpi_registry.KPI_DEFS
        ]

        bias_only = len(samples) < BIAS_ONLY_TRIALS
        for kpi_id in kpi_ids:
            pairs = [(x, dict(values)) for x, (_, values) in zip(x_rows, samples) if kpi_id in values]
            if len(pairs) < MIN_TRIALS:
                continue
            residuals: List[float] = []
            design: List[List[float]] = []
            for x, values in pairs:
                # Model the *gap* between the plant and the physics, never the
                # absolute response: the physics stays the backbone.
                residuals.append(float(values[kpi_id]) - _first_principles(kpi_id, x, self))
                design.append(x)
            if bias_only:
                self.models[kpi_id] = _bias_model(kpi_id, residuals, self.feature_names)
            else:
                self.models[kpi_id] = _ridge_fit(kpi_id, design, residuals, self.feature_names)

        self.samples = len(samples)
        self.is_fitted = bool(self.models)
        # A one-parameter offset estimated from n trials is far better determined
        # than a 20-coefficient fit, so it is weighted more strongly: this is what
        # lets a single trial move the next formulation decisively instead of
        # nudging it by a quarter.
        self.blend = (
            len(samples) / (len(samples) + 1.0)
            if bias_only
            else len(samples) / (len(samples) + BLEND_HALF_SATURATION)
        )
        self.bias_only = bias_only
        self.notes.append(
            f"Fitted {len(self.models)} residual model(s) on {len(samples)} trial(s) with "
            f"{feature_count} features; corrections are applied at {self.blend*100:.0f}% weight."
        )
        if bias_only:
            self.notes.append(
                "With fewer than "
                f"{BIAS_ONLY_TRIALS} trials the gap is modelled as a single plant offset per KPI "
                "rather than as a function of the formulation, which is the honest reading of "
                "that much data."
            )
        return self

    # ---------------------------------------------------------------- apply #
    def apply(self, values: Dict[str, float], formulation: Formulation) -> Tuple[Dict[str, float], str]:
        """Blend the physics prediction with the learned residual correction."""
        if not self.is_fitted:
            return {}, "no surrogate fitted"
        x = self.features(formulation)
        corrected: Dict[str, float] = {}
        touched: List[str] = []
        for kpi_id, model in self.models.items():
            if kpi_id not in values:
                continue
            residual = model.predict_vector(x)
            corrected[kpi_id] = float(values[kpi_id]) + self.blend * residual
            if abs(self.blend * residual) > 1e-9:
                touched.append(kpi_id)
        note = (
            f"residual correction from {self.samples} trial(s) applied to "
            f"{len(touched)} KPI(s) at {self.blend*100:.0f}% weight"
        )
        return corrected, note

    def report(self) -> Dict[str, Any]:
        return {
            "fitted": self.is_fitted,
            "samples": self.samples,
            "blend": round(self.blend, 3),
            "features": len(self.ingredient_ids) + len(self.param_ids),
            "models": [m.as_dict() for m in self.models.values()],
            "notes": list(self.notes),
        }

    def sensitivity(self, top: int = 6) -> List[Dict[str, Any]]:
        """Rank features by their influence across all fitted KPI models."""
        rows: List[Dict[str, Any]] = []
        names = self.feature_names
        for index, name in enumerate(names):
            total = 0.0
            drivers: List[str] = []
            for kpi_id, model in self.models.items():
                if index >= len(model.coefficients):
                    continue
                coef = model.coefficients[index]
                total += abs(coef)
                if abs(coef) > 0.15 * max(abs(c) for c in model.coefficients or [1.0]):
                    drivers.append(kpi_id)
            rows.append({"feature": name, "influence": round(total, 4), "drives": drivers[:5]})
        rows.sort(key=lambda r: -r["influence"])
        return rows[:top]

    def as_dict(self) -> Dict[str, Any]:
        return self.report()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _first_principles(kpi_id: str, x: Sequence[float], surrogate: "SurrogateSet") -> float:
    """Rebuild the physics prediction for a feature vector.

    The surrogate stores feature vectors, not formulations, so the physics
    baseline has to be reconstructable from a vector: this rebuilds a formulation
    from the ingredient part of the vector, which is exact for the features that
    were kept (ingredients at or above the inclusion threshold).
    """
    formulation = Formulation(category=surrogate.category, version=0, items=[])
    for ingredient_id, pct in zip(surrogate.ingredient_ids, x[: len(surrogate.ingredient_ids)]):
        if pct > 0.001:
            formulation.items.append(Item(ingredient_id, float(pct)))
    for param_id, value in zip(surrogate.param_ids, x[len(surrogate.ingredient_ids) :]):
        formulation.params[param_id] = float(value)
    result = engine.predict(formulation)
    return float(result.values.get(kpi_id, 0.0))


def _bias_model(kpi_id: str, residuals: List[float], feature_names: List[str]) -> RidgeModel:
    """Intercept-only residual model: a constant offset for this plant."""
    n = len(residuals)
    mean = sum(residuals) / max(n, 1)
    variance = sum((r - mean) ** 2 for r in residuals) / max(n - 1, 1)
    # Leave-one-out RMSE for an intercept-only model is the sample standard
    # deviation scaled by sqrt(1 + 1/n).
    loo = math.sqrt(variance * (1.0 + 1.0 / max(n, 1)))
    return RidgeModel(
        kpi=kpi_id,
        intercept=mean,
        coefficients=[0.0] * len(feature_names),
        feature_names=list(feature_names),
        means=[0.0] * len(feature_names),
        scales=[1.0] * len(feature_names),
        samples=n,
        r2=0.0,
        loo_rmse=loo,
        residual_bias=mean,
    )


def _ridge_fit(
    kpi_id: str,
    design: List[List[float]],
    targets: List[float],
    feature_names: List[str],
    lam: float = RIDGE_LAMBDA,
) -> RidgeModel:
    n = len(design)
    p = len(feature_names)
    means = [0.0] * p
    for row in design:
        for j in range(min(p, len(row))):
            means[j] += row[j] / n
    scales = [1.0] * p
    for j in range(p):
        variance = 0.0
        for row in design:
            if j < len(row):
                variance += (row[j] - means[j]) ** 2
        scales[j] = math.sqrt(variance / max(n - 1, 1)) or 1.0

    z = [[(row[j] - means[j]) / scales[j] if j < len(row) else 0.0 for j in range(p)] for row in design]
    intercept = sum(targets) / n
    centred = [t - intercept for t in targets]

    xtx = [[sum(z[i][a] * z[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    for j in range(p):
        xtx[j][j] += lam
    xty = [sum(z[i][j] * centred[i] for i in range(n)) for j in range(p)]
    coefficients = _gauss_solve(xtx, xty)

    predictions = [intercept + sum(z[i][j] * coefficients[j] for j in range(p)) for i in range(n)]
    ss_res = sum((targets[i] - predictions[i]) ** 2 for i in range(n))
    mean_target = intercept
    ss_tot = sum((t - mean_target) ** 2 for t in targets)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    # Leave-one-out error: the honest error estimate for a small sample.
    loo_errors: List[float] = []
    if n >= 3:
        for hold_out in range(n):
            z_train = [z[i] for i in range(n) if i != hold_out]
            y_train = [centred[i] for i in range(n) if i != hold_out]
            inner = [[sum(z_train[i][a] * z_train[i][b] for i in range(len(z_train))) for b in range(p)] for a in range(p)]
            for j in range(p):
                inner[j][j] += lam
            rhs = [sum(z_train[i][j] * y_train[i] for i in range(len(z_train))) for j in range(p)]
            beta = _gauss_solve(inner, rhs)
            predicted = intercept + sum(z[hold_out][j] * beta[j] for j in range(p))
            loo_errors.append(targets[hold_out] - predicted)
    loo_rmse = math.sqrt(sum(e * e for e in loo_errors) / len(loo_errors)) if loo_errors else 0.0
    bias = sum(centred) / n

    return RidgeModel(
        kpi=kpi_id,
        intercept=intercept,
        coefficients=coefficients,
        feature_names=list(feature_names),
        means=means,
        scales=scales,
        samples=n,
        r2=r2,
        loo_rmse=loo_rmse,
        residual_bias=bias,
    )


def build_samples(trials: Iterable[Any]) -> List[Tuple[Formulation, Dict[str, float]]]:
    """Turn stored trial records into (formulation, measured) pairs for fitting."""
    samples: List[Tuple[Formulation, Dict[str, float]]] = []
    for trial in trials:
        formulation = getattr(trial, "formulation", None)
        if formulation is None:
            continue
        measurements = dict(getattr(trial, "measurements", {}) or {})
        if measurements:
            samples.append((formulation, measurements))
    return samples
