"""The manufacturing process plan that goes with a formulation.

A formulation without a process is not a product. This module turns a
formulation into the document a pilot plant actually works from:

* the **unit operations** for the category, in order, with equipment, duration
  and the control point that matters at each step;
* the **process parameters** at their designed values, with the validated range
  each one must stay inside, so an operator can see when they are off the map;
* the **in-process targets** - the moisture, water activity, pH and texture the
  line must achieve, taken from the prediction for this formulation;
* the **critical control points**, derived from *this* recipe rather than copied
  from a template: an allergen changeover only appears if the formulation
  contains an allergen, a water-activity verification only if the product is
  preserved by dryness, and a metal detector always;
* the **batch sheet**: ingredient weights for the batch size, theoretical yield
  from the mass balance (which knows how much water the process removes), and the
  packing count.

All of it is derived from the formulation and the category knowledge base, so it
changes when the recipe changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import kb, nutrition, physical
from .types import Brief, Formulation

# Pack format typical of each category, used for the packing section.
PACK_FORMAT = {
    "cookie": "Flow-wrap, metallised BOPP/PE laminate, nitrogen flush optional",
    "spread": "Hot-filled glass jar or PET jar with lug cap, pre-sterilised",
    "drymix": "Four-side-seal laminate pouch with nitrogen flush",
    "extruded_snack": "VFFS pillow pack, metallised laminate, nitrogen flush",
    "bar": "Flow-wrap with cold-seal or heat-seal film, foil laminate",
    "sauce": "Hot-filled glass bottle with induction seal, then shrink sleeve",
    "beverage": "Aseptic PET bottle with HDPE screw cap and induction seal; shrink-sleeve label",
}


def plan(formulation: Formulation, brief: Optional[Brief] = None, batch_size_kg: float = 50.0) -> Dict[str, Any]:
    """Build the process plan for a formulation."""
    category = kb.category(formulation.category)
    comp = nutrition.analyse(formulation, final_moisture=_predicted_moisture(formulation))
    yield_pct = comp.aggregates.get("final_total_g", 100.0)
    unit_weight = brief.unit_weight_g if brief else category.typical_unit_weight_g
    # A drink is filled by volume, so its weights-and-measures check has to say so.
    declared_unit = (brief.declared_unit if brief else "") or category.pack_unit
    declared_size = (brief.declared_unit_size if brief else None) or unit_weight
    declared_measure = "volume" if declared_unit == "ml" else "weight"

    operations: List[Dict[str, Any]] = []
    cumulative = 0
    for index, step in enumerate(category.unit_operations, start=1):
        cumulative += int(step.get("duration_min", 0))
        operations.append(
            {
                "step": index,
                "operation": step.get("op", ""),
                "equipment": step.get("equipment", ""),
                "duration_min": step.get("duration_min", 0),
                "cumulative_min": cumulative,
                "control": step.get("control", ""),
            }
        )

    parameters: List[Dict[str, Any]] = []
    for parameter in category.parameters:
        value = float(formulation.params.get(parameter.id, parameter.default))
        if parameter.id == "water_added_pct":
            value = round(formulation.pct_of("water"), 3)
        at_edge = (value - parameter.min) < 0.05 * (parameter.max - parameter.min) or (
            parameter.max - value
        ) < 0.05 * (parameter.max - parameter.min)
        parameters.append(
            {
                "id": parameter.id,
                "label": parameter.label,
                "unit": parameter.unit,
                "value": round(value, 3),
                "validated_range": [parameter.min, parameter.max],
                "at_range_edge": bool(at_edge),
            }
        )

    # In-process targets come from the same physics the predictions use.
    probe = nutrition.analyse(formulation, final_moisture=_predicted_moisture(formulation))
    aw, _, _ = physical.water_activity(probe)
    ph, _ = physical.predict_ph(probe, formulation.category)
    texture, _ = physical.predict_texture(probe, formulation.category, formulation.params)
    in_process = {
        "moisture_pct": round(probe.final_moisture, 2),
        "water_activity": round(aw, 3),
        "ph": round(ph, 2),
        "texture_index": round(texture.get("texture_index", 0.0), 1),
        "mix_moisture_pct": round(probe.moisture_mix, 2),
    }

    table = kb.ingredients()
    allergens = sorted({a for item in formulation.items for a in (table.get(item.ingredient_id).allergens if item.ingredient_id in table else ())})
    ccps: List[Dict[str, str]] = [
        {
            "point": "Foreign matter",
            "limit": "No detectable metal >= 1.5 mm Fe / 2.0 mm non-Fe",
            "control": "In-line metal detector and magnet; verify with test pieces at start, every 2 h and at end of run.",
            "action": "Stop, isolate the affected batch since the last acceptable check, re-screen the line.",
        },
        {
            "point": "Seal integrity",
            "limit": "No leakers; seal strength within the film supplier's specification",
            "control": "Visual and dye-penetration check every 30 min; pack vacuum/leak test at start of run.",
            "action": "Quarantine the packs since the last acceptable check and re-work or reject.",
        },
        {
            "point": "Weights and measures",
            "limit": (
                f"Net {declared_measure} {declared_size:.0f} {declared_unit}, "
                "tolerance as declared (typically -2% to +4% individual)"
            ),
            "control": "Check-weigher on 100% of packs; manual check of 10 packs per hour.",
            "action": "Reject out-of-tolerance packs; re-fill and re-verify the filler.",
        },
    ]
    if aw >= 0.70:
        ccps.append(
            {
                "point": "Thermal process / hot fill",
                "limit": "Fill temperature at or above the declared minimum and hold time achieved",
                "control": "Continuous temperature recorder on the filler line; chart reviewed per batch.",
                "action": "Hold and re-process the batch if the recorded profile deviates; do not release without review.",
            }
        )
    if ph <= 4.6:
        ccps.append(
            {
                "point": "Acidification",
                "limit": f"Finished-product pH {in_process['ph']:.2f} +/- 0.10",
                "control": "pH of every batch before filling, on a calibrated meter, after cooling to 25 degC.",
                "action": "Re-dose and re-check; hold the batch until the target is met on a re-test.",
            }
        )
    if aw <= 0.70:
        ccps.append(
            {
                "point": "Water activity",
                "limit": f"a_w <= {min(0.70, aw + 0.02):.2f}",
                "control": "a_w of the cooled product before packing; moisture by oven method as the routine check.",
                "action": "Extend drying/re-work the batch; do not pack until the limit is met.",
            }
        )
    if allergens:
        labels = [kb.allergen_labels().get(a, a) for a in allergens]
        ccps.append(
            {
                "point": "Allergen changeover",
                "limit": "No carry-over of " + ", ".join(labels) + " into allergen-free product",
                "control": "Documented wet clean plus inspection after any run containing " + ", ".join(labels) + ".",
                "action": "Re-clean and re-inspect before releasing the line; record the clean in the batch sheet.",
            }
        )

    batch_weights = []
    for item in sorted(formulation.items, key=lambda i: -i.pct):
        ing = table.get(item.ingredient_id)
        batch_weights.append(
            {
                "ingredient_id": item.ingredient_id,
                "name": ing.name if ing else item.ingredient_id,
                "pct": round(item.pct, 3),
                "grams_per_batch": round(item.pct / 100.0 * batch_size_kg * 1000.0, 1),
                "cost_inr_per_batch": round(item.pct / 100.0 * batch_size_kg * (ing.cost_inr_kg if ing else 0.0), 2),
            }
        )

    units = batch_size_kg * 1000.0 * (yield_pct / 100.0) / max(unit_weight, 0.1)
    return {
        "category": formulation.category,
        "category_label": category.label,
        "version": formulation.version,
        "unit_operations": operations,
        "total_process_min": cumulative,
        "parameters": parameters,
        "in_process_targets": in_process,
        "critical_control_points": ccps,
        "packaging": {
            "format": PACK_FORMAT.get(formulation.category, "Flow-wrap laminate"),
            "unit_weight_g": unit_weight,
            "units_per_batch": int(units),
            "storage": "Ambient, below 30 degC, away from direct sunlight; 60-65% RH maximum.",
        },
        "batch": {
            "batch_size_kg": batch_size_kg,
            "theoretical_yield_pct": round(yield_pct, 2),
            "expected_yield_pct": round(yield_pct * 0.98, 2),
            "yield_note": (
                "Theoretical yield is the mass balance of the formulation after the water the process "
                "removes; the expected figure applies a 2% handling loss."
            ),
            "ingredients": batch_weights,
        },
        "allergens_present": allergens,
    }


def _predicted_moisture(formulation: Formulation) -> float:
    probe = nutrition.analyse(formulation, final_moisture=10.0)
    moisture, _ = physical.predict_moisture(formulation.category, probe.moisture_mix, formulation.params)
    return moisture


def summary(formulation: Formulation, brief: Optional[Brief] = None) -> Dict[str, Any]:
    """A compact version of the plan for list views and the report."""
    full = plan(formulation, brief)
    return {
        "unit_operations": [
            {"operation": step["operation"], "equipment": step["equipment"], "duration_min": step["duration_min"]}
            for step in full["unit_operations"]
        ],
        "total_process_min": full["total_process_min"],
        "parameters": full["parameters"],
        "in_process_targets": full["in_process_targets"],
        "critical_control_points": full["critical_control_points"],
        "packaging": full["packaging"],
        "batch": {
            "theoretical_yield_pct": full["batch"]["theoretical_yield_pct"],
            "expected_yield_pct": full["batch"]["expected_yield_pct"],
        },
    }
