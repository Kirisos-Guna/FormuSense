"""Design of experiments for the next trial.

A reformulation is a hypothesis. Testing it with one more physical trial answers
"did it work?" but not "which factor did it?" and not "is the response curved?".
This module turns the reformulation's levers into a small, randomisation-ordered
design:

* **1 factor** - a three-level sweep (low, current, high). Two points cannot show
  curvature, and curvature is exactly what a drying or browning response has.
* **2-4 factors** - a two-level full factorial with two centre points, so every
  main effect and every interaction is estimable, and the centre points give a
  pure-error estimate plus a check on curvature.
* **5+ factors** - a resolution IV fractional factorial (half or quarter fraction)
  with centre points: main effects stay clear of two-factor interactions, which
  is the standard screening compromise when trials are expensive.

Whatever the design, a replicate of the *current* formulation is included as a
control run. Without it, a shift between trials cannot be separated from a shift
in the plant, which is the most common way a pilot trial is misread.

The design is emitted as runs with factor values in real units, a purpose for
each run, and a fixed randomisation order so the same plan is reproducible.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import kb
from .types import Brief, DoePlan, DoeRun, Formulation


@dataclass
class Factor:
    """One experimental factor: an ingredient percentage or a process parameter."""

    key: str
    label: str
    low: float
    centre: float
    high: float
    unit: str = ""
    kind: str = "ingredient"
    weight: float = 0.0  # expected influence, used to rank and to cap the design

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "kind": self.kind,
            "low": round(self.low, 3),
            "centre": round(self.centre, 3),
            "high": round(self.high, 3),
        }


def factors_from_moves(
    formulation: Formulation,
    moves: Sequence[Any],
    max_factors: int = 4,
    ingredient_span_pct: float = 1.5,
    param_span_fraction: float = 0.18,
) -> List[Factor]:
    """Turn the accepted reformulation moves into experiment factors.

    The span matters: a factor varied over a range too narrow to change the
    response wastes the whole design, and too wide stops being a product. Bulk
    ingredients are varied by a fixed percentage-point span, *relatively minor*
    lines by a proportion of their own level, and process parameters by a
    fraction of their validated range.
    """
    category = kb.category(formulation.category)
    seen: Dict[str, Factor] = {}
    for move in moves:
        key = getattr(move, "key", None) or getattr(move, "lever", "")
        kind = getattr(move, "kind", "ingredient")
        if kind == "pair":
            continue  # a paired move is not a single experimental factor
        current = float(getattr(move, "to_value", 0.0))
        weight = abs(float(getattr(move, "objective_after", 0.0)) - float(getattr(move, "objective_before", 0.0)))
        label = getattr(move, "lever", "") or key
        if kind == "param":
            parameter = category.param(key)
            if parameter is None:
                continue
            span = max((parameter.max - parameter.min) * param_span_fraction, 0.5)
            low = max(parameter.min, current - span)
            high = min(parameter.max, current + span)
        else:
            if key not in kb.ingredients():
                continue
            span = min(ingredient_span_pct, max(0.4, 0.35 * current)) if current < 4.0 else ingredient_span_pct
            low = max(0.0, current - span)
            high = current + span
        if close := seen.get(key):
            close.weight = max(close.weight, weight)
            continue
        seen[key] = Factor(
            key=key,
            label=label,
            low=low,
            centre=current,
            high=high,
            unit="%" if kind == "ingredient" else str(getattr(move, "unit", "")),
            kind=kind,
            weight=weight,
        )
    ranked = sorted(seen.values(), key=lambda f: -f.weight)
    return ranked[:max_factors]


def _fractional_design(factor_count: int, half: bool = True) -> List[Tuple[int, ...]]:
    """Resolution IV fraction: the first k-1 factors full, the last as their product."""
    base_count = factor_count - 1
    base = list(itertools.product((0, 1), repeat=base_count))
    runs: List[Tuple[int, ...]] = []
    for combo in base:
        if half and sum(combo) % 2 == 1:
            continue
        parity = 0
        for value in combo:
            parity ^= value
        runs.append(tuple(combo) + (parity,))
    return runs


def plan(
    brief: Brief,
    formulation: Formulation,
    moves: Sequence[Any],
    response_kpis: Sequence[str],
    max_factors: int = 4,
    seed: int = 7,
    rationale_prefix: str = "",
) -> Optional[DoePlan]:
    """Build a DOE plan from the moves of a reformulation."""
    factors = factors_from_moves(formulation, moves, max_factors=max_factors)
    if not factors:
        return None
    responses = [k for k in response_kpis][:6]
    runs: List[DoeRun] = []

    if len(factors) == 1:
        factor = factors[0]
        design = "three-level sweep with control replicate"
        rationale = (
            f"One factor dominates the change ({factor.label}), so a three-level sweep "
            "tests its direction and its curvature. Three levels, not two, because "
            "moisture, browning and texture responses are curved, and a two-point test "
            "cannot see that."
        )
        for level, value, purpose in (
            ("low", factor.low, "lower level"),
            ("current", factor.centre, "current formulation (control)"),
            ("high", factor.high, "upper level"),
        ):
            runs.append(DoeRun(run=0, factors={factor.label: value}, purpose=purpose))
    else:
        use_fraction = len(factors) >= 5
        design = (
            f"{'resolution IV fractional' if use_fraction else 'two-level full factorial'} "
            f"in {len(factors)} factors with 2 centre points"
        )
        rationale = (
            f"{len(factors)} factors moved together, so main effects and their "
            f"interactions must be separable. {'A half fraction keeps the design at ' + str(2 ** (len(factors) - 1) + 2) + ' runs while keeping main effects clear of two-factor interactions. ' if use_fraction else 'A full factorial resolves every interaction. '}"
            "Centre points give a pure-error estimate and a curvature check, and the "
            "control replicate separates a formulation change from a plant shift."
        )
        combos = (
            _fractional_design(len(factors))
            if use_fraction
            else list(itertools.product((0, 1), repeat=len(factors)))
        )
        for combo in combos:
            values = {
                factor.label: (factor.high if level else factor.low)
                for factor, level in zip(factors, combo)
            }
            runs.append(DoeRun(run=0, factors=values, purpose="factorial point"))
        runs.append(DoeRun(run=0, factors={f.label: f.centre for f in factors}, purpose="centre point (curvature check)"))
        runs.append(DoeRun(run=0, factors={f.label: f.centre for f in factors}, purpose="centre point (pure error)"))

    # A control replicate of the *current* formulation is always included.
    runs.append(
        DoeRun(
            run=0,
            factors={f.label: f.centre for f in factors},
            purpose="control replicate - current formulation at current settings",
        )
    )

    rng = random.Random(seed)
    order = list(range(len(runs)))
    rng.shuffle(order)
    ordered: List[DoeRun] = []
    for position, index in enumerate(order, start=1):
        run = runs[index]
        ordered.append(DoeRun(run=position, factors=run.factors, purpose=run.purpose))

    rationale = (rationale_prefix + rationale).strip()
    return DoePlan(
        design=design,
        rationale=rationale,
        factors=[f.label for f in factors],
        runs=ordered,
        response_kpis=list(responses),
    )
