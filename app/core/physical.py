"""Physical, textural and stability models.

These are first-principles-plus-calibration models, deliberately transparent:
every coefficient below can be argued about, replaced with plant data, or
refitted. Each model returns a value *and* a short method note so the report and
the UI can show how a prediction was produced.

Key models
----------
moisture      drying / evaporation kinetics anchored to the category defaults
water_activity two-regime model: bound-water term, then Norrish/Ross aqueous-phase
              model for intermediate/high moisture products, log-linear sorption
              for dry glassy products
pH            buffered weak-acid equilibrium (Ka-based) with a matrix buffer term
texture       composition-driven firmness index with process modulation
stability     aw/pH/preservative driven mould-free life and fat-driven oxidation life
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import kb
from .nutrition import Composition

# Matrix binding coefficients: grams of water bound per gram of component.
BIND_STARCH = 0.10
BIND_PROTEIN = 0.25
BIND_FIBRE = 0.35

# Dry (glassy) regime sorption model: aw = AW_MONO + AW_SLOPE * ln(moisture / AW_REF)
AW_MONO = 0.43
AW_SLOPE = 0.16
AW_REF = 6.0

# Norrish constants (mole-fraction form) used for the aqueous phase.
NORRISH_K = {"sugar": 6.47, "polyol": 1.6, "salt": 20.0, "acid": 1.0, "other": 3.0}
MW_WATER = 18.015
MW_POLYOL = 120.0
MW_SALT = 58.44
MW_OTHER = 200.0

# Matrix buffer capacity in mol/L of proton equivalents per pH unit. Calibrated
# on two anchors: a tomato-based sauce reaches pH ~3.9 with about 3% vinegar,
# and a fruit spread reaches pH ~3.5 with 0.05% citric acid.
PH_BUFFER_M = {
    "cookie": 0.0100,
    "spread": 0.0260,
    "drymix": 0.0100,
    "extruded_snack": 0.0100,
    "bar": 0.0120,
    "sauce": 0.0132,
    "beverage": 0.0180,
}
# Maximum pH drop the model will ever attribute to added acid.
PH_MAX_DROP = 1.80
# pH region in which acid potency is evaluated (the 3-4.5 band of acid products).
PH_REGION = 3.8
# Unbuffered response: pH units per mol/L of titratable acidity beyond the buffer.
PH_UNBUFFERED_SLOPE = 2.0

# Category-typical pH of the base matrix before any added acidulant.
PH_BASELINE = {
    "cookie": 6.4,
    "spread": 3.9,
    "drymix": 6.4,
    "extruded_snack": 6.2,
    "bar": 6.0,
    "sauce": 4.4,
    # A milk/whey-based drink sits near neutral before any acid is added.
    "beverage": 6.6,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# --------------------------------------------------------------------------- #
# Moisture
# --------------------------------------------------------------------------- #
def predict_moisture(category: str, mix_moisture: float, params: Dict[str, float]) -> Tuple[float, str]:
    m0 = _clamp(mix_moisture, 0.5, 92.0)
    if category == "cookie":
        temp = params.get("bake_temp_c", 175.0)
        time = params.get("bake_time_min", 11.0)
        thickness = params.get("sheet_thickness_mm", 6.0)
        k = 0.200 * (temp / 175.0) ** 2.6 * (6.0 / max(thickness, 1.0)) ** 1.1
        m_eq = 1.5
        moisture = m_eq + (m0 - m_eq) * math.exp(-k * time)
        note = f"thin-slab drying: m = {m_eq} + (m0-{m_eq})*exp(-k*t), k={k:.3f}/min"
    elif category in ("spread", "sauce"):
        # Open-kettle evaporation. The two reference evaporation depths are very
        # different: a fruit spread is boiled down from ~50% to ~30% moisture
        # (about 20 points), while a sauce thickened for viscosity loses only a
        # few points. The scaffold `ceiling` is the practical end point where the
        # mass becomes unpumpable or the kettle scorches.
        temp = params.get("cook_temp_c", 95.0)
        reference_time = 22.0 if category == "spread" else 18.0
        reference_evap = 22.0 if category == "spread" else 9.0
        time = params.get("cook_time_min", reference_time)
        ceiling = 24.0 if category == "spread" else 50.0
        evap = reference_evap * (time / reference_time) ** 0.6 * (temp / 95.0) ** 2.0
        moisture = max(m0 - evap, ceiling)
        note = (
            f"open-kettle evaporation: -{evap:.1f} points to {moisture:.1f}% at "
            f"{temp:.0f} degC / {time:.0f} min (reference {reference_evap:.0f} points)"
        )
    elif category == "drymix":
        # Drying towards an equilibrium moisture: the roast/conditioning step
        # drives the blend towards the equilibrium moisture of the unit at that
        # air temperature, so moisture falls exponentially with temperature and
        # can be pushed to the 4-6% band an instant mix needs.
        roast = params.get("roast_temp_c", 120.0)
        m_eq = 2.0
        k = 0.0165 * max(roast, 0.0)
        moisture = m_eq + (m0 - m_eq) * math.exp(-k)
        moisture = _clamp(moisture, 2.5, 13.5)
        note = (
            f"roast/conditioning towards equilibrium moisture {m_eq:.1f}%: "
            f"m = {m_eq} + (m0-{m_eq})*exp(-{k:.2f}) at {roast:.0f} degC"
        )
    elif category == "extruded_snack":
        feed = params.get("feed_moisture_pct", 18.0)
        dryer_t = params.get("dryer_temp_c", 115.0)
        dryer_time = params.get("dryer_time_min", 16.0)
        moisture = _clamp(feed * 0.55 * math.exp(-0.060 * dryer_time * (dryer_t / 115.0) ** 1.5), 1.2, 8.0)
        note = "extrudate moisture after belt drying"
    elif category == "bar":
        syrup = params.get("syrup_temp_c", 105.0)
        moisture = _clamp(m0 * 0.95 - 0.05 * (syrup - 105.0), 6.0, 18.0)
        note = "slab moisture after syrup binding and cooling"
    elif category == "beverage":
        # A drink is filled as blended: there is no drying step, so the finished
        # moisture is the mix moisture, set by how much water the slots carry.
        moisture = _clamp(m0, 70.0, 95.0)
        note = "no concentration step: filled at the blended moisture (water is a formulation slot)"
    else:
        moisture = m0
        note = "no moisture change modelled for this category"
    return _clamp(moisture, 0.5, 92.0), note


# --------------------------------------------------------------------------- #
# Water activity
# --------------------------------------------------------------------------- #
def _bound_water(comp: Composition) -> Tuple[float, Dict[str, float]]:
    scale = comp.aggregates["concentration_factor"]
    starch = comp.aggregates["starch_frac"] * 100.0 * scale
    protein = comp.final["protein_g"]
    fibre = comp.final["fibre_g"]
    bound = BIND_STARCH * starch + BIND_PROTEIN * protein + BIND_FIBRE * fibre
    return bound, {"starch_g": starch, "protein_g": protein, "fibre_g": fibre}


def water_activity(comp: Composition) -> Tuple[float, str, Dict[str, float]]:
    moisture = comp.final_moisture
    bound, parts = _bound_water(comp)
    free = max(moisture - bound, 0.0)
    detail = {"moisture": moisture, "bound_water": bound, "free_water": free, **parts}
    sugar_frac = comp.final.get("sugar_g", 0.0) / 100.0

    # Domain of validity: the aqueous-phase (Norrish) model describes products
    # with a genuine continuous aqueous phase - sauces, spreads, syrups. Dry and
    # intermediate products (biscuits, bars, instant mixes) are glassy or
    # semi-crystalline and follow sorption behaviour instead, even though a small
    # amount of water is technically "free". Getting this boundary right matters:
    # it is the difference between a bar at aw 0.64 and aw 0.46.
    if free >= 12.0 and moisture >= 18.0:
        n_w = free / MW_WATER
        n_sugar = max(comp.sugar_equiv_g, 0.0) / 300.0
        n_polyol = max(comp.polyol_g, 0.0) / MW_POLYOL
        n_salt = max(comp.salt_g, 0.0) / MW_SALT
        n_acid = sum(comp.acid_moles_per_100g.values()) * comp.aggregates["concentration_factor"]
        n_other = 0.02 * (comp.final["protein_g"] + comp.final["fibre_g"]) / MW_OTHER
        solutes = {
            "sugar": n_sugar,
            "polyol": n_polyol,
            "salt": n_salt,
            "acid": n_acid,
            "other": n_other,
        }
        n_total = sum(solutes.values())
        if n_total <= 1e-9:
            return _clamp(moisture / 100.0 + 0.2, 0.05, 0.99), "aqueous phase: negligible solute", detail
        x_solute = n_total / (n_w + n_total)
        k_eff = sum(n * NORRISH_K[key] for key, n in solutes.items()) / n_total
        aw = (1.0 - x_solute) * math.exp(-k_eff * x_solute ** 2)
        detail.update({"x_solute": x_solute, "k_eff": k_eff, "n_water": n_w, **{f"n_{k}": v for k, v in solutes.items()}})
        method = f"Norrish/Ross aqueous phase (Xs={x_solute:.3f}, K={k_eff:.2f})"
        return _clamp(aw, 0.05, 0.99), method, detail

    # Dry / glassy regime. Sugars still act as humectants here - a cereal bar at
    # 11% moisture and 22% sugars sits near aw 0.6, while a plain flour at the
    # same moisture sits near aw 0.45 - so the sorption baseline is lifted by a
    # sugar term, and reduced by the polyol/salt that genuinely depress aw.
    ratio = max(moisture, 0.2) / AW_REF
    aw = AW_MONO + AW_SLOPE * math.log(ratio)
    sugar_uplift = 1.0 + 0.90 * min(max(sugar_frac, 0.0), 0.45)
    humectant_depression = math.exp(-(2.0 * comp.polyol_g + 2.6 * comp.salt_g) / 100.0)
    aw *= sugar_uplift * humectant_depression
    detail.update(
        {
            "humectant_depression": humectant_depression,
            "sugar_uplift": sugar_uplift,
            "regime": "dry",
        }
    )
    method = (
        f"log-linear sorption in the glassy regime: aw = {AW_MONO} + {AW_SLOPE}*ln(m/{AW_REF})"
        f" x sugar uplift {sugar_uplift:.3f} x humectant factor {humectant_depression:.3f}"
    )
    return _clamp(aw, 0.05, 0.95), method, detail


# --------------------------------------------------------------------------- #
# pH
# --------------------------------------------------------------------------- #
def predict_ph(comp: Composition, category: str) -> Tuple[float, str]:
    """Titration model: added acid against the buffering capacity of the matrix.

    A free-acid dissociation model (pH = -log sqrt(Ka*C)) is wrong for food
    matrices, because it ignores the buffering that dominates them: 0.05% citric
    acid in water is pH 3.1, but in a tomato sauce the same dose barely moves the
    pH, while 3% vinegar moves it half a unit. What matters is the *titratable*
    acidity against the matrix's buffer capacity, so that is what is modelled:

        drop = MAX_DROP * (1 - exp(-A / capacity)),   pH = baseline - drop

    ``capacity`` is set per category from two calibration anchors - a ketchup
    reaching pH ~3.9 with about 3% vinegar, and a fruit spread reaching pH ~3.5
    with 0.05% citric acid - and corrected for protein, mineral and fibre solids,
    which all add buffering.
    """
    baseline = PH_BASELINE.get(category, 6.0)
    bound, _ = _bound_water(comp)
    free_water = max(comp.final_moisture - bound, 1.0)
    litres = free_water / 100.0  # per 100 g of product, water density ~1
    scale = comp.aggregates["concentration_factor"]

    protein_frac = comp.final["protein_g"] / 100.0
    fibre_frac = comp.final["fibre_g"] / 100.0
    mineral_frac = comp.aggregates["mineral_frac"] * scale
    capacity = PH_BUFFER_M.get(category, 0.006) * (
        1.0 + 0.15 * protein_frac + 0.25 * mineral_frac + 0.10 * fibre_frac
    )

    # Acids are not interchangeable: acetic acid (pKa 4.76) is only about 10%
    # dissociated at pH 3.8 while citric acid (pKa 3.13) is over 80%, which is why
    # a ketchup is acidified with 3% vinegar but only about 0.05% citric acid.
    # Potency is therefore evaluated at the pH region these products live in.
    region = 10 ** (-PH_REGION)
    # Both anchors land on the same effective acidity, which is the whole point:
    # 3% vinegar and 0.05% citric acid are the real-world equivalents in a sauce,
    # and the potency weighting reproduces that.
    titratable = 0.0
    contributors: List[str] = []
    for ing_id, moles in comp.acid_moles_per_100g.items():
        ing = kb.ingredient(ing_id)
        if ing.molar_mass is None:
            continue
        pka = float(ing.pka) if ing.pka is not None else 3.5
        ka = 10 ** (-pka)
        potency = ka / (ka + region)
        concentration = moles * scale / max(litres, 1e-4)
        titratable += concentration * potency
        contributors.append(
            f"{ing.id}:{concentration:.3f} M x{potency:.2f} potency"
        )
    # Two terms: the buffered term (which saturates once the buffer is spent) and
    # an unbuffered term that keeps responding. Without the second term the model
    # is flat above about five times the buffer capacity - pH 2.5 and pH 2.9 both
    # report the same number - and every search over an acid dose stalls there.
    buffered = PH_MAX_DROP * (1.0 - math.exp(-titratable / max(capacity, 1e-6)))
    unbuffered = PH_UNBUFFERED_SLOPE * titratable
    drop = buffered + unbuffered
    ph = baseline - drop if titratable > 0 else baseline
    note = (
        f"titration of {titratable:.3f} mol/L proton equivalents "
        f"({', '.join(contributors) if contributors else 'no added acid'}) against a matrix buffer "
        f"capacity of {capacity:.3f} mol/L per pH unit: drop {drop:.2f} "
        f"({buffered:.2f} buffered + {unbuffered:.2f} unbuffered) from baseline pH {baseline}"
    )
    return _clamp(ph, 2.0, 8.0), note


# --------------------------------------------------------------------------- #
# Texture / consistency
# --------------------------------------------------------------------------- #
def predict_texture(comp: Composition, category: str, params: Dict[str, float]) -> Tuple[Dict[str, float], str]:
    scale = comp.aggregates["concentration_factor"]
    protein = comp.final["protein_g"]
    fibre = comp.final["fibre_g"]
    fat = comp.final["fat_g"]
    sugar = comp.final["sugar_g"]
    moisture = comp.final_moisture
    starch = comp.aggregates["starch_frac"] * 100.0 * scale

    firmness = (
        18.0
        + 0.90 * protein
        + 1.10 * fibre
        + 0.55 * starch
        - 0.75 * fat
        - 1.30 * moisture
        + 0.35 * sugar
    )
    if category == "extruded_snack":
        expansion = 1.6 - (params.get("feed_moisture_pct", 18.0) - 14.0) * 0.05 - (
            params.get("barrel_temp_c", 145.0) - 110.0
        ) * 0.004
        firmness *= _clamp(1.35 - expansion * 0.28, 0.6, 1.4)
    if category == "bar":
        firmness += 0.22 * (params.get("syrup_temp_c", 105.0) - 100.0)
    if category == "cookie":
        firmness += 0.04 * (params.get("bake_temp_c", 175.0) - 175.0)
    if category == "beverage":
        # A drink's useful texture number is viscosity, not firmness. It is set by
        # the hydrocolloid, dissolved solids and protein, and it must stay low
        # enough to pour; the firmness index is meaningless for a liquid, so the
        # reported texture index becomes the (low) viscosity reading.
        starch_gel_b = comp.aggregates["starch_frac"] * comp.aggregates["concentration_factor"]
        hydrocolloid_b = comp.aggregates["hydrocolloid_frac"] * comp.aggregates["concentration_factor"]
        viscosity = _clamp(
            3.0
            + 1200.0 * hydrocolloid_b
            + 180.0 * starch_gel_b
            + 0.30 * (100.0 - moisture)
            + 18.0 * (sugar / 100.0)
            + 0.25 * protein,
            0.0,
            100.0,
        )
        values = {
            "texture_index": _clamp(0.6 * viscosity, 1.0, 100.0),
            "hardness_n": 0.0,
            "consistency_index": viscosity,
            "spreadability": _clamp(100.0 - viscosity, 0.0, 100.0),
        }
        note = (
            "beverage viscosity index from hydrocolloid "
            f"{hydrocolloid_b*100:.2f}%, dissolved solids and protein "
            f"{protein:.1f} g/100 g at {moisture:.1f}% moisture; "
            "firmness and hardness are not meaningful for a liquid"
        )
        return values, note
    strength = _clamp(firmness, 1.0, 100.0)
    hardness_n = 1.70 * (strength ** 0.90)
    # A thick sauce gets its body from gelatinised starch as much as from a
    # hydrocolloid: without the starch-gel term a 3% cornstarch ketchup reads as
    # watery, which is not what the pilot plant sees.
    starch_gel = comp.aggregates["starch_frac"] * comp.aggregates["concentration_factor"]
    hydrocolloid = comp.aggregates["hydrocolloid_frac"] * comp.aggregates["concentration_factor"]
    consistency = _clamp(
        5.0
        + 700.0 * hydrocolloid
        + 900.0 * starch_gel
        + 0.55 * (100.0 - moisture)
        + 30.0 * (sugar / 100.0),
        0.0,
        100.0,
    )
    spreadability = _clamp(105.0 - 0.90 * consistency - 0.40 * fibre, 0.0, 100.0)
    values = {
        "texture_index": strength,
        "hardness_n": hardness_n,
        "consistency_index": consistency,
        "spreadability": spreadability,
    }
    note = (
        "composition-driven firmness index (protein/fibre/starch positive, fat/moisture negative)"
        " with process modulation; hardness calibrated indicatively to a 3-point break test;"
        f" consistency = starch gel {starch_gel*100:.1f}% + hydrocolloid {hydrocolloid*100:.1f}%"
        " + solids and sugar terms"
    )
    return values, note


# --------------------------------------------------------------------------- #
# Stability
# --------------------------------------------------------------------------- #
def predict_stability(
    comp: Composition,
    aw: float,
    ph: float,
    category: str,
    params: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], str]:
    # Effectiveness is driven by parts-per-million in the finished product:
    # 800 ppm of sorbate is close to full effectiveness in an acid product, and
    # 60 ppm is close to none.
    scale = comp.aggregates["concentration_factor"]
    preservative_ppm = comp.aggregates["preservative_ppm"] * scale
    antioxidant_ppm = comp.aggregates["antioxidant_ppm"] * scale
    preservative_eff = min(1.0, preservative_ppm / 600.0)
    antioxidant_eff = min(1.0, antioxidant_ppm / 250.0)
    unsat_fat_g = comp.aggregates["unsaturated_fat_frac"] * 100.0 * comp.aggregates["concentration_factor"]

    if category == "beverage":
        # Shelf life of a drink is set by the validated heat process and the
        # packaging, not by water activity: aw is ~0.97 whatever the recipe, so
        # the aw-based mould model below would hand a UHT product a 40-day life.
        heat = float((params or {}).get("heat_treat_temp_c", 121.0))
        hold = float((params or {}).get("hold_time_s", 4.0))
        if heat >= 135.0:
            base_life, process = 270.0, "UHT / aseptic"
        elif heat >= 115.0:
            base_life, process = 210.0, "sterilised / retort"
        else:
            base_life, process = 45.0, "pasteurised (chilled distribution)"
        hold_factor = _clamp(hold / 4.0, 0.4, 1.6)
        acidity_bonus = _clamp(1.0 + 0.45 * (4.6 - ph), 1.0, 2.0)
        water_life = base_life * hold_factor * acidity_bonus * (1.0 + 0.8 * preservative_eff)
        oxidation_life = 450.0 * math.exp(-0.075 * max(unsat_fat_g, 0.0)) * (1.0 + 0.9 * antioxidant_eff)
        shelf_life = min(water_life, oxidation_life)
        # A validated sterilising heat treatment removes the mould risk a high-aw
        # liquid would otherwise carry; a pasteurised drink keeps it.
        process_mould = 6.0 if heat >= 115.0 else 45.0
        mould_risk = _clamp(
            process_mould - 25.0 * preservative_eff + 6.0 * math.tanh((ph - 4.6) * 1.5),
            0.0,
            100.0,
        )
        oxidation_risk = _clamp(
            100.0 * min(1.0, unsat_fat_g / 20.0) * (1.0 - 0.6 * antioxidant_eff), 0.0, 100.0
        )
        values = {
            "shelf_life_days": round(shelf_life, 1),
            "mould_risk": mould_risk,
            "oxidation_risk": oxidation_risk,
            "water_life_days": round(water_life, 1),
            "oxidation_life_days": round(oxidation_life, 1),
        }
        note = (
            f"shelf life from the heat process ({process} at {heat:.0f} degC, "
            f"{hold:.0f} s hold): base {base_life:.0f} d x hold {hold_factor:.2f} "
            f"x acidity {acidity_bonus:.2f} x preservative {1.0 + 0.8 * preservative_eff:.2f}; "
            "water activity does not limit a drink"
        )
        return values, note

    # Mould-free life falls sharply with water activity. The high-aw branch is
    # calibrated on the products that actually live there: a hot-filled, acid,
    # sorbate-preserved reduced-sugar spread at aw 0.88 must be able to claim a
    # 6-9 month unopened shelf life, which a 60-day ceiling forbids.
    if aw <= 0.60:
        water_life = 365.0 * (1.0 - (aw / 0.60) * 0.35)
    elif aw <= 0.85:
        water_life = 200.0 * math.exp(-(aw - 0.60) * 6.0)
    else:
        water_life = 150.0 * math.exp(-(aw - 0.85) * 9.0)
    # Smooth, monotone acidity bonus rather than step functions. A step at pH 4.0
    # creates a plateau that a search cannot climb, and it also overstates the
    # benefit of acid beyond the point where it stops mattering.
    acidity_bonus = _clamp(1.0 + 0.10 * (4.6 - ph), 1.0, 1.25)
    water_life *= acidity_bonus
    water_life *= 1.0 + 1.20 * preservative_eff

    oxidation_life = 450.0 * math.exp(-0.075 * max(unsat_fat_g, 0.0)) * (1.0 + 0.9 * antioxidant_eff)
    shelf_life = min(water_life, oxidation_life)

    mould_risk = 0.0
    if aw > 0.55:
        mould_risk += min(1.0, (aw - 0.55) / 0.30) * 55.0
    if aw > 0.85:
        mould_risk += 20.0
    # Acid suppression of mould, smoothly: fully restraining below pH 4.0, a
    # neutral 0 at pH 4.6, and a penalty above it.
    mould_risk += 10.0 * math.tanh((ph - 4.6) * 1.5)
    mould_risk -= 30.0 * preservative_eff
    mould_risk = _clamp(mould_risk, 0.0, 100.0)

    oxidation_risk = _clamp(100.0 * min(1.0, unsat_fat_g / 20.0) * (1.0 - 0.6 * antioxidant_eff), 0.0, 100.0)

    values = {
        "shelf_life_days": round(shelf_life, 1),
        "mould_risk": mould_risk,
        "oxidation_risk": oxidation_risk,
        "water_life_days": round(water_life, 1),
        "oxidation_life_days": round(oxidation_life, 1),
    }
    note = "min(water-activity-limited life, oxidation-limited life) with pH and preservative multipliers"
    return values, note


# --------------------------------------------------------------------------- #
# Processability
# --------------------------------------------------------------------------- #
def predict_processability(comp: Composition, category: str, params: Dict[str, float]) -> Tuple[float, List[str]]:
    cat = kb.category(category)
    score = 96.0
    drivers: List[str] = []

    count = int(comp.aggregates["ingredient_count"])
    if count > 12:
        penalty = min((count - 12) * 1.5, 12.0)
        score -= penalty
        drivers.append(f"{count} ingredient lines (-{penalty:.1f})")

    near_limit = []
    for item in comp.item_detail:
        ing = kb.ingredient(str(item["ingredient_id"]))
        hard_max = ing.hard_max_pct
        if hard_max > 0 and float(item["pct"]) > 0.92 * hard_max:
            near_limit.append(ing.name)
    if near_limit:
        score -= min(4.0 * len(near_limit), 14.0)
        drivers.append("ingredient at/near legal or practical maximum: " + ", ".join(near_limit[:3]))

    gluten = comp.aggregates["gluten_frac"] * comp.aggregates["concentration_factor"]
    if gluten > 0.12:
        score -= 6.0
        drivers.append("high gluten load - dough strength/resting control needed")
    if comp.aggregates["fibre_frac"] * comp.aggregates["concentration_factor"] > 0.10:
        score -= 5.0
        drivers.append("high fibre load - water absorption and machinability risk")

    if comp.final_moisture > cat.typical_moisture_pct[1] + 4.0:
        score -= 8.0
        drivers.append("moisture above category range - drying/packing risk")
    if comp.final_moisture < cat.typical_moisture_pct[0] - 2.0:
        score -= 5.0
        drivers.append("moisture below category range - brittle/dusty handling")

    if category == "extruded_snack":
        if params.get("feed_moisture_pct", 18.0) > 22.0:
            score -= 6.0
            drivers.append("high feed moisture - extrudate collapse risk")
        if params.get("screw_speed_rpm", 340.0) > 420.0:
            score -= 4.0
            drivers.append("high screw speed - shear/over-cooking risk")
    if category == "beverage":
        if params.get("homogenisation_bar", 180.0) < 100.0:
            score -= 5.0
            drivers.append("low homogenisation pressure - sedimentation and cream-ring risk")
        if comp.final["protein_g"] > 8.0:
            score -= 4.0
            drivers.append("high protein load - heat stability and viscosity control needed")
        if int(comp.aggregates["ingredient_count"]) == 0:
            drivers.append("no ingredients resolved for the beverage")
        if comp.aggregates["water_binding_frac"] > 0.15:
            score -= 3.0
            drivers.append("high water-binding load - viscosity build during blending")
    if comp.aggregates["water_binding_frac"] > 0.20:
        score -= 6.0
        drivers.append("high aggregate water binding - viscosity build during mixing")
    if not drivers:
        drivers.append("no significant manufacturability flags")
    return _clamp(score, 20.0, 99.0), drivers
