"""Brief understanding: turning a target product into structured, testable targets.

The agent has to understand *what it is being asked to build* before it can build
anything. That arrives in three forms:

1. a free-text specification (marketing brief, customer e-mail, spec sheet),
2. one or more reference images of the target product,
3. explicit structured values from the UI.

This module normalises all three into a single :class:`~app.core.types.Brief`
holding the product identity, the dietary/allergen envelope, the claims being
aimed at, and a list of :class:`~app.core.types.KpiTarget` rows.

Two design choices matter:

* **Deterministic and offline first.** Unit-aware regular expressions pull numbers
  out of specifications; keyword tables infer category, diet and allergens; the
  claim rules stored in the knowledge base decide which claims the product is
  chasing. No network call is required, so the understanding step is reproducible
  and auditable - which matters because everything downstream inherits it.
* **Unknowns are explicit.** Whatever the parser cannot infer becomes an open
  question on the brief. The UI shows those questions and the agent counts them
  against its own confidence: a brief with unanswered questions is a brief that
  will cost a physical trial later.

Category-typical default targets live in :data:`DEFAULT_TARGETS`. They are the
band a development team would normally start from for that product class; a
specification can override any of them, and a claim tightens the relevant one.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import kb, kpi as kpi_registry
from .types import Brief, KpiTarget

# --------------------------------------------------------------------------- #
# Category inference
# --------------------------------------------------------------------------- #
CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cookie": (
        "cookie", "biscuit", "cracker", "wafer", "rusk", "shortbread", "snap",
        "tea biscuit", "digestive",
    ),
    "spread": (
        "spread", "jam", "jelly", "marmalade", "preserve", "fruit butter",
        "conserve", "honey spread", "choco spread", "peanut butter",
    ),
    "drymix": (
        "dry mix", "drymix", "porridge", "instant mix", "instant", "premix",
        "beverage powder", "malt drink", "health mix", "ready mix", "powder mix",
        "soup mix", "idli mix", "dosa mix", "upma mix",
    ),
    "extruded_snack": (
        "extruded", "puff", "puffed", "pellet", "namkeen", "kurkure", "chips",
        "rings", "snack pellet", "fryum", "extrudate",
    ),
    "bar": (
        "bar", "energy bar", "nutrition bar", "cereal bar", "granola", "protein bar",
        "fruit bar", "breakfast bar", "muesli bar",
    ),
    "sauce": (
        "sauce", "chutney", "ketchup", "dip", "paste gravy", "gravy", "relish",
        "mayonnaise", "thokku", "pickle",
    ),
    "beverage": (
        "ready to drink", "ready-to-drink", "rtd", "protein shake", "milkshake",
        "smoothie", "liquid protein", "protein water", "protein beverage",
        "protein drink", "bottled drink", "whey beverage", "lassi", "buttermilk",
    ),
}

DIET_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("vegan", ("vegan", "plant based", "plant-based", "no dairy", "dairy free", "dairy-free")),
    ("vegetarian", ("vegetarian", "veg only", "eggless", "no egg", "egg free", "egg-free", "lacto")),
)

ALLERGEN_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "gluten": ("gluten", "wheat", "maida", "atta", "semolina", "suji", "barley", "rye"),
    "milk": ("milk", "dairy", "whey", "casein", "lactose", "ghee", "butter", "paneer", "curd"),
    "egg": ("egg", "albumen", "ovalbumin"),
    "soy": ("soy", "soya", "soybean"),
    "peanut": ("peanut", "groundnut", "moongphali"),
    "tree_nuts": ("almond", "cashew", "pistachio", "walnut", "hazelnut", "tree nut"),
    "sesame": ("sesame", "til", "gingelly"),
    "mustard": ("mustard", "sarson", "rai"),
}

CLAIM_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "high_protein": ("high protein", "high-protein", "protein rich", "protein-rich"),
    "source_protein": ("source of protein", "protein source"),
    "high_fibre": ("high fibre", "high fiber", "high-fibre", "high-fiber", "fibre rich", "fiber rich"),
    "source_fibre": ("source of fibre", "source of fiber"),
    # "reduced sugar" belongs to the reduced_sugar claim below. Listing it here as
    # well meant every reduced-sugar brief was also read as a *low sugar* brief,
    # which dragged the sugar target from 40 g to 5 g and made the case unsolvable.
    "low_sugar": ("low sugar", "less sugar", "no added sugar"),
    "sugar_free": ("sugar free", "sugar-free", "zero sugar"),
    "reduced_sugar": ("reduced sugar", "25% less sugar"),
    "low_sodium": ("low sodium", "low salt", "no added salt", "salt free"),
    "low_fat": ("low fat", "low-fat", "fat free"),
    "high_energy": ("high energy", "energy dense", "calorie dense"),
}

# --------------------------------------------------------------------------- #
# Numeric specification patterns
# --------------------------------------------------------------------------- #
# The gap between a nutrient name and its number is what makes this parser safe.
# An unbounded gap such as ``protein[^\d]{0,25}?(\d+)`` reads "High protein high
# fibre ragi cookie, 40 g pack" as *protein 40 g*, which silently poisons every
# target downstream. The gap below therefore admits only whitespace, separators
# and a short list of spec-speak words, so a nutrient name must be followed by
# its own value.
_GAP = (
    r"(?:"
    r"\s|[:=()[\]{}]|>|<|~|\u2264|\u2265|\u00b1|,|;|\u2013|-|\."
    r"|content|level|target|targeted|value|of|at|is|are|approx\.?|approximately|about|around"
    r"|min|max|minimum|maximum|not|less|more|than|up|to|and|the|per"
    r"){0,24}?"
)

# A number must not be followed by a *container*, which is how the pattern used to
# read "40 g pack" as protein. It must be allowed to be followed by "per 100 g"
# though: that is the standard way every Indian nutrition specification states a
# value, and blocking it silently dropped protein, sugars and fibre from the brief.
_UNIT_LOOKAHEAD = r"(?!\s*(?:pack|pouch|sachet|bottle|jar|box|tin|carton|bar\b|pieces?|biscuits?|servings?|units?))"

# A number is a number, not "0.90." - the greedy ``[\d.]+`` used to swallow the
# sentence-ending full stop, ``float()`` then failed and the KPI was dropped from
# the brief without a word.
_NUMBER = r"(\d+(?:\.\d+)?)"


def _pat(name: str, unit: str) -> str:
    return name + _GAP + _NUMBER + r"\s*(?:" + unit + r")" + _UNIT_LOOKAHEAD


_NUM_PATTERNS: Dict[str, List[str]] = {
    "energy_kcal": [_pat(r"energy", r"kcal\b|cal/100|kilocalorie")],
    "protein_g": [_pat(r"protein", r"g\b|gram|%")],
    "fat_g": [_pat(r"(?:total\s*)?fat", r"g\b|gram|%")],
    "satfat_g": [_pat(r"saturat\w*", r"g\b|gram|%")],
    "carb_g": [_pat(r"carbohydrate", r"g\b|gram|%")],
    "sugar_g": [_pat(r"(?:total\s*)?sugars?", r"g\b|gram|%")],
    "fibre_g": [_pat(r"(?:dietary\s*)?fib(?:re|er)", r"g\b|gram|%")],
    "sodium_mg": [_pat(r"sodium", r"mg\b|milligram")],
    "moisture_pct": [_pat(r"moisture", r"%|percent|pct")],
    "water_activity": [_pat(r"(?:water\s*activity|aw)", r"\b")],
    "ph": [_pat(r"\bph\b", r"\b")],
    "texture_index": [_pat(r"texture", r"\b|index")],
    "consistency_index": [_pat(r"consistency", r"\b|index")],
    "cost_inr_kg": [
        r"(?:cost|price|costing)"
        + _GAP
        + r"(?:inr|rs\.?|rupees|\u20b9)?\s*([\d.]+)\s*(?:/\s*kg|per\s*kg)",
        r"(?:inr|rs\.?|\u20b9)\s*([\d.]+)\s*(?:/\s*kg|per\s*kg)",
    ],
    "cost_inr_unit": [
        r"(?:cost|price|mrp)"
        + _GAP
        + r"(?:inr|rs\.?|\u20b9)?\s*([\d.]+)\s*(?:/\s*(?:unit|pack|piece|bar|pouch)|per\s*(?:unit|pack))",
    ],
}

_MONTH_PATTERN = (
    r"shelf\s*life" + _GAP + r"([\d.]+)\s*(month|months|mo|year|years|day|days)"
)
# The pack size, with the unit it was declared in. A beverage is sold by volume and
# everything else by weight, so the unit is carried out of the parse rather than
# assumed: it is what the brief, the interface and the process sheet have to print.
# Millilitres and grams are the same number at a density near 1 g/ml, which is the
# convention the target maths uses, and the nutrition engine applies the real
# density when it reports per 100 ml.
_PACK_SIZE_PATTERNS = [
    r"(?:unit|net|pack|piece|serving|bar|biscuit)\s*weight"
    + _GAP
    + r"([\d.]+)\s*(kg|g|gram|ml|litre|liter|l)\b",
    r"([\d.]+)\s*(kg)\s*(?:pack|pouch|box|unit)",
    r"per\s*(?:unit|piece|pack|bar)" + _GAP + r"([\d.]+)\s*(g|gram|ml)\b",
    r"([\d.]+)\s*(g|gram)\s*(?:pack|pouch|unit|bar|piece|serving)",
    r"([\d.]+)\s*(ml|litre|liter|l)\s*(?:bottle|can|tetra|carton|jar|pack|pouch)",
    r"(?:unit|net|pack|serving|bottle|can|jar|carton)\s*(?:weight|volume)"
    + _GAP
    + r"([\d.]+)\s*(ml|litre|liter|kg|g|gram)",
]

# Nutrient figures are quoted per 100 g by convention, but a supplement is
# almost always quoted per serving ("20 g protein per bottle"). A per-serving
# number has to be converted before it can be compared with the per-100 g model.
_PER_SERVING_BASIS = re.compile(
    r"(?:per|/)\s*(?:serving|bottle|pack|pouch|bar|unit|piece|can|carton|tetra|jar|sachet)\b",
    re.IGNORECASE,
)
_PER_100_BASIS = re.compile(r"per\s*(?:100\s*(?:ml|g)|100ml|100g)\b", re.IGNORECASE)
_SERVING_CONVERTIBLE = frozenset(
    {"energy_kcal", "protein_g", "fat_g", "satfat_g", "carb_g", "sugar_g", "fibre_g", "sodium_mg"}
)

# Comparators appear between the nutrient name and its number - "sugar <= 12 g",
# "aw not more than 0.90" - so they are read out of the matched text itself.
# Looking only *before* the nutrient name misses every one of them.
_COMPARATOR = re.compile(
    r"(<\s*=|>\s*=|<=|<|>=|>|max(?:imum)?|min(?:imum)?|up to|at least|at most"
    r"|not (?:more|less) than|no more than|less than|more than|under|below|over|above)",
    re.IGNORECASE,
)


def _first_number(text: str, patterns: Iterable[str]) -> Optional[float]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _declared_pack_size(text: str) -> Optional[Tuple[float, str]]:
    """The declared pack size as ``(value, unit)``, where the unit is "g" or "ml".

    A beverage is sized in millilitres, not grams, and the unit leaves this function
    with the number: the brief, the interface and the process sheet print what the
    pack says. Kilograms and litres are converted, so ``1 kg pouch`` arrives as 1000
    g and ``1 litre bottle`` as 1000 ml.
    """
    for pattern in _PACK_SIZE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        unit = match.group(2).strip().lower()
        if unit.startswith("k"):
            return value * 1000.0, "g"
        if unit.startswith("l"):
            return value * 1000.0, "ml"
        return value, "ml" if unit == "ml" else "g"
    return None


def _comparator_in(window: str) -> str:
    found = _COMPARATOR.search(window)
    if not found:
        return ""
    token = found.group(1).lower().strip()
    if token in (
        "<=", "<", "max", "maximum", "up to", "at most",
        "not more than", "no more than", "less than", "under", "below",
    ):
        return "<="
    if token in (">=", ">", "min", "minimum", "at least", "more than", "over", "above"):
        return ">="
    return ""


def parse_spec_numbers(text: str) -> Dict[str, Dict[str, Any]]:
    """Extract every numeric specification the text declares.

    Returns ``{kpi_id: {"value": float, "comparator": str}}``. The comparator is
    kept because ``sugar <= 8 g`` and ``sugar 8 g`` mean different things to a
    developer: the first is a ceiling, the second is a point target.

    A figure quoted per serving is recalculated onto the per-100 g basis the
    models use, and the conversion is recorded on the row so the brief can show
    the original declaration alongside the derived target.
    """
    out: Dict[str, Dict[str, Any]] = {}
    pack = _declared_pack_size(text)
    # A pack declared in millilitres is read as grams for the per-serving conversion
    # below: the two are the same number at a density near 1 g/ml, and the category's
    # own unit is applied to the brief once the category is known.
    unit_weight = pack[0] if pack else None
    for kpi_id, patterns in _NUM_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            window = match.group(0)
            # The basis word ("per bottle", "per 100 ml") sits just after the
            # number, so a short tail of the text is part of the window.
            basis_text = window + " " + text[match.end(): match.end() + 28]
            converted_from = None
            if kpi_id in _SERVING_CONVERTIBLE and unit_weight and unit_weight > 0:
                serving = _PER_SERVING_BASIS.search(basis_text)
                per100 = _PER_100_BASIS.search(basis_text)
                if serving and (per100 is None or serving.start() <= per100.start()):
                    converted_from = value
                    value = value * 100.0 / unit_weight
            row: Dict[str, Any] = {
                "value": value,
                "comparator": _comparator_in(window),
                "evidence": window.strip(),
            }
            if converted_from is not None:
                row["per_serving"] = converted_from
                row["basis"] = "per serving"
                row["evidence"] = (
                    f"{window.strip()} (declared per serving; recalculated to per 100 g "
                    f"using a {unit_weight:g} g serving)"
                )
            out[kpi_id] = row
            break

    shelf = re.search(_MONTH_PATTERN, text, re.IGNORECASE)
    if shelf:
        value = float(shelf.group(1))
        unit = shelf.group(2).lower()
        if unit.startswith("month") or unit == "mo":
            value *= 30.0
        elif unit.startswith("year"):
            value *= 365.0
        out["shelf_life_days"] = {"value": value, "comparator": "", "evidence": shelf.group(0).strip()}

    if pack:
        size, unit = pack
        out["__pack_size"] = {
            "value": size,
            "unit": unit,
            "comparator": "",
            "evidence": "declared net volume" if unit == "ml" else "declared net weight",
        }
    return out


def infer_category(text: str, fallback: str = "cookie") -> Tuple[str, float, List[str]]:
    """Infer the product category, returning the label, a confidence and the hits."""
    lowered = text.lower()
    scores: Dict[str, float] = {}
    hits: Dict[str, List[str]] = {}
    for category_id, keywords in CATEGORY_KEYWORDS.items():
        score = 0.0
        found: List[str] = []
        for keyword in keywords:
            if keyword in lowered:
                # Longer keywords are stronger evidence: "extruded snack" beats "snack".
                score += 1.0 + 0.25 * len(keyword.split())
                found.append(keyword)
        if score:
            scores[category_id] = score
            hits[category_id] = found
    if not scores:
        return fallback, 0.35, []
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    confidence = round(scores[best] / total, 3)
    if best not in kb.categories():
        return fallback, confidence, hits.get(best, [])
    return best, confidence, hits.get(best, [])


def infer_diet(text: str, fallback: str = "vegetarian") -> str:
    lowered = text.lower()
    for diet, keywords in DIET_PATTERNS:
        if any(keyword in lowered for keyword in keywords):
            return diet
    return fallback


def allergens_in_text(text: str) -> List[str]:
    """Allergens the specification *avoids*, e.g. 'gluten free, no soy'."""
    lowered = text.lower()
    avoided: List[str] = []
    for allergen_id, keywords in ALLERGEN_KEYWORDS.items():
        for keyword in keywords:
            for negator in (f"free from {keyword}", f"no {keyword}", f"without {keyword}", f"{keyword} free", f"{keyword}-free", f"avoid {keyword}"):
                if negator in lowered and allergen_id not in avoided:
                    avoided.append(allergen_id)
    return avoided


def claims_in_text(text: str) -> List[str]:
    lowered = text.lower()
    valid = {rule["id"] for rule in kb.claim_rules()}
    found: List[str] = []
    for claim_id, keywords in CLAIM_KEYWORDS.items():
        if claim_id not in valid:
            continue
        if any(keyword in lowered for keyword in keywords):
            if claim_id == "low_sugar" and any(k in lowered for k in ("sugar free", "sugar-free", "zero sugar")):
                continue
            found.append(claim_id)
    return found


# --------------------------------------------------------------------------- #
# Default targets per category
# --------------------------------------------------------------------------- #
# (kpi_id, target, tolerance, priority, hard)
DEFAULT_TARGETS: Dict[str, List[Tuple[str, float, float, float, bool]]] = {
    "cookie": [
        ("energy_kcal", 470.0, 40.0, 1.0, True),
        ("protein_g", 10.0, 1.2, 1.3, True),
        ("fibre_g", 6.0, 1.0, 1.2, True),
        ("sugar_g", 14.0, 2.0, 1.0, True),
        ("moisture_pct", 3.2, 1.0, 1.1, True),
        ("water_activity", 0.35, 0.05, 1.1, True),
        ("texture_index", 58.0, 8.0, 1.0, True),
        ("cost_inr_kg", 235.0, 15.0, 1.2, True),
        ("sodium_mg", 320.0, 45.0, 0.8, False),
        ("shelf_life_days", 150.0, 35.0, 0.8, False),
        # Manufacturability of a high-fibre, high-protein biscuit is inherently
        # lower than a plain one: 70 is a good, achievable score for this class.
        ("processability_score", 70.0, 8.0, 0.7, False),
        ("mould_risk", 25.0, 12.0, 0.6, False),
    ],
    "spread": [
        ("sugar_g", 40.0, 4.0, 1.1, True),
        ("moisture_pct", 32.0, 3.0, 1.0, True),
        ("water_activity", 0.88, 0.04, 1.1, True),
        ("ph", 3.60, 0.15, 1.2, True),
        ("consistency_index", 55.0, 8.0, 1.0, True),
        ("shelf_life_days", 240.0, 40.0, 1.0, True),
        ("cost_inr_kg", 165.0, 15.0, 1.2, True),
        ("energy_kcal", 260.0, 30.0, 0.8, False),
        ("fibre_g", 3.0, 1.0, 0.8, False),
        ("processability_score", 78.0, 8.0, 0.7, False),
        ("mould_risk", 25.0, 12.0, 0.8, False),
    ],
    "drymix": [
        ("energy_kcal", 400.0, 30.0, 1.0, True),
        ("protein_g", 14.0, 1.5, 1.3, True),
        ("sugar_g", 16.0, 2.5, 1.0, True),
        ("moisture_pct", 5.0, 1.0, 1.0, True),
        # A whole-grain instant mix carries real fibre: asking a ragi-and-soy base
        # for 5 g fibre is a target conflict, not a formulation task.
        ("fibre_g", 8.0, 1.2, 1.1, True),
        ("water_activity", 0.40, 0.06, 1.1, True),
        ("cost_inr_kg", 215.0, 20.0, 1.2, True),
        ("shelf_life_days", 200.0, 45.0, 0.9, True),
        ("sodium_mg", 300.0, 50.0, 0.8, False),
        ("processability_score", 70.0, 8.0, 0.7, False),
    ],
    "extruded_snack": [
        # Monitored, not gated: a savoury pellet at 15 g protein and 3% moisture is
        # an energy-dense product by construction (~425 kcal/100 g), and a brief
        # that does not state an energy figure should not fail a trial over one.
        ("energy_kcal", 425.0, 35.0, 0.9, False),
        ("protein_g", 15.0, 1.5, 1.3, True),
        ("fat_g", 8.0, 1.5, 1.0, True),
        ("moisture_pct", 3.0, 1.0, 1.0, True),
        ("water_activity", 0.35, 0.06, 1.1, True),
        ("texture_index", 62.0, 8.0, 1.0, True),
        ("cost_inr_kg", 190.0, 20.0, 1.2, True),
        ("fibre_g", 6.0, 1.2, 1.0, False),
        ("sodium_mg", 450.0, 60.0, 0.8, False),
        ("processability_score", 74.0, 8.0, 0.8, False),
    ],
    "bar": [
        ("energy_kcal", 400.0, 30.0, 1.0, True),
        ("protein_g", 18.0, 2.0, 1.3, True),
        ("fibre_g", 7.0, 1.2, 1.1, True),
        ("sugar_g", 22.0, 3.0, 1.0, True),
        ("moisture_pct", 12.0, 2.0, 1.0, True),
        ("water_activity", 0.65, 0.05, 1.1, True),
        ("texture_index", 60.0, 8.0, 1.0, True),
        ("cost_inr_kg", 290.0, 25.0, 1.2, True),
        ("shelf_life_days", 180.0, 35.0, 0.9, False),
        ("processability_score", 74.0, 8.0, 0.7, False),
    ],
    "beverage": [
        # A protein drink is a water-based product: total protein per 100 g is
        # the headline target, and the population section turns it into grams per
        # serving and a share of each group's daily requirement.
        ("protein_g", 10.0, 1.5, 1.4, True),
        ("energy_kcal", 60.0, 15.0, 0.9, False),
        ("sugar_g", 6.0, 1.5, 1.0, True),
        ("fat_g", 2.0, 0.8, 0.8, False),
        ("sodium_mg", 90.0, 25.0, 0.8, False),
        # Moisture and water activity are properties of a drink, not levers: a
        # beverage is ~86% water and aw ~0.97 by construction, and it is
        # preserved by heat treatment and packaging, not by drying. They are
        # reported and monitored, never gated.
        ("moisture_pct", 86.0, 5.0, 0.7, False),
        # A drink's aw is 0.95-0.995 by construction (the category range), so the
        # band is the category, not a figure to aim at. Tightening it to 0.97 +/-
        # 0.02 turned a normal drink into a reported "conflict" with no lever.
        ("water_activity", 0.98, 0.03, 0.5, False),
        ("ph", 6.60, 0.30, 0.9, False),
        ("shelf_life_days", 180.0, 40.0, 1.1, True),
        ("consistency_index", 18.0, 8.0, 0.8, False),
        ("processability_score", 85.0, 8.0, 0.7, False),
        ("cost_inr_kg", 320.0, 30.0, 1.2, True),
        ("mould_risk", 15.0, 10.0, 0.6, False),
    ],
    "sauce": [
        ("ph", 3.80, 0.20, 1.2, True),
        # A tomato-based sauce sits at aw 0.92-0.95 by design: it is preserved by
        # acid, heat and packaging, not by drying. Water activity is therefore
        # monitored here, not driven - the mould-risk and pH targets do the work.
        ("water_activity", 0.94, 0.03, 0.7, False),
        ("moisture_pct", 62.0, 4.0, 1.0, True),
        # Sugar and shelf life are coupled in a sauce: sugar is the cheapest way to
        # depress water activity, so an aggressive sugar ceiling and a long
        # ambient shelf life cannot both be satisfied - see conflicts().
        ("sugar_g", 22.0, 3.0, 1.0, True),
        ("consistency_index", 62.0, 8.0, 1.0, True),
        ("sodium_mg", 700.0, 80.0, 1.0, True),
        ("shelf_life_days", 180.0, 35.0, 1.0, True),
        ("cost_inr_kg", 145.0, 15.0, 1.2, True),
        ("processability_score", 76.0, 8.0, 0.7, False),
        ("mould_risk", 30.0, 12.0, 0.8, False),
    ],
}

# Numeric overrides keyed to a claim: a claim is a promise about a specific KPI.
CLAIM_TARGETS: Dict[str, Tuple[str, float, float, str]] = {
    # claim_id: (kpi_id, target, tolerance, comparison)
    "high_protein": ("protein_g", 12.0, 1.5, "higher"),
    "source_protein": ("protein_g", 7.0, 1.0, "higher"),
    "high_fibre": ("fibre_g", 7.0, 1.0, "higher"),
    "source_fibre": ("fibre_g", 4.0, 0.8, "higher"),
    "low_sugar": ("sugar_g", 5.0, 1.0, "lower"),
    "sugar_free": ("sugar_g", 0.5, 0.3, "lower"),
    "reduced_sugar": ("sugar_g", 15.0, 2.0, "lower"),
    "low_sodium": ("sodium_mg", 120.0, 25.0, "lower"),
    "low_fat": ("fat_g", 3.0, 0.8, "lower"),
    "high_energy": ("energy_kcal", 420.0, 30.0, "higher"),
}


# KPIs where a number in a specification is always a limit, never an ideal.
ALWAYS_ONE_SIDED = frozenset(
    {"cost_inr_kg", "cost_inr_unit", "mould_risk", "oxidation_risk", "shelf_life_days"}
)


def direction_for(kpi_id: str, comparator: str, default: str) -> str:
    """Decide whether a specified number is a limit or a target to hit.

    "sugar <= 12 g" is a ceiling; "sugar 12 g" is a value the developer wants to
    land on. Treating a bare number as a one-sided limit is what lets an
    optimiser quietly deliver a nearly sugar-free biscuit and call it a pass.
    An explicit comparator, or a KPI that is inherently a limit (cost, risk
    indices), keeps its natural one-sided direction.
    """
    if comparator == "<=":
        return "lower"
    if comparator == ">=":
        return "higher"
    if kpi_id in ALWAYS_ONE_SIDED:
        return kpi_registry.kpi_direction(kpi_id)
    return "target"


def targets_for(
    category: str,
    overrides: Optional[Dict[str, float]] = None,
    claims: Optional[Iterable[str]] = None,
    cost_ceiling: Optional[float] = None,
    comparators: Optional[Dict[str, str]] = None,
) -> List[KpiTarget]:
    """Build the target set for a category, then apply claim and spec overrides."""
    rows = DEFAULT_TARGETS.get(category, DEFAULT_TARGETS["cookie"])
    targets: List[KpiTarget] = [
        kpi_registry.make_target(kpi_id, target, tolerance, priority, hard)
        for kpi_id, target, tolerance, priority, hard in rows
    ]
    index = {t.id: t for t in targets}

    # Claims tighten (or add) the KPI the claim is about.
    for claim_id in claims or []:
        rule = CLAIM_TARGETS.get(claim_id)
        if not rule:
            continue
        kpi_id, target, tolerance, _ = rule
        if kpi_id in index:
            existing = index[kpi_id]
            # Keep the more demanding of the two directions.
            if existing.direction == "lower":
                existing.target = min(existing.target, target)
            elif existing.direction == "higher":
                existing.target = max(existing.target, target)
            else:
                existing.target = target
            existing.tolerance = min(existing.tolerance, tolerance)
            existing.priority = max(existing.priority, 1.3)
            existing.hard = True
        else:
            new = kpi_registry.make_target(kpi_id, target, tolerance, 1.3, True)
            targets.append(new)
            index[kpi_id] = new

    # Explicit specification numbers win over the defaults.
    for kpi_id, value in (overrides or {}).items():
        if kpi_id.startswith("__"):
            continue
        comparator = (comparators or {}).get(kpi_id, "")
        if kpi_id in index:
            index[kpi_id].target = float(value)
            index[kpi_id].direction = direction_for(
                kpi_id, comparator, index[kpi_id].direction
            )
        elif kpi_id in kpi_registry.KPI_DEFS:
            new = kpi_registry.make_target(kpi_id, float(value), None, 1.2, True)
            new.direction = direction_for(kpi_id, comparator, new.direction)
            targets.append(new)
            index[kpi_id] = new

    if cost_ceiling:
        ceiling = float(cost_ceiling)
        if "cost_inr_kg" in index:
            index["cost_inr_kg"].target = ceiling
            index["cost_inr_kg"].hard = True
        else:
            targets.append(kpi_registry.make_target("cost_inr_kg", ceiling, None, 1.2, True))

    # Only keep targets that this category can actually express.
    allowed = set(kpi_registry.kpis_for_category(category))
    return [t for t in targets if t.id in allowed]


def open_questions(brief: Brief) -> List[str]:
    """What the agent still does not know, phrased as questions for the team."""
    questions: List[str] = []
    if not brief.spec_text.strip():
        questions.append(
            "No written specification was supplied: targets were taken from the "
            "category defaults. Confirm or edit each target before the first trial."
        )
    if not brief.cost_ceiling_inr_kg:
        questions.append(
            "Is there a hard cost ceiling per kilogram (in INR)? Cost is currently "
            "optimised as a soft objective only."
        )
    if not brief.claims:
        questions.append(
            "Which on-pack nutrition claims must be substantiated (high protein, "
            "high fibre, low sugar, ...)? Claims constrain the formulation."
        )
    if brief.diet == "vegetarian":
        questions.append("Is the product required to be vegan (no milk, ghee, whey) or is vegetarian acceptable?")
    if not brief.targets:
        questions.append("No KPI targets could be inferred for this category - a category must be selected.")
    hard = [t for t in brief.targets if t.hard]
    if len(hard) > 10:
        questions.append(
            f"{len(hard)} KPIs are marked as hard targets. Fewer hard targets usually "
            "means fewer trials - consider relaxing the lowest-priority ones."
        )
    if not brief.images:
        questions.append(
            "No reference image was provided. Appearance, piece size and surface "
            "finish cannot be inferred from text alone."
        )
    return questions


def _clean_pack_unit(value: Any, fallback: str) -> str:
    unit = str(value or "").strip().lower()
    return unit if unit in ("g", "ml") else fallback


def _pack_size_for(
    payload: Dict[str, Any], numbers: Dict[str, Dict[str, Any]], category: kb.Category
) -> Tuple[float, str]:
    """The pack size the brief is built with: ``(size, unit)``, the unit "g" or "ml".

    Three sources, in order: what the form sent, whose field is labelled with the
    category's own unit; then what the specification text declared; then the
    category's typical pack. A drink is sized in millilitres and everything else in
    grams. The size doubles as the mass the models work in, because a product of
    density near 1 g/ml is one gram per millilitre - and the nutrition engine applies
    the real density when it reports per 100 ml.
    """
    sent = payload.get("unit_weight_g")
    if sent:
        return float(sent), _clean_pack_unit(payload.get("unit"), category.pack_unit)
    declared = numbers.get("__pack_size") or {}
    if declared.get("value"):
        return float(declared["value"]), _clean_pack_unit(declared.get("unit"), category.pack_unit)
    return float(category.typical_unit_weight_g), category.pack_unit


def build_brief(payload: Dict[str, Any]) -> Brief:
    """Build a brief from an API/UI payload, parsing any free-text specification."""
    spec_text = str(payload.get("spec_text") or payload.get("specification") or "")
    description = str(payload.get("description") or "")
    combined = "\n".join(part for part in (spec_text, description) if part)

    declared_category = str(payload.get("category") or "").strip()
    if declared_category and declared_category in kb.categories():
        category = declared_category
        category_confidence = 1.0
        category_hits: List[str] = ["declared"]
    else:
        category, category_confidence, category_hits = infer_category(combined, fallback="cookie")

    numbers = parse_spec_numbers(combined)
    overrides = {k: v["value"] for k, v in numbers.items() if not k.startswith("__")}
    comparators = {k: str(v.get("comparator", "")) for k, v in numbers.items() if not k.startswith("__")}

    claims = list(dict.fromkeys(list(payload.get("claims") or []) + claims_in_text(combined)))
    valid_claims = {rule["id"] for rule in kb.claim_rules()}
    claims = [c for c in claims if c in valid_claims]

    avoided = list(dict.fromkeys(list(payload.get("allergens_to_avoid") or []) + allergens_in_text(combined)))

    diet = str(payload.get("diet") or "").strip() or infer_diet(combined)
    if diet not in ("vegan", "vegetarian", "any"):
        diet = "vegetarian"

    declared_size, declared_unit = _pack_size_for(payload, numbers, kb.category(category))

    cost_ceiling = payload.get("cost_ceiling_inr_kg") or overrides.get("cost_inr_kg")

    product_name = str(payload.get("product_name") or "").strip()
    if not product_name:
        product_name = f"{kb.category(category).label} - untitled concept"

    brief = Brief(
        product_name=product_name,
        category=category,
        unit_weight_g=float(declared_size),
        declared_unit=declared_unit,
        declared_unit_size=float(declared_size),
        description=description or _first_sentence(spec_text),
        claims=claims,
        diet=diet,
        cost_ceiling_inr_kg=float(cost_ceiling) if cost_ceiling else None,
        allergens_to_avoid=avoided,
        targets=targets_for(
            category,
            overrides=overrides,
            claims=claims,
            cost_ceiling=cost_ceiling,
            comparators=comparators,
        ),
        spec_text=spec_text,
        images=list(payload.get("images") or []),
        understood_by=str(payload.get("understood_by") or "offline-parser"),
    )
    brief.open_questions = open_questions(brief)
    return brief


def _first_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    for stop in (". ", "\n"):
        index = text.find(stop)
        if index > 20:
            return text[: index + 1].strip()
    return text[:220]


def summary(brief: Brief) -> Dict[str, Any]:
    """A compact, human-readable summary of what the agent understood."""
    hard = [t for t in brief.targets if t.hard]
    return {
        "product_name": brief.product_name,
        "category": brief.category,
        "category_label": kb.category(brief.category).label,
        "description": brief.description,
        "unit_weight_g": brief.unit_weight_g,
        # What the pack says: a drink is declared in millilitres, everything else in
        # grams. The interface prints these, so the summary has to carry them.
        "declared_unit": brief.declared_unit,
        "declared_unit_size": brief.declared_unit_size,
        "diet": brief.diet,
        "claims": [rule["label"] for rule in kb.claim_rules() if rule["id"] in brief.claims],
        "claim_ids": list(brief.claims),
        "allergens_to_avoid": [kb.allergen_labels().get(a, a) for a in brief.allergens_to_avoid],
        "cost_ceiling_inr_kg": brief.cost_ceiling_inr_kg,
        "hard_target_count": len(hard),
        "target_count": len(brief.targets),
        "targets": [t.as_dict() for t in brief.targets],
        "open_questions": list(brief.open_questions),
        "understood_by": brief.understood_by,
        "images": list(brief.images),
    }
