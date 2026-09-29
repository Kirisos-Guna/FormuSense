"""The learning algorithms: ridge regression, logistic regression, baselines.

Plain Python, standard library only, so the model trains on a laptop with no
wheels to install and no key to configure. Both models standardise their inputs
and are persisted as JSON.

Two design choices worth stating:

* **Ridge, not ordinary least squares.** The dataset is small and the features
  are correlated (group shares must sum to one), so OLS would fit noise. A ridge
  penalty with a standardised design keeps the coefficients interpretable.
* **Baselines are trained too.** A model that predicts the mean, and one that
  predicts the base rate, are fitted and scored alongside the real models. If the
  learned model cannot beat them out of sample, the report says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..core.surrogate import _gauss_solve

RIDGE_LAMBDA = 2.0
LOGISTIC_LR = 0.35
LOGISTIC_EPOCHS = 900
LOGISTIC_L2 = 0.02
EPS = 1e-9


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 1.0
    mean = _mean(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance) or 1.0


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def standardise(train: Sequence[Sequence[float]]):
    """Column means and scales from the training matrix only (never the test set)."""
    if not train:
        return [], []
    width = len(train[0])
    means = [0.0] * width
    for row in train:
        for j in range(width):
            means[j] += row[j]
    means = [m / len(train) for m in means]
    scales = [1.0] * width
    for j in range(width):
        column = [row[j] for row in train]
        scales[j] = _std(column)
    return means, scales


def apply_standardisation(
    matrix: Sequence[Sequence[float]], means: Sequence[float], scales: Sequence[float]
) -> List[List[float]]:
    out: List[List[float]] = []
    for row in matrix:
        out.append(
            [
                (row[j] - means[j]) / (scales[j] if abs(scales[j]) > EPS else 1.0)
                for j in range(len(means))
            ]
        )
    return out


@dataclass
class RidgeRegressor:
    """Ridge regression on the objective, solved in closed form."""

    target: str = "objective"
    lam: float = RIDGE_LAMBDA
    intercept: float = 0.0
    coefficients: List[float] = field(default_factory=list)
    means: List[float] = field(default_factory=list)
    scales: List[float] = field(default_factory=list)
    samples: int = 0

    def fit(self, matrix: Sequence[Sequence[float]], targets: Sequence[float]) -> "RidgeRegressor":
        if not matrix:
            return self
        self.means, self.scales = standardise(matrix)
        z = apply_standardisation(matrix, self.means, self.scales)
        n = len(z)
        p = len(self.means)
        self.intercept = _mean(targets)
        centred = [t - self.intercept for t in targets]
        xtx = [[0.0] * p for _ in range(p)]
        for row in z:
            for a in range(p):
                za = row[a]
                if za == 0.0:
                    continue
                for b in range(p):
                    xtx[a][b] += za * row[b]
        for j in range(p):
            xtx[j][j] += self.lam
        xty = [0.0] * p
        for row, y in zip(z, centred):
            for j in range(p):
                xty[j] += row[j] * y
        self.coefficients = _gauss_solve(xtx, xty)
        self.samples = n
        return self

    def predict_one(self, row: Sequence[float]) -> float:
        total = self.intercept
        for j, value in enumerate(row):
            scale = self.scales[j] if j < len(self.scales) else 1.0
            if abs(scale) <= EPS:
                continue
            total += self.coefficients[j] * ((value - self.means[j]) / scale)
        return float(total)

    def predict(self, matrix: Sequence[Sequence[float]]) -> List[float]:
        return [self.predict_one(row) for row in matrix]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "ridge",
            "target": self.target,
            "lam": self.lam,
            "intercept": self.intercept,
            "coefficients": list(self.coefficients),
            "means": list(self.means),
            "scales": list(self.scales),
            "samples": self.samples,
        }

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "RidgeRegressor":
        return RidgeRegressor(
            target=payload.get("target", "objective"),
            lam=float(payload.get("lam", RIDGE_LAMBDA)),
            intercept=float(payload.get("intercept", 0.0)),
            coefficients=[float(c) for c in payload.get("coefficients", [])],
            means=[float(m) for m in payload.get("means", [])],
            scales=[float(s) for s in payload.get("scales", [])],
            samples=int(payload.get("samples", 0)),
        )


@dataclass
class LogisticRegressor:
    """Logistic regression by gradient descent, with optional Platt calibration."""

    coefficients: List[float] = field(default_factory=list)
    intercept: float = 0.0
    means: List[float] = field(default_factory=list)
    scales: List[float] = field(default_factory=list)
    samples: int = 0
    base_rate: float = 0.0
    lr: float = LOGISTIC_LR
    epochs: int = LOGISTIC_EPOCHS
    l2: float = LOGISTIC_L2
    platt_a: float = 1.0
    platt_b: float = 0.0
    calibrated: bool = False

    def fit(self, matrix: Sequence[Sequence[float]], labels: Sequence[float]) -> "LogisticRegressor":
        if not matrix:
            return self
        self.means, self.scales = standardise(matrix)
        z = apply_standardisation(matrix, self.means, self.scales)
        n = len(z)
        p = len(self.means)
        self.base_rate = _mean(labels)
        self.coefficients = [0.0] * p
        self.intercept = 0.0
        # Balanced class weights: a dataset dominated by passes should not be
        # allowed to make the classifier predict "pass" for everything.
        positives = sum(1 for y in labels if y >= 0.5)
        negatives = n - positives
        weight_pos = (n / (2.0 * positives)) if positives else 1.0
        weight_neg = (n / (2.0 * negatives)) if negatives else 1.0
        for _ in range(self.epochs):
            grad_b = 0.0
            grad_w = [0.0] * p
            for row, y in zip(z, labels):
                prob = _sigmoid(self.intercept + sum(w * x for w, x in zip(self.coefficients, row)))
                weight = weight_pos if y >= 0.5 else weight_neg
                error = (prob - y) * weight
                grad_b += error
                for j in range(p):
                    grad_w[j] += error * row[j]
            self.intercept -= self.lr * grad_b / n
            for j in range(p):
                penalty = self.l2 * self.coefficients[j]
                self.coefficients[j] -= self.lr * (grad_w[j] / n + penalty)
        self.samples = n
        return self

    def calibrate(self, matrix: Sequence[Sequence[float]], labels: Sequence[float]) -> "LogisticRegressor":
        """Fit Platt scaling on a held-out fold so probabilities mean something.

        An uncalibrated logistic output is a ranking score, not a probability. The
        report quotes a pass *probability*, so it has to be calibrated, and it is
        calibrated on data the coefficients did not see.
        """
        if not matrix:
            return self
        z = apply_standardisation(matrix, self.means, self.scales)
        raw = [self.intercept + sum(w * x for w, x in zip(self.coefficients, row)) for row in z]
        a, b = 1.0, 0.0
        lr = 0.08
        for _ in range(600):
            grad_a = 0.0
            grad_b = 0.0
            for score, y in zip(raw, labels):
                p = _sigmoid(a * score + b)
                grad_a += (p - y) * score
                grad_b += (p - y)
            grad_a /= len(raw)
            grad_b /= len(raw)
            a -= lr * grad_a
            b -= lr * grad_b
        self.platt_a = float(a)
        self.platt_b = float(b)
        self.calibrated = True
        return self

    def predict_proba_one(self, row: Sequence[float]) -> float:
        score = self.intercept
        for j, value in enumerate(row):
            scale = self.scales[j] if j < len(self.scales) else 1.0
            if abs(scale) <= EPS:
                continue
            score += self.coefficients[j] * ((value - self.means[j]) / scale)
        if self.calibrated:
            return _sigmoid(self.platt_a * score + self.platt_b)
        return _sigmoid(score)

    def predict_proba(self, matrix: Sequence[Sequence[float]]) -> List[float]:
        return [self.predict_proba_one(row) for row in matrix]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "logistic",
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
            "means": list(self.means),
            "scales": list(self.scales),
            "samples": self.samples,
            "base_rate": self.base_rate,
            "platt_a": self.platt_a,
            "platt_b": self.platt_b,
            "calibrated": self.calibrated,
        }

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "LogisticRegressor":
        return LogisticRegressor(
            coefficients=[float(c) for c in payload.get("coefficients", [])],
            intercept=float(payload.get("intercept", 0.0)),
            means=[float(m) for m in payload.get("means", [])],
            scales=[float(s) for s in payload.get("scales", [])],
            samples=int(payload.get("samples", 0)),
            base_rate=float(payload.get("base_rate", 0.0)),
            platt_a=float(payload.get("platt_a", 1.0)),
            platt_b=float(payload.get("platt_b", 0.0)),
            calibrated=bool(payload.get("calibrated", False)),
        )


@dataclass
class MeanBaseline:
    """Predicts the training mean: the bar any regression must clear."""

    value: float = 0.0

    def fit(self, targets: Sequence[float]) -> "MeanBaseline":
        self.value = _mean(targets)
        return self

    def predict(self, matrix: Sequence[Sequence[float]]) -> List[float]:
        return [self.value for _ in matrix]

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "mean", "value": self.value}


@dataclass
class MajorityBaseline:
    """Predicts the training base rate: the bar any classifier must clear."""

    rate: float = 0.0

    def fit(self, labels: Sequence[float]) -> "MajorityBaseline":
        self.rate = _mean(labels)
        return self

    def predict_proba(self, matrix: Sequence[Sequence[float]]) -> List[float]:
        return [self.rate for _ in matrix]

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "base_rate", "rate": self.rate}
