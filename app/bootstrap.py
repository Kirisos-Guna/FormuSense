"""Seeded case studies: three Indian snack products and the plants that make them.

Each case is a complete, realistic brief plus the *plant* that will manufacture it,
with its documented deviation from the models. The deviations are the point: a
benchmark is only meaningful if the physical trials behave like physical trials,
and each deviation below is a failure mode a food development team would
recognise.

===================  ==========================================================
Case                 Documented plant behaviour
===================  ==========================================================
Masala namkeen       Extruder runs 9 degC below the setpoint, and the belt
pellet (extruded)    dryer delivers a little less duty than modelled. Moisture
                     lands high and the pellet reads soft.
Ragi protein         Deck oven runs 12% short on drying duty, 5 degC under its
cookie (cookie)      reading, and the minor premix scale over-delivers about
                     6%. Moisture and texture both land out of band.
Reduced-sugar        The kettle's steam line cannot boil off what the recipe
mango spread         assumes, acid is dosed early into the open cook and the
(spread)             syrup inverts a little extra. Moisture, pH and sugars
                     all drift away from the target.

The fourth entry is deliberately infeasible: it is there to demonstrate that the
agent says so, with numbers, instead of quietly producing a formula that misses a
target.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .store import Store

CASES: List[Dict[str, Any]] = [
    {
        "key": "namkeen",
        "name": "High-protein masala namkeen pellet",
        "category": "extruded_snack",
        "diet": "vegetarian",
        "claims": ["high_protein"],
        "unit_weight_g": 30.0,
        "spec_text": (
            "High protein masala extruded namkeen pellet for the evening-snack segment. "
            "30 g pack. Protein 15 g per 100 g (high protein claim, vegetarian). "
            "Moisture 3%. Sodium 480 mg per 100 g for a savoury profile. "
            "Shelf life 6 months. Ingredient cost not more than INR 185 per kg. "
            "Masala seasoning to be visible on the pellet; texture should be crisp."
        ),
        "plant": {
            "name": "Pilot plant A - twin-screw extruder line",
            "drying_efficiency": 0.96,
            "temp_offset_c": -9.0,
            "noise_scale": 1.10,
        },
        "plant_narrative": (
            "The extruder's barrel controller reads 9 degC below setpoint (a known thermocouple "
            "placement error), and the belt dryer leaves a little more moisture than modelled. "
            "Both were found during commissioning and are documented in the trial records."
        ),
    },
    {
        "key": "cookie",
        "name": "High-protein high-fibre ragi cookie",
        "category": "cookie",
        "diet": "vegetarian",
        "claims": ["high_protein", "high_fibre"],
        "unit_weight_g": 40.0,
        "spec_text": (
            "High protein and high fibre ragi biscuit for a health-conscious biscuit range. "
            "40 g pack (8 biscuits). Protein 12 g per 100 g and dietary fibre 7 g per 100 g. "
            "Sugar not more than 12 g per 100 g. Moisture 3%. Shelf life 6 months. "
            "Ingredient cost target INR 240 per kg. Eggless and vegetarian."
        ),
        "plant": {
            "name": "Pilot plant B - deck oven and rotary moulder",
            "drying_efficiency": 0.88,
            "sodium_carry": 1.06,
            "temp_offset_c": -5.0,
            "noise_scale": 1.15,
        },
        "plant_narrative": (
            "The deck oven's dampers were never re-commissioned after the burner change, so bake "
            "duty is about 12% short and the deck runs about 5 degC under the controller reading. "
            "The minor-ingredient scale over-delivers roughly 6% at the low end of its range, which "
            "matters for salt and the leavening system."
        ),
    },
    {
        "key": "spread",
        "name": "Reduced-sugar mango fruit spread",
        "category": "spread",
        "diet": "vegetarian",
        "claims": ["reduced_sugar"],
        "unit_weight_g": 200.0,
        "spec_text": (
            "Reduced sugar mango fruit spread (jam) with a clean label for retail. 200 g glass jar. "
            "Total sugars not more than 40 g per 100 g (at least 25% below a standard jam). "
            "pH 3.6. Water activity not more than 0.90. Shelf life 9 months unopened at ambient. "
            "Ingredient cost target INR 160 per kg. Set and spreadable, no artificial colour."
        ),
        "plant": {
            "name": "Pilot plant C - steam-jacketed kettle and hot fill",
            "acid_retention": 0.74,
            "sugar_inversion": 1.08,
            "drying_efficiency": 0.90,
            # The kettle holds temperature on setpoint; the deviations on this
            # line are the boil-off, the acid loss and the extra inversion.
            "temp_offset_c": 0.0,
            "noise_scale": 1.20,
        },
        "plant_narrative": (
            "The kettle's steam line is undersized for a full batch, so the cook boils off about a "
            "tenth less water than the recipe assumes. Acid dosed early into the open kettle loses "
            "about a quarter of its strength with the steam, and the long hold at temperature "
            "inverts about 8% extra sugar."
        ),
    },
]

# A brief that cannot be satisfied as written, kept because being able to say so
# is a feature: a ragi-and-soy base that carries 15 g protein also carries the
# whole grain's fibre, so 5 g fibre is unreachable without changing the design.
INFEASIBLE_CASE: Dict[str, Any] = {
    "key": "conflict_demo",
    "name": "Ragi malt dry mix (target conflict demonstration)",
    "category": "drymix",
    "diet": "vegetarian",
    "claims": ["high_protein"],
    "unit_weight_g": 40.0,
    "spec_text": (
        "High protein ragi malt instant health drink mix. 40 g sachet. Protein 15 g per 100 g. "
        "Dietary fibre 4 g per 100 g (a deliberately low fibre ceiling to demonstrate target "
        "conflict detection). Moisture 5%. Ingredient cost not more than INR 210 per kg."
    ),
    "plant": {
        "name": "Pilot plant D - ribbon blender and roaster",
        "drying_efficiency": 0.95,
        "noise_scale": 1.15,
    },
    "plant_narrative": (
        "Blending and roasting only. The deviations here are minor by design: this case exists to "
        "show the target conflict, not a plant effect."
    ),
}


def case_definitions(include_conflict: bool = False) -> List[Dict[str, Any]]:
    cases = [dict(case) for case in CASES]
    if include_conflict:
        cases.append(dict(INFEASIBLE_CASE))
    return cases


def seed_all(force: bool = False) -> Dict[str, Any]:
    """Create the seeded products in the database (no trials are run)."""
    from .service import AgentService

    service = AgentService(Store())
    if force:
        for product in service.store.products():
            if product["name"] in {case["name"] for case in case_definitions(True)}:
                service.store.clear(int(product["id"]))
    created: List[Dict[str, Any]] = []
    for case in case_definitions():
        payload = {
            "product_name": case["name"],
            "category": case["category"],
            "spec_text": case["spec_text"],
            "diet": case["diet"],
            "claims": case["claims"],
            "unit_weight_g": case["unit_weight_g"],
            "plant": case["plant"],
        }
        result = service.create_product(payload)
        created.append(
            {
                "key": case["key"],
                "product_id": result["product_id"],
                "objective": result["evaluation"]["objective"],
                "conflicts": len(result["conflicts"]),
            }
        )
        service.store.log(
            result["product_id"],
            "bootstrap",
            f"Seeded case study: {case['plant_narrative']}",
            {"plant": case["plant"]},
        )
    return {"products": len(created), "cases": created}
