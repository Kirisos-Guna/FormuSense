"""Constrained, explainable formulation optimiser.

One optimiser serves two purposes, because they are the same problem:

* **generation** - move a plausible starting formula onto the brief's targets
  without destroying its plausibility;
* **reformulation** - move the current version onto the targets *after* trial
  results have been analysed, using the trial-corrected predictor.

The method is bounded coordinate pattern search over a small vector of levers.
Every lever is a single ingredient percentage or process parameter, bounded by
its practical/legal limits in the knowledge base. A move is accepted only if the
objective improves; steps shrink geometrically when no move helps. There is no
randomness anywhere, so a run is reproducible and the accepted-move log is a
complete explanation of the difference between the input and output formula.

Objective
---------
``score = weighted geometric mean desirability - 0.35 * slot plausibility penalty
- constraint penalties``

The desirability term is the standard Deringer-Suich product across the brief's
targets (priority-weighted), so a formula that misses one hard target badly
cannot hide behind good scores elsewhere. The plausibility penalty pulls each
category *slot* (structure, sweetener, fat, ...) back towards the share a product
of that class would normally use, which is what stops the optimiser from driving
sugar to zero just because a ceiling exists. Constraint penalties cover cost
ceilings and slot bounds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import engine, kb, kpi as kpi_registry, nutrition
from .engine import PredictionResult
from .formulate import (
    PREFERRED,
    SLOT_KPI,
    _allowed,
    _slot_intent,
    _target_weights,
    _utility,
)
from .types import Brief, Formulation, Item


# Slots whose share is set by a *function*, not by proportion: nobody designs a
# sauce around "5% acidulant", they dose acid until the pH is right. Their share
# deviation is therefore weighted far lower than a bulk slot's.
PRECISION_SLOTS = frozenset(
    {
        "acidulant",
        "preservative",
        "leavening",
        "emulsifier",
        "fortificant",
        "hydrocolloid",
        "gelling",
        "thickener",
        "humectant",
    }
)

# Minimum share of its nominal level that a slot may be driven to before the
# objective starts charging for it. These are the plausibility floors a developer
# would enforce by hand: a sweet biscuit needs a real sweetener level (taste and
# structure), and a fruit spread needs its sugar because sugar is what keeps it
# safe.
PLAUSIBILITY_FLOOR: Dict[str, Dict[str, float]] = {
    "cookie": {"structure": 0.70, "sweetener": 0.55, "fat": 0.55, "leavening": 0.55, "salt": 0.50},
    "spread": {"fruit": 0.75, "sweetener": 0.75, "gelling": 0.60},
    "drymix": {"grain": 0.70, "protein": 0.60, "sweetener": 0.55, "dairy": 0.35},
    "extruded_snack": {"grain": 0.75, "protein": 0.55, "fat": 0.45, "salt": 0.45},
    "bar": {"base": 0.70, "protein": 0.55, "syrup": 0.70, "inclusion": 0.25},
    "sauce": {"base": 0.60, "sweetener": 0.25, "thickener": 0.55, "salt": 0.45, "spice_flavour": 0.50},
}


@dataclass
class Lever:
    """One adjustable knob: an ingredient line, a candidate new line, or a parameter."""

    kind: str  # 'ingredient' | 'param'
    key: str
    lo: float
    hi: float
    step: float
    label: str = ""
    unit: str = ""
    slot: str = ""
    current: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.label or self.key,
            "unit": self.unit,
            "slot": self.slot,
            "lo": round(self.lo, 3),
            "hi": round(self.hi, 3),
            "from": round(self.current, 3),
        }


@dataclass
class Move:
    """An accepted change, with the KPI that justified it."""

    lever: str
    kind: str
    from_value: float
    to_value: float
    kpi: str = ""
    kpi_delta: float = 0.0
    objective_before: float = 0.0
    objective_after: float = 0.0
    # Key, unit and slot let the DOE planner, the report and the UI map a move
    # back onto the ingredient it belongs to instead of matching on a label.
    key: str = ""
    unit: str = ""
    slot: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "lever": self.lever,
            "key": self.key,
            "kind": self.kind,
            "unit": self.unit,
            "slot": self.slot,
            "from": round(self.from_value, 3),
            "to": round(self.to_value, 3),
            "driving_kpi": self.kpi,
            "kpi_improvement": round(self.kpi_delta, 4),
            "objective_before": round(self.objective_before, 5),
            "objective_after": round(self.objective_after, 5),
        }


@dataclass
class OptimizeResult:
    formulation: Formulation
    score: float
    evaluations: int
    moves: List[Move] = field(default_factory=list)
    breakdown: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    history: List[float] = field(default_factory=list)
    stopped: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 5),
            "evaluations": self.evaluations,
            "moves": [m.as_dict() for m in self.moves],
            "breakdown": self.breakdown,
            "notes": list(self.notes),
            "stopped": self.stopped,
            "history": [round(h, 5) for h in self.history],
        }


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _slot_targets(formulation: Formulation) -> Dict[str, float]:
    cat = kb.category(formulation.category)
    return {slot.id: float(slot.target) for slot in cat.slots}


def score_result(
    result: PredictionResult,
    formulation: Formulation,
    brief: Brief,
) -> Dict[str, Any]:
    """Objective breakdown for one evaluated formulation."""
    values = result.values
    weights_sum = 0.0
    log_sum = 0.0
    terms: List[Dict[str, Any]] = []
    for target in brief.targets:
        if target.id not in values:
            continue
        value = float(values[target.id])
        d = kpi_registry.desirability(target, value)
        weight = max(target.priority, 0.1) * (1.35 if target.hard else 0.75)
        log_sum += weight * math.log(max(d, 1e-3))
        weights_sum += weight
        terms.append(
            {
                "kpi": target.id,
                "value": round(value, 3),
                "target": target.target,
                "desirability": round(d, 4),
                "weight": round(weight, 3),
            }
        )
    desirability = math.exp(log_sum / weights_sum) if weights_sum > 0 else 0.0

    cat = kb.category(formulation.category)
    slot_totals: Dict[str, float] = {}
    for item in formulation.items:
        slot_totals[item.slot] = slot_totals.get(item.slot, 0.0) + item.pct
    slot_penalty = 0.0
    slot_rows: List[Dict[str, Any]] = []
    floors = PLAUSIBILITY_FLOOR.get(formulation.category, {})
    # Deviations are measured against the *brief-adjusted* intent, not the raw
    # category nominal: a biscuit asked for 12 g sugar is legitimately a less
    # sweet biscuit, so trimming its sweetener slot is intent, not drift.
    intent = _slot_intent(formulation.category, brief)
    for slot in cat.slots:
        actual = slot_totals.get(slot.id, 0.0)
        nominal = float(intent.get(slot.id, slot.target))
        weight = 0.6 if nominal > 0 else 0.15
        if slot.id in PRECISION_SLOTS:
            weight *= 0.2
        if nominal > 0:
            deviation = abs(actual - nominal) / max(nominal, 0.5)
        else:
            deviation = actual / 5.0
        slot_penalty += weight * deviation ** 2
        slot_rows.append(
            {
                "slot": slot.id,
                "pct": round(actual, 2),
                "nominal": nominal,
                "deviation": round(deviation, 3),
                "weight": round(weight, 3),
            }
        )

    constraint_penalty = 0.0
    violations: List[str] = []
    for slot_id, fraction in floors.items():
        slot = cat.slot(slot_id)
        if slot is None or slot.target <= 0:
            continue
        floor = fraction * float(intent.get(slot_id, slot.target))
        actual = slot_totals.get(slot_id, 0.0)
        if actual < floor - 1e-6:
            shortfall = (floor - actual) / max(floor, 0.5)
            constraint_penalty += 2.2 * shortfall
            violations.append(
                f"{slot.label} at {actual:.1f}% is below the {floor:.1f}% needed for a "
                f"plausible {formulation.category} (shortfall {shortfall*100:.0f}%)"
            )
    for slot in cat.slots:
        actual = slot_totals.get(slot.id, 0.0)
        if actual > slot.max + 1e-6:
            over = (actual - slot.max) / max(slot.max, 0.5)
            constraint_penalty += 1.5 * over
            violations.append(f"slot {slot.id} above maximum")
        elif slot.required and actual < slot.min - 1e-6 and slot.id not in PRECISION_SLOTS:
            # Bulk slots keep their declared minimum. Precision slots do not: a
            # "minimum 1.5% acidulant" is a vinegar-scale number, and enforcing it
            # on citric acid forbids the 0.05% dose that actually hits the pH
            # target. For those slots the KPI (pH, aw, mould risk) is the limit.
            short = (slot.min - actual) / max(slot.min, 0.5)
            constraint_penalty += 1.5 * short
            violations.append(f"slot {slot.id} below minimum")
    ceiling = brief.cost_ceiling_inr_kg
    if ceiling and "cost_inr_kg" in values:
        cost = float(values["cost_inr_kg"])
        if cost > ceiling:
            over = (cost - ceiling) / max(ceiling, 1.0)
            constraint_penalty += 3.0 * over
            violations.append(f"cost INR {cost:.0f}/kg above ceiling INR {ceiling:.0f}/kg")

    score = log_sum / weights_sum if weights_sum > 0 else 0.0
    score -= 0.35 * slot_penalty
    score -= constraint_penalty
    return {
        "score": score,
        "desirability": round(desirability, 4),
        "log_desirability": round(log_sum / weights_sum if weights_sum > 0 else 0.0, 4),
        "slot_penalty": round(slot_penalty, 4),
        "constraint_penalty": round(constraint_penalty, 4),
        "terms": terms,
        "slots": slot_rows,
        "violations": violations,
    }


def score_formulation(
    formulation: Formulation,
    brief: Brief,
    predictor: Optional[Callable[[Formulation], PredictionResult]] = None,
) -> Dict[str, Any]:
    predict = predictor or (lambda f: engine.predict(f, brief))
    return score_result(predict(formulation), formulation, brief)


# --------------------------------------------------------------------------- #
# Levers
# --------------------------------------------------------------------------- #
def _params_category(formulation: Formulation) -> kb.Category:
    return kb.category(formulation.category)


def build_levers(
    formulation: Formulation,
    brief: Brief,
    max_new_ingredients: int = 5,
    min_pct_to_move: float = 0.02,
) -> List[Lever]:
    """Enumerate the adjustable ingredients and process parameters."""
    table = kb.ingredients()
    cat = _params_category(formulation)
    weights = _target_weights(brief)
    levers: List[Lever] = []
    present = {item.ingredient_id for item in formulation.items}

    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        if ing is None or item.pct < min_pct_to_move:
            continue
        hi = ing.hard_max_pct if ing.hard_max_pct > 0 else 100.0
        # Floor is zero, not the ingredient's min_pct: "minimum 0.05% citric
        # acid" describes a line that is used, and it forbids the far more
        # common decision - to leave the acid out of this formula entirely.
        # Slot-level constraints and per-ingredient maxima still apply.
        lo = 0.0
        # Step size scales with the line: a 40% flour moves in whole points, a
        # 0.05% citric acid dose moves in hundredths. Without this the acid dose
        # cannot be found and the pH target is overshot into pH 2.
        levers.append(
            Lever(
                kind="ingredient",
                key=ing.id,
                lo=lo,
                hi=hi,
                step=max(0.015, min(2.0, 0.35 * item.pct + 0.02)),
                label=ing.name,
                unit="%",
                slot=item.slot,
                current=item.pct,
            )
        )

    # Candidate lines the optimiser may introduce (a lever that starts at zero).
    introduced: Dict[str, float] = {}
    for slot in cat.slots:
        preferred = PREFERRED.get(brief.category, {}).get(slot.id, [])
        pool: List[kb.Ingredient] = []
        for ingredient_id in preferred:
            ing = table.get(ingredient_id)
            if ing and ing.group in slot.groups and _allowed(ing, brief) and ing.id not in present:
                pool.append(ing)
        for ing in kb.by_group(*slot.groups):
            if ing.id in present or ing in pool or not _allowed(ing, brief):
                continue
            pool.append(ing)
        pool.sort(key=lambda i: (-_utility(i, slot, brief, weights), i.cost_inr_kg))
        for ing in pool[:2]:
            if ing.id in introduced:
                continue
            hi = min(ing.hard_max_pct if ing.hard_max_pct > 0 else 10.0, slot.max)
            if hi <= 0.05:
                continue
            introduced[ing.id] = hi
            levers.append(
                Lever(
                    kind="ingredient",
                    key=ing.id,
                    lo=0.0,
                    hi=hi,
                    step=0.5,
                    label=ing.name,
                    unit="%",
                    slot=slot.id,
                    current=0.0,
                )
            )
            if len(introduced) >= max_new_ingredients:
                break
        if len(introduced) >= max_new_ingredients:
            break

    for param in cat.parameters:
        if "water_added_pct" in param.id:
            continue  # process water is already an ingredient line; avoid double counting
        span = param.max - param.min
        levers.append(
            Lever(
                kind="param",
                key=param.id,
                lo=param.min,
                hi=param.max,
                step=max(span * 0.06, 0.1),
                label=param.label,
                unit=param.unit,
                current=float(formulation.params.get(param.id, param.default)),
            )
        )
    return levers


def _fit_to_100(formulation: Formulation, slack_ingredients: Sequence[str] = ()) -> None:
    """Rescale lines so the formulation totals 100% inside every declared maximum."""
    table = kb.ingredients()
    total = formulation.total_pct
    if total <= 0 or abs(total - 100.0) < 1e-9:
        return
    factor = 100.0 / total
    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        hi = (ing.hard_max_pct if ing and ing.hard_max_pct > 0 else 100.0)
        item.pct = min(item.pct * factor, hi)
    residual = 100.0 - formulation.total_pct
    if abs(residual) < 1e-6:
        return
    if residual < 0:
        # Over 100: trim the largest lines proportionally.
        for _ in range(6):
            ordered = sorted(formulation.items, key=lambda i: -i.pct)
            for item in ordered:
                if residual >= -1e-6:
                    break
                take = min(item.pct, -residual)
                item.pct -= take
                residual += take
        return
    # Under 100: push the residual into the water line, then into lines with room.
    preferred = [i for i in formulation.items if i.slot == "water" or i.ingredient_id in slack_ingredients]
    for group in (preferred, sorted(formulation.items, key=lambda i: -i.pct)):
        for item in group:
            if residual <= 1e-6:
                break
            ing = table.get(item.ingredient_id)
            hi = (ing.hard_max_pct if ing and ing.hard_max_pct > 0 else 100.0)
            room = max(hi - item.pct, 0.0)
            if room <= 0:
                continue
            step = min(room, residual)
            item.pct += step
            residual -= step
    if residual > 0.05:
        # No declared room anywhere: scale the whole formula up (documented in notes
        # by the caller) so the mass balance still closes.
        for item in formulation.items:
            item.pct += residual * (item.pct / max(formulation.total_pct, 1e-9))


def _apply_move(formulation: Formulation, lever: Lever, delta: float) -> Tuple[Formulation, float]:
    """Return a moved copy of the formulation and the realised change."""
    clone = Formulation(
        category=formulation.category,
        version=formulation.version,
        label=formulation.label,
        notes=list(formulation.notes),
        items=[Item(i.ingredient_id, i.pct, i.slot) for i in formulation.items],
        params=dict(formulation.params),
    )
    if lever.kind == "param":
        old = float(clone.params.get(lever.key, lever.current))
        new = min(max(old + delta, lever.lo), lever.hi)
        clone.params[lever.key] = new
        return clone, new - old
    target_ingredient = next((i for i in clone.items if i.ingredient_id == lever.key), None)
    if target_ingredient is None:
        old = 0.0
        new = min(max(delta, lever.lo), lever.hi)
        if new <= 1e-6:
            return clone, 0.0
        clone.items.append(Item(lever.key, new, lever.slot))
    else:
        old = target_ingredient.pct
        new = min(max(old + delta, lever.lo), lever.hi)
        target_ingredient.pct = new
    _fit_to_100(clone)
    realised = new - old
    if abs(realised) < 1e-9:
        return clone, 0.0
    for item in clone.items:
        if item.ingredient_id == lever.key:
            realised = item.pct - old
            break
    return clone, realised


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def optimise(
    start: Formulation,
    brief: Brief,
    predictor: Optional[Callable[[Formulation], PredictionResult]] = None,
    budget: int = 900,
    max_levers: int = 36,
    step_scale_start: float = 1.0,
    min_step_scale: float = 0.075,
    history_limit: int = 240,
) -> OptimizeResult:
    """Bounded coordinate pattern search on the formulation and its process."""
    predict = predictor or (lambda f: engine.predict(f, brief))
    notes: List[str] = []
    working = Formulation(
        category=start.category,
        version=start.version,
        label=start.label,
        notes=list(start.notes),
        items=[Item(i.ingredient_id, i.pct, i.slot) for i in start.items],
        params={**kb.category(start.category).default_params(), **dict(start.params)},
    )
    _fit_to_100(working)

    levers = build_levers(working, brief, max_new_ingredients=5)
    if len(levers) > max_levers:
        # Keep the biggest lines and every parameter; drop the smallest lines.
        ingredient_levers = [l for l in levers if l.kind == "ingredient"]
        param_levers = [l for l in levers if l.kind == "param"]
        ingredient_levers.sort(key=lambda l: -l.current)
        keep = max(max_levers - len(param_levers), 8)
        dropped = ingredient_levers[keep:]
        levers = ingredient_levers[:keep] + param_levers
        if dropped:
            notes.append(
                f"{len(dropped)} minor lines were held fixed during optimisation "
                f"(largest held: {max(dropped, key=lambda l: l.current).label})."
            )

    best_result = predict(working)
    best = score_result(best_result, working, brief)
    evaluations = 1
    history: List[float] = [best["score"]]
    moves: List[Move] = []
    step_scale = step_scale_start
    stopped = "step floor reached"

    while evaluations < budget and step_scale > min_step_scale:
        improved_in_pass = False
        for lever in levers:
            if evaluations >= budget:
                break
            for direction in (1.0, -1.0):
                delta = direction * lever.step * step_scale
                if abs(delta) < 0.02 and lever.kind == "param":
                    continue
                candidate, realised = _apply_move(working, lever, delta)
                if abs(realised) < 0.01 if lever.kind == "param" else abs(realised) < 1e-4:
                    continue
                candidate_result = predict(candidate)
                evaluations += 1
                score = score_result(candidate_result, candidate, brief)
                # A move must earn a meaningful gain. Accepting one-in-a-million
                # improvements lets the search wander for hundreds of moves on
                # micro-tuning while the structural fixes (dropping an acid line,
                # swapping a grain) never get tried, because every pass finds
                # *something* and so never escalates. The threshold scales with
                # the step size, so fine polishing is still allowed at the end.
                if score["score"] > best["score"] + improve_eps(step_scale):
                    driving_kpi, kpi_delta = _best_kpi_improvement(best, score)
                    moves.append(
                        Move(
                            lever=lever.label or lever.key,
                            kind=lever.kind,
                            key=lever.key,
                            unit=lever.unit,
                            slot=lever.slot,
                            from_value=lever.current,
                            to_value=lever.current + realised,
                            kpi=driving_kpi,
                            kpi_delta=kpi_delta,
                            objective_before=best["score"],
                            objective_after=score["score"],
                        )
                    )
                    working = candidate
                    best = score
                    best_result = candidate_result
                    # Re-read every lever from the moved formulation: rescaling to
                    # 100% shifts other lines too, so lever state must not drift.
                    _refresh_levers(levers, working)
                    history.append(best["score"])
                    improved_in_pass = True
                    break
        if not improved_in_pass:
            # Try full-removal moves once per pass: they let the optimiser simplify.
            for lever in [l for l in levers if l.kind == "ingredient" and l.current > 1.0]:
                if evaluations >= budget:
                    break
                candidate, realised = _apply_move(working, lever, -lever.current)
                if abs(realised) < 1e-3:
                    continue
                candidate_result = predict(candidate)
                evaluations += 1
                score = score_result(candidate_result, candidate, brief)
                if score["score"] > best["score"] + improve_eps(step_scale):
                    driving_kpi, kpi_delta = _best_kpi_improvement(best, score)
                    moves.append(
                        Move(
                            lever=f"{lever.label} (removed)",
                            kind="drop",
                            key=lever.key,
                            unit=lever.unit,
                            slot=lever.slot,
                            from_value=lever.current,
                            to_value=0.0,
                            kpi=driving_kpi,
                            kpi_delta=kpi_delta,
                            objective_before=best["score"],
                            objective_after=score["score"],
                        )
                    )
                    working = candidate
                    best = score
                    best_result = candidate_result
                    _refresh_levers(levers, working)
                    improved_in_pass = True
                    break
        if not improved_in_pass:
            # Coordinated moves. Many real formulation changes need two levers at
            # once - grow a low-fibre grain while shrinking a high-fibre one, or
            # trade protein source against cost - and a single-coordinate search
            # can never make them because either half alone looks worse.
            paired = _pair_pass(
                working,
                best,
                best_result,
                levers,
                brief,
                predict,
                budget - evaluations,
                step_scale=step_scale,
            )
            if paired is not None:
                working, best, best_result, evaluations, move = paired
                moves.append(move)
                _refresh_levers(levers, working)
                history.append(best["score"])
                improved_in_pass = True
        if not improved_in_pass:
            step_scale *= 0.55
            stopped = "converged (no improving single or paired move at this step size)"
        if len(history) > history_limit:
            history = history[::2] + [history[-1]]

    working.notes = list(working.notes) + [n for n in notes if n not in working.notes]
    return OptimizeResult(
        formulation=working,
        score=best["score"],
        evaluations=evaluations,
        moves=moves,
        breakdown=best,
        notes=notes,
        history=history,
        stopped=stopped,
    )


# Acceptance threshold as a function of the current step size: coarse at the
# start (structural decisions only), fine at the end (polish).
IMPROVE_EPS_ROOT = 5.0e-3


def improve_eps(step_scale: float) -> float:
    return max(IMPROVE_EPS_ROOT * step_scale, 5.0e-5)


def _pair_pass(
    working: Formulation,
    best: Dict[str, Any],
    best_result: PredictionResult,
    levers: Sequence[Lever],
    brief: Brief,
    predict: Callable[[Formulation], PredictionResult],
    budget_left: int,
    step_scale: float = 1.0,
    candidate_limit: int = 9,
    magnitudes: Sequence[float] = (1.0, 0.35),
) -> Optional[Tuple[Formulation, Dict[str, Any], PredictionResult, int, Move]]:
    """Search for a two-lever move: grow one lever while shrinking another."""
    if budget_left <= 6:
        return None
    candidates = sorted(levers, key=lambda l: -abs(l.step * (1.0 if l.kind == "param" else l.current)))[
        :candidate_limit
    ]
    best_move = None
    best_score = best["score"]
    best_formulation = None
    best_prediction = None
    evaluations = 0
    for index, first in enumerate(candidates):
        for second in candidates[index + 1 :]:
            for magnitude in magnitudes:
                for first_step, second_step in ((1.0, -1.0), (-1.0, 1.0)):
                    if evaluations >= budget_left - 1:
                        return None
                    grown, delta_one = _apply_move(working, first, first_step * first.step * magnitude)
                    if abs(delta_one) < (0.01 if first.kind == "param" else 1e-4):
                        continue
                    # Find the moved lever's new position, then pair it.
                    moved_first = Lever(**{**first.__dict__, "current": first.current + delta_one})
                    paired, delta_two = _apply_move(grown, second, second_step * second.step * magnitude)
                    if abs(delta_two) < (0.01 if second.kind == "param" else 1e-4):
                        continue
                    evaluations += 1
                    result = predict(paired)
                    score = score_result(result, paired, brief)
                    if score["score"] > best_score + improve_eps(step_scale):
                        best_score = score["score"]
                        best_formulation = paired
                        best_prediction = result
                        best_move = (moved_first, second, score)
    if best_formulation is None or best_move is None:
        return None
    first_lever, second_lever, score = best_move
    move = Move(
        lever=f"{first_lever.label} + {second_lever.label} (paired move)",
        kind="pair",
        key=first_lever.key,
        unit=first_lever.unit,
        slot=first_lever.slot,
        from_value=0.0,
        to_value=0.0,
        kpi=_best_kpi_improvement(best, score)[0],
        kpi_delta=_best_kpi_improvement(best, score)[1],
        objective_before=best["score"],
        objective_after=score["score"],
    )
    return best_formulation, score, best_prediction, evaluations, move


def _refresh_levers(levers: Sequence[Lever], formulation: Formulation) -> None:
    """Sync lever state with the formulation they describe."""
    percentages = {item.ingredient_id: item.pct for item in formulation.items}
    for lever in levers:
        if lever.kind == "param":
            lever.current = float(formulation.params.get(lever.key, lever.current))
        else:
            lever.current = float(percentages.get(lever.key, 0.0))


def _best_kpi_improvement(before: Dict[str, Any], after: Dict[str, Any]) -> Tuple[str, float]:
    """Which KPI gained the most desirability in this move."""
    before_terms = {t["kpi"]: t for t in before.get("terms", [])}
    best_kpi = ""
    best_gain = 0.0
    for term in after.get("terms", []):
        previous = before_terms.get(term["kpi"])
        if previous is None:
            continue
        gain = term["desirability"] - previous["desirability"]
        if gain > best_gain:
            best_gain = gain
            best_kpi = term["kpi"]
    if not best_kpi:
        # Nothing directly improved: report the worst remaining KPI so the log
        # still says what the move was protecting.
        worst = min(after.get("terms", []), key=lambda t: t["desirability"], default=None)
        if worst:
            best_kpi = worst["kpi"]
    return best_kpi, best_gain


def leverage_report(levers: Sequence[Lever], formulation: Formulation, brief: Brief) -> List[Dict[str, Any]]:
    """Finite-difference sensitivity of each KPI to each lever.

    Used by the UI and the report to answer "what actually controls this KPI?".
    Each lever is nudged by half its step and the resulting KPI changes are
    recorded and normalised per unit (% or parameter unit).
    """
    base = engine.predict(formulation, brief)
    rows: List[Dict[str, Any]] = []
    for lever in levers:
        delta = 0.5 * lever.step
        if lever.kind == "ingredient":
            delta = max(delta, 0.5)
        if abs(delta) < 1e-6:
            continue
        moved, realised = _apply_move(formulation, lever, delta)
        if abs(realised) < 1e-6:
            moved, realised = _apply_move(formulation, lever, -delta)
        if abs(realised) < 1e-6:
            continue
        after = engine.predict(moved, brief)
        effects: List[Dict[str, Any]] = []
        for kpi_id, value in after.values.items():
            before_value = base.values.get(kpi_id)
            if before_value is None:
                continue
            change = (value - before_value) / realised
            if abs(change) < 1e-6:
                continue
            effects.append({"kpi": kpi_id, "per_unit": round(change, 4), "direction": "increase" if change > 0 else "decrease"})
        effects.sort(key=lambda e: -abs(e["per_unit"]))
        rows.append(
            {
                "lever": lever.label or lever.key,
                "key": lever.key,
                "kind": lever.kind,
                "unit": lever.unit,
                "slot": lever.slot,
                "top_effects": effects[:4],
            }
        )
    rows.sort(key=lambda r: -abs(r["top_effects"][0]["per_unit"]) if r["top_effects"] else 0.0)
    return rows
