"""Model artefacts: what a trained model is, and how it is stored and loaded.

A bundle is a self-describing JSON file: the fitted coefficients, the feature
order, the metrics that were measured out of sample when it was trained, the
dataset it was trained on (with that dataset's content hash) and the date. The
runtime never re-fits: it loads the newest bundle, or runs without one and says
so. That keeps a prediction reproducible - the same bundle always gives the same
number - and makes "which model produced this figure" answerable after the fact.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.types import Formulation
from . import features as feature_module
from .models import LogisticRegressor, RidgeRegressor

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MODELS_ROOT = DATA_DIR / "models"
DEFAULT_NAME = "acceptance"


@dataclass
class ModelBundle:
    """A fitted regression + classifier with its provenance and metrics."""

    name: str = DEFAULT_NAME
    version: str = "v1"
    created_at: str = ""
    feature_version: str = feature_module.FEATURE_VERSION
    feature_names: List[str] = field(default_factory=list)
    dataset: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    train_config: Dict[str, Any] = field(default_factory=dict)
    ridge: RidgeRegressor = field(default_factory=RidgeRegressor)
    logistic: LogisticRegressor = field(default_factory=LogisticRegressor)

    def predict(self, formulation: Formulation) -> Dict[str, Any]:
        """Score one formulation: continuous objective and calibrated pass probability."""
        vector = feature_module.extract(formulation)
        objective = self.ridge.predict_one(vector)
        if self.logistic.coefficients:
            probability = self.logistic.predict_proba_one(vector)
        else:
            probability = self.logistic.base_rate
        return {
            "objective": round(float(objective), 4),
            "pass_probability": round(float(probability), 4),
            "model": f"{self.name}/{self.version}",
        }

    def driver_notes(self, top: int = 6) -> List[Dict[str, Any]]:
        """The largest standardised coefficients, for explaining the model."""
        names = self.feature_names or feature_module.feature_names()
        rows: List[Dict[str, Any]] = []
        for index, name in enumerate(names):
            if index >= len(self.ridge.coefficients):
                continue
            rows.append(
                {
                    "feature": name,
                    "objective_coefficient": round(self.ridge.coefficients[index], 4),
                }
            )
        rows.sort(key=lambda r: -abs(r["objective_coefficient"]))
        return rows[:top]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "feature_version": self.feature_version,
            "feature_names": list(self.feature_names),
            "dataset": dict(self.dataset),
            "metrics": dict(self.metrics),
            "train_config": dict(self.train_config),
            "ridge": self.ridge.to_dict(),
            "logistic": self.logistic.to_dict(),
        }

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "ModelBundle":
        return ModelBundle(
            name=payload.get("name", DEFAULT_NAME),
            version=payload.get("version", "v1"),
            created_at=payload.get("created_at", ""),
            feature_version=payload.get("feature_version", feature_module.FEATURE_VERSION),
            feature_names=list(payload.get("feature_names") or []),
            dataset=dict(payload.get("dataset") or {}),
            metrics=dict(payload.get("metrics") or {}),
            train_config=dict(payload.get("train_config") or {}),
            ridge=RidgeRegressor.from_dict(payload.get("ridge") or {}),
            logistic=LogisticRegressor.from_dict(payload.get("logistic") or {}),
        )


def save_bundle(bundle: ModelBundle, root: Optional[Path] = None) -> Path:
    directory = Path(root or MODELS_ROOT) / bundle.name / bundle.version
    directory.mkdir(parents=True, exist_ok=True)
    if not bundle.created_at:
        bundle.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = directory / "model.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(bundle.as_dict(), handle, indent=2, sort_keys=True)
    return path


def load_bundle(path: Path) -> ModelBundle:
    target = Path(path)
    if target.is_dir():
        target = target / "model.json"
    with open(target, "r", encoding="utf-8") as handle:
        return ModelBundle.from_dict(json.load(handle))


def latest_bundle(name: str = DEFAULT_NAME, root: Optional[Path] = None) -> Optional[ModelBundle]:
    base = Path(root or MODELS_ROOT) / name
    if not base.is_dir():
        return None
    candidates = [p for p in base.iterdir() if (p / "model.json").is_file()]
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: (p / "model.json").stat().st_mtime)
    try:
        return load_bundle(newest)
    except (OSError, ValueError, KeyError):
        return None
