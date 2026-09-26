"""Uncertainty and confidence for every prediction.

A number without an interval is not actionable in product development, so every
KPI carries:

* a base model error, declared per KPI (relative standard deviation)
* an extrapolation penalty that grows as the formulation or process moves away
  from the region the model was calibrated in
* a calibration term that shrinks the interval when a surrogate has been fitted
  from real trial data for that specific product

The interval is reported as value +/- 1.96 sigma (95%) and is displayed in the
UI and in the report so that low-confidence predictions are obvious.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import kb
from .nutrition import Composition

# Relative standard deviation of the model itself, per KPI.
BASE_SIGMA: Dict[str, float] = {
    "energy_kcal": 0.05,
    "protein_g": 0.04,
    "protein_energy_pct": 0.05,
    "fat_g": 0.05,
    "satfat_g": 0.06,
    "carb_g": 0.05,
    "sugar_g": 0.05,
    "fibre_g": 0.05,
    "sodium_mg": 0.05,
    "moisture_pct": 0.08,
    "water_activity": 0.075,
    "ph": 0.060,
    "shelf_life_days": 0.30,
    "texture_index": 0.12,
    "hardness_n": 0.18,
    "consistency_index": 0.12,
    "spreadability": 0.12,
    "processability_score": 0.07,
    "oxidation_risk": 0.25,
    "mould_risk": 0.25,
    "cost_inr_kg": 0.05,
    "cost_inr_unit": 0.05,
}

# Analytical repeatability of the laboratory method, in absolute units of the KPI,
# for a competent contract lab on the second aliquot of the same batch.
#
# This is deliberately *not* BASE_SIGMA. BASE_SIGMA says how wrong the model may
# be - a property of the mathematics. This says how far two aliquots of the same
# batch can differ - a property of the bench. Using the model error as the
# measurement error made every trial look as if it had been analysed with a
# rubber ruler: +/- 1.6 moisture points against a 3.0 +/- 1.0 target, which no
# development kitchen would recognise, and which buried the systematic process
# offset that the diagnosis exists to find.
MEASUREMENT_SIGMA: Dict[str, float] = {
    "energy_kcal": 2.5,  # calculated from the measured composition
    "protein_g": 0.12,  # Kjeldahl, N x 6.25
    "protein_energy_pct": 0.4,
    "fat_g": 0.15,  # Soxhlet
    "satfat_g": 0.08,  # GC fatty-acid profile
    "carb_g": 0.4,  # by difference: accumulates the others
    "sugar_g": 0.35,  # Lane-Eynon / HPLC
    "fibre_g": 0.20,  # enzymatic-gravimetric
    "sodium_mg": 4.0,  # flame photometry
    "moisture_pct": 0.12,  # hot-air oven to constant weight
    "water_activity": 0.005,  # chilled-mirror dew point
    "ph": 0.02,  # glass electrode, calibrated
    "shelf_life_days": 5.0,  # the accelerated study itself is the uncertainty
    "texture_index": 1.5,  # texture analyser, 3-point bend
    "hardness_n": 1.5,
    "consistency_index": 1.5,
    "spreadability": 1.5,  # spreadability rig / Bostwick
    "processability_score": 1.0,
    "oxidation_risk": 2.0,  # peroxide value on a stored sample
    "mould_risk": 2.0,  # plate count, log-scaled to the index
    "cost_inr_kg": 0.5,  # invoice cost: essentially exact
    "cost_inr_unit": 0.05,
}

# Absolute floors so tiny values do not produce absurdly narrow intervals.
ABSOLUTE_FLOOR: Dict[str, float] = {
    "water_activity": 0.02,
    "ph": 0.08,
    "shelf_life_days": 7.0,
    "texture_index": 3.0,
    "hardness_n": 3.0,
    "consistency_index": 3.0,
    "spreadability": 3.0,
    "processability_score": 3.0,
    "oxidation_risk": 4.0,
    "mould_risk": 4.0,
    "cost_inr_kg": 2.0,
    "cost_inr_unit": 0.2,
    "moisture_pct": 0.25,
}


def measurement_sigma(kpi_id: str, value: float = 0.0) -> float:
    """Repeatability of the laboratory method for one KPI, in absolute units."""
    base = MEASUREMENT_SIGMA.get(kpi_id)
    if base is None:
        # Unknown KPI: fall back to a generic 1% of reading, never below 0.05.
        return max(abs(value) * 0.01, 0.05)
    return float(base)


def extrapolation_factor(comp: Composition, params: Dict[str, float]) -> Tuple[float, List[str]]:
    """How far outside the calibrated operating region this point sits."""
    flags: List[str] = []
    factor = 1.0
    cat = kb.category(comp.category)

    low, high = cat.typical_moisture_pct
    if comp.final_moisture < low - 2.0 or comp.final_moisture > high + 4.0:
        factor *= 1.25
        flags.append(
            f"moisture {comp.final_moisture:.1f}% outside typical {low:.0f}-{high:.0f}% for {cat.label}"
        )

    ingredient_count = int(comp.aggregates["ingredient_count"])
    if ingredient_count > 14:
        factor *= 1.10
        flags.append(f"{ingredient_count} ingredient lines (interaction risk)")

    for item in comp.item_detail:
        ing = kb.ingredient(str(item["ingredient_id"]))
        if ing.hard_max_pct > 0 and float(item["pct"]) > ing.hard_max_pct:
            factor *= 1.15
            flags.append(f"{ing.name} above its declared maximum inclusion")

    for pid, value in params.items():
        param = cat.param(pid)
        if param is None:
            continue
        span = max(param.max - param.min, 1e-9)
        if value < param.min or value > param.max:
            factor *= 1.10
            flags.append(f"{param.label} {value:.1f}{param.unit} outside validated range")
        elif (value - param.min) / span < 0.05 or (param.max - value) / span < 0.05:
            factor *= 1.05
            flags.append(f"{param.label} at the edge of its validated range")

    return factor, flags


def interval(
    kpi_id: str,
    value: float,
    extrapolation: float = 1.0,
    calibration_samples: int = 0,
) -> Tuple[float, float, float]:
    """Return (lo, hi, confidence) for one KPI."""
    rel = BASE_SIGMA.get(kpi_id, 0.10) * extrapolation
    if calibration_samples > 0:
        # Each real trial shrinks the residual uncertainty, asymptotically to 45%.
        shrink = 1.0 / (1.0 + 0.22 * calibration_samples)
        rel *= max(shrink, 0.45)
    sigma = max(abs(value) * rel, ABSOLUTE_FLOOR.get(kpi_id, 0.0) * extrapolation)
    lo = value - 1.96 * sigma
    hi = value + 1.96 * sigma
    confidence = max(0.30, min(0.97, 1.0 - 2.2 * rel))
    return lo, hi, confidence


def confidence_label(confidence: float) -> str:
    if confidence >= 0.80:
        return "high"
    if confidence >= 0.62:
        return "moderate"
    return "low"


def summarise(values: Dict[str, float], extrapolation: float = 1.0) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for kpi_id, value in values.items():
        lo, hi, conf = interval(kpi_id, value, extrapolation)
        rows.append({"kpi": kpi_id, "value": value, "lo": lo, "hi": hi, "confidence": conf})
    return rows
