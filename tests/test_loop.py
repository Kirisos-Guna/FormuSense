"""Tests for the loop that the whole problem statement is about.

The claim under test is not "the agent produces a formulation" - that is the easy
part and the model tests cover it. The claim is that *the number of physical
trials needed to reach the target goes down*, on a plant that does not behave the
way the models say it should. So these tests run real trials against the simulated
plants of the seeded cases and check that the loop closes, that it stops when the
product passes, and that the reformulation it proposes is a response to the
measured evidence (a process offset compensated, a residual corrected) rather
than a fresh guess.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.bootstrap import CASES
from app.bootstrap import case_definitions
from app.benchmark import _gate, format_benchmark, run_benchmark
from app.service import AgentService, _brief_from_payload
from app.store import Store


class LoopTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-loop-"))
        self.store = Store(self.tmp / "test.db")
        self.service = AgentService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def case(self, key: str):
        return next(c for c in CASES if c["key"] == key)

    def create(self, case):
        return self.service.create_product(
            {
                "product_name": case["name"],
                "category": case["category"],
                "spec_text": case["spec_text"],
                "diet": case.get("diet", "vegetarian"),
                "claims": list(case.get("claims") or []),
                "unit_weight_g": case.get("unit_weight_g"),
                "plant": case.get("plant") or {},
            }
        )


class DesignTests(LoopTestCase):
    def test_designing_a_product_records_the_brief_the_prediction_and_the_constraints(self) -> None:
        created = self.create(self.case("namkeen"))
        self.assertEqual(created["formulation"]["version"], 1)
        self.assertTrue(created["prediction"]["predictions"])
        self.assertAlmostEqual(created["formulation"]["total_pct"], 100.0, delta=0.05)
        self.assertIn("objective", created["evaluation"])
        self.assertTrue(created["explanation"])

    def test_the_prediction_on_record_carries_its_evaluation(self) -> None:
        created = self.create(self.case("cookie"))
        record = self.store.prediction_for_version(created["product_id"], 1)
        self.assertIsNotNone(record)
        self.assertIsNotNone(record.get("evaluation"))
        self.assertIn("conflicts", record)
        self.assertAlmostEqual(record["objective"], created["evaluation"]["objective"], places=5)


class TrialAndDiagnosisTests(LoopTestCase):
    def test_a_trial_is_measured_on_the_plant_of_the_case(self) -> None:
        created = self.create(self.case("namkeen"))
        trial = self.service.run_trial(created["product_id"])
        self.assertTrue(trial["trial"]["measurements"])
        self.assertEqual(trial["plant"]["name"], self.case("namkeen")["plant"]["name"])

    def test_analysis_flags_the_residual_the_plant_created(self) -> None:
        created = self.create(self.case("namkeen"))
        trial = self.service.run_trial(created["product_id"])
        analysed = self.service.analyse_trial(created["product_id"], trial["trial_id"])
        self.assertTrue(analysed["analysis"]["residuals"])
        names = {row["kpi"] for row in analysed["analysis"]["flagged"]}
        self.assertIn("moisture_pct", names, "the dryer shortfall was not detected")
        causes = [c["cause"] for c in analysed["diagnosis"]["causes"]]
        self.assertTrue(causes)
        joined = " ".join(causes).lower()
        self.assertTrue("temperature" in joined or "drying" in joined or "water" in joined)

    def test_the_next_version_compensates_the_measured_process_offset(self) -> None:
        # This plant delivered every temperature 9 degC below the setpoint, and the
        # plan asks for more heat to make up for it. Which setpoint carries the
        # correction is the optimiser's decision - on this line the dryer is what
        # removes water, so the correction lands there - but a correction has to be
        # there, labelled, and it has to survive the search.
        created = self.create(self.case("namkeen"))
        product_id = created["product_id"]
        trial = self.service.run_trial(product_id)
        self.service.analyse_trial(product_id, trial["trial_id"])
        started = self.store.formulation(product_id).params
        planned = self.service.plan_reformulation(product_id)
        after = planned["plan"]["formulation"]["params"]
        deltas = {row["parameter"]: row for row in planned["plan"]["param_deltas"]}
        corrected = [
            row["parameter"]
            for row in planned["plan"]["param_deltas"]
            if row.get("reason") and "process offset" in str(row["reason"])
        ]
        self.assertTrue(corrected, "no setpoint was corrected for the measured offset")
        for parameter in corrected:
            self.assertGreater(after[parameter], started[parameter])
            self.assertGreater(deltas[parameter]["to"], deltas[parameter]["from"])
        rationale = " ".join(planned["plan"]["rationale"])
        self.assertIn("corrected", rationale)

    def test_the_offset_is_applied_to_the_model_that_designs_the_next_trial(self) -> None:
        # The plan is optimised against the plant as measured, so the parameters it
        # publishes are setpoints, and the correction is not silently undone.
        created = self.create(self.case("namkeen"))
        product_id = created["product_id"]
        trial = self.service.run_trial(product_id)
        self.service.analyse_trial(product_id, trial["trial_id"])
        planned = self.service.plan_reformulation(product_id)
        expected = planned["plan"]["expected"]
        measured_moisture = trial["trial"]["measurements"]["moisture_pct"]
        predicted_moisture = next(row["value"] for row in expected if row["id"] == "moisture_pct")
        self.assertLess(
            abs(predicted_moisture - 3.0),
            abs(measured_moisture - 3.0),
            "the proposed version does not improve on the moisture that was just produced",
        )

    def test_accepting_a_plan_creates_the_next_version_and_publishes_its_prediction(self) -> None:
        created = self.create(self.case("cookie"))
        product_id = created["product_id"]
        trial = self.service.run_trial(product_id)
        self.service.analyse_trial(product_id, trial["trial_id"])
        planned = self.service.plan_reformulation(product_id)
        accepted = self.service.accept_plan(product_id, planned["plan_id"])
        self.assertEqual(accepted["version"], 2)
        self.assertIsNotNone(self.store.prediction_for_version(product_id, 2))


class ClosedLoopTests(LoopTestCase):
    def test_the_loop_reaches_the_target_on_every_seeded_case(self) -> None:
        for case in case_definitions():
            with self.subTest(case=case["key"]):
                created = self.create(case)
                result = self.service.closed_loop(created["product_id"], max_trials=6)
                self.assertTrue(
                    result["success"],
                    f"{case['key']} did not converge: objective history "
                    f"{[round(h['measured_objective'], 3) for h in result['history']]}",
                )
                self.assertGreaterEqual(len(result["history"]), 1)
                self.assertLessEqual(len(result["history"]), 6)

    def test_the_loop_stops_as_soon_as_the_product_passes(self) -> None:
        created = self.create(self.case("spread"))
        result = self.service.closed_loop(created["product_id"], max_trials=6)
        self.assertTrue(result["success"])
        trials = self.store.trials(created["product_id"])
        self.assertEqual(len(trials), len(result["history"]), "a trial was run after the product passed")

    def test_efficiency_reports_what_the_loop_actually_cost(self) -> None:
        created = self.create(self.case("cookie"))
        self.service.closed_loop(created["product_id"], max_trials=6)
        efficiency = self.service.efficiency(created["product_id"])
        self.assertGreaterEqual(efficiency["trials_used"], 1)
        self.assertEqual(efficiency["versions_used"], efficiency["trials_used"])
        self.assertIsNotNone(efficiency["trials_to_target"])
        self.assertGreater(efficiency["material_used_kg"], 0.0)
        accuracy = efficiency["prediction_accuracy"]
        self.assertGreater(accuracy["samples"], 0)
        self.assertGreater(accuracy["overall_mape_pct"], 0.0)
        self.assertGreaterEqual(accuracy["interval_coverage_pct"], 0.0)
        self.assertLessEqual(accuracy["interval_coverage_pct"], 100.0)


class BenchmarkTests(LoopTestCase):
    def test_the_two_arms_are_scored_by_the_same_gate(self) -> None:
        case = self.case("cookie")
        created = self.create(case)
        trial = self.service.run_trial(created["product_id"])
        brief = _brief_from_payload(self.store.product(created["product_id"])["brief"])
        passed, hard_on, hard_total, objective = _gate(brief, trial["trial"]["measurements"])
        self.assertGreater(hard_total, 0)
        self.assertLessEqual(hard_on, hard_total)
        self.assertEqual(passed, hard_on == hard_total and objective >= 0.85)
        self.assertGreaterEqual(objective, 0.0)

    def test_a_benchmark_run_is_internally_consistent(self) -> None:
        report = run_benchmark(self.store, max_trials=3, include_conflict=False)
        self.assertTrue(report["arms"])
        self.assertTrue(report["comparison"])
        for row in report["comparison"]:
            agent = row["agent_trials"] if row["agent_trials"] is not None else report["max_trials"] + 1
            ofat = row["ofat_trials"] if row["ofat_trials"] is not None else report["max_trials"] + 1
            self.assertEqual(row["trials_saved"], ofat - agent)
        summary = report["summary"]
        self.assertEqual(summary["cases"], len(report["comparison"]))
        self.assertTrue(format_benchmark(report))

    def test_the_agent_arm_beats_a_single_lever_search_on_the_seeded_cases(self) -> None:
        # The headline claim, stated as a comparison and not as an absolute: the
        # agent reaches the target on more cases, and in fewer trials, than
        # one-factor-at-a-time with the same budget and the same starting point.
        report = run_benchmark(self.store, max_trials=4, include_conflict=False)
        summary = report["summary"]
        self.assertGreater(summary["agent_successes"], summary["ofat_successes"])
        self.assertIsNotNone(summary["agent_mean_trials_to_target"])
        self.assertLess(summary["agent_mean_trials_to_target"], summary["ofat_mean_trials_to_target"])

    def test_a_brief_that_cannot_be_met_is_reported_with_numbers(self) -> None:
        report = run_benchmark(self.store, max_trials=1, include_conflict=True)
        demonstration = report["conflict_demonstration"]
        self.assertIsNotNone(demonstration)
        self.assertTrue(demonstration["conflicts"])
        conflict = demonstration["conflicts"][0]
        self.assertIn("achieved", conflict)
        self.assertIn("target", conflict)
        self.assertTrue(conflict["recommendation"])


if __name__ == "__main__":
    unittest.main()
