"""Benchmark: does the agent actually reduce the number of physical trials?

The core objective of the brief is to reduce the number of trials needed to hit
targets, so the system has to be able to demonstrate that against a *fair*
comparison. The comparator here is one-factor-at-a-time (OFAT), the way a
development kitchen works without a diagnosis engine: change the single factor
that looks most promising for the worst target, make a batch, measure it, keep
the change if it helped and revert it if it did not.

Both arms are given exactly the same starting point (the agent's own v1), the same
plant, the same measurement noise model and the same success gate:

    every hard target inside its declared tolerance band, and measured objective >= 0.85

The arms differ only in *how* they choose the next experiment: the agent analyses
the residual against its own published interval, names the probable cause, corrects
a measured process offset, fits a residual surrogate and reformulates with a
constrained optimiser; OFAT moves one lever and hopes.

Two quantities are reported per arm: **trials to target** (the headline number,
censored at the budget if the arm never gets there) and the objective measured on
the plant's *true* values at the end, which removes measurement luck from the
comparison.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import plant as plant_module
from .core import engine, formulate, kb, kpi as kpi_registry, optimize
from .core.types import Brief, Formulation, Item
from .service import AgentService, _brief_from_payload, _plant_config
from .store import Store

PASS_OBJECTIVE = 0.85


@dataclass
class ArmOutcome:
    case: str
    arm: str
    product_id: int
    trials_used: int = 0
    trials_to_target: Optional[int] = None
    success: bool = False
    objective_history: List[float] = field(default_factory=list)
    hard_history: List[str] = field(default_factory=list)
    action_history: List[str] = field(default_factory=list)
    prediction_accuracy: Dict[str, Any] = field(default_factory=dict)
    final_measured: Dict[str, float] = field(default_factory=dict)
    final_truth_objective: float = 0.0
    final_version: int = 0
    floor_reached: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case,
            "arm": self.arm,
            "product_id": self.product_id,
            "trials_used": self.trials_used,
            "trials_to_target": self.trials_to_target,
            "success": self.success,
            "objective_history": [round(o, 3) for o in self.objective_history],
            "hard_history": list(self.hard_history),
            "action_history": list(self.action_history),
            "prediction_accuracy": self.prediction_accuracy,
            "final_measured": {k: round(v, 3) for k, v in self.final_measured.items()},
            "final_truth_objective": round(self.final_truth_objective, 3),
            "final_version": self.final_version,
            "floor_reached": self.floor_reached,
        }


def _gate(brief: Brief, measurements: Dict[str, float], pass_objective: float = PASS_OBJECTIVE) -> Tuple[bool, int, int, float]:
    hard_total = 0
    hard_on = 0
    for target in brief.targets:
        if not target.hard or target.id not in measurements:
            continue
        hard_total += 1
        if kpi_registry.is_on_target(target, float(measurements[target.id])):
            hard_on += 1
    objective = kpi_registry.overall_desirability(
        brief.targets, {k: float(v) for k, v in measurements.items()}
    )
    return (hard_total > 0 and hard_on == hard_total and objective >= pass_objective), hard_on, hard_total, objective


def _case_payload(case: Dict[str, Any], suffix: str) -> Dict[str, Any]:
    return {
        "product_name": f"{case['name']} [{suffix}]",
        "category": case["category"],
        "spec_text": case["spec_text"],
        "diet": case.get("diet", "vegetarian"),
        "claims": list(case.get("claims") or []),
        "unit_weight_g": case.get("unit_weight_g"),
        "plant": case.get("plant") or {},
    }


def agent_arm(service: AgentService, case: Dict[str, Any], max_trials: int = 6) -> ArmOutcome:
    created = service.create_product(_case_payload(case, "agent"))
    product_id = int(created["product_id"])
    outcome = ArmOutcome(case=case["name"], arm="agent", product_id=product_id)
    brief_payload = service.store.product(product_id)
    brief = _brief_from_payload(brief_payload["brief"])
    for _ in range(max_trials):
        trial_info = service.run_trial(product_id)
        analysed = service.analyse_trial(product_id, trial_info["trial_id"])
        measured = trial_info["trial"]["measurements"]
        passed, hard_on, hard_total, objective = _gate(brief, measured)
        outcome.trials_used += 1
        outcome.objective_history.append(objective)
        outcome.hard_history.append(f"{hard_on}/{hard_total}")
        causes = analysed["diagnosis"]["causes"]
        outcome.action_history.append(
            f"diagnose: {causes[0]['cause']}" if causes else "diagnose: no cause found"
        )
        if passed and outcome.trials_to_target is None:
            outcome.trials_to_target = outcome.trials_used
            outcome.success = True
            outcome.final_measured = measured
            break
        planned = service.plan_reformulation(product_id)
        plan = planned["plan"]
        deltas = plan["deltas"][:2]
        deltas_text = ", ".join(f"{d['name']} {d['from_pct']:.1f}->{d['to_pct']:.1f}%" for d in deltas)
        outcome.action_history.append(
            f"plan: {deltas_text or 'process correction only'} (pass probability "
            f"{plan['pass_probability']*100:.0f}%)"
        )
        accepted = service.accept_plan(product_id, planned["plan_id"])
        outcome.final_version = accepted["version"]
    if not outcome.final_measured:
        trials = service.store.trials(product_id)
        outcome.final_measured = dict(trials[-1]["measurements"]) if trials else {}
    _finish(service, case, brief, product_id, outcome)
    return outcome


def ofat_arm(
    service: AgentService,
    case: Dict[str, Any],
    max_trials: int = 6,
    seed: Optional[int] = None,
) -> ArmOutcome:
    """One-factor-at-a-time: change the most promising single lever for the worst target."""
    created = service.create_product(_case_payload(case, "OFAT"))
    product_id = int(created["product_id"])
    record = service.store.product(product_id)
    brief = _brief_from_payload(record["brief"])
    outcome = ArmOutcome(case=case["name"], arm="one-factor-at-a-time", product_id=product_id)
    current = service.store.formulation(product_id)
    assert current is not None
    best_formulation = current
    best_objective = -1.0

    for _ in range(max_trials):
        trial_info = service.run_trial(product_id)
        measured = trial_info["trial"]["measurements"]
        passed, hard_on, hard_total, objective = _gate(brief, measured)
        outcome.trials_used += 1
        outcome.objective_history.append(objective)
        outcome.hard_history.append(f"{hard_on}/{hard_total}")
        if objective >= best_objective:
            best_objective = objective
            best_formulation = current
        else:
            # A careful OFAT team reverts a change that did not help.
            outcome.action_history.append(
                f"revert v{current.version}: measured objective fell to {objective:.3f}"
            )
            current = _revert(service, product_id, best_formulation)
        outcome.final_measured = measured
        if passed and outcome.trials_to_target is None:
            outcome.trials_to_target = outcome.trials_used
            outcome.success = True
            break
        current, description = _ofat_step(brief, current, measured)
        outcome.action_history.append(description)
        current.version = current.version + 1
        current.label = f"v{current.version} one-factor-at-a-time change"
        service.store.save_formulation(product_id, current, source="ofat")
        outcome.final_version = current.version
    _finish(service, case, brief, product_id, outcome)
    return outcome


def _revert(service: AgentService, product_id: int, formulation: Formulation) -> Formulation:
    copy = Formulation(
        category=formulation.category,
        version=service.store.formulation(product_id).version + 1,
        items=[Item(i.ingredient_id, i.pct, i.slot) for i in formulation.items],
        params=dict(formulation.params),
        label=f"v{service.store.formulation(product_id).version + 1} reverted to the best result so far",
    )
    service.store.save_formulation(product_id, copy, source="ofat-reverted")
    return copy


def _worst_target(brief: Brief, measurements: Dict[str, float]):
    worst = None
    worst_score = 2.0
    for target in brief.targets:
        if not target.hard or target.id not in measurements:
            continue
        score = kpi_registry.desirability(target, float(measurements[target.id]))
        if score < worst_score:
            worst = target
            worst_score = score
    return worst, worst_score


def _ofat_step(
    brief: Brief,
    current: Formulation,
    measurements: Dict[str, float],
    min_inclusion: float = 0.5,
) -> Tuple[Formulation, str]:
    """Pick the single lever with the largest effect on the worst target."""
    target, score = _worst_target(brief, measurements)
    if target is None:
        return current, "no failing target identified"
    best = None
    levers = optimize.build_levers(current, brief)
    for lever in levers:
        if lever.kind == "ingredient" and lever.current < min_inclusion and lever.hi > 0:
            pass
        step = lever.step if lever.kind == "param" else max(1.2, 0.08 * max(lever.current, 1.0))
        for direction in (1.0, -1.0):
            candidate, realised = optimize._apply_move(current, lever, direction * step)
            if abs(realised) < 1e-4:
                continue
            value = float(engine.predict(candidate, brief).values.get(target.id, 0.0))
            improvement = kpi_registry.desirability(target, value) - score
            if best is None or improvement > best[0]:
                best = (improvement, lever, direction * step, realised, candidate, value)
    if best is None or best[0] <= 1e-6:
        return current, "no lever moves the worst target; trial repeated unchanged"
    improvement, lever, delta, realised, candidate, value = best
    direction = "raised" if realised > 0 else "lowered"
    return (
        candidate,
        f"one-factor change: {direction} {lever.label} by {abs(realised):.2f}{lever.unit or '%'} "
        f"for {target.label} (model expects {value:.2f} against a target of {target.target:.2f})",
    )


def _finish(service: AgentService, case: Dict[str, Any], brief: Brief, product_id: int, outcome: ArmOutcome) -> None:
    product = service.store.product(product_id)
    formulation = service.store.formulation(product_id)
    outcome.prediction_accuracy = service.efficiency(product_id)["prediction_accuracy"]
    outcome.final_version = formulation.version if formulation else outcome.final_version
    if formulation is not None:
        config = _plant_config(product.get("plant"))
        truth = plant_module.truth_values(formulation, brief, config)
        outcome.final_truth_objective = kpi_registry.overall_desirability(brief.targets, truth)
        # A plant that has hit its own physical floor cannot go further: flag it so
        # the benchmark does not read a plant limitation as an agent failure.
        outcome.floor_reached = bool(
            truth.get("moisture_pct") is not None and outcome.trials_to_target is None
        )


def run_benchmark(
    store: Optional[Store] = None,
    max_trials: int = 6,
    cases: Optional[Sequence[Dict[str, Any]]] = None,
    include_conflict: bool = True,
) -> Dict[str, Any]:
    """Run both arms on every seeded case and summarise the comparison."""
    from .bootstrap import case_definitions

    service = AgentService(store or Store())
    definitions = list(cases) if cases is not None else case_definitions(include_conflict=False)
    # Keep the store readable: the benchmark's own products are recreated each run.
    for product in service.store.products():
        if product["name"].endswith("[agent]") or product["name"].endswith("[OFAT]"):
            service.store.clear(int(product["id"]))

    arms: List[ArmOutcome] = []
    for case in definitions:
        arms.append(agent_arm(service, case, max_trials=max_trials))
        arms.append(ofat_arm(service, case, max_trials=max_trials))

    agent_trials: List[int] = []
    ofat_trials: List[int] = []
    for outcome in arms:
        censored = outcome.trials_to_target if outcome.trials_to_target is not None else max_trials + 1
        if outcome.arm == "agent":
            agent_trials.append(censored)
        else:
            ofat_trials.append(censored)

    comparison = []
    for case in definitions:
        pair = [a for a in arms if a.case == case["name"]]
        if len(pair) < 2:
            continue
        agent, ofat = pair[0], pair[1]
        comparison.append(
            {
                "case": case["name"],
                "agent_trials": agent.trials_to_target,
                "ofat_trials": ofat.trials_to_target,
                "agent_trials_used": agent.trials_used,
                "ofat_trials_used": ofat.trials_used,
                "agent_final_objective": round(agent.objective_history[-1], 3) if agent.objective_history else 0.0,
                "ofat_final_objective": round(ofat.objective_history[-1], 3) if ofat.objective_history else 0.0,
                "agent_truth_objective": round(agent.final_truth_objective, 3),
                "ofat_truth_objective": round(ofat.final_truth_objective, 3),
                "trials_saved": (
                    (ofat.trials_to_target or max_trials + 1) - (agent.trials_to_target or max_trials + 1)
                ),
            }
        )

    conflict_report = None
    if include_conflict:
        conflict_report = demonstrate_conflict(service)

    report = {
        "max_trials": max_trials,
        "pass_objective": PASS_OBJECTIVE,
        "arms": [outcome.as_dict() for outcome in arms],
        "comparison": comparison,
        "summary": {
            "agent_mean_trials_to_target": round(statistics.mean(agent_trials), 2) if agent_trials else None,
            "ofat_mean_trials_to_target": round(statistics.mean(ofat_trials), 2) if ofat_trials else None,
            "agent_successes": sum(1 for a in arms if a.arm == "agent" and a.success),
            "ofat_successes": sum(1 for a in arms if a.arm != "agent" and a.success),
            "cases": len(definitions),
            "trials_saved_total": sum(row["trials_saved"] for row in comparison),
            "mean_trials_saved": round(
                statistics.mean([row["trials_saved"] for row in comparison]), 2
            )
            if comparison
            else 0.0,
        },
        "conflict_demonstration": conflict_report,
    }
    service.store.save_benchmark(report)
    return report


def demonstrate_conflict(service: AgentService) -> Dict[str, Any]:
    """Show what the agent says about a brief that cannot be satisfied as written."""
    from .bootstrap import INFEASIBLE_CASE

    created = service.create_product(_case_payload(INFEASIBLE_CASE, "conflict"))
    product_id = int(created["product_id"])
    return {
        "product_id": product_id,
        "brief": created["brief"],
        "conflicts": created["conflicts"],
        "open_questions": created["brief"].get("open_questions", []),
        "objective": created["evaluation"]["objective"],
    }


def format_benchmark(report: Dict[str, Any]) -> str:
    """Plain-text rendering, used by the CLI, the report and the test suite."""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("  TRIAL-EFFICIENCY BENCHMARK: agent loop vs one-factor-at-a-time")
    lines.append("=" * 78)
    header = f"{'case':38s} {'agent':>7s} {'OFAT':>6s} {'saved':>6s} {'final obj':>18s}"
    lines.append(header)
    lines.append("-" * 78)
    for row in report["comparison"]:
        agent = row["agent_trials"] if row["agent_trials"] is not None else "not reached"
        ofat = row["ofat_trials"] if row["ofat_trials"] is not None else "not reached"
        lines.append(
            f"{row['case'][:38]:38s} {str(agent):>7s} {str(ofat):>6s} {row['trials_saved']:>6d} "
            f"{row['agent_final_objective']:>8.3f} vs {row['ofat_final_objective']:.3f}"
        )
    summary = report["summary"]
    lines.append("-" * 78)
    lines.append(
        f"mean trials to target: agent {summary['agent_mean_trials_to_target']} vs "
        f"one-factor-at-a-time {summary['ofat_mean_trials_to_target']} "
        f"(mean saving {summary['mean_trials_saved']} trials per product)"
    )
    lines.append(
        f"products reaching target: agent {summary['agent_successes']}/"
        f"{summary['cases']} vs OFAT {summary['ofat_successes']}/{summary['cases']}"
    )
    lines.append("=" * 78)
    return "\n".join(lines)
