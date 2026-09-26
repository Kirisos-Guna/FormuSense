"""A simulated pilot plant, so the closed loop can be demonstrated honestly.

The system must be shown to reduce the number of physical trials, and that claim
is only meaningful if the trials it learns from behave like trials. Every
evaluation here goes through :class:`PlantConfig`, which encodes *how the plant
differs from the model*, in the ways a real plant differs:

* the dryer or oven runs at a temperature offset, so moisture lands high;
* an acid added early to an open kettle loses part of its dose, so pH lands high;
* a dosing scale over- or under-delivers a minor component;
* ageing fat oxidises faster than the model assumes.

Those deviations are **structured and repeatable** - the same plant makes the same
mistake every time. That is the whole reason a development team can converge: the
error is learnable, which is exactly what the surrogate in
:mod:`app.core.surrogate` exploits. Random measurement noise is layered on top,
using the per-KPI relative standard deviations declared in
:mod:`app.core.uncertainty`, so a KPI can be fractionally wrong and still
"within measurement error".

Nothing here pretends to be a real plant. It is a documented simulation used to
test the agent, and every seeded case states the deviation it embodies.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core import engine, kb, uncertainty
from .core.nutrition import NUTRIENTS
from .core.types import Brief, Formulation, Item, TrialResult


@dataclass
class PlantConfig:
    """How this plant deviates from the models, plus its measurement noise."""

    name: str = "Pilot plant A"
    # Moisture: fraction of the *drying* action actually delivered (1.0 = as modelled).
    drying_efficiency: float = 1.0
    # Oven/dryer temperature offset actually delivered, in degC.
    temp_offset_c: float = 0.0
    # Fraction of the acidulant dose that survives to the finished product.
    acid_retention: float = 1.0
    # Extra reducing sugars formed by inversion/holding (as a fraction of sugars).
    sugar_inversion: float = 1.0
    # Dosing accuracy on sodium-bearing minor ingredients.
    sodium_carry: float = 1.0
    # Fat quality: >1 means oxidation proceeds faster than modelled.
    oxidation_factor: float = 1.0
    # How sloppy this laboratory is relative to the reference method: 1.0 is a
    # competent contract lab, 1.2 is a pilot plant run by the shift chemist.
    noise_scale: float = 1.15
    # Relative floor on noise so trace components still vary between aliquots.
    noise_floor: float = 0.004
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "drying_efficiency": self.drying_efficiency,
            "temp_offset_c": self.temp_offset_c,
            "acid_retention": self.acid_retention,
            "sugar_inversion": self.sugar_inversion,
            "sodium_carry": self.sodium_carry,
            "oxidation_factor": self.oxidation_factor,
            "noise_scale": self.noise_scale,
            "notes": list(self.notes),
        }


def _perturbed(formulation: Formulation, config: PlantConfig) -> Formulation:
    """The formulation as the plant actually made it."""
    items: List[Item] = []
    table = kb.ingredients()
    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        pct = item.pct
        if ing is not None:
            if ing.group == "acidulant":
                pct *= config.acid_retention
            if ing.group == "salt_mineral" or ing.group == "fortificant":
                carried = 1.0 + (config.sodium_carry - 1.0)
                pct *= carried
        items.append(Item(item.ingredient_id, pct, item.slot))
    params = dict(formulation.params)
    for key in ("bake_temp_c", "cook_temp_c", "dryer_temp_c", "roast_temp_c", "barrel_temp_c"):
        if key in params:
            params[key] = params[key] + config.temp_offset_c
    return Formulation(
        category=formulation.category,
        version=formulation.version,
        items=items,
        params=params,
        label=formulation.label,
        notes=list(formulation.notes),
    )


def _apply_plant_effects(
    values: Dict[str, float], config: PlantConfig, mix_moisture: float = 0.0
) -> Dict[str, float]:
    """Effects the composition/process vector cannot express."""
    out = dict(values)
    if config.drying_efficiency != 1.0:
        # Incomplete drying is expressed as a fraction of the water the process
        # was *supposed* to remove, not as a fraction of the process range: a
        # dryer delivering 86% of its duty leaves behind 14% of the water that
        # should have gone, which is a couple of moisture points on a snack and
        # a fraction of a point on a biscuit.
        removed = max(mix_moisture - out.get("moisture_pct", 10.0), 0.0)
        if removed <= 0.0:
            removed = max(out.get("moisture_pct", 10.0) * 0.5, 1.0)
        delta = (1.0 - config.drying_efficiency) * removed
        out["moisture_pct"] = out.get("moisture_pct", 10.0) + delta
        if "water_activity" in out:
            # aw tracks moisture, and the sensitivity differs sharply between the
            # glassy and the moist regime, so it is taken from the sorption slope.
            out["water_activity"] = min(0.98, out["water_activity"] + 0.16 * math.log1p(delta / max(out["moisture_pct"], 0.5)))
        if "texture_index" in out:
            out["texture_index"] = max(1.0, out["texture_index"] - 0.55 * delta)
            if "hardness_n" in out:
                out["hardness_n"] = max(0.5, out["hardness_n"] - 0.9 * delta)
        if "mould_risk" in out:
            out["mould_risk"] = min(100.0, out["mould_risk"] + 1.2 * delta)
    if config.sugar_inversion != 1.0 and "sugar_g" in out:
        out["sugar_g"] = out["sugar_g"] * config.sugar_inversion
    if config.oxidation_factor != 1.0 and "oxidation_risk" in out:
        out["oxidation_risk"] = min(100.0, out["oxidation_risk"] * config.oxidation_factor)
    return out


def measure(
    values: Dict[str, float],
    config: PlantConfig,
    rng: random.Random,
    kpis: Optional[List[str]] = None,
) -> Dict[str, float]:
    """Add measurement noise to the plant's true values."""
    measured: Dict[str, float] = {}
    for kpi_id, value in values.items():
        if kpis is not None and kpi_id not in kpis:
            continue
        # Trial scatter is the repeatability of the analytical method, not the
        # error of the model. The model's own error stays where it belongs: in the
        # prediction interval the agent publishes before the trial, which is what
        # makes a residual meaningful instead of drowned in fake noise.
        sigma = (
            max(uncertainty.measurement_sigma(kpi_id, value), abs(value) * config.noise_floor)
            * config.noise_scale
        )
        measured[kpi_id] = max(0.0, value + rng.gauss(0.0, sigma))
    return measured


def run_trial(
    formulation: Formulation,
    brief: Optional[Brief],
    config: PlantConfig,
    seed: int,
    label: str,
    version: Optional[int] = None,
    operator: str = "pilot plant",
    trial_date: str = "",
    include: Optional[List[str]] = None,
    sensory: Optional[Dict[str, float]] = None,
) -> TrialResult:
    """Run one physical trial and return what the laboratory would report."""
    rng = random.Random(seed)
    actual = _perturbed(formulation, config)
    truth = engine.predict(actual, brief)
    true_values = _apply_plant_effects(
        dict(truth.values), config, mix_moisture=truth.composition.moisture_mix
    )
    measured = measure(true_values, config, rng, kpis=include)

    actuals: Dict[str, float] = {}
    for key in (
        "bake_temp_c",
        "bake_time_min",
        "cook_temp_c",
        "cook_time_min",
        "dryer_temp_c",
        "dryer_time_min",
        "roast_temp_c",
        "barrel_temp_c",
        "screw_speed_rpm",
        "feed_moisture_pct",
        "syrup_temp_c",
    ):
        if key in actual.params:
            actuals[key] = round(actual.params[key], 3)

    notes = list(config.notes)
    if config.drying_efficiency < 1.0:
        notes.append(
            f"{config.name}: drying delivered {config.drying_efficiency*100:.0f}% of the modelled moisture removal."
        )
    if config.temp_offset_c:
        notes.append(
            f"{config.name}: process temperature read {config.temp_offset_c:+.1f} degC against the setpoint."
        )
    if config.acid_retention < 1.0:
        notes.append(
            f"{config.name}: only {config.acid_retention*100:.0f}% of the added acid survived to the finished product."
        )

    return TrialResult(
        label=label,
        formulation_version=int(version if version is not None else formulation.version),
        measurements={k: round(v, 4) for k, v in measured.items()},
        sensory=dict(sensory or {}),
        process_actuals=actuals,
        batch_size_kg=1.0,
        operator=operator,
        trial_date=trial_date,
        notes=" ".join(notes),
    )


def truth_values(formulation: Formulation, brief: Optional[Brief], config: PlantConfig) -> Dict[str, float]:
    """The plant's true (un-noised) values, used by the benchmark to score true quality."""
    actual = _perturbed(formulation, config)
    prediction = engine.predict(actual, brief)
    return _apply_plant_effects(
        dict(prediction.values), config, mix_moisture=prediction.composition.moisture_mix
    )
