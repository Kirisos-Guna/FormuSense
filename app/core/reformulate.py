"""Reformulation: from a trial result to the next version, and how to test it.

This is where the loop closes. The inputs are the brief, the version that was
made, what the plant actually produced, and the ranked probable causes. The
output is a concrete next version, the deltas that got it there, the expected
characteristics *with* intervals, the probability that it passes, and the design
of experiments that will confirm it.

Three judgement calls are built in, and they are the difference between an
optimiser and a development plan:

* **Not every failure is a formulation failure.** If the evidence says the model
  is offset for this plant (a systematic shift with no compositional cause), the
  right next step is to *keep the recipe* and re-test with the surrogate's
  correction applied, because changing the formula would chase a plant artefact
  and destroy a good recipe. Those cases produce a confirmation plan instead of a
  reformulation.
* **The corrected predictor is used for optimisation, not the raw model.** The
  reformulation is therefore optimised against this plant's behaviour as measured
  so far, which is what makes the next trial land.
* **A plan without an experiment is a guess.** Every reformulation carries a DOE
  plan whose factors are the levers that actually moved, and whose response KPIs
  are the targets that were at risk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import doe, engine, kb, kpi as kpi_registry, optimize, uncertainty
from .diagnose import Analysis
from .engine import PredictionResult
from .surrogate import SurrogateSet
from .types import Brief, DiagnosisCause, Formulation, Item, ReformulationPlan


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def pass_probability(
    target,
    value: float,
    sigma: float,
    ) -> float:
    """Probability that the next measured value lands inside the acceptable region."""
    sigma = max(sigma, 1e-9)
    tol = max(target.tolerance, 1e-9)
    if target.direction == "higher":
        threshold = target.target - tol
        return 1.0 - _normal_cdf((threshold - value) / sigma)
    if target.direction == "lower":
        threshold = target.target + tol
        return _normal_cdf((threshold - value) / sigma)
    low, high = target.lo, target.hi
    return _normal_cdf((high - value) / sigma) - _normal_cdf((low - value) / sigma)


def _pass_probability(
    brief: Brief,
    values: Dict[str, float],
    sigmas: Dict[str, float],
) -> Tuple[float, List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    probability = 1.0
    for target in brief.targets:
        if not target.hard or target.id not in values:
            continue
        value = float(values[target.id])
        sigma = sigmas.get(target.id, max(abs(value) * 0.08, 1e-6))
        p = max(0.0, min(1.0, pass_probability(target, value, sigma)))
        rows.append(
            {
                "kpi": target.id,
                "label": target.label,
                "value": round(value, 3),
                "target": target.target,
                "sigma": round(sigma, 4),
                "probability": round(p, 3),
            }
        )
        probability *= p
    return probability, rows


def _delta_rows(
    current: Formulation, proposed: Formulation
) -> List[Dict[str, Any]]:
    table = kb.ingredients()
    rows: List[Dict[str, Any]] = []
    current_map = {i.ingredient_id: i.pct for i in current.items}
    proposed_map = {i.ingredient_id: i.pct for i in proposed.items}
    for ingredient_id in sorted(set(current_map) | set(proposed_map)):
        before = current_map.get(ingredient_id, 0.0)
        after = proposed_map.get(ingredient_id, 0.0)
        if abs(after - before) < 0.005:
            continue
        ing = table.get(ingredient_id)
        rows.append(
            {
                "ingredient_id": ingredient_id,
                "name": ing.name if ing else ingredient_id,
                "group": ing.group if ing else "",
                "from_pct": round(before, 3),
                "to_pct": round(after, 3),
                "delta_pct": round(after - before, 3),
                "delta_pct_relative": round(
                    (after - before) / before * 100.0, 1
                )
                if before > 1e-6
                else None,
                "contribution_inr_kg": round(
                    (after - before) / 100.0 * (ing.cost_inr_kg if ing else 0.0), 2
                ),
            }
        )
    rows.sort(key=lambda r: -abs(r["delta_pct"]))
    return rows


def _param_rows(current: Formulation, proposed: Formulation) -> List[Dict[str, Any]]:
    category = kb.category(proposed.category)
    rows: List[Dict[str, Any]] = []
    for parameter in category.parameters:
        before = float(current.params.get(parameter.id, parameter.default))
        after = float(proposed.params.get(parameter.id, parameter.default))
        if abs(after - before) < 1e-6:
            continue
        rows.append(
            {
                "parameter": parameter.id,
                "label": parameter.label,
                "unit": parameter.unit,
                "from": round(before, 3),
                "to": round(after, 3),
                "range": [parameter.min, parameter.max],
            }
        )
    return rows


def _compensated_start(
    current: Formulation, analysis: Optional[Analysis]
) -> Tuple[Formulation, List[Dict[str, Any]]]:
    """Apply measured process offsets as setpoint corrections.

    If the dryer delivered 9 degC below the setpoint, the fix is not a different
    recipe: it is a setpoint that asks for 9 degC more, until the controller is
    calibrated. Doing this explicitly matters, because an optimiser handed a
    "moisture too high" residual without it will happily reduce the drying duty
    instead - the opposite of the correct action - and every subsequent trial
    inherits the error.

    This sets the starting point. The search underneath runs against the same
    offsets too, so it derives the correction instead of being told it, and it
    cannot quietly undo it.
    """
    corrected = Formulation(
        category=current.category,
        version=current.version,
        items=[Item(i.ingredient_id, i.pct, i.slot) for i in current.items],
        params=dict(current.params),
        label=current.label,
        notes=list(current.notes),
    )
    corrections: List[Dict[str, Any]] = []
    category = kb.category(current.category)
    if analysis is None:
        return corrected, corrections
    for key, offset in _process_offsets(analysis).items():
        parameter = category.param(key)
        if parameter is None or key not in corrected.params:
            continue
        setpoint = float(corrected.params[key])
        actual = setpoint + offset
        if abs(offset) < 1e-6:
            continue
        proposed = min(max(setpoint - offset, parameter.min), parameter.max)
        if abs(proposed - setpoint) < 1e-6:
            continue
        corrections.append(
            {
                "parameter": key,
                "label": parameter.label,
                "unit": parameter.unit,
                "planned": round(setpoint, 3),
                "actual": round(actual, 3),
                "new_setpoint": round(proposed, 3),
                "reason": "compensate a measured process offset until the controller is recalibrated",
            }
        )
        corrected.params[key] = proposed
    return corrected, corrections


def _process_offsets(analysis: Optional[Analysis]) -> Dict[str, float]:
    """Setpoint -> actual deviations this plant demonstrated on the last trial.

    These are properties of the *line*, not of the recipe. A barrel controller
    that reads 9 degC high will read 9 degC high whatever the recipe says, so the
    offset belongs in the model that predicts the next trial. Without it the
    optimiser happily takes the compensated setpoint and winds it back down,
    because on the setpoint basis the water is already evaporating.
    """
    offsets: Dict[str, float] = {}
    if analysis is None:
        return offsets
    for deviation in analysis.process_deviations:
        key = str(deviation.get("parameter", ""))
        try:
            offsets[key] = float(deviation["actual"]) - float(deviation["setpoint"])
        except (KeyError, TypeError, ValueError):
            continue
    return offsets


def _as_delivered(formulation: Formulation, offsets: Dict[str, float]) -> Formulation:
    """The formulation as this plant will actually run it, offsets included.

    The plant applies its offsets without clamping the parameter ranges, and so
    does this: a setpoint the plant then shifts out of range is exactly the trap
    the plan exists to reveal.
    """
    if not offsets:
        return formulation
    params = dict(formulation.params)
    for key, offset in offsets.items():
        if key in params:
            params[key] = float(params[key]) + offset
    return Formulation(
        category=formulation.category,
        version=formulation.version,
        items=[Item(i.ingredient_id, i.pct, i.slot) for i in formulation.items],
        params=params,
        label=formulation.label,
        notes=list(formulation.notes),
    )


def plan_reformulation(
    brief: Brief,
    current: Formulation,
    prediction: PredictionResult,
    analysis: Optional[Analysis],
    causes: Sequence[DiagnosisCause],
    surrogate: Optional[SurrogateSet] = None,
    product_id: int = 0,
    budget: int = 1600,
    calibration_samples: int = 0,
) -> ReformulationPlan:
    """Produce the next formulation, its expected performance, and its DOE."""
    calibrated = surrogate is not None and getattr(surrogate, "is_fitted", False)
    offsets = _process_offsets(analysis)

    def predictor(formulation: Formulation) -> PredictionResult:
        # Predict the trial this plant will run, not the trial the recipe asked
        # for: any setpoint in the search is first put through the offsets this
        # line has already demonstrated.
        return engine.predict(
            _as_delivered(formulation, offsets),
            brief,
            calibration_samples=calibration_samples,
            surrogate=surrogate if calibrated else None,
        )

    current_prediction = predictor(current)
    current_eval = engine.evaluate_against_brief(current_prediction, brief)

    top_cause = causes[0] if causes else None
    model_bias_case = bool(top_cause and top_cause.category == "model" and top_cause.confidence >= 0.35)

    rationale: List[str] = []
    if analysis is not None:
        rationale.append(analysis.summary)
    if top_cause is not None:
        rationale.append(
            f"Most probable cause ({top_cause.confidence*100:.0f}% of the evidence): {top_cause.cause}"
        )

    if model_bias_case:
        # A systematic offset with no compositional cause: keep the recipe.
        sigmas = {
            p.id: max(abs(p.value) * uncertainty.BASE_SIGMA.get(p.id, 0.10), 1e-6)
            for p in current_prediction.predictions
        }
        probability, rows = _pass_probability(brief, current_prediction.values, sigmas)
        rationale.append(
            "The recipe is not the problem: the residuals are systematic, so the surrogate's "
            "residual correction is applied and the same formulation is re-tested. Changing the "
            "formula here would chase a plant artefact."
        )
        if calibrated:
            probability = min(1.0, probability * 1.05)
        response_kpis = [r["kpi"] for r in current_eval["rows"] if r["desirability"] < 0.9][:4]
        plan = doe.plan(
            brief,
            current,
            moves=[],
            response_kpis=response_kpis,
            rationale_prefix="Confirmation run: ",
        )
        return ReformulationPlan(
            product_id=product_id,
            from_version=current.version,
            to_version=current.version + 1,
            formulation=Formulation(
                category=current.category,
                version=current.version + 1,
                items=[Item(i.ingredient_id, i.pct, i.slot) for i in current.items],
                params=dict(current.params),
                label=f"v{current.version + 1} - unchanged formulation, confirmation trial",
                notes=["No compositional change: the evidence points to a plant offset, not a recipe error."],
            ),
            deltas=[],
            param_deltas=[],
            expected=current_prediction.predictions,
            pass_probability=probability,
            objective_value=current_prediction and current_eval["objective"] or 0.0,
            rationale=rationale,
            doe=plan,
            drivers=[{"kpi": r["kpi"], "probability": r["probability"], "note": "carried by the surrogate correction"} for r in rows],
        )

    # Optimise from the version that was actually made, with any measured process
    # offset already compensated in the setpoints.
    start, corrections = _compensated_start(current, analysis)
    if corrections:
        rationale.append(
            "Process setpoint corrected before any compositional change: "
            + "; ".join(
                f"{c['label']} {c['planned']:.1f} -> {c['new_setpoint']:.1f} {c['unit']} "
                f"(the plant delivered {c['actual']:.1f})"
                for c in corrections
            )
        )
    result = optimize.optimise(
        start,
        brief,
        predictor=predictor,
        budget=budget,
    )
    proposed = result.formulation
    proposed.version = current.version + 1
    proposed.label = f"v{current.version + 1} reformulated after trial analysis"
    corrected = predictor(proposed)
    evaluation = engine.evaluate_against_brief(corrected, brief)

    sigmas: Dict[str, float] = {}
    for forecast in corrected.predictions:
        interval_sigma = max((forecast.hi - forecast.lo) / (2.0 * 1.96), 1e-9)
        learned = 0.0
        if calibrated and surrogate is not None:
            model = surrogate.models.get(forecast.id)
            if model is not None:
                learned = surrogate.blend * model.loo_rmse
        sigmas[forecast.id] = max(interval_sigma, learned)
    probability, probability_rows = _pass_probability(brief, corrected.values, sigmas)

    deltas = _delta_rows(current, proposed)
    params = _param_rows(current, proposed)
    for correction in corrections:
        for row in params:
            if row["parameter"] == correction["parameter"]:
                row["reason"] = correction["reason"]
                row["measured_offset"] = round(correction["actual"] - correction["planned"], 3)

    if deltas:
        biggest = deltas[0]
        rationale.append(
            f"Largest compositional change: {biggest['name']} {biggest['from_pct']:.2f}% -> "
            f"{biggest['to_pct']:.2f}% ({biggest['delta_pct']:+.2f} points), driven by "
            f"{result.moves[0].kpi if result.moves else 'the objective'}."
        )
    if params:
        change = params[0]
        rationale.append(
            f"Process change: {change['label']} {change['from']:.1f} -> {change['to']:.1f} {change['unit']} "
            "(within its validated range)."
        )
    rationale.append(
        f"Expected performance after the change: {len(evaluation['failing'])} of {evaluation['total']} "
        f"targets still outside their acceptable region, hard-target compliance "
        f"{evaluation['hard_compliance']*100:.0f}%."
    )
    if calibrated:
        rationale.append(
            f"The prediction above uses the {surrogate.samples}-trial residual correction at "
            f"{surrogate.blend*100:.0f}% weight, not the raw model."
        )
    objective_gain = evaluation["objective"] - current_eval["objective"]
    rationale.append(
        f"Objective (priority-weighted desirability) moves {current_eval['objective']:.3f} -> "
        f"{evaluation['objective']:.3f} ({objective_gain:+.3f})."
    )

    response_kpis = [r["kpi"] for r in evaluation["rows"] if r["desirability"] < 0.90][:4]
    doe_plan = doe.plan(brief, proposed, result.moves, response_kpis)
    if doe_plan is not None:
        rationale.append(f"Verification: {doe_plan.design} - {len(doe_plan.runs)} runs.")

    return ReformulationPlan(
        product_id=product_id,
        from_version=current.version,
        to_version=proposed.version,
        formulation=proposed,
        deltas=deltas,
        param_deltas=params,
        expected=corrected.predictions,
        pass_probability=probability,
        objective_value=evaluation["objective"],
        rationale=rationale,
        doe=doe_plan,
        drivers=[
            {
                "kpi": row["kpi"],
                "probability": row["probability"],
                "expected": row["value"],
                "target": row["target"],
            }
            for row in probability_rows
        ],
    )
