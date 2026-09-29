"""Costing model: ingredient cost, process cost and cost per selling unit."""
from __future__ import annotations

from typing import Dict, List

from . import kb

# Indicative Indian pilot-plant/works cost assumptions (editable in one place).
ELECTRICITY_INR_PER_KWH = 9.0
# Specific process energy by category: kWh per kg of finished product.
PROCESS_ENERGY_KWH_PER_KG = {
    "cookie": 1.35,
    "spread": 0.95,
    "drymix": 0.55,
    "extruded_snack": 1.10,
    "bar": 0.80,
    "sauce": 0.90,
    # UHT + aseptic filling is energy-light per kg against a bakery line, but the
    # aseptic zone and clean-in-place carry real utility cost.
    "beverage": 0.85,
}
# Labour + utilities + overhead, INR per kg of finished product at pilot scale.
CONVERSION_OVERHEAD_INR_PER_KG = {
    "cookie": 22.0,
    "spread": 18.0,
    "drymix": 14.0,
    "extruded_snack": 20.0,
    "bar": 26.0,
    "sauce": 16.0,
    "beverage": 30.0,
}
# Packaging: primary pack cost as a function of net weight (INR per unit).
PACK_BASE_INR = {
    "cookie": 2.2, "spread": 9.0, "drymix": 3.4, "extruded_snack": 3.0, "bar": 2.6,
    "sauce": 10.0, "beverage": 11.0,
}


def ingredient_cost(formulation) -> Dict[str, object]:
    table = kb.ingredients()
    per_kg = 0.0
    rows: List[Dict[str, object]] = []
    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        if ing is None:
            continue
        contribution = (item.pct / 100.0) * ing.cost_inr_kg
        per_kg += contribution
        rows.append(
            {
                "ingredient_id": ing.id,
                "name": ing.name,
                "pct": round(item.pct, 3),
                "cost_inr_kg": ing.cost_inr_kg,
                "contribution_inr_kg": round(contribution, 3),
            }
        )
    rows.sort(key=lambda r: -float(r["contribution_inr_kg"]))
    return {"ingredient_cost_inr_kg": round(per_kg, 3), "rows": rows}


def process_cost(category: str, unit_weight_g: float) -> Dict[str, float]:
    energy = PROCESS_ENERGY_KWH_PER_KG.get(category, 1.0) * ELECTRICITY_INR_PER_KWH
    overhead = CONVERSION_OVERHEAD_INR_PER_KG.get(category, 20.0)
    energy *= 1.0
    return {
        "energy_inr_kg": round(energy, 3),
        "conversion_inr_kg": round(overhead, 3),
        "process_inr_kg": round(energy + overhead, 3),
    }


def packaging_cost(category: str, unit_weight_g: float) -> float:
    base = PACK_BASE_INR.get(category, 3.0)
    # Larger packs need more material but carry proportionally less cost per kg.
    weight_factor = (max(unit_weight_g, 5.0) / 12.0) ** 0.35 if category == "cookie" else 1.0
    return round(base * weight_factor, 3)


def total_cost(formulation, unit_weight_g: float) -> Dict[str, object]:
    ing = ingredient_cost(formulation)
    proc = process_cost(formulation.category, unit_weight_g)
    pack = packaging_cost(formulation.category, unit_weight_g)
    ing_kg = float(ing["ingredient_cost_inr_kg"])
    per_kg = ing_kg + float(proc["process_inr_kg"]) + pack * (1000.0 / max(unit_weight_g, 1.0))
    return {
        "ingredient_cost_inr_kg": round(ing_kg, 3),
        "process_cost_inr_kg": proc["process_inr_kg"],
        "packaging_inr_kg": round(pack * (1000.0 / max(unit_weight_g, 1.0)), 3),
        "total_cost_inr_kg": round(per_kg, 3),
        "cost_inr_unit": round(per_kg * unit_weight_g / 1000.0, 3),
        "pack_inr_unit": pack,
        "breakdown": ing["rows"],
        "assumptions": {
            "electricity_inr_per_kwh": ELECTRICITY_INR_PER_KWH,
            "process_energy_kwh_per_kg": PROCESS_ENERGY_KWH_PER_KG.get(formulation.category, 1.0),
            "conversion_overhead_inr_per_kg": CONVERSION_OVERHEAD_INR_PER_KG.get(formulation.category, 20.0),
            "pack_inr_unit": pack,
        },
    }
