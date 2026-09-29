"""Composition and nutrition engine.

Everything here is exact mass balance: a formulation is a set of mass fractions,
so nutrient content per 100 g of the *mix* is a straight weighted sum. The
*final* product composition then accounts for the water the process removes
(or adds), which concentrates every other component.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from . import kb

# Energy factors (Atwater)
KCAL_PROTEIN = 4.0
KCAL_FAT = 9.0
KCAL_CARB = 4.0
KCAL_FIBRE = 2.0

NUTRIENTS = ("protein_g", "fat_g", "satfat_g", "carb_g", "sugar_g", "fibre_g", "sodium_mg")

# Categories that are a continuous liquid at ambient: their composition is more
# naturally quoted per 100 ml than per 100 g, and their density can be modelled
# from dissolved solids rather than from the bulk density of powders.
LIQUID_CATEGORIES = ("beverage", "sauce", "spread")
# Density gain per gram of dissolved solids per 100 g, in g/ml. Calibrated on
# sugar solutions and milk-based drinks: ~14 g solids/100 g gives ~1.05 g/ml.
DENSITY_PER_SOLID_G = 0.0035

# Average molar masses used for the aqueous-phase (water activity) model.
MW_SUGAR = 300.0
MW_ACID_DEFAULT = 150.0
MW_NONSOLUBLE = 5000.0


@dataclass
class Composition:
    """Result of analysing one formulation."""

    category: str
    total_pct: float
    moisture_mix: float
    mix_dry_matter: float
    mix: Dict[str, float] = field(default_factory=dict)
    final: Dict[str, float] = field(default_factory=dict)
    final_moisture: float = 0.0
    aggregates: Dict[str, float] = field(default_factory=dict)
    allergens: Set[str] = field(default_factory=set)
    diets: Set[str] = field(default_factory=set)
    acid_moles_per_100g: Dict[str, float] = field(default_factory=dict)
    sugar_equiv_g: float = 0.0
    polyol_g: float = 0.0
    salt_g: float = 0.0
    dissolved_g: float = 0.0
    item_detail: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    density_g_per_ml: float = 1.0

    @property
    def energy_kcal(self) -> float:
        return self.final.get("energy_kcal", 0.0)

    @property
    def is_liquid(self) -> bool:
        return self.category in LIQUID_CATEGORIES

    def value(self, key: str) -> float:
        if key in self.final:
            return self.final[key]
        return self.aggregates.get(key, 0.0)

    def per_100ml(self) -> Dict[str, float]:
        """Composition per 100 ml: per-100 g values scaled by the density.

        100 ml of product weighs 100 x density grams, so a nutrient quoted per
        100 g becomes ``per_100g x density`` per 100 ml.
        """
        return {key: value * self.density_g_per_ml for key, value in self.final.items()}

    def per_serving(self, serving_g: float) -> Dict[str, float]:
        """Composition of one serving, given the serving mass in grams."""
        factor = max(float(serving_g), 0.0) / 100.0
        return {key: value * factor for key, value in self.final.items()}


def _energy(protein: float, fat: float, carb: float, fibre: float) -> float:
    return KCAL_PROTEIN * protein + KCAL_FAT * fat + KCAL_CARB * carb + KCAL_FIBRE * fibre


def analyse(formulation, final_moisture: float) -> Composition:
    """Mass-balance a formulation at one per-100 g mix basis.

    ``final_moisture`` is the moisture of the finished product (per cent), which
    the physical model predicts from the formulation and the process settings.
    """
    table = kb.ingredients()
    mix_nutrients: Dict[str, float] = {k: 0.0 for k in NUTRIENTS}
    moisture_mix = 0.0
    aggregates: Dict[str, float] = {
        "starch_frac": 0.0,
        "protein_frac": 0.0,
        "fibre_frac": 0.0,
        "fat_frac": 0.0,
        "sugar_frac": 0.0,
        "hydrocolloid_frac": 0.0,
        "humectant_frac": 0.0,
        "acid_frac": 0.0,
        "salt_frac": 0.0,
        "leaven_frac": 0.0,
        "emulsifier_frac": 0.0,
        "preservative_frac": 0.0,
        "antioxidant_frac": 0.0,
        "preservative_ppm": 0.0,
        "antioxidant_ppm": 0.0,
        "gluten_frac": 0.0,
        "colour_frac": 0.0,
        "flavour_frac": 0.0,
        "structure_frac": 0.0,
        "water_binding_frac": 0.0,
        "sweet_frac": 0.0,
        "buffering_frac": 0.0,
        "oxid_risk_frac": 0.0,
        "mineral_frac": 0.0,
        "unsaturated_fat_frac": 0.0,
        "ingredient_count": 0.0,
        "water_pct": 0.0,
    }
    allergens: Set[str] = set()
    diets: Set[str] = set()
    acid_moles: Dict[str, float] = {}
    sugar_equiv = 0.0
    polyol_g = 0.0
    salt_g = 0.0
    detail: List[Dict[str, object]] = []
    warnings: List[str] = []

    for item in formulation.items:
        if item.ingredient_id not in table:
            warnings.append(f"Ingredient not found in knowledge base: {item.ingredient_id}")
            continue
        ing = table[item.ingredient_id]
        w = item.pct / 100.0
        mix_nutrients["protein_g"] += ing.protein * w
        mix_nutrients["fat_g"] += ing.fat * w
        mix_nutrients["satfat_g"] += ing.satfat * w
        mix_nutrients["carb_g"] += ing.carb * w
        mix_nutrients["sugar_g"] += ing.sugar * w
        mix_nutrients["fibre_g"] += ing.fibre * w
        mix_nutrients["sodium_mg"] += ing.sodium_mg * w
        moisture_mix += ing.moisture * w

        aggregates["starch_frac"] += ing.effect("starch") * w
        aggregates["protein_frac"] += (ing.protein / 100.0) * w
        aggregates["fibre_frac"] += (ing.fibre / 100.0) * w
        aggregates["fat_frac"] += (ing.fat / 100.0) * w
        aggregates["sugar_frac"] += (ing.sugar / 100.0) * w
        aggregates["water_binding_frac"] += ing.effect("water_binding") * w
        aggregates["structure_frac"] += ing.effect("structure") * w
        aggregates["sweet_frac"] += ing.effect("sweet") * w
        aggregates["leaven_frac"] += ing.effect("leaven") * w
        aggregates["emulsifier_frac"] += ing.effect("emulsifier") * w
        aggregates["gluten_frac"] += ing.effect("gluten") * w
        aggregates["colour_frac"] += ing.effect("colour") * w
        aggregates["flavour_frac"] += ing.effect("flavour") * w
        aggregates["buffering_frac"] += ing.effect("buffering") * w
        aggregates["preservative_frac"] += ing.effect("preservative") * w
        aggregates["antioxidant_frac"] += ing.effect("antioxidant") * w
        # Preservative and antioxidant action is a concentration effect, and the
        # concentrations the industry works with are parts per million in the
        # finished product - not a percentage that can be forgotten in a
        # percentage-scale model.
        aggregates["preservative_ppm"] += w * 1e6 * ing.effect("preservative")
        aggregates["antioxidant_ppm"] += w * 1e6 * ing.effect("antioxidant")
        aggregates["oxid_risk_frac"] += ing.effect("oxid_risk") * w
        if ing.group == "hydrocolloid":
            aggregates["hydrocolloid_frac"] += w
        if ing.group == "humectant":
            aggregates["humectant_frac"] += w
            if ing.id == "glycerol":
                polyol_g += item.pct
            else:
                polyol_g += item.pct
        if ing.group == "acidulant":
            aggregates["acid_frac"] += w
        if ing.group == "salt_mineral":
            aggregates["salt_frac"] += w
            aggregates["mineral_frac"] += w
        if ing.group in ("fortificant",):
            aggregates["mineral_frac"] += w * 0.5
        if ing.group == "water":
            aggregates["water_pct"] += item.pct
        if ing.is_acidulant and ing.molar_mass:
            # ``acid_strength`` carries the delivered acid fraction so that a
            # diluted commercial acidulant is not modelled as pure acid: vinegar
            # is 5% acetic acid in water, and 80% lactic acid is not 100%.
            strength = float(ing.fx.get("acid_strength", 1.0))
            moles = (item.pct / 100.0) / (ing.molar_mass / 1000.0) * strength
            acid_moles[ing.id] = moles * (ing.proton_equivalents or 1.0)
        # solute bookkeeping for the water-activity model
        # Grams of dissolved-sugar-equivalent per 100 g of mix. ``w`` is already
        # the mass fraction, so an ingredient's own sugar content is simply
        # multiplied by it: getting this wrong by a factor of 100 turns a mango
        # spread into a glucose syrup and drives the water-activity model mad.
        if ing.group == "sweetener" and ing.id not in ("maltodextrin",):
            sugar_equiv += item.pct * (1.0 if ing.id != "erythritol" else 0.35)
        elif ing.group in ("fruit",):
            sugar_equiv += ing.sugar * w
        elif ing.group == "dairy" and ing.sugar > 40:
            sugar_equiv += ing.sugar * w
        elif ing.group in ("humectant",):
            sugar_equiv += item.pct * 0.6
        salt_g += ing.sodium_mg * w * 2.54 / 1000.0

        # unsaturated fat proxy for oxidation risk
        if ing.fat > 0:
            unsat = max(ing.fat - ing.satfat, 0.0) / ing.fat
            aggregates["unsaturated_fat_frac"] += (ing.fat / 100.0) * w * unsat

        allergens.update(ing.allergens)
        diets.add(ing.diet)
        aggregates["ingredient_count"] += 1.0
        detail.append(
            {
                "ingredient_id": ing.id,
                "name": ing.name,
                "group": ing.group,
                "slot": item.slot,
                "pct": round(item.pct, 3),
                "cost_inr_kg": ing.cost_inr_kg,
                "allergens": list(ing.allergens),
            }
        )

    total = formulation.total_pct
    if abs(total - 100.0) > 0.5:
        warnings.append(f"Formulation totals {total:.2f}% instead of 100%.")

    mix_energy = _energy(
        mix_nutrients["protein_g"], mix_nutrients["fat_g"], mix_nutrients["carb_g"], mix_nutrients["fibre_g"]
    )
    mix = dict(mix_nutrients)
    mix["energy_kcal"] = mix_energy

    dry_mix = max(100.0 - moisture_mix, 0.001)
    final_moisture = min(max(final_moisture, 0.5), 92.0)
    final_total = dry_mix / max(1.0 - final_moisture / 100.0, 0.05)
    scale = 100.0 / final_total
    final = {k: mix_nutrients[k] * scale for k in NUTRIENTS}
    final["energy_kcal"] = _energy(
        final["protein_g"], final["fat_g"], final["carb_g"], final["fibre_g"]
    )

    density = _density(formulation, final_moisture, table)

    aggregates["final_total_g"] = final_total
    aggregates["concentration_factor"] = scale
    aggregates["density_g_per_ml"] = density
    aggregates["sugar_equiv_g"] = sugar_equiv * scale
    aggregates["polyol_g"] = polyol_g * scale
    aggregates["salt_g"] = salt_g * scale
    aggregates["added_water_pct"] = aggregates["water_pct"]

    comp = Composition(
        category=formulation.category,
        total_pct=total,
        moisture_mix=moisture_mix,
        mix_dry_matter=dry_mix,
        mix=mix,
        final=final,
        final_moisture=final_moisture,
        aggregates=aggregates,
        allergens=allergens,
        diets=diets,
        acid_moles_per_100g=acid_moles,
        sugar_equiv_g=sugar_equiv * scale,
        polyol_g=polyol_g * scale,
        salt_g=salt_g * scale,
        item_detail=detail,
        warnings=warnings,
        density_g_per_ml=density,
    )
    return comp


def _density(formulation, final_moisture: float, table: Dict[str, object]) -> float:
    """Product density in g/ml.

    A liquid's density is driven by its dissolved solids, so it is modelled from
    the dry matter (the ingredient ``dens`` figures are *bulk* densities of
    powders, which would give a drink 0.7 g/ml). A solid's density is the
    mass-weighted bulk density of its lines, which is only used for reference.
    """
    if formulation.category in LIQUID_CATEGORIES:
        solids = max(100.0 - final_moisture, 0.0)
        return float(min(1.0 + DENSITY_PER_SOLID_G * solids, 1.20))
    total = 0.0
    weighted = 0.0
    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        if ing is None:
            continue
        total += item.pct
        weighted += item.pct * float(getattr(ing, "density", 0.6))
    if total <= 1e-9:
        return 1.0
    return float(weighted / total)


def nutrition_kpis(comp: Composition) -> Dict[str, float]:
    energy = comp.final["energy_kcal"]
    protein_share = (comp.final["protein_g"] * KCAL_PROTEIN / energy * 100.0) if energy > 0 else 0.0
    return {
        "energy_kcal": energy,
        "protein_g": comp.final["protein_g"],
        "protein_energy_pct": protein_share,
        "fat_g": comp.final["fat_g"],
        "satfat_g": comp.final["satfat_g"],
        "carb_g": comp.final["carb_g"],
        "sugar_g": comp.final["sugar_g"],
        "fibre_g": comp.final["fibre_g"],
        "sodium_mg": comp.final["sodium_mg"],
    }


def claims_met(comp: Composition, claim_ids: List[str]) -> Dict[str, bool]:
    """Check declared claims against the computed composition."""
    values = nutrition_kpis(comp)
    out: Dict[str, bool] = {}
    for rule in kb.claim_rules():
        if rule["id"] not in claim_ids:
            continue
        nutrient = rule["nutrient"]
        if nutrient not in values:
            out[rule["id"]] = False
            continue
        if rule["op"] == ">=":
            out[rule["id"]] = values[nutrient] >= float(rule["value"])
        else:
            out[rule["id"]] = values[nutrient] <= float(rule["value"])
    return out


def label_table(comp: Composition) -> List[Dict[str, object]]:
    """A nutrition-facts style table for the finished product."""
    values = nutrition_kpis(comp)
    rows = [
        ("Energy", values["energy_kcal"], "kcal"),
        ("Protein", values["protein_g"], "g"),
        ("Total fat", values["fat_g"], "g"),
        ("  of which saturated", values["satfat_g"], "g"),
        ("Carbohydrate", values["carb_g"], "g"),
        ("  of which sugars", values["sugar_g"], "g"),
        ("Dietary fibre", values["fibre_g"], "g"),
        ("Sodium", values["sodium_mg"], "mg"),
    ]
    return [
        {"nutrient": name, "per_100g": round(value, 2), "unit": unit} for name, value, unit in rows
    ]
