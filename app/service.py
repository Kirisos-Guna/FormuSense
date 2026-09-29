"""The agent's orchestration layer.

Every capability the problem statement asks for is one method here, and each one
writes to the record as it goes, so the transcript of a project is exactly the
sequence of calls that produced it:

``create_product``  understand the brief (specification + images) and design v1
``predict``         predicted characteristics of any version, with intervals
``run_trial``       run the physical trial (simulated plant) and store it
``analyse``         residuals against the published intervals, then causes
``plan``            fit the surrogate, reformulate, and design the next DOE
``accept``          promote a plan into the next formulation version
``closed_loop``     keep going until the measured product passes, or the budget ends
``efficiency``      the trial-efficiency KPIs the whole exercise is judged on

The service never invents numbers: it calls the engine, the plant, the analyser
and the optimiser, and stores what they return.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import plant as plant_module
from .core import brief as brief_module
from .core import diagnose as diagnose_module
from .core import doe as doe_module
from .core import (
    engine,
    formulate,
    kb,
    kpi as kpi_registry,
    population as population_module,
    process as process_module,
    reformulate,
    vision,
)
from .core.surrogate import SurrogateSet
from .core.types import Brief, Formulation, Item, TrialResult
from .store import Store, prediction_accuracy

_UNSET = object()
_ACCEPTANCE_BUNDLE: Any = _UNSET

DEFAULT_PLANT = {
    "name": "Pilot plant (default)",
    "drying_efficiency": 0.94,
    "temp_offset_c": -5.0,
    "acid_retention": 0.90,
    "noise_scale": 1.15,
}


def _plant_config(payload: Optional[Dict[str, Any]]) -> plant_module.PlantConfig:
    data = dict(DEFAULT_PLANT)
    data.update(payload or {})
    return plant_module.PlantConfig(
        name=str(data.get("name", DEFAULT_PLANT["name"])),
        drying_efficiency=float(data.get("drying_efficiency", 0.94)),
        temp_offset_c=float(data.get("temp_offset_c", 0.0)),
        acid_retention=float(data.get("acid_retention", 1.0)),
        sugar_inversion=float(data.get("sugar_inversion", 1.0)),
        sodium_carry=float(data.get("sodium_carry", 1.0)),
        oxidation_factor=float(data.get("oxidation_factor", 1.0)),
        noise_scale=float(data.get("noise_scale", 1.15)),
        notes=list(data.get("notes") or []),
    )


def _prediction_record(
    result: Any,
    evaluation: Dict[str, Any],
    conflicts: Sequence[Dict[str, Any]],
    claims: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """What is stored alongside a formulation when a prediction is published.

    The whole evaluation is kept, not just the values, because a prediction on the
    record has to answer "what did we believe about every target, how far from it
    were we, and what did we flag" long after the fact - and re-running the models
    against a later version of the code would not reproduce the belief that the
    trial was actually measured against.
    """
    return {
        "values": result.values,
        "predictions": [p.as_dict() for p in result.predictions],
        "objective": evaluation["objective"],
        "methods": result.details.get("methods", {}),
        "flags": result.flags,
        "evaluation": evaluation,
        "conflicts": conflicts,
        "claims": claims or {},
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


class AgentService:
    def __init__(self, store: Optional[Store] = None) -> None:
        self.store = store or Store()

    # ------------------------------------------------------------- 1. design #
    def create_product(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Understand the target product and design the first formulation."""
        started = time.time()
        image_paths: List[str] = []
        for image in payload.get("images") or []:
            if isinstance(image, dict) and image.get("data_url"):
                saved = vision.save_upload(str(image.get("name") or "reference"), image["data_url"])
                if saved:
                    image_paths.append(saved)
            elif isinstance(image, str):
                image_paths.append(image)
        brief_payload = dict(payload)
        brief_payload["images"] = image_paths

        brief = brief_module.build_brief(brief_payload)
        category_confidence = None
        if not str(payload.get("category") or "").strip():
            _, category_confidence, hits = brief_module.infer_category(
                "\n".join([str(payload.get("spec_text") or ""), str(payload.get("description") or "")])
            )
            brief.open_questions.append(
                "Category was inferred"
                + (f" from the words: {', '.join(hits[:3])}" if hits else "")
                + f" (confidence {category_confidence:.0%}). Confirm before the first trial."
            )

        understanding = None
        if image_paths:
            understanding = vision.describe_images(image_paths, kb.category(brief.category).label)
            for hint in understanding.get("hints", []):
                brief.open_questions.append(f"From the reference image: {hint}")

        product_id = self.store.create_product(brief, plant=payload.get("plant") or DEFAULT_PLANT)
        self.store.log(product_id, "understand", f"Brief understood: {brief.product_name} ({brief.category})")

        seed_result = formulate.generate_optimised(brief, seed=int(payload.get("seed") or 1))
        formulation = seed_result.formulation
        formulation.version = 1
        formulation.label = "v1 generated from the brief"
        self.store.save_formulation(product_id, formulation, source="generated")
        if seed_result.moves:
            self.store.log(
                product_id,
                "design",
                f"Initial formulation built: {len(formulation.items)} lines, "
                f"{len(seed_result.moves)} design moves, {seed_result.evaluations} model evaluations",
                {"moves": [m.as_dict() for m in seed_result.moves[:12]]},
            )

        constraint_report = formulate.check(formulation, brief)
        prediction = engine.predict(formulation, brief)
        evaluation = engine.evaluate_against_brief(prediction, brief)
        conflicts = formulate.conflicts(brief, formulation, prediction)
        self.store.save_prediction(
            product_id,
            formulation,
            _prediction_record(
                prediction, evaluation, conflicts, nutrition_claim_check(prediction, brief)
            ),
        )
        self.store.log(
            product_id,
            "predict",
            f"v1 predicted: objective {evaluation['objective']:.3f}, "
            f"{len(evaluation['failing'])} target(s) outside the acceptable region",
        )
        elapsed = time.time() - started
        return {
            "product_id": product_id,
            "brief": brief_module.summary(brief),
            "understanding": understanding,
            "formulation": formulation.as_dict(),
            "constraints": constraint_report.as_dict(),
            "prediction": {
                "values": prediction.values,
                "predictions": [p.as_dict() for p in prediction.predictions],
                "details": prediction.details,
                "flags": prediction.flags,
            },
            "evaluation": evaluation,
            "explanation": formulate.explain(formulation, brief),
            "conflicts": conflicts,
            "design_moves": [m.as_dict() for m in seed_result.moves[:25]],
            "design_history": seed_result.history,
            "elapsed_s": round(elapsed, 2),
        }

    # -------------------------------------------------------------- 2. view #
    def product_view(self, product_id: int) -> Dict[str, Any]:
        product = self.store.product(product_id)
        if product is None:
            raise KeyError(f"Unknown product {product_id}")
        brief = _brief_from_payload(product["brief"])
        versions = self.store.versions(product_id)
        latest = self.store.formulation(product_id)
        trials = self.store.trials(product_id)
        predictions_by_version: Dict[int, Dict[str, Any]] = {}
        for version in versions:
            record = self.store.prediction_for_version(product_id, int(version["version"]))
            if record:
                predictions_by_version[int(version["version"])] = record
        return {
            "product": product,
            "brief": brief_module.summary(brief),
            "versions": versions,
            "formulation": latest.as_dict() if latest else None,
            "formulation_detail": formulate.check(latest, brief).as_dict() if latest else None,
            "process": process_module.summary(latest, brief) if latest else None,
            "trials": [
                {
                    **trial,
                    "analysis": self.store.analysis_for_trial(trial["id"], "analysis"),
                    "diagnosis": self.store.analysis_for_trial(trial["id"], "diagnosis"),
                }
                for trial in trials
            ],
            "plans": [
                {**self.store.plan(plan_id), "id": plan_id}
                for plan_id in [p["id"] for p in self.store.plans(product_id)]
            ],
            "efficiency": self.efficiency(product_id),
            "ledger": self.store.entries(product_id, limit=60),
            "predictions_by_version": predictions_by_version,
            "populations": self.population_guide(product_id),
        }

    def population_guide(self, product_id: int, version: Optional[int] = None) -> Dict[str, Any]:
        """What one serving contributes to each population group's protein need.

        Computed from the values that are on record for the version (not from a
        fresh prediction), so the guidance the UI shows is the guidance that was
        published alongside the formulation.
        """
        product = self.store.product(product_id)
        if product is None:
            raise KeyError(f"Unknown product {product_id}")
        brief = _brief_from_payload(product["brief"])
        formulation = self.store.formulation(product_id, version)
        if formulation is None:
            raise KeyError(f"No formulation for product {product_id}")
        record = self.store.prediction_for_version(product_id, formulation.version)
        values = dict((record or {}).get("values") or {})
        if not values:
            values = engine.predict(formulation, brief).values
        serving_g = float(brief.unit_weight_g or 100.0)
        guide = _population_from_values(values, serving_g, brief.category)
        return {
            "product_id": product_id,
            "version": formulation.version,
            "category": brief.category,
            "category_label": kb.category(brief.category).label,
            "unit_weight_g": round(serving_g, 2),
            **guide,
        }

    def predict(
        self,
        product_id: int,
        version: Optional[int] = None,
        trials_for_calibration: bool = True,
        store_record: bool = True,
    ) -> Dict[str, Any]:
        product = self.store.product(product_id)
        if product is None:
            raise KeyError(f"Unknown product {product_id}")
        brief = _brief_from_payload(product["brief"])
        formulation = self.store.formulation(product_id, version)
        if formulation is None:
            raise KeyError(f"No formulation for product {product_id}")
        surrogate = self._surrogate(product_id) if trials_for_calibration else None
        samples = len(self.store.trials(product_id))
        result = engine.predict(
            formulation,
            brief,
            calibration_samples=samples,
            surrogate=surrogate if surrogate and surrogate.is_fitted else None,
        )
        evaluation = engine.evaluate_against_brief(result, brief)
        claims = nutrition_claim_check(result, brief)
        conflicts = formulate.conflicts(brief, formulation, result)
        if store_record:
            # Re-predicting publishes a new record for the same version: it is the
            # forecast this product would be run against from now on, and the
            # accuracy metric scores the record that was on file before a trial.
            self.store.save_prediction(
                product_id, formulation, _prediction_record(result, evaluation, conflicts, claims)
            )
            self.store.log(
                product_id,
                "predict",
                f"v{formulation.version} re-predicted on {samples} trial(s): "
                f"objective {evaluation['objective']:.3f}",
            )
        return {
            "product_id": product_id,
            "version": formulation.version,
            "prediction": {
                "values": result.values,
                "predictions": [p.as_dict() for p in result.predictions],
                "details": result.details,
                "flags": result.flags,
            },
            "evaluation": evaluation,
            "claims": claims,
            "conflicts": conflicts,
            "surrogate": surrogate.report() if surrogate else None,
            "process": process_module.summary(formulation, brief),
            "population": _population_from_values(
                result.values, float(brief.unit_weight_g or 100.0), brief.category
            ),
            "learned_acceptance": self.learned_acceptance(formulation),
        }

    # ------------------------------------------------------------- 3. trial #
    def run_trial(
        self,
        product_id: int,
        version: Optional[int] = None,
        seed: Optional[int] = None,
        operator: str = "pilot plant",
        trial_date: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        product = self.store.product(product_id)
        if product is None:
            raise KeyError(f"Unknown product {product_id}")
        brief = _brief_from_payload(product["brief"])
        formulation = self.store.formulation(product_id, version)
        if formulation is None:
            raise KeyError(f"No formulation for product {product_id}")
        config = _plant_config(product.get("plant"))
        trials_so_far = len(self.store.trials(product_id))
        seed = seed if seed is not None else 1000 + 97 * trials_so_far + formulation.version
        trial = plant_module.run_trial(
            formulation,
            brief,
            config,
            seed=seed,
            label=f"T{trials_so_far + 1} (v{formulation.version})",
            version=formulation.version,
            operator=operator,
            trial_date=trial_date,
            include=list(kpi_registry.kpis_for_category(formulation.category)),
        )
        if note:
            trial.notes = (trial.notes + " " + note).strip()
        trial_id = self.store.save_trial(product_id, trial)
        self.store.log(
            product_id,
            "trial",
            f"{trial.label}: {len(trial.measurements)} KPI(s) measured on {config.name}",
            {"trial_id": trial_id, "measurements": trial.measurements},
        )
        return {"trial_id": trial_id, "trial": trial.as_dict(), "plant": config.as_dict()}

    # ----------------------------------------------------------- 4. analyse #
    def analyse_trial(self, product_id: int, trial_id: int) -> Dict[str, Any]:
        product = self.store.product(product_id)
        trial = self.store.trial(trial_id)
        if product is None or trial is None:
            raise KeyError("Unknown product or trial")
        brief = _brief_from_payload(product["brief"])
        formulation = self.store.formulation(product_id, trial["formulation_version"])
        if formulation is None:
            raise KeyError("Missing formulation for the trial")
        record = self.store.prediction_for_version(product_id, trial["formulation_version"])
        surrogate = self._surrogate(product_id)
        samples = len(self.store.trials(product_id))
        result = engine.predict(
            formulation,
            brief,
            calibration_samples=max(samples - 1, 0),
            surrogate=surrogate if surrogate and surrogate.is_fitted else None,
        )
        trial_result = _trial_from_payload(trial)
        # Setpoints are the values this version *asked* for, not the category
        # defaults: the offset that matters is against the recipe that was run.
        planned = {**kb.category(formulation.category).default_params(), **formulation.params}
        setpoints = {key: value for key, value in planned.items() if key in trial_result.process_actuals}
        analysis = diagnose_module.analyse(trial_result, result, brief, setpoints=setpoints)
        causes = diagnose_module.diagnose(
            analysis,
            formulation,
            brief,
            process_actuals=trial_result.process_actuals,
        )
        self.store.save_analysis(
            product_id,
            trial_id,
            {**analysis.as_dict(), "predicted_objective": analysis.predicted_objective},
            kind="analysis",
        )
        self.store.save_analysis(
            product_id,
            trial_id,
            {"causes": [c.as_dict() for c in causes], "summary": analysis.summary},
            kind="diagnosis",
        )
        self.store.log(
            product_id,
            "diagnose",
            f"{trial['label']}: {len(analysis.flagged)} KPI(s) outside prediction interval; "
            f"top cause: {causes[0].cause if causes else 'none'}",
            {"causes": [c.as_dict() for c in causes]},
        )
        return {
            "product_id": product_id,
            "trial_id": trial_id,
            "analysis": analysis.as_dict(),
            "diagnosis": {"causes": [c.as_dict() for c in causes], "summary": analysis.summary},
            "prediction_snapshot": {
                "predictions": [p.as_dict() for p in result.predictions],
                "objective": engine.evaluate_against_brief(result, brief)["objective"],
            },
            "stored_prediction": record,
            "measured_summary": self._measured_summary(brief, trial),
        }

    # -------------------------------------------------------------- 5. plan #
    def plan_reformulation(self, product_id: int, budget: int = 1600) -> Dict[str, Any]:
        product = self.store.product(product_id)
        if product is None:
            raise KeyError(f"Unknown product {product_id}")
        brief = _brief_from_payload(product["brief"])
        formulation = self.store.formulation(product_id)
        if formulation is None:
            raise KeyError("No formulation to reformulate from")
        surrogate = self._surrogate(product_id)
        trials = self.store.trials(product_id)
        analysis = None
        causes: List[Any] = []
        if trials:
            latest = trials[-1]
            stored = self.store.analysis_for_trial(latest["id"], "analysis")
            diagnosis = self.store.analysis_for_trial(latest["id"], "diagnosis")
            if stored:
                analysis = _analysis_from_payload(stored)
            if diagnosis:
                causes = [diagnose_module.DiagnosisCause(**_cause_kwargs(c)) for c in diagnosis.get("causes", [])]
        samples = len([t for t in trials if t["measurements"]])
        result = engine.predict(
            formulation,
            brief,
            calibration_samples=samples,
            surrogate=surrogate if surrogate and surrogate.is_fitted else None,
        )
        plan = reformulate.plan_reformulation(
            brief,
            formulation,
            result,
            analysis,
            causes,
            surrogate=surrogate,
            product_id=product_id,
            budget=budget,
            calibration_samples=samples,
        )
        payload = plan.as_dict()
        payload["surrogate"] = surrogate.report() if surrogate else {"fitted": False}
        payload["cause_summary"] = [c.as_dict() for c in causes]
        plan_id = self.store.save_plan(product_id, payload)
        self.store.log(
            product_id,
            "reformulate",
            f"Plan {plan_id}: v{plan.from_version} -> v{plan.to_version}, "
            f"{len(plan.deltas)} change(s), pass probability {plan.pass_probability*100:.0f}%",
            {"plan_id": plan_id, "doe": plan.doe.as_dict() if plan.doe else None},
        )
        return {"plan_id": plan_id, "plan": payload}

    # ------------------------------------------------------------ 6. accept #
    def accept_plan(self, product_id: int, plan_id: int) -> Dict[str, Any]:
        plan = self.store.plan(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan {plan_id}")
        product = self.store.product(product_id)
        if product is None:
            raise KeyError(f"Unknown product {product_id}")
        brief = _brief_from_payload(product["brief"])
        formulation = Formulation.from_dict(plan["formulation"])
        self.store.save_formulation(product_id, formulation, source="accepted-plan")
        self.store.mark_plan_accepted(plan_id)
        surrogate = self._surrogate(product_id)
        samples = len(self.store.trials(product_id))
        result = engine.predict(
            formulation,
            brief,
            calibration_samples=samples,
            surrogate=surrogate if surrogate and surrogate.is_fitted else None,
        )
        evaluation = engine.evaluate_against_brief(result, brief)
        self.store.save_prediction(
            product_id,
            formulation,
            _prediction_record(
                result,
                evaluation,
                formulate.conflicts(brief, formulation, result),
                nutrition_claim_check(result, brief),
            ),
        )
        self.store.log(
            product_id,
            "accept",
            f"v{formulation.version} accepted from plan {plan_id}; predicted objective "
            f"{evaluation['objective']:.3f}",
        )
        return {
            "product_id": product_id,
            "plan_id": plan_id,
            "version": formulation.version,
            "formulation": formulation.as_dict(),
            "prediction": {
                "values": result.values,
                "predictions": [p.as_dict() for p in result.predictions],
                "details": result.details,
            },
            "evaluation": evaluation,
        }

    # --------------------------------------------------------- 7. the loop #
    def closed_loop(
        self,
        product_id: int,
        max_trials: int = 6,
        budget: int = 1600,
        accept_plans: bool = True,
        pass_objective: float = 0.85,
    ) -> Dict[str, Any]:
        """Run trials, diagnoses and reformulations until the product passes."""
        history: List[Dict[str, Any]] = []
        trials = self.store.trials(product_id)
        start_version = self.store.formulation(product_id)
        start_trials = len(trials)
        success = False
        for _ in range(max_trials):
            trial_info = self.run_trial(product_id)
            analysed = self.analyse_trial(product_id, trial_info["trial_id"])
            analysis = analysed["analysis"]
            summary = analysed["measured_summary"]
            # The gate is the *hard* targets, which is what a development team has
            # to meet to move forward. Soft targets stay monitored, and the
            # measured objective has to be respectable at the same time so a
            # product cannot scrape through on a technicality.
            measured_pass = (
                summary["hard_total"] > 0
                and summary["hard_on_target"] == summary["hard_total"]
                and analysis["measured_objective"] >= pass_objective
            )
            step = {
                "trial_id": trial_info["trial_id"],
                "trial": trial_info["trial"],
                "analysis": analysis,
                "diagnosis": analysed["diagnosis"],
                "measured_summary": summary,
                "measured_objective": analysis["measured_objective"],
                "measured_on_target": f"{analysis['measured_on_target']}/{analysis['measured_total']}",
                "hard_on_target": f"{summary['hard_on_target']}/{summary['hard_total']}",
            }
            history.append(step)
            if measured_pass:
                success = True
                self.store.log(
                    product_id,
                    "verdict",
                    f"Target achieved at trial {len(history)}: all {summary['hard_total']} measured hard "
                    f"targets on target, measured objective {analysis['measured_objective']:.3f}",
                )
                break
            planned = self.plan_reformulation(product_id, budget=budget)
            step["plan"] = {
                "plan_id": planned["plan_id"],
                "pass_probability": planned["plan"]["pass_probability"],
                "deltas": planned["plan"]["deltas"][:6],
                "rationale": planned["plan"]["rationale"],
                "doe": planned["plan"]["doe"],
            }
            if accept_plans:
                accepted = self.accept_plan(product_id, planned["plan_id"])
                step["accepted_version"] = accepted["version"]
                step["predicted_objective"] = accepted["evaluation"]["objective"]
        end_formulation = self.store.formulation(product_id)
        return {
            "product_id": product_id,
            "success": success,
            "trials_used": len(history),
            "trials_before": start_trials,
            "from_version": start_version.version if start_version else None,
            "to_version": end_formulation.version if end_formulation else None,
            "history": history,
            "efficiency": self.efficiency(product_id),
        }

    # --------------------------------------------------------- 8. metrics #
    def efficiency(self, product_id: int) -> Dict[str, Any]:
        trials = self.store.trials(product_id)
        versions = self.store.versions(product_id)
        predictions: Dict[int, Dict[str, Any]] = {}
        for version in versions:
            record = self.store.prediction_for_version(product_id, int(version["version"]))
            if record:
                predictions[int(version["version"])] = record
        accuracy = prediction_accuracy(trials, predictions)

        product = self.store.product(product_id)
        brief = _brief_from_payload(product["brief"]) if product else None
        trials_to_target: Optional[int] = None
        first_pass = False
        for index, trial in enumerate(trials, start=1):
            if not brief:
                break
            passed = self._measured_pass(brief, trial)
            if passed and trials_to_target is None:
                trials_to_target = index
            if index == 1 and passed:
                first_pass = True
        material = sum(float(t.get("batch_size_kg") or 0.0) for t in trials)
        return {
            "trials_used": len(trials),
            "versions_used": len(versions),
            "trials_to_target": trials_to_target,
            "first_version_passed": first_pass,
            "material_used_kg": round(material, 2),
            "prediction_accuracy": accuracy,
            "measured_history": [
                {
                    "trial": trial["label"],
                    "version": trial["formulation_version"],
                    **self._measured_summary(brief, trial),
                }
                for trial in trials
            ],
        }

    def _measured_pass(self, brief: Brief, trial: Dict[str, Any]) -> bool:
        summary = self._measured_summary(brief, trial)
        return summary["measured_total"] > 0 and summary["measured_on_target"] == summary["measured_total"]

    def _measured_summary(self, brief: Brief, trial: Dict[str, Any]) -> Dict[str, Any]:
        measurements = trial["measurements"]
        on_target = 0
        total = 0
        hard_total = 0
        hard_on_target = 0
        for target in brief.targets:
            if target.id not in measurements:
                continue
            ok = kpi_registry.is_on_target(target, float(measurements[target.id]))
            total += 1
            if ok:
                on_target += 1
            if target.hard:
                hard_total += 1
                if ok:
                    hard_on_target += 1
        objective = kpi_registry.overall_desirability(brief.targets, {k: float(v) for k, v in measurements.items()})
        return {
            "measured_on_target": on_target,
            "measured_total": total,
            "hard_on_target": hard_on_target,
            "hard_total": hard_total,
            "measured_objective": round(objective, 4),
        }

    # ------------------------------------------------- learned acceptance #
    def acceptance_model(self) -> Optional[Any]:
        """The trained acceptance model, loaded once per process.

        Loaded lazily and cached at module level: the model file is small and does
        not change while the process runs, and the alternative - reading and
        parsing it on every prediction - would put file I/O in the hot path.
        """
        global _ACCEPTANCE_BUNDLE
        if _ACCEPTANCE_BUNDLE is _UNSET:
            try:
                from .ml.registry import latest_bundle

                _ACCEPTANCE_BUNDLE = latest_bundle()
            except Exception:  # pragma: no cover - a missing model must not break the app
                _ACCEPTANCE_BUNDLE = None
        return _ACCEPTANCE_BUNDLE  # type: ignore[return-value]

    def learned_acceptance(self, formulation: Formulation) -> Dict[str, Any]:
        """The learned model's view of a formulation, alongside the physical one.

        This is deliberately advisory: it is reported next to the physics-based
        pass probability, never substituted for it, and it says so when no model
        has been trained.
        """
        bundle = self.acceptance_model()
        if bundle is None:
            return {
                "available": False,
                "note": "no trained acceptance model; run: python run.py --build-dataset && python run.py --train",
            }
        scored = bundle.predict(formulation)
        test = (bundle.metrics or {}).get("test", {})
        return {
            "available": True,
            "trained": f"{bundle.name}/{bundle.version}",
            "created_at": bundle.created_at,
            "dataset": bundle.dataset,
            "metrics": {
                "regression": test.get("regression", {}),
                "classification": test.get("classification", {}),
                "beats_baseline": (bundle.metrics or {}).get("beats_baseline", {}),
            },
            "drivers": bundle.driver_notes(),
            **scored,
        }

    # ------------------------------------------------------------- helpers #
    def _surrogate(self, product_id: int) -> Optional[SurrogateSet]:
        product = self.store.product(product_id)
        samples = self.store.trial_samples(product_id)
        surrogate = SurrogateSet(category=product["category"] if product else "cookie")
        if samples:
            brief = _brief_from_payload(product["brief"]) if product else None
            surrogate.fit(samples, brief)
        return surrogate


# --------------------------------------------------------------------------- #
# Small conversion helpers
# --------------------------------------------------------------------------- #
def _brief_from_payload(payload: Dict[str, Any]) -> Brief:
    return brief_module.build_brief(
        {
            "product_name": payload.get("product_name"),
            "category": payload.get("category"),
            "unit_weight_g": payload.get("unit_weight_g"),
            "spec_text": payload.get("spec_text", ""),
            "description": payload.get("description", ""),
            "claims": payload.get("claims"),
            "allergens_to_avoid": payload.get("allergens_to_avoid"),
            "diet": payload.get("diet"),
            "cost_ceiling_inr_kg": payload.get("cost_ceiling_inr_kg"),
            "images": payload.get("images"),
        }
    )


def _trial_from_payload(payload: Dict[str, Any]) -> TrialResult:
    return TrialResult(
        label=payload.get("label") or "trial",
        formulation_version=int(payload.get("formulation_version") or 1),
        measurements=dict(payload.get("measurements") or {}),
        sensory=dict(payload.get("sensory") or {}),
        process_actuals=dict(payload.get("process_actuals") or {}),
        batch_size_kg=float(payload.get("batch_size_kg") or 1.0),
        operator=payload.get("operator") or "pilot plant",
        trial_date=payload.get("trial_date") or "",
        notes=payload.get("notes") or "",
    )


def _analysis_from_payload(payload: Dict[str, Any]) -> diagnose_module.Analysis:
    residuals = [
        diagnose_module.Residual(
            kpi=row["kpi"],
            label=row.get("label", row["kpi"]),
            unit=row.get("unit", ""),
            predicted=float(row["predicted"]),
            measured=float(row["measured"]),
            sigma=float(row.get("sigma") or 1e-6),
            z=float(row.get("z") or 0.0),
            direction=row.get("direction", "consistent"),
            target=row.get("target"),
            on_target=row.get("on_target"),
        )
        for row in payload.get("residuals", [])
    ]
    return diagnose_module.Analysis(
        trial_label=payload.get("trial_label", "trial"),
        version=int(payload.get("version") or 1),
        residuals=residuals,
        systematic=bool(payload.get("systematic")),
        mean_z=float(payload.get("mean_z") or 0.0),
        process_deviations=list(payload.get("process_deviations") or []),
        summary=payload.get("summary", ""),
        predicted_objective=float(payload.get("predicted_objective") or 0.0),
        measured_objective=float(payload.get("measured_objective") or 0.0),
        measured_on_target=int(payload.get("measured_on_target") or 0),
        measured_total=int(payload.get("measured_total") or 0),
        flagged=[r for r in residuals if r.direction != "consistent"],
    )


def _cause_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rank": int(payload.get("rank") or 1),
        "cause": payload.get("cause", ""),
        "category": payload.get("category", "process"),
        "confidence": float(payload.get("confidence") or 0.0),
        "evidence": list(payload.get("evidence") or []),
        "kpis": list(payload.get("kpis") or []),
        "recommended_action": payload.get("recommended_action", ""),
    }


def nutrition_claim_check(result, brief: Brief) -> Dict[str, Any]:
    return engine.claim_check(result, brief)


def _population_from_values(values: Dict[str, Any], serving_g: float, category: str) -> Dict[str, Any]:
    """Build the population guidance payload from a per-100 g value set."""
    factor = max(float(serving_g), 0.0) / 100.0

    def per_serving(kpi_id: str) -> float:
        try:
            return float(values.get(kpi_id) or 0.0) * factor
        except (TypeError, ValueError):
            return 0.0

    if category == "beverage":
        label = f"one {serving_g:.0f} g serving (about {serving_g:.0f} ml)"
    else:
        label = f"one {serving_g:.0f} g serving"
    guide = population_module.guidance(
        per_serving("protein_g"),
        serving_label=label,
        energy_per_serving_kcal=per_serving("energy_kcal"),
        sugar_per_serving_g=per_serving("sugar_g"),
        sodium_per_serving_mg=per_serving("sodium_mg"),
    )
    guide["serving"] = {
        "serving_g": round(float(serving_g), 2),
        "protein_g": round(per_serving("protein_g"), 3),
        "energy_kcal": round(per_serving("energy_kcal"), 2),
        "sugar_g": round(per_serving("sugar_g"), 3),
        "fat_g": round(per_serving("fat_g"), 3),
        "fibre_g": round(per_serving("fibre_g"), 3),
        "sodium_mg": round(per_serving("sodium_mg"), 2),
    }
    return guide
