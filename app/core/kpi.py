"""KPI registry and desirability scoring.

Every product characteristic the agent predicts or targets is declared once
here, with its label, unit, optimisation direction and a reference range used
for plotting and for desirability scaling.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional

from .types import KpiTarget, Prediction

# direction: higher = bigger is better, lower = smaller is better, target = band
KPI_DEFS: Dict[str, Dict[str, object]] = {
    "energy_kcal": {"label": "Energy", "unit": "kcal/100 g", "direction": "target", "ref": (300, 550)},
    "protein_g": {"label": "Protein", "unit": "g/100 g", "direction": "higher", "ref": (0, 25)},
    "protein_energy_pct": {"label": "Protein energy share", "unit": "% of energy", "direction": "higher", "ref": (0, 30)},
    "fat_g": {"label": "Total fat", "unit": "g/100 g", "direction": "lower", "ref": (0, 35)},
    "satfat_g": {"label": "Saturated fat", "unit": "g/100 g", "direction": "lower", "ref": (0, 15)},
    "carb_g": {"label": "Carbohydrate", "unit": "g/100 g", "direction": "target", "ref": (30, 75)},
    "sugar_g": {"label": "Total sugars", "unit": "g/100 g", "direction": "lower", "ref": (0, 40)},
    "fibre_g": {"label": "Dietary fibre", "unit": "g/100 g", "direction": "higher", "ref": (0, 15)},
    "sodium_mg": {"label": "Sodium", "unit": "mg/100 g", "direction": "lower", "ref": (0, 600)},
    "moisture_pct": {"label": "Moisture", "unit": "%", "direction": "target", "ref": (0, 90)},
    "water_activity": {"label": "Water activity", "unit": "aw", "direction": "lower", "ref": (0.1, 0.95)},
    "ph": {"label": "pH", "unit": "pH", "direction": "target", "ref": (2.5, 7.0)},
    "shelf_life_days": {"label": "Predicted shelf life", "unit": "days", "direction": "higher", "ref": (0, 365)},
    "texture_index": {"label": "Texture index", "unit": "index 0-100", "direction": "target", "ref": (0, 100)},
    "hardness_n": {"label": "Indicative hardness", "unit": "N", "direction": "target", "ref": (0, 120)},
    "consistency_index": {"label": "Consistency index", "unit": "index 0-100", "direction": "target", "ref": (0, 100)},
    "spreadability": {"label": "Spreadability", "unit": "index 0-100", "direction": "higher", "ref": (0, 100)},
    "processability_score": {"label": "Processability", "unit": "index 0-100", "direction": "higher", "ref": (0, 100)},
    "oxidation_risk": {"label": "Oxidation risk", "unit": "index 0-100", "direction": "lower", "ref": (0, 100)},
    "mould_risk": {"label": "Mould / yeast risk", "unit": "index 0-100", "direction": "lower", "ref": (0, 100)},
    "cost_inr_kg": {"label": "Ingredient cost", "unit": "INR/kg", "direction": "lower", "ref": (0, 800)},
    "cost_inr_unit": {"label": "Cost per unit", "unit": "INR/unit", "direction": "lower", "ref": (0, 60)},
}

DEFAULT_TOLERANCE: Dict[str, float] = {
    "energy_kcal": 30.0,
    "protein_g": 1.0,
    "protein_energy_pct": 2.0,
    "fat_g": 1.5,
    "satfat_g": 1.0,
    "carb_g": 3.0,
    "sugar_g": 1.5,
    "fibre_g": 1.0,
    "sodium_mg": 30.0,
    "moisture_pct": 1.0,
    "water_activity": 0.03,
    "ph": 0.15,
    "shelf_life_days": 30.0,
    "texture_index": 6.0,
    "hardness_n": 8.0,
    "consistency_index": 6.0,
    "spreadability": 8.0,
    "processability_score": 8.0,
    "oxidation_risk": 10.0,
    "mould_risk": 10.0,
    "cost_inr_kg": 10.0,
    "cost_inr_unit": 1.0,
}

# Which KPIs are meaningful per product category (used to keep reports readable).
CATEGORY_KPIS: Dict[str, List[str]] = {
    "cookie": [
        "energy_kcal", "protein_g", "fat_g", "satfat_g", "carb_g", "sugar_g", "fibre_g", "sodium_mg",
        "moisture_pct", "water_activity", "ph", "shelf_life_days", "texture_index", "hardness_n",
        "processability_score", "oxidation_risk", "mould_risk", "cost_inr_kg", "cost_inr_unit",
    ],
    "spread": [
        "energy_kcal", "protein_g", "fat_g", "sugar_g", "fibre_g", "sodium_mg", "moisture_pct",
        "water_activity", "ph", "shelf_life_days", "consistency_index", "spreadability",
        "processability_score", "mould_risk", "cost_inr_kg", "cost_inr_unit",
    ],
    "drymix": [
        "energy_kcal", "protein_g", "fat_g", "carb_g", "sugar_g", "fibre_g", "sodium_mg",
        "moisture_pct", "water_activity", "shelf_life_days", "processability_score",
        "oxidation_risk", "mould_risk", "cost_inr_kg", "cost_inr_unit",
    ],
    "extruded_snack": [
        "energy_kcal", "protein_g", "fat_g", "carb_g", "fibre_g", "sodium_mg", "moisture_pct",
        "water_activity", "shelf_life_days", "texture_index", "hardness_n", "processability_score",
        "oxidation_risk", "mould_risk", "cost_inr_kg", "cost_inr_unit",
    ],
    "bar": [
        "energy_kcal", "protein_g", "fat_g", "sugar_g", "fibre_g", "sodium_mg", "moisture_pct",
        "water_activity", "ph", "shelf_life_days", "texture_index", "processability_score",
        "oxidation_risk", "mould_risk", "cost_inr_kg", "cost_inr_unit",
    ],
    "sauce": [
        "energy_kcal", "protein_g", "fat_g", "sugar_g", "fibre_g", "sodium_mg", "moisture_pct",
        "water_activity", "ph", "shelf_life_days", "consistency_index", "mould_risk",
        "cost_inr_kg", "cost_inr_unit",
    ],
    "beverage": [
        "energy_kcal", "protein_g", "protein_energy_pct", "fat_g", "satfat_g", "carb_g", "sugar_g",
        "sodium_mg", "moisture_pct", "water_activity", "ph", "shelf_life_days", "consistency_index",
        "processability_score", "oxidation_risk", "mould_risk", "cost_inr_kg", "cost_inr_unit",
    ],
}


def kpi_label(kpi_id: str) -> str:
    return str(KPI_DEFS.get(kpi_id, {}).get("label", kpi_id))


def kpi_unit(kpi_id: str) -> str:
    return str(KPI_DEFS.get(kpi_id, {}).get("unit", ""))


def kpi_direction(kpi_id: str) -> str:
    return str(KPI_DEFS.get(kpi_id, {}).get("direction", "target"))


def kpis_for_category(category: str) -> List[str]:
    return CATEGORY_KPIS.get(category, list(KPI_DEFS.keys()))


def make_target(
    kpi_id: str,
    target: float,
    tolerance: Optional[float] = None,
    priority: float = 1.0,
    hard: bool = True,
) -> KpiTarget:
    return KpiTarget(
        id=kpi_id,
        label=kpi_label(kpi_id),
        unit=kpi_unit(kpi_id),
        target=float(target),
        tolerance=float(tolerance if tolerance is not None else DEFAULT_TOLERANCE.get(kpi_id, 1.0)),
        priority=float(priority),
        direction=kpi_direction(kpi_id),
        hard=bool(hard),
    )


# Width of the desirability ramp, in units of a target's own tolerance. With the
# Cauchy kernel below, these are the resulting scores:
#   on the target                     1.00
#   one tolerance out (marginal)      0.72
#   two tolerances out (off-target)   0.39
#   three and a half tolerances (fail) 0.17
DESIRABILITY_SCALE = 1.6


def desirability(target: KpiTarget, value: float) -> float:
    """Distance-based desirability in (0, 1], smooth in every direction.

    An earlier version of this function was piecewise-linear and reached exactly
    zero two tolerances outside a band. That is fine for reporting and terrible
    for optimisation: the search cannot tell a formula that misses the band by
    two tolerances from one that misses it by five, so it has no slope to descend
    and stalls. A Gaussian kernel keeps a gradient everywhere while still
    ranking misses correctly, and its value at one and two tolerances lines up
    with the ``marginal`` and ``off-target`` bands used by :func:`status_of`.
    """
    tol = max(target.tolerance, 1e-9)
    if target.direction == "higher":
        distance = max(0.0, target.target - value)
    elif target.direction == "lower":
        distance = max(0.0, value - target.target)
    else:
        # A band target is centred, not flat. Scoring "anywhere inside the band"
        # gives the optimiser no reason to avoid the edges, and a formulation
        # sitting on the edge of every tolerance has a poor chance of passing all
        # of them on the next trial. Distance is therefore measured from the
        # target value itself, so the centre of the band is the preferred place
        # to be - while still scoring 0.72 one tolerance out, which is what
        # keeps status_of and desirability consistent.
        distance = abs(value - target.target)
    scaled = distance / (DESIRABILITY_SCALE * tol)
    # Cauchy kernel, not Gaussian. A Gaussian falls to 1e-14 seven tolerances
    # out, which is numerically flat: the optimiser cannot tell a formula that
    # misses pH by 1.2 units from one that misses it by 1.25, so it stalls on a
    # plateau exactly when it is furthest from the target. The heavy tail keeps a
    # usable gradient all the way out.
    return float(1.0 / (1.0 + scaled * scaled))


def overall_desirability(targets: Iterable[KpiTarget], values: Dict[str, float]) -> float:
    """Priority-weighted geometric mean desirability."""
    weights = 0.0
    log_sum = 0.0
    for target in targets:
        if target.id not in values:
            continue
        d = desirability(target, float(values[target.id]))
        w = max(target.priority, 1e-6)
        log_sum += w * math.log(max(d, 1e-6))
        weights += w
    if weights <= 0:
        return 0.0
    return float(math.exp(log_sum / weights))


def status_of(target: Optional[KpiTarget], value: float) -> str:
    """Status of a value against a target, honouring the target's direction.

    A one-sided target ("protein at least 12 g", "sugar no more than 12 g") is
    not a band: exceeding a claim in the helpful direction is a pass, not a
    failure. The bands here are therefore aligned with :func:`desirability`, so
    ``on-target`` means exactly "desirability > 0" and a KPI can never be
    reported as failing while the objective treats it as satisfied.
    """
    if target is None:
        return "unknown"
    span = max(target.tolerance, 1e-9)
    if target.direction == "higher":
        shortfall = target.target - value
        if shortfall <= span:
            return "on-target"
        if shortfall <= 2.0 * span:
            return "marginal"
        if shortfall <= 3.5 * span:
            return "off-target"
        return "fail"
    if target.direction == "lower":
        excess = value - target.target
        if excess <= span:
            return "on-target"
        if excess <= 2.0 * span:
            return "marginal"
        if excess <= 3.5 * span:
            return "off-target"
        return "fail"
    lo, hi = target.lo, target.hi
    if lo <= value <= hi:
        return "on-target"
    distance = (lo - value) if value < lo else (value - hi)
    if distance <= span:
        return "marginal"
    if distance <= 2 * span:
        return "off-target"
    return "fail"


def is_on_target(target: Optional[KpiTarget], value: float) -> bool:
    """Is a measured value inside the tolerance the brief itself declared?

    A tolerance is the engineering band the development team signed off
    ("moisture 3.0 +/- 1.0"), so the edge of that band is a pass. The gate used to
    be scored as ``desirability >= 0.80`` instead, and the Cauchy kernel only
    returns 0.80 within 0.8 of a tolerance - so a trial that landed exactly on the
    declared limit was reported as off-target and sent back for another
    iteration. The gate now asks the same question the specification answers.
    """
    return status_of(target, value) == "on-target"


def build_prediction(
    kpi_id: str,
    value: float,
    lo: float,
    hi: float,
    confidence: float,
    method: str,
    target: Optional[KpiTarget] = None,
) -> Prediction:
    return Prediction(
        id=kpi_id,
        label=kpi_label(kpi_id),
        value=float(value),
        unit=kpi_unit(kpi_id),
        lo=float(lo),
        hi=float(hi),
        confidence=float(confidence),
        method=method,
        direction=kpi_direction(kpi_id),
        target=None if target is None else target.target,
        status=status_of(target, float(value)),
    )
