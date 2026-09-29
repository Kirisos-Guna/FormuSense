"""Initial formulation generation and constraint checking.

This is the "put something on the bench" step. Given a brief, the agent must
produce a first formulation that is simultaneously:

* **plausible** - made of ingredients a development chef would actually use for
  that product class, in the proportions that class uses;
* **compliant** - inside every dietary, allergen and additive limit held in the
  knowledge base, and inside each ingredient's practical or legal maximum;
* **on intent** - biased towards the targets and claims in the brief.

The method is a slot-fill optimiser rather than a black box:

1. every category declares *slots* (structure, sweetener, fat, protein, fibre,
   inclusion, hydrocolloid, ...) with a target share and hard bounds;
2. candidate ingredients for a slot are ranked by a transparent utility that
   combines their contribution to the brief's targets, their functional role in
   that slot, their cost, and a plausibility prior for the category;
3. slot shares are reconciled to exactly 100% with iterative proportional
   fitting, which keeps every slot inside its declared bounds while absorbing the
   residual in the process-water slot;
4. within a slot, the share is split between its ingredient lines with the same
   reconciliation, respecting per-ingredient maxima.

Every choice is therefore traceable to a rule, which is what makes the output
explainable to a product developer - and auditable in the report.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import engine, kb, kpi as kpi_registry, nutrition
from .types import Brief, Formulation, Item, KpiTarget

# Plausibility priors: the first ingredients a chef would reach for, per slot.
PREFERRED: Dict[str, Dict[str, List[str]]] = {
    "cookie": {
        "structure": ["atta", "ragi_flour", "maida", "jowar_flour", "oat_flour", "besan", "rice_flour", "bajra_flour", "semolina", "corn_flour"],
        "sweetener": ["sugar", "jaggery", "date_paste", "glucose_syrup", "invert_syrup", "honey", "erythritol", "fructose", "maltodextrin", "stevia_reba", "sucralose"],
        "fat": ["palm_oil", "sunflower_oil", "butter", "ghee", "groundnut_oil", "rice_bran_oil", "coconut_oil", "vanaspati", "cocoa_butter"],
        "protein_boost": ["defatted_soy_flour", "besan", "whey_protein_concentrate", "soy_protein_isolate", "moong_flour", "peanut_flour", "egg_white_powder"],
        "fibre": ["oat_bran", "wheat_bran", "psyllium_husk", "inulin"],
        "inclusion": ["almond", "cashew", "raisin", "date_diced", "desiccated_coconut", "chia_seed", "flax_seed", "peanut_roasted", "sesame_seed"],
        "hydrocolloid": ["xanthan_gum", "cmc", "guar_gum", "carrageenan", "pectin_lm"],
        "emulsifier": ["lecithin", "mono_diglycerides"],
        "humectant": ["sorbitol", "glycerol"],
        "leavening": ["baking_soda", "baking_powder", "yeast_dry"],
        "salt": ["salt", "potassium_chloride"],
        "flavour": ["cocoa_powder", "choco_chips", "vanilla_flavour", "beetroot_powder", "fruit_flavour", "spice_masala", "onion_powder"],
        "preservative": ["calcium_propionate", "potassium_sorbate", "sodium_benzoate", "ascorbic_acid"],
    },
    "spread": {
        "fruit": ["mango_pulp", "mixed_fruit_pulp", "apple_pulp", "date_paste", "tomato_paste", "raisin"],
        "sweetener": ["sugar", "jaggery", "fructose", "glucose_syrup", "honey", "invert_syrup", "erythritol", "date_paste"],
        "humectant": ["sorbitol", "glycerol"],
        "gelling": ["pectin_lm", "carrageenan", "xanthan_gum", "guar_gum", "cmc"],
        "acidulant": ["citric_acid", "tartaric_acid", "malic_acid", "lactic_acid", "vinegar", "sodium_citrate"],
        "preservative": ["potassium_sorbate", "sodium_benzoate", "ascorbic_acid", "calcium_propionate"],
        "flavour": ["fruit_flavour", "beetroot_powder", "vanilla_flavour", "spice_masala", "cocoa_powder"],
        "fortificant": ["vitamin_premix", "calcium_carbonate", "ferrous_fumarate"],
    },
    "drymix": {
        "grain": ["ragi_flour", "jowar_flour", "bajra_flour", "oat_flour", "rice_flour", "atta", "corn_flour", "semolina", "maida"],
        "protein": ["defatted_soy_flour", "besan", "moong_flour", "whey_protein_concentrate", "soy_protein_isolate", "peanut_flour", "egg_white_powder"],
        "dairy": ["skim_milk_powder", "whole_milk_powder", "whey_powder", "milk_toned", "curd"],
        "sweetener": ["sugar", "jaggery", "maltodextrin", "glucose_syrup", "date_paste", "fructose", "honey", "erythritol", "stevia_reba"],
        "fibre": ["oat_bran", "wheat_bran", "inulin", "psyllium_husk"],
        "inclusion": ["almond", "cashew", "desiccated_coconut", "chia_seed", "flax_seed", "raisin", "date_diced", "peanut_roasted"],
        "salt": ["salt", "potassium_chloride"],
        "flavour": ["cocoa_powder", "vanilla_flavour", "spice_masala", "onion_powder", "fruit_flavour", "beetroot_powder"],
        "fortificant": ["vitamin_premix", "calcium_carbonate", "ferrous_fumarate"],
    },
    "extruded_snack": {
        "grain": ["corn_flour", "rice_flour", "ragi_flour", "jowar_flour", "bajra_flour", "semolina", "oat_flour", "atta", "maida", "tapioca_starch"],
        "protein": ["defatted_soy_flour", "besan", "moong_flour", "soy_protein_isolate", "peanut_flour", "whey_protein_concentrate", "egg_white_powder"],
        "fibre": ["oat_bran", "wheat_bran", "inulin", "psyllium_husk"],
        "fat": ["sunflower_oil", "rice_bran_oil", "groundnut_oil", "palm_oil", "coconut_oil"],
        "salt": ["salt", "potassium_chloride"],
        "flavour": ["spice_masala", "onion_powder", "beetroot_powder", "fruit_flavour", "vanilla_flavour", "cocoa_powder"],
        "emulsifier": ["lecithin", "mono_diglycerides"],
    },
    "bar": {
        "base": ["oat_flour", "ragi_flour", "jowar_flour", "rice_flour", "atta", "oat_bran", "wheat_bran", "inulin", "psyllium_husk"],
        "protein": ["whey_protein_concentrate", "soy_protein_isolate", "defatted_soy_flour", "skim_milk_powder", "whole_milk_powder", "besan", "peanut_flour", "moong_flour", "egg_white_powder", "whey_powder", "milk_toned"],
        "syrup": ["glucose_syrup", "invert_syrup", "honey", "date_paste", "jaggery", "sugar", "maltodextrin", "sorbitol", "glycerol", "erythritol", "fructose"],
        "fat": ["cocoa_butter", "palm_oil", "groundnut_oil", "sunflower_oil", "ghee", "butter", "coconut_oil"],
        "inclusion": ["almond", "cashew", "raisin", "date_diced", "chia_seed", "flax_seed", "desiccated_coconut", "peanut_roasted", "sesame_seed"],
        "salt": ["salt", "potassium_chloride"],
        "flavour": ["cocoa_powder", "choco_chips", "vanilla_flavour", "spice_masala", "beetroot_powder", "fruit_flavour"],
        "water": ["water"],
    },
    "sauce": {
        "base": ["tomato_paste", "mixed_fruit_pulp", "mango_pulp", "apple_pulp", "date_paste", "raisin"],
        "sweetener": ["sugar", "jaggery", "fructose", "glucose_syrup", "date_paste", "honey", "invert_syrup", "erythritol"],
        "acidulant": ["vinegar", "citric_acid", "lactic_acid", "malic_acid", "tartaric_acid", "sodium_citrate"],
        "thickener": ["cornstarch", "tapioca_starch", "xanthan_gum", "cmc", "guar_gum", "pectin_lm", "maida", "rice_flour"],
        "salt": ["salt", "potassium_chloride"],
        "spice_flavour": ["spice_masala", "onion_powder", "beetroot_powder", "cocoa_powder", "fruit_flavour", "vanilla_flavour"],
        "preservative": ["potassium_sorbate", "sodium_benzoate", "calcium_propionate", "ascorbic_acid"],
    },
    "beverage": {
        "water": ["water"],
        # Whey first: it is the protein a ready-to-drink line actually hydrates.
        "protein": ["whey_protein_concentrate", "soy_protein_isolate", "skim_milk_powder", "whey_powder", "whole_milk_powder", "defatted_soy_flour", "egg_white_powder"],
        "dairy": ["milk_toned", "skim_milk_powder", "whey_powder", "curd", "whole_milk_powder"],
        "sweetener": ["stevia_reba", "sucralose", "erythritol", "sugar", "fructose", "glucose_syrup", "maltodextrin", "sorbitol"],
        "fat": ["palm_oil", "sunflower_oil", "cocoa_butter", "butter", "ghee", "rice_bran_oil"],
        "stabiliser": ["carrageenan", "xanthan_gum", "cmc", "pectin_lm", "guar_gum"],
        "acidulant": ["citric_acid", "malic_acid", "lactic_acid", "tartaric_acid", "sodium_citrate", "ascorbic_acid"],
        "flavour": ["cocoa_powder", "vanilla_flavour", "fruit_flavour", "beetroot_powder"],
        "preservative": ["potassium_sorbate", "sodium_benzoate", "ascorbic_acid"],
        "fortificant": ["vitamin_premix", "calcium_carbonate"],
    },
}

# Which KPI a slot is primarily responsible for; used to bias ingredient choice.
# Deliberately narrow: only the slot's *own* job is allowed to steer the choice.
# Scoring every candidate against every target is what fills a cereal bar's
# cereal base with bran and psyllium, because "more fibre is better" is true of
# the product but not of the base slot, whose job is to be a cereal.
SLOT_KPI: Dict[str, str] = {
    "structure": "fibre_g",
    "grain": "fibre_g",
    "sweetener": "sugar_g",
    "syrup": "sugar_g",
    "fat": "fat_g",
    "protein_boost": "protein_g",
    "protein": "protein_g",
    "dairy": "protein_g",
    "fibre": "fibre_g",
    "gelling": "consistency_index",
    "thickener": "consistency_index",
    "acidulant": "ph",
    "salt": "sodium_mg",
    "spice_flavour": "sodium_mg",
    "humectant": "water_activity",
    "stabiliser": "consistency_index",
}


@dataclass
class ConstraintReport:
    """Result of checking one formulation against the brief and the rule set."""

    ok: bool
    total_pct: float
    hard_violations: List[str] = field(default_factory=list)
    soft_violations: List[str] = field(default_factory=list)
    slots: List[Dict[str, Any]] = field(default_factory=list)
    diet_conflicts: List[str] = field(default_factory=list)
    allergen_conflicts: List[str] = field(default_factory=list)
    cost_inr_kg: float = 0.0
    cost_ceiling_inr_kg: Optional[float] = None
    claim_status: Dict[str, bool] = field(default_factory=dict)
    checks: Dict[str, bool] = field(default_factory=dict)
    ingredient_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "total_pct": round(self.total_pct, 3),
            "hard_violations": list(self.hard_violations),
            "soft_violations": list(self.soft_violations),
            "slots": self.slots,
            "diet_conflicts": list(self.diet_conflicts),
            "allergen_conflicts": list(self.allergen_conflicts),
            "cost_inr_kg": round(self.cost_inr_kg, 2),
            "cost_ceiling_inr_kg": self.cost_ceiling_inr_kg,
            "claim_status": dict(self.claim_status),
            "checks": dict(self.checks),
            "ingredient_count": self.ingredient_count,
        }


# --------------------------------------------------------------------------- #
# Ingredient candidate ranking
# --------------------------------------------------------------------------- #
# Per-1%-inclusion linear response of each tractable KPI, used for ranking only.
_NUTRIENT_OF_KPI = {
    "protein_g": "protein",
    "protein_energy_pct": "protein",
    "fat_g": "fat",
    "satfat_g": "satfat",
    "carb_g": "carb",
    "sugar_g": "sugar",
    "fibre_g": "fibre",
    "sodium_mg": "sodium_mg",
    "energy_kcal": "kcal",
}


def _target_weights(brief: Brief) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for target in brief.targets:
        weights[target.id] = max(target.priority, 0.1) * (1.35 if target.hard else 0.75)
    return weights


def _allowed(ing: kb.Ingredient, brief: Brief) -> bool:
    if not kb.diet_allowed(ing, brief.diet):
        return False
    if set(ing.allergens) & set(brief.allergens_to_avoid):
        return False
    return True


def _utility(ing: kb.Ingredient, slot: kb.Slot, brief: Brief, weights: Dict[str, float]) -> float:
    """Transparent score for how well an ingredient serves *this* slot and brief."""
    score = 0.0
    primary = SLOT_KPI.get(slot.id)
    if primary:
        nutrient = _NUTRIENT_OF_KPI.get(primary)
        target = brief.target(primary)
        weight = weights.get(primary, 1.0)
        if nutrient and (target is not None or primary in weights):
            if nutrient == "sodium_mg":
                per_unit = ing.sodium_mg / 100.0
            else:
                per_unit = float(getattr(ing, nutrient)) / 100.0
            direction = target.direction if target else kpi_registry.kpi_direction(primary)
            if direction == "higher":
                sign = 1.0
            elif direction == "lower":
                sign = -1.0
            else:
                # Band target: prefer the *effective source* of the nutrient, but
                # only damped when the target sits far below what the category
                # naturally delivers. A "5 g fibre" instant mix must not fill its
                # cereal base with bran, but a protein bar still wants whey.
                reference = reference_values(brief.category, brief.diet).get(primary)
                if reference is None or target.target >= 0.85 * reference:
                    sign = 1.0
                else:
                    sign = -0.35
            score += 12.0 * weight * per_unit * sign
    # Cost is always a commercial pressure, at a lower weight than the targets.
    score -= 0.9 * (ing.cost_inr_kg / 1000.0)
    # Functional role in this particular slot.
    primary = SLOT_KPI.get(slot.id)
    if primary:
        if primary == "consistency_index":
            score += 9.0 * ing.effect("water_binding")
        elif primary == "water_activity":
            score += 6.0 * ing.effect("humectant")
        elif primary == "sodium_mg":
            score += 1.2 * ing.effect("flavour")
        # No acidulant bonus: which acid to use is a dosing question (potency
        # against the matrix buffer), and the close call between citric acid and
        # vinegar is settled by cost and by the pH target, not by a rule of
        # thumb. A hand-set bonus here is what put 5% citric acid in a sauce.
        elif primary == "fibre_g":
            score += 5.0 * (ing.fibre / 100.0) + 1.8 * ing.effect("structure")
        elif primary == "protein_g":
            score += 5.0 * (ing.protein / 100.0)
        elif primary == "sugar_g":
            score += 6.0 * (ing.sugar / 100.0)
        elif primary == "fat_g":
            score += 5.0 * ing.effect("fat_effect")
    if slot.id in ("hydrocolloid", "gelling", "thickener"):
        score += 4.0 * ing.effect("water_binding")
    if slot.id in ("leavening",):
        score += 4.0 * ing.effect("leaven")
    if slot.id == "flavour":
        score += 3.0 * ing.effect("flavour") + 1.5 * ing.effect("colour")
    if slot.id == "preservative":
        score += 3.0 * ing.effect("preservative")
    # Whole-formulation risk: strongly oxidising or strongly water-binding
    # ingredients at high levels destabilise a dry product.
    score -= 1.5 * ing.effect("oxid_risk")
    return score


def _pool(slot: kb.Slot, brief: Brief) -> List[kb.Ingredient]:
    """Ordered candidate list for a slot: preferred names first, then the rest."""
    preferred = PREFERRED.get(brief.category, {}).get(slot.id, [])
    table = kb.ingredients()
    ordered: List[kb.Ingredient] = []
    for ingredient_id in preferred:
        ing = table.get(ingredient_id)
        if ing and ing.group in slot.groups and _allowed(ing, brief) and ing not in ordered:
            ordered.append(ing)
    group_members = [i for i in kb.by_group(*slot.groups) if _allowed(i, brief) and i not in ordered]
    group_members.sort(key=lambda i: (-i.max_pct, i.cost_inr_kg, i.id))
    ordered.extend(group_members)
    return ordered


def _choose(
    slot: kb.Slot,
    brief: Brief,
    weights: Dict[str, float],
    rng: random.Random,
    share: float = 0.0,
) -> List[kb.Ingredient]:
    """Pick the ingredient lines for a slot, in allocation order.

    Selection is utility-ranked, but the list is then grown until the chosen
    lines can *physically* carry the slot's share: if every candidate were capped
    at its declared maximum and the sum of those maxima is below the share, the
    share is unachievable and the allocation below would fail. Growing the list
    first keeps the split feasible and is why an acidulant slot is never asked to
    deliver 5% from a single ingredient limited to 1%.
    """
    pool = _pool(slot, brief)
    if not pool:
        return []
    want = max(1, int(slot.count))
    if slot.id == "water":
        return pool[:1]
    scored: List[Tuple[float, str, kb.Ingredient]] = []
    for index, ing in enumerate(pool):
        prior = max(0.0, 1.6 - 0.22 * index)
        jitter = rng.uniform(-0.25, 0.25)
        scored.append((_utility(ing, slot, brief, weights) + prior + jitter, ing.id, ing))
    scored.sort(key=lambda row: (-row[0], row[1]))
    chosen = [row[2] for row in scored[:want]]

    def capacity(lines: List[kb.Ingredient]) -> float:
        ceiling = share if share > 0 else 100.0
        return sum(min(max(ing.hard_max_pct, 0.0), ceiling) for ing in lines)

    index = want
    while capacity(chosen) < share - 1e-6 and index < len(scored):
        chosen.append(scored[index][2])
        index += 1
    # A single source is rarely enough for a demanding target: extend once more.
    if slot.required and len(chosen) < want + 1 and len(pool) > len(chosen):
        chosen.append(scored[len(chosen)][2])
    return chosen


# --------------------------------------------------------------------------- #
# Share reconciliation
# --------------------------------------------------------------------------- #
def _reconcile(
    weights: Dict[str, float],
    bounds: Dict[str, Tuple[float, float]],
    total: float = 100.0,
    iterations: int = 200,
    tolerance: float = 1e-4,
) -> Dict[str, float]:
    """Scale ``weights`` to sum to ``total`` while honouring per-key bounds.

    Iterative proportional fitting: multiply everything up to the total, clip to
    the bounds, repeat. Converges in a handful of passes for the slot structures
    used here; the residual is then pushed onto whichever key still has slack.
    """
    keys = list(weights.keys())
    if not keys:
        return {}
    lo_sum = sum(bounds[k][0] for k in keys)
    hi_sum = sum(bounds[k][1] for k in keys)
    if lo_sum > total + tolerance or hi_sum < total - tolerance:
        raise ValueError(
            f"infeasible allocation: bounds span [{lo_sum:.2f}, {hi_sum:.2f}] but total is {total:.2f}"
        )
    shares = {k: min(max(weights[k], bounds[k][0]), bounds[k][1]) for k in keys}
    for _ in range(iterations):
        current = sum(shares.values())
        if abs(current - total) <= tolerance:
            break
        if current <= 1e-9:
            break
        factor = total / current
        for k in keys:
            lo, hi = bounds[k]
            shares[k] = min(max(shares[k] * factor, lo), hi)
        residual = total - sum(shares.values())
        if abs(residual) > tolerance:
            # Push the residual onto the key with the most remaining slack.
            room = {k: (bounds[k][1] - shares[k]) if residual > 0 else (shares[k] - bounds[k][0]) for k in keys}
            best = max(room, key=lambda k: room[k])
            if room[best] > 1e-9:
                shares[best] += residual
    return shares


# Which slots can move a particular KPI. Used to turn a target into an intent.
KPI_SLOTS: Dict[str, Tuple[str, ...]] = {
    "protein_g": ("protein_boost", "protein", "dairy"),
    "protein_energy_pct": ("protein_boost", "protein", "dairy"),
    "fibre_g": ("fibre",),
    "sugar_g": ("sweetener", "syrup"),
    "fat_g": ("fat",),
    "satfat_g": ("fat",),
    "sodium_mg": ("salt", "spice_flavour"),
    "energy_kcal": ("fat", "sweetener", "syrup", "inclusion"),
    "water_activity": ("humectant",),
    "consistency_index": ("thickener", "gelling"),
    "shelf_life_days": ("preservative",),
}

_REFERENCE_CACHE: Dict[str, Dict[str, float]] = {}


def _base_intent(category: str) -> Dict[str, float]:
    """The category's own slot targets, untouched by any brief."""
    return {slot.id: float(slot.target) for slot in kb.category(category).slots}


def reference_values(category: str, diet: str = "vegetarian") -> Dict[str, float]:
    """KPI values of the category's *neutral* formulation (no brief at all).

    This is the calibration reference that turns a brief into an intent: a target
    is only meaningful relative to what the category does when nobody asks for
    anything. It is cached, and computed with a brief-free slot fill, so it costs
    one prediction per category.
    """
    key = f"{category}|{diet}"
    if key not in _REFERENCE_CACHE:
        neutral = Brief(
            product_name="reference",
            category=category,
            unit_weight_g=kb.category(category).typical_unit_weight_g,
            diet=diet,
        )
        formulation = _build(
            category, neutral, _base_intent(category), seed=0, version=0, label="reference"
        )
        _REFERENCE_CACHE[key] = dict(engine.predict(formulation, neutral).values)
    return _REFERENCE_CACHE[key]


def _slot_intent(
    category: str,
    brief: Brief,
    references: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Adjust each slot's nominal share to serve the brief.

    The adjustment is driven by the *gap* between the target and the category's
    neutral formulation, not by the target's direction alone. That distinction
    matters for band targets: "sugar 12 g" in a biscuit is a reduction even
    though a one-sided reading treats 12 as a ceiling, and a formula that ignores
    it will never come close. Each gap is applied to the slots that can actually
    move that KPI, at a bounded gain so the seed stays a product rather than a
    numbers exercise.
    """
    refs = references if references is not None else reference_values(category, brief.diet)
    intent = _base_intent(category)
    for target in brief.targets:
        slots = KPI_SLOTS.get(target.id, ())
        if not slots:
            continue
        reference = float(refs.get(target.id, 0.0))
        if abs(reference) < 1e-9:
            continue
        weight = max(target.priority, 0.1)
        if target.direction == "higher":
            # Grow towards the target; if the reference already exceeds it, the
            # category is comfortable and only a light touch is applied.
            gap = (target.target - reference) / max(abs(target.target), 1e-6)
            factor = 1.0 + 0.55 * weight * max(-0.5, min(gap, 1.0))
        elif target.direction == "lower":
            excess = (reference - target.target) / max(abs(reference), 1e-6)
            factor = 1.0 / (1.0 + 0.75 * weight * max(-0.5, min(excess, 1.0)))
        else:
            ratio = target.target / reference
            factor = 1.0 + 0.60 * weight * (max(0.4, min(ratio, 2.0)) - 1.0)
        factor = max(0.45, min(factor, 1.70))
        for slot_id in slots:
            if slot_id in intent:
                intent[slot_id] *= factor
    return intent


def slot_bounds(category: str, brief: Brief) -> Dict[str, Tuple[float, float]]:
    cat = kb.category(category)
    return {slot.id: (float(slot.min), float(slot.max)) for slot in cat.slots}


def slot_plan(
    category: str,
    brief: Brief,
    intent: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Compute the reconciled slot shares for a category and brief."""
    cat = kb.category(category)
    if intent is None:
        intent = _slot_intent(category, brief)
    # Optional slots with no demand are removed entirely so they cannot consume
    # a share; a slot is "demanded" if its nominal target is non-zero.
    active = {slot.id: max(intent.get(slot.id, slot.target), 0.0) for slot in cat.slots}
    active = {k: (v if v > 0.01 else 0.0) for k, v in active.items()}
    required = {slot.id: float(slot.min) for slot in cat.slots if slot.required}
    for slot_id, minimum in required.items():
        active[slot_id] = max(active.get(slot_id, 0.0), minimum)
    bounds = slot_bounds(category, brief)
    weights = {k: (v if v > 0 else 1e-6) for k, v in active.items()}
    return _reconcile(weights, {k: bounds[k] for k in weights}, total=100.0)


def generate(brief: Brief, seed: int = 0, version: int = 1, label: str = "") -> Formulation:
    """Generate an initial formulation for the brief."""
    intent = _slot_intent(brief.category, brief)
    formulation = _build(brief.category, brief, intent, seed=seed, version=version, label=label)
    dose_acidulant(formulation, brief)
    return formulation


def _set_acid_total(formulation: Formulation, total_pct: float) -> None:
    """Scale the acidulant lines so they carry ``total_pct`` between them."""
    table = kb.ingredients()
    lines = [i for i in formulation.items if table.get(i.ingredient_id, None) and table[i.ingredient_id].group == "acidulant"]
    if not lines:
        return
    current = sum(i.pct for i in lines)
    if current <= 1e-9:
        lines[0].pct = total_pct
    else:
        factor = total_pct / current
        for item in lines:
            ing = table[item.ingredient_id]
            ceiling = ing.hard_max_pct if ing.hard_max_pct > 0 else 100.0
            item.pct = min(item.pct * factor, ceiling)
    _repair_total(formulation)


def dose_acidulant(formulation: Formulation, brief: Brief, iterations: int = 16) -> Optional[float]:
    """Dose the acidulant against the pH target by bisection.

    Acid is not a proportional ingredient. Nobody designs a sauce around "5%
    acidulant": they dose vinegar or citric acid until the pH is right, and the
    dose differs by more than an order of magnitude between the two because
    their potencies differ (acetic acid is about 10% dissociated at pH 3.8,
    citric acid over 80%). Treating the acidulant slot as a share is what put 2%
    citric acid - its legal maximum - into a first-pass sauce.

    Returns the dose found, or ``None`` when there is no pH target to dose to.
    """
    target = brief.target("ph")
    table = kb.ingredients()
    lines = [i for i in formulation.items if table.get(i.ingredient_id, None) and table[i.ingredient_id].group == "acidulant"]
    if target is None or not lines:
        return None
    slot = kb.category(formulation.category).slot("acidulant")
    ceiling = float(slot.max) if slot else 100.0
    low, high = 0.0, min(ceiling, sum(i.pct for i in lines) * 2.0 + 1.0)
    best = None
    for _ in range(iterations):
        mid = 0.5 * (low + high)
        _set_acid_total(formulation, mid)
        ph = float(engine.predict(formulation, brief).values.get("ph", 7.0))
        best = mid
        if ph > target.target:
            low = mid
        else:
            high = mid
    _set_acid_total(formulation, best if best is not None else 0.0)
    return best


def _build(
    category: str,
    brief: Brief,
    intent: Dict[str, float],
    seed: int = 0,
    version: int = 1,
    label: str = "",
) -> Formulation:
    """Slot fill for a category under a given slot intent."""
    rng = random.Random(seed)
    cat = kb.category(category)
    weights = _target_weights(brief)
    shares = slot_plan(category, brief, intent=intent)
    items: List[Item] = []
    notes: List[str] = []

    for slot in cat.slots:
        share = shares.get(slot.id, 0.0)
        if share <= 0.02:
            continue
        chosen = _choose(slot, brief, weights, rng, share=share)
        if not chosen:
            notes.append(f"No compliant ingredient available for slot '{slot.label}'.")
            continue
        # Cap by slot share and by each ingredient's hard maximum.
        caps = {ing.id: min(max(ing.hard_max_pct, 0.0), share) for ing in chosen}
        if sum(caps.values()) < share - 1e-6:
            # The slot's own ingredients cannot cover its share; fall back to
            # equal split up to the declared maxima and let the total be repaired.
            caps = {ing.id: max(ing.hard_max_pct, 0.0) for ing in chosen}
        base = {}
        for index, ing in enumerate(chosen):
            rank_weight = 1.0 / (1.0 + 0.55 * index)
            nutrient_bias = 1.0 + 0.02 * max(_utility(ing, slot, brief, weights), 0.0)
            base[ing.id] = max(rank_weight * nutrient_bias, 1e-4)
        floors = {ing.id: 0.0 for ing in chosen}
        for ing in chosen:
            floors[ing.id] = min(max(ing.min_pct, 0.0), caps[ing.id])
        try:
            split = _reconcile(base, {k: (floors[k], caps[k]) for k in base}, total=share)
        except ValueError:
            # Still infeasible (every candidate is capped below the share): fill
            # greedily up to the caps rather than dumping the share on one line,
            # and say so, because the shortfall is a real formulation risk.
            split = _greedy_fill(chosen, share)
            delivered = sum(split.values())
            notes.append(
                f"Slot '{slot.label}' could only be filled to {delivered:.2f}% of its "
                f"target share ({share:.2f}%): every compliant line is capped below the "
                "demand. Consider relaxing an ingredient limit or adding a source."
            )
        for ing in chosen:
            pct = split.get(ing.id, 0.0)
            if pct >= 0.005:
                items.append(Item(ingredient_id=ing.id, pct=round(pct, 4), slot=slot.id))

    formulation = Formulation(
        category=brief.category,
        version=version,
        items=items,
        params=cat.default_params(),
        label=label or f"v{version} generated",
        notes=notes,
    )
    _repair_total(formulation)
    return formulation


def _greedy_fill(chosen: Sequence[kb.Ingredient], share: float) -> Dict[str, float]:
    """Fill a slot share in rank order, respecting each ingredient's ceiling."""
    out: Dict[str, float] = {ing.id: 0.0 for ing in chosen}
    remaining = share
    for ing in chosen:
        if remaining <= 1e-6:
            break
        cap = min(max(ing.hard_max_pct, 0.0), share)
        take = min(cap, remaining)
        out[ing.id] = take
        remaining -= take
    return out


def _repair_total(formulation: Formulation) -> None:
    """Nudge the formulation so its lines sum to exactly 100%."""
    total = formulation.total_pct
    if abs(total - 100.0) < 0.02:
        return
    delta = 100.0 - total
    # Prefer to absorb the correction in water, then in the largest line whose
    # declared maximum still has room.
    water = next((i for i in formulation.items if i.slot == "water"), None)
    if water is not None:
        ing = kb.ingredients().get(water.ingredient_id)
        ceiling = ing.hard_max_pct if ing else 100.0
        new_value = min(max(water.pct + delta, 0.0), ceiling)
        delta = delta - (new_value - water.pct)
        water.pct = new_value
        if abs(delta) < 0.02:
            return
    ordered = sorted(formulation.items, key=lambda i: -i.pct)
    for item in ordered:
        ing = kb.ingredients().get(item.ingredient_id)
        ceiling = ing.hard_max_pct if ing else 100.0
        if delta > 0:
            room = max(ceiling - item.pct, 0.0)
            if room <= 0:
                continue
            step = min(room, delta)
            item.pct += step
            delta -= step
        else:
            step = min(item.pct, -delta)
            item.pct -= step
            delta += step
        if abs(delta) < 0.02:
            return


# --------------------------------------------------------------------------- #
# Constraint checking
# --------------------------------------------------------------------------- #
def check(formulation: Formulation, brief: Brief) -> ConstraintReport:
    """Check a formulation against the rule set and the brief, with reasons."""
    cat = kb.category(formulation.category)
    table = kb.ingredients()
    hard: List[str] = []
    soft: List[str] = []
    diet_conflicts: List[str] = []
    allergen_conflicts: List[str] = []
    slot_totals: Dict[str, float] = {}
    ingredient_count = 0

    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        if ing is None:
            hard.append(f"Unknown ingredient '{item.ingredient_id}' (not in the knowledge base).")
            continue
        ingredient_count += 1
        slot_totals[item.slot] = slot_totals.get(item.slot, 0.0) + item.pct
        if not kb.diet_allowed(ing, brief.diet):
            diet_conflicts.append(f"{ing.name} is {ing.diet}, but the brief requires {brief.diet}.")
        forbidden = sorted(set(ing.allergens) & set(brief.allergens_to_avoid))
        if forbidden:
            labels = [kb.allergen_labels().get(a, a) for a in forbidden]
            allergen_conflicts.append(f"{ing.name} declares {'/'.join(labels)}, which the brief excludes.")
        if ing.hard_max_pct > 0 and item.pct > ing.hard_max_pct + 1e-6:
            hard.append(
                f"{ing.name} at {item.pct:.2f}% exceeds its permitted maximum of {ing.hard_max_pct:.2f}%."
            )
        elif ing.hard_max_pct > 0 and item.pct > 0.95 * ing.hard_max_pct:
            soft.append(
                f"{ing.name} at {item.pct:.2f}% is within 5% of its permitted maximum "
                f"({ing.hard_max_pct:.2f}%) - no headroom left for reformulation."
            )
        if ing.min_pct > 0 and item.pct < ing.min_pct - 1e-6:
            soft.append(f"{ing.name} at {item.pct:.2f}% is below its usual minimum of {ing.min_pct:.2f}%.")

    total = formulation.total_pct
    if abs(total - 100.0) > 0.5:
        hard.append(f"Formulation totals {total:.2f}% instead of 100.00%.")

    slot_rows: List[Dict[str, Any]] = []
    for slot in cat.slots:
        value = slot_totals.get(slot.id, 0.0)
        status = "ok"
        if value <= 0.005 and slot.required:
            status = "missing"
            hard.append(f"Required slot '{slot.label}' is empty.")
        elif value > slot.max + 1e-6:
            status = "over"
            hard.append(f"Slot '{slot.label}' at {value:.2f}% exceeds its maximum of {slot.max:.2f}%.")
        elif value and value < slot.min - 1e-6:
            status = "under"
            soft.append(f"Slot '{slot.label}' at {value:.2f}% is below its usual minimum of {slot.min:.2f}%.")
        slot_rows.append(
            {
                "slot": slot.id,
                "label": slot.label,
                "pct": round(value, 3),
                "target": slot.target,
                "min": slot.min,
                "max": slot.max,
                "status": status,
                "required": slot.required,
                "lines": sum(1 for i in formulation.items if i.slot == slot.id),
            }
        )

    comp = nutrition.analyse(formulation, final_moisture=5.0)
    claim_status = nutrition.claims_met(comp, brief.claims)
    for claim_id, met in claim_status.items():
        label = next((rule["label"] for rule in kb.claim_rules() if rule["id"] == claim_id), claim_id)
        if not met:
            soft.append(
                f"Declared claim '{label}' is not currently met by the formulation "
                "(it is checked again after prediction, on final-product composition)."
            )

    cost_data = nutrition_cost(formulation, brief)
    cost = float(cost_data)
    ceiling = brief.cost_ceiling_inr_kg
    cost_ok = True
    if ceiling:
        cost_ok = cost <= ceiling
        if not cost_ok:
            hard.append(f"Ingredient cost INR {cost:.1f}/kg exceeds the brief's ceiling of INR {ceiling:.1f}/kg.")

    checks = {
        "totals_100": abs(total - 100.0) <= 0.5,
        "diet": not diet_conflicts,
        "allergens": not allergen_conflicts,
        "slots": all(row["status"] in ("ok", "under") for row in slot_rows),
        "ingredient_limits": not any("exceeds its permitted maximum" in h for h in hard),
        "cost_ceiling": cost_ok,
    }
    report = ConstraintReport(
        ok=not hard,
        total_pct=total,
        hard_violations=hard,
        soft_violations=soft,
        slots=slot_rows,
        diet_conflicts=diet_conflicts,
        allergen_conflicts=allergen_conflicts,
        cost_inr_kg=cost,
        cost_ceiling_inr_kg=ceiling,
        claim_status=claim_status,
        checks=checks,
        ingredient_count=ingredient_count,
    )
    return report


def conflicts(
    brief: Brief,
    formulation: Formulation,
    result,
    levers: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """Explain every target this formulation cannot reach, and why.

    A development brief can be internally inconsistent - "sugar 18 g and nine
    months ambient shelf life" in a sauce, "15 g protein and 5 g fibre from a
    ragi base". The useful answer is not a formulation that misses quietly; it is
    the number that is actually achievable, the levers that control it, and
    whether those levers are already at their limits. That is what this returns,
    and it is what turns a failed optimisation into a decision for the team.
    """
    from . import kpi as kpi_registry  # local: keeps the module import graph flat

    table = kb.ingredients()
    rows: List[Dict[str, Any]] = []
    for target in brief.targets:
        value = result.values.get(target.id)
        if value is None:
            continue
        score = kpi_registry.desirability(target, float(value))
        if kpi_registry.is_on_target(target, float(value)):
            continue
        entry: Dict[str, Any] = {
            "kpi": target.id,
            "label": target.label,
            "unit": target.unit,
            "target": target.target,
            "tolerance": target.tolerance,
            "direction": target.direction,
            "achieved": round(float(value), 3),
            "desirability": round(score, 3),
            "hard": target.hard,
            "blockers": [],
            "recommendation": "",
        }
        # Which ingredient or parameter controls this KPI, and is it already at a limit?
        if levers:
            base = float(value)
            sensitivities: List[Tuple[float, str, str, bool]] = []
            for lever in levers:
                try:
                    moved, realised = optimize_module()._apply_move(
                        formulation, lever, 0.5 * lever.step
                    )
                except Exception:
                    continue
                if abs(realised) < 1e-9:
                    continue
                changed = float(engine.predict(moved, brief).values.get(target.id, base))
                sensitivity = (changed - base) / realised
                at_limit = abs(lever.current - lever.hi) < 1e-6 or abs(lever.current - lever.lo) < 1e-6
                sensitivities.append((abs(sensitivity), lever.label or lever.key, lever.unit, at_limit))
            sensitivities.sort(key=lambda row: -row[0])
            for _, name, unit, at_limit in sensitivities[:3]:
                entry["blockers"].append(
                    {"lever": name, "unit": unit, "at_limit": bool(at_limit)}
                )
        blockers = entry["blockers"]
        if blockers and all(b["at_limit"] for b in blockers):
            entry["recommendation"] = (
                "Every effective lever for this KPI is already at its limit. Relax the "
                "target, relax an ingredient maximum, or accept the nearest achievable value."
            )
        elif blockers:
            names = ", ".join(b["lever"] for b in blockers if not b["at_limit"])
            entry["recommendation"] = (
                f"Still controllable through {names or 'the levers listed'}; the target is "
                "being traded against another target that is currently winning."
            )
        else:
            entry["recommendation"] = (
                "No lever moves this KPI: it is set by the category, the process envelope "
                "or the target set itself."
            )
        rows.append(entry)
    return rows


def optimize_module():
    """Access the optimiser without a module-level circular import."""
    from . import optimize

    return optimize


def nutrition_cost(formulation: Formulation, brief: Optional[Brief] = None) -> float:
    """Ingredient cost only, in INR per kg of mix (no process or packaging)."""
    total = 0.0
    for item in formulation.items:
        ing = kb.ingredients().get(item.ingredient_id)
        if ing is None:
            continue
        total += (item.pct / 100.0) * ing.cost_inr_kg
    return round(total, 3)


def generate_optimised(
    brief: Brief,
    seed: int = 0,
    version: int = 1,
    budget: int = 1600,
    predictor=None,
    label: str = "",
):
    """Generate the slot-fill seed, then move it onto the brief's targets.

    The optimiser is imported inside the function because
    :mod:`app.core.optimize` depends on the ranking helpers in this module; a
    module-level import would be circular. The split of responsibility is the
    point: this module decides what a *plausible* formula looks like, the
    optimiser decides how far to move it to hit the numbers.
    """
    from . import optimize

    formulation = generate(brief, seed=seed, version=version, label=label)
    result = optimize.optimise(formulation, brief, predictor=predictor, budget=budget)
    return result


def explain(formulation: Formulation, brief: Brief) -> List[str]:
    """Human-readable reasons for the main compositional choices."""
    reasons: List[str] = []
    table = kb.ingredients()
    weights = _target_weights(brief)
    cat = kb.category(formulation.category)
    for slot in cat.slots:
        lines = [i for i in formulation.items if i.slot == slot.id]
        if not lines:
            continue
        parts = []
        for item in sorted(lines, key=lambda i: -i.pct):
            ing = table.get(item.ingredient_id)
            if ing is None:
                continue
            parts.append(f"{ing.name} {item.pct:.1f}%")
        reasons.append(f"{slot.label} ({slot.id}): " + ", ".join(parts))
    if brief.claims:
        labels = [rule["label"] for rule in kb.claim_rules() if rule["id"] in brief.claims]
        reasons.append("Claims driving the composition: " + ", ".join(labels) + ".")
    if brief.allergens_to_avoid:
        labels = [kb.allergen_labels().get(a, a) for a in brief.allergens_to_avoid]
        reasons.append("Excluded by allergen constraint: " + ", ".join(labels) + ".")
    hydro = formulation.pct_of("xanthan_gum") + formulation.pct_of("cmc") + formulation.pct_of("pectin_lm")
    if hydro > 0.2:
        reasons.append(
            "A hydrocolloid is included for water binding and structure: it raises the "
            "consistency index and slows moisture migration during storage."
        )
    if formulation.pct_of("salt") > 1.4 and "sodium_mg" in weights:
        reasons.append("Salt is held near the low end of the category range to protect the sodium target.")
    return reasons
