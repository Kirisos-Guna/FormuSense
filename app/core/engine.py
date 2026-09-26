"""The prediction engine: formulation + process -> every predicted characteristic.

This module is the single entry point the rest of the system uses. It chains the
composition balance, the physical models, the stability models and the cost
model, then attaches a confidence interval to each KPI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import cost as cost_model
from . import kb, kpi as kpi_registry, nutrition, physical, uncertainty
from .nutrition import Composition
from .types import Brief, Formulation, Prediction


@dataclass
class PredictionResult:
    values: Dict[str, float]
    predictions: List[Prediction]
    composition: Composition
    details: Dict[str, Any] = field(default_factory=dict)
    extrapolation: float = 1.0
    flags: List[str] = field(default_factory=list)

    def value(self, kpi_id: str) -> float:
        return float(self.values.get(kpi_id, 0.0))

    def prediction(self, kpi_id: str) -> Optional[Prediction]:
        for prediction in self.predictions:
            if prediction.id == kpi_id:
                return prediction
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "values": {k: round(v, 4) for k, v in self.values.items()},
            "predictions": [p.as_dict() for p in self.predictions],
            "details": self.details,
            "extrapolation": round(self.extrapolation, 3),
            "flags": self.flags,
            "allergens": sorted(self.composition.allergens),
            "warnings": self.composition.warnings,
        }


def predict(
    formulation: Formulation,
    brief: Optional[Brief] = None,
    calibration_samples: int = 0,
    surrogate=None,
) -> PredictionResult:
    """Predict all product characteristics for a formulation.

    ``surrogate`` (optional) is a fitted :class:`~app.core.surrogate.SurrogateSet`.
    When present, its residual correction is blended with the first-principles
    value, which is how real trial data progressively takes over from the priors.
    """
    params = formulation.params or kb.category(formulation.category).default_params()
    comp_probe = nutrition.analyse(formulation, final_moisture=10.0)
    final_moisture, moisture_note = physical.predict_moisture(
        formulation.category, comp_probe.moisture_mix, params
    )
    comp = nutrition.analyse(formulation, final_moisture=final_moisture)

    values: Dict[str, float] = {}
    methods: Dict[str, str] = {}
    values.update(nutrition.nutrition_kpis(comp))
    methods["nutrition"] = "exact mass balance with Atwater energy factors (protein 4, fat 9, carb 4, fibre 2 kcal/g)"

    aw, aw_note, aw_detail = physical.water_activity(comp)
    values["moisture_pct"] = comp.final_moisture
    methods["moisture_pct"] = moisture_note
    values["water_activity"] = aw
    methods["water_activity"] = aw_note

    ph, ph_note = physical.predict_ph(comp, formulation.category)
    values["ph"] = ph
    methods["ph"] = ph_note

    texture_values, texture_note = physical.predict_texture(comp, formulation.category, params)
    values.update(texture_values)
    methods["texture"] = texture_note

    stability_values, stability_note = physical.predict_stability(comp, aw, ph, formulation.category)
    values.update({k: v for k, v in stability_values.items() if k in kpi_registry.KPI_DEFS})
    methods["stability"] = stability_note

    processability, drivers = physical.predict_processability(comp, formulation.category, params)
    values["processability_score"] = processability
    methods["processability"] = "; ".join(drivers)

    unit_weight = brief.unit_weight_g if brief else kb.category(formulation.category).typical_unit_weight_g
    cost_detail = cost_model.total_cost(formulation, unit_weight)
    values["cost_inr_kg"] = float(cost_detail["total_cost_inr_kg"])
    values["cost_inr_unit"] = float(cost_detail["cost_inr_unit"])
    methods["cost"] = (
        "ingredient cost + process energy + conversion overhead + packaging, at prototype scale"
    )

    extrapolation, flags = uncertainty.extrapolation_factor(comp, params)

    surrogate_note = ""
    if surrogate is not None and getattr(surrogate, "is_fitted", False):
        try:
            corrected, surrogate_note = surrogate.apply(values, formulation)
            values.update(corrected)
        except Exception as exc:  # pragma: no cover - defensive
            surrogate_note = f"surrogate correction skipped: {exc}"

    relevant = kpi_registry.kpis_for_category(formulation.category)
    predictions: List[Prediction] = []
    for kpi_id in relevant:
        if kpi_id not in values:
            continue
        value = float(values[kpi_id])
        target = brief.target(kpi_id) if brief else None
        lo, hi, confidence = uncertainty.interval(
            kpi_id, value, extrapolation, calibration_samples=calibration_samples
        )
        method = methods.get(kpi_id) or _method_for(kpi_id, methods)
        predictions.append(
            kpi_registry.build_prediction(
                kpi_id, value, lo, hi, confidence, method, target=target
            )
        )

    details: Dict[str, Any] = {
        "methods": methods,
        "manufacturability_drivers": drivers,
        "cost": cost_detail,
        "water_activity_chemistry": aw_detail,
        "stability_breakdown": stability_values,
        "concentration_factor": comp.aggregates["concentration_factor"],
        "surrogate_note": surrogate_note,
        "calibration_samples": calibration_samples,
    }
    return PredictionResult(
        values=values,
        predictions=predictions,
        composition=comp,
        details=details,
        extrapolation=extrapolation,
        flags=flags,
    )


def _method_for(kpi_id: str, methods: Dict[str, str]) -> str:
    if kpi_id in ("energy_kcal", "protein_g", "fat_g", "satfat_g", "carb_g", "sugar_g", "fibre_g", "sodium_mg", "protein_energy_pct"):
        return methods.get("nutrition", "mass balance")
    if kpi_id in ("texture_index", "hardness_n", "consistency_index", "spreadability"):
        return methods.get("texture", "texture model")
    if kpi_id in ("shelf_life_days", "mould_risk", "oxidation_risk"):
        return methods.get("stability", "stability model")
    if kpi_id.startswith("cost_"):
        return methods.get("cost", "cost model")
    return methods.get(kpi_id, "model")


def evaluate_against_brief(result: PredictionResult, brief: Brief) -> Dict[str, Any]:
    """Score a prediction set against the brief's targets."""
    rows: List[Dict[str, Any]] = []
    values = result.values
    for target in brief.targets:
        if target.id not in values:
            continue
        value = float(values[target.id])
        d = kpi_registry.desirability(target, value)
        status = kpi_registry.status_of(target, value)
        rows.append(
            {
                "kpi": target.id,
                "label": target.label,
                "unit": target.unit,
                "value": round(value, 4),
                "target": target.target,
                "tolerance": target.tolerance,
                "direction": target.direction,
                "priority": target.priority,
                "hard": target.hard,
                "desirability": round(d, 4),
                "status": status,
                "gap": round(value - target.target, 4),
            }
        )
    objective = kpi_registry.overall_desirability(brief.targets, values)
    # "Failing" is a desirability threshold, not a separate rule, so the
    # pass/fail statement and the objective can never disagree. At this smooth
    # kernel, 0.2 sits at about two tolerances out - the same place status_of
    # starts calling a KPI off-target.
    failing = [r for r in rows if r["desirability"] < 0.15]
    on_target = [r for r in rows if r["status"] == "on-target"]
    # A stricter, more honest measure: a KPI at 0.01 desirability is technically
    # not failing but is useless in the plant. Passing means inside the tolerance
    # the brief declared - the same rule the trial gate applies, so the predicted
    # verdict and the measured verdict cannot disagree.
    hard_rows = [r for r in rows if r["hard"]]
    strict = [r for r in hard_rows if r["status"] == "on-target"]
    hard_compliance = (
        sum(r["desirability"] for r in hard_rows) / len(hard_rows) if hard_rows else 1.0
    )
    weakest = min(hard_rows, key=lambda r: r["desirability"]) if hard_rows else None
    return {
        "objective": round(objective, 4),
        "rows": rows,
        "failing": failing,
        "on_target_count": len(on_target),
        "total": len(rows),
        "all_on_target": len(failing) == 0 and len(rows) > 0,
        "strict_pass": len(hard_rows) > 0 and len(strict) == len(hard_rows),
        "strict_count": len(strict),
        "hard_compliance": round(hard_compliance, 4),
        "weakest": weakest,
    }


def claim_check(result: PredictionResult, brief: Brief) -> Dict[str, Any]:
    met = nutrition.claims_met(result.composition, brief.claims)
    return {
        "claims": brief.claims,
        "status": met,
        "all_met": all(met.values()) if met else True,
    }
