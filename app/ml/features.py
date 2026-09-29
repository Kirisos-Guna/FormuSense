"""Feature extraction for the learned acceptance model.

The features are deliberately **category-agnostic**: the same vector shape for a
cookie, a spread and a drink, so one model covers the whole knowledge base and a
new category does not require retraining code.

What goes in:

* the fraction of the formulation contributed by each ingredient *group*
  (grain, protein, fat, sweetener, water, hydrocolloid, ...). Group shares are
  what actually drive sensory outcome, and they are defined for every category.
* a small set of process descriptors, derived from the category's parameters by
  their unit: temperature, time, water level, and how many knobs the process has.

What deliberately does **not** go in: any output of the physics engine. If the
model were fed ``engine.predict`` values it would be a surrogate for the engine
rather than an independent learner, and its accuracy would say nothing about
whether formulation and process predict what the plant measures.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ..core import kb
from ..core.types import Formulation

FEATURE_VERSION = "1"

# Stable order, taken from the knowledge base so a new ingredient group is
# picked up by adding it there. Sorted for reproducibility across runs.
GROUP_FEATURES = tuple(sorted(kb.GROUP_FX_DEFAULTS.keys()))

PROCESS_FEATURES = (
    "param:max_temp_norm",
    "param:mean_time_norm",
    "param:water_norm",
    "param:count_norm",
)

TEMP_UNITS = ("degc", "°c", "c")
TIME_UNITS = ("min", "mins", "minute", "s", "sec", "hour", "h")


def feature_names() -> List[str]:
    """The ordered feature names. The vector order must never change silently."""
    return [f"group:{group}" for group in GROUP_FEATURES] + list(PROCESS_FEATURES)


def _process_descriptors(formulation: Formulation) -> Dict[str, float]:
    """Temperature, time and water descriptors for a category's parameters."""
    try:
        category = kb.category(formulation.category)
    except KeyError:
        return {name: 0.0 for name in PROCESS_FEATURES}
    temps: List[float] = []
    times: List[float] = []
    water = 0.0
    for parameter in category.parameters:
        value = float(formulation.params.get(parameter.id, parameter.default))
        unit = (parameter.unit or "").strip().lower()
        if unit in TEMP_UNITS:
            temps.append(value)
        elif unit in TIME_UNITS:
            times.append(value)
        if parameter.id == "water_added_pct":
            water = value
    span = max(1, len(category.parameters))
    return {
        # Normalised so the scales are comparable across categories; the model is
        # standardised anyway, but this keeps the numbers interpretable.
        "param:max_temp_norm": (max(temps) / 150.0) if temps else 0.0,
        "param:mean_time_norm": (sum(times) / len(times) / 20.0) if times else 0.0,
        "param:water_norm": water / 20.0,
        "param:count_norm": len(category.parameters) / span,
    }


def extract(formulation: Formulation) -> List[float]:
    """The feature vector for one formulation, in :func:`feature_names` order."""
    table = kb.ingredients()
    shares: Dict[str, float] = {group: 0.0 for group in GROUP_FEATURES}
    total = max(formulation.total_pct, 1e-6)
    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        if ing is None:
            continue
        if ing.group in shares:
            shares[ing.group] += item.pct / total
    descriptors = _process_descriptors(formulation)
    vector = [shares[group] for group in GROUP_FEATURES]
    vector += [descriptors[name] for name in PROCESS_FEATURES]
    return [float(v) for v in vector]


def row_features(formulation: Formulation) -> Dict[str, float]:
    """Named features, for a dataset row that can be read by a human."""
    names = feature_names()
    values = extract(formulation)
    return {name: round(value, 6) for name, value in zip(names, values)}


def vector_from_row(row: Dict[str, Any]) -> List[float]:
    """Rebuild a feature vector from a stored dataset row."""
    features = row.get("features") or {}
    return [float(features.get(name, 0.0)) for name in feature_names()]


def matrix_from_rows(rows: Sequence[Dict[str, Any]]) -> List[List[float]]:
    return [vector_from_row(row) for row in rows]
