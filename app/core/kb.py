"""Knowledge base and shared domain types.

Loads the ingredient composition table, the category/process library and the
regulatory constraint reference, and exposes them as simple typed objects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Fallback functional contributions by ingredient group, used when an
# ingredient record does not override a particular contribution.
GROUP_FX_DEFAULTS: Dict[str, Dict[str, float]] = {
    "grain": {"structure": 0.5, "starch": 0.85},
    "protein": {"protein_effect": 0.6, "structure": 0.35},
    "fat": {"fat_effect": 0.9},
    "sweetener": {"sweet": 0.9, "humectant": 0.3},
    "humectant": {"humectant": 0.9, "water_binding": 0.3},
    "hydrocolloid": {"water_binding": 0.9, "structure": 0.4},
    "leavening": {"leaven": 0.9},
    "emulsifier": {"emulsifier": 0.9},
    "acidulant": {"acid": 0.8},
    "preservative": {"preservative": 0.7},
    "salt_mineral": {"salt": 0.8},
    "colour_flavour": {"flavour": 0.7, "colour": 0.5},
    "fruit": {"flavour": 0.7, "water_binding": 0.2},
    "dairy": {"flavour": 0.4, "protein_effect": 0.4},
    "nut_seed": {"flavour": 0.5, "fat_effect": 0.4, "protein_effect": 0.3},
    "fibre": {"water_binding": 0.6, "structure": 0.4},
    "water": {},
    "fortificant": {},
}

FX_KEYS = (
    "sweet",
    "humectant",
    "water_binding",
    "structure",
    "starch",
    "gluten",
    "fat_effect",
    "protein_effect",
    "leaven",
    "emulsifier",
    "acid",
    "salt",
    "buffering",
    "preservative",
    "antioxidant",
    "colour",
    "flavour",
    "oxid_risk",
)


@dataclass(frozen=True)
class Ingredient:
    id: str
    name: str
    group: str
    kcal: float
    protein: float
    fat: float
    satfat: float
    carb: float
    sugar: float
    fibre: float
    sodium_mg: float
    moisture: float
    aw: float
    cost_inr_kg: float
    min_pct: float
    max_pct: float
    allergens: tuple
    diet: str
    density: float
    fx: Dict[str, float] = field(default_factory=dict)
    molar_mass: Optional[float] = None
    pka: Optional[float] = None
    proton_equivalents: Optional[float] = None
    umax_pct: Optional[float] = None

    def effect(self, key: str) -> float:
        """Functional contribution, falling back to the group default."""
        if key in self.fx:
            return float(self.fx[key])
        return float(GROUP_FX_DEFAULTS.get(self.group, {}).get(key, 0.0))

    @property
    def is_acidulant(self) -> bool:
        return self.group == "acidulant" and self.molar_mass is not None

    @property
    def hard_max_pct(self) -> float:
        """Maximum usable inclusion: ingredient specific umax if declared."""
        return float(self.umax_pct) if self.umax_pct is not None else float(self.max_pct)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "kcal": self.kcal,
            "protein": self.protein,
            "fat": self.fat,
            "satfat": self.satfat,
            "carb": self.carb,
            "sugar": self.sugar,
            "fibre": self.fibre,
            "sodium_mg": self.sodium_mg,
            "moisture": self.moisture,
            "aw": self.aw,
            "cost_inr_kg": self.cost_inr_kg,
            "min_pct": self.min_pct,
            "max_pct": self.max_pct,
            "allergens": list(self.allergens),
            "diet": self.diet,
            "density": self.density,
        }


@dataclass(frozen=True)
class Slot:
    id: str
    label: str
    groups: tuple
    target: float
    min: float
    max: float
    count: int
    required: bool


@dataclass(frozen=True)
class Parameter:
    id: str
    label: str
    unit: str
    min: float
    max: float
    default: float


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    typical_moisture_pct: tuple
    typical_aw: tuple
    typical_unit_weight_g: float
    slots: tuple
    parameters: tuple
    unit_operations: tuple
    #: The unit this category's pack size is stated in: "g", or "ml" for a product
    #: sold by volume. A drink is bottled in millilitres, so the brief, the interface
    #: and the process sheet say millilitres for it; everything else is by weight.
    pack_unit: str = "g"

    def slot(self, slot_id: str) -> Optional[Slot]:
        for slot in self.slots:
            if slot.id == slot_id:
                return slot
        return None

    def param(self, param_id: str) -> Optional[Parameter]:
        for param in self.parameters:
            if param.id == param_id:
                return param
        return None

    def default_params(self) -> Dict[str, float]:
        return {p.id: p.default for p in self.parameters}

    def clamp_params(self, params: Dict[str, float]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for p in self.parameters:
            value = float(params.get(p.id, p.default))
            out[p.id] = min(max(value, p.min), p.max)
        return out


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pack_unit(record: Dict[str, Any]) -> str:
    """The unit the category's pack size is stated in.

    Only the categories that sell by volume declare this; the rest are weighed, so
    anything unrecognised falls back to grams rather than labelling a cookie in
    millilitres.
    """
    unit = str(record.get("pack_unit") or "g").strip().lower()
    return unit if unit in ("g", "ml") else "g"


@lru_cache(maxsize=1)
def _load_json(name: str) -> Dict[str, Any]:
    with open(DATA_DIR / name, "r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def ingredients() -> Dict[str, Ingredient]:
    raw = _load_json("ingredients.json")
    out: Dict[str, Ingredient] = {}
    for record in raw["ingredients"]:
        ing = Ingredient(
            id=record["id"],
            name=record["name"],
            group=record["g"],
            kcal=_to_float(record.get("kcal")),
            protein=_to_float(record.get("p")),
            fat=_to_float(record.get("f")),
            satfat=_to_float(record.get("sf")),
            carb=_to_float(record.get("c")),
            sugar=_to_float(record.get("su")),
            fibre=_to_float(record.get("fb")),
            sodium_mg=_to_float(record.get("na")),
            moisture=_to_float(record.get("h2o")),
            aw=_to_float(record.get("aw"), 0.5),
            cost_inr_kg=_to_float(record.get("cost")),
            min_pct=_to_float(record.get("min")),
            max_pct=_to_float(record.get("max")),
            allergens=tuple(record.get("alg") or ()),
            diet=record.get("diet", "vegan"),
            density=_to_float(record.get("dens"), 0.6),
            fx={k: float(v) for k, v in (record.get("fx") or {}).items()},
            molar_mass=record.get("mw"),
            pka=record.get("pka"),
            proton_equivalents=record.get("neq"),
            umax_pct=record.get("umax"),
        )
        out[ing.id] = ing
    return out


def ingredient(ingredient_id: str) -> Ingredient:
    table = ingredients()
    if ingredient_id not in table:
        raise KeyError(f"Unknown ingredient: {ingredient_id}")
    return table[ingredient_id]


def by_group(*groups: str) -> List[Ingredient]:
    table = ingredients()
    wanted = set(groups)
    return [i for i in table.values() if i.group in wanted]


@lru_cache(maxsize=1)
def categories() -> Dict[str, Category]:
    raw = _load_json("processes.json")
    out: Dict[str, Category] = {}
    for cat_id, record in raw["categories"].items():
        out[cat_id] = Category(
            id=cat_id,
            label=record["label"],
            typical_moisture_pct=tuple(record["typical_moisture_pct"]),
            typical_aw=tuple(record["typical_aw"]),
            typical_unit_weight_g=float(record["typical_unit_weight_g"]),
            slots=tuple(
                Slot(
                    id=s["id"],
                    label=s["label"],
                    groups=tuple(s["groups"]),
                    target=float(s["target"]),
                    min=float(s["min"]),
                    max=float(s["max"]),
                    count=int(s.get("count", 1)),
                    required=bool(s.get("required", False)),
                )
                for s in record["slots"]
            ),
            parameters=tuple(
                Parameter(
                    id=p["id"],
                    label=p["label"],
                    unit=p["unit"],
                    min=float(p["min"]),
                    max=float(p["max"]),
                    default=float(p["default"]),
                )
                for p in record["parameters"]
            ),
            unit_operations=tuple(record["unit_operations"]),
            pack_unit=_pack_unit(record),
        )
    return out


def category(category_id: str) -> Category:
    table = categories()
    if category_id not in table:
        raise KeyError(f"Unknown category: {category_id}")
    return table[category_id]


@lru_cache(maxsize=1)
def limits() -> Dict[str, Any]:
    return _load_json("limits.json")


def claim_rules() -> List[Dict[str, Any]]:
    return limits()["claims"]


def allergen_labels() -> Dict[str, str]:
    return {a["id"]: a["label"] for a in limits()["allergens"]}


def diet_allowed(ing: Ingredient, diet: str) -> bool:
    if diet == "vegan":
        return ing.diet == "vegan"
    if diet == "vegetarian":
        return ing.diet in ("vegan", "vegetarian")
    return True


def summarise_kb() -> Dict[str, Any]:
    table = ingredients()
    groups: Dict[str, int] = {}
    for ing in table.values():
        groups[ing.group] = groups.get(ing.group, 0) + 1
    return {
        "ingredients": len(table),
        "groups": dict(sorted(groups.items())),
        "categories": [c.label for c in categories().values()],
        "claims": [c["label"] for c in claim_rules()],
    }


def all_ingredient_ids() -> Iterable[str]:
    return ingredients().keys()
