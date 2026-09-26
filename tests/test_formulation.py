"""Tests for generating a formulation and checking it against the brief.

A generated formulation has to be legal before it is good: it must add up, respect
slot limits and hard inclusion limits, honour the diet and the allergen list, and
land inside the cost ceiling. Only then does the *quality* of the target match
matter, which is what the optimiser tests cover.
"""
from __future__ import annotations

import unittest

from app.bootstrap import CASES, INFEASIBLE_CASE
from app.core import brief as brief_module
from app.core import engine, formulate, kb, optimize
from tests.test_brief import payload


class GenerationTests(unittest.TestCase):
    def test_every_seeded_case_produces_a_legal_formulation(self) -> None:
        for case in CASES:
            with self.subTest(case=case["key"]):
                brief = brief_module.build_brief(payload(case))
                formulation = formulate.generate(brief, seed=1)
                self.assertAlmostEqual(formulation.total_pct, 100.0, delta=0.05)
                self.assertTrue(formulation.items)
                for item in formulation.items:
                    ingredient = kb.ingredient(item.ingredient_id)
                    self.assertGreaterEqual(item.pct, -1e-9)
                    if ingredient.hard_max_pct > 0:
                        self.assertLessEqual(item.pct, ingredient.hard_max_pct + 1e-6)

    def test_the_diet_is_respected(self) -> None:
        # A vegan version of the seeded cookie brief: the same product, no egg,
        # no dairy, no honey.
        brief = brief_module.build_brief(payload(CASES[1], category="bar", diet="vegan"))
        formulation = formulate.generate(brief, seed=2)
        for item in formulation.items:
            self.assertTrue(
                kb.diet_allowed(kb.ingredient(item.ingredient_id), "vegan"),
                f"{item.ingredient_id} is not vegan",
            )

    def test_allergens_to_avoid_stay_out(self) -> None:
        brief = brief_module.build_brief(
            payload(CASES[1], allergens_to_avoid=["gluten", "soy"])
        )
        formulation = formulate.generate(brief, seed=2)
        for item in formulation.items:
            ingredient = kb.ingredient(item.ingredient_id)
            for allergen in ("gluten", "soy"):
                self.assertNotIn(allergen, ingredient.allergens, f"{item.ingredient_id} carries {allergen}")

    def test_generation_is_reproducible(self) -> None:
        brief = brief_module.build_brief(payload(CASES[0]))
        first = formulate.generate(brief, seed=7)
        second = formulate.generate(brief, seed=7)
        self.assertEqual(
            sorted((i.ingredient_id, round(i.pct, 6)) for i in first.items),
            sorted((i.ingredient_id, round(i.pct, 6)) for i in second.items),
        )

    def test_the_cost_ceiling_is_honoured(self) -> None:
        brief = brief_module.build_brief(payload(CASES[0]))
        result = formulate.generate_optimised(brief, seed=1)
        report = formulate.check(result.formulation, brief).as_dict()
        self.assertTrue(report["ok"], report["hard_violations"])
        self.assertEqual(report["diet_conflicts"], [])
        self.assertEqual(report["allergen_conflicts"], [])
        self.assertIsNotNone(report["cost_ceiling_inr_kg"])
        self.assertLessEqual(report["cost_inr_kg"], report["cost_ceiling_inr_kg"] * 1.02)


class ConflictTests(unittest.TestCase):
    def test_an_inconsistent_brief_is_reported_as_a_conflict(self) -> None:
        brief = brief_module.build_brief(payload(INFEASIBLE_CASE))
        result = formulate.generate_optimised(brief, seed=1)
        conflicts = formulate.conflicts(brief, result.formulation, engine.predict(result.formulation, brief))
        self.assertTrue(conflicts, "the deliberately infeasible brief produced no conflict")
        fibre = next((c for c in conflicts if c["kpi"] == "fibre_g"), None)
        self.assertIsNotNone(fibre)
        self.assertGreater(fibre["achieved"], fibre["target"])
        self.assertTrue(fibre["recommendation"])

    def test_a_satisfiable_brief_reports_no_conflict(self) -> None:
        brief = brief_module.build_brief(payload(CASES[1]))
        result = formulate.generate_optimised(brief, seed=1)
        conflicts = formulate.conflicts(brief, result.formulation, engine.predict(result.formulation, brief))
        self.assertEqual(conflicts, [])


class AcidDosingTests(unittest.TestCase):
    def test_the_acidulant_is_dosed_to_the_ph_the_brief_asks_for(self) -> None:
        brief = brief_module.build_brief(payload(CASES[2]))
        result = formulate.generate_optimised(brief, seed=1)
        prediction = engine.predict(result.formulation, brief)
        target = brief.target("ph")
        self.assertIsNotNone(target)
        self.assertLess(abs(prediction.values["ph"] - target.target), target.tolerance * 1.5)


class ExplainTests(unittest.TestCase):
    def test_every_formulation_line_is_explained(self) -> None:
        brief = brief_module.build_brief(payload(CASES[1]))
        formulation = formulate.generate(brief, seed=1)
        lines = formulate.explain(formulation, brief)
        self.assertTrue(lines)
        self.assertTrue(all(isinstance(line, str) and line for line in lines))


class OptimiserTests(unittest.TestCase):
    def test_optimising_never_makes_the_objective_worse(self) -> None:
        brief = brief_module.build_brief(payload(CASES[0]))
        start = formulate.generate(brief, seed=1)
        before = engine.evaluate_against_brief(engine.predict(start, brief), brief)["objective"]
        result = optimize.optimise(start, brief, budget=800)
        after = engine.evaluate_against_brief(engine.predict(result.formulation, brief), brief)["objective"]
        self.assertGreaterEqual(after, before - 1e-6)

    def test_the_optimiser_records_the_moves_that_produced_the_result(self) -> None:
        brief = brief_module.build_brief(payload(CASES[1]))
        start = formulate.generate(brief, seed=1)
        result = optimize.optimise(start, brief, budget=600)
        self.assertTrue(result.moves)
        for move in result.moves:
            self.assertTrue(move.key)
            self.assertGreaterEqual(move.objective_after, move.objective_before - 1e-6)
        self.assertGreater(result.evaluations, 0)
        self.assertTrue(result.history)
        self.assertTrue(result.stopped)


if __name__ == "__main__":
    unittest.main()
