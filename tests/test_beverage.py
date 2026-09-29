"""Tests for the ready-to-drink protein beverage category.

Three things are asserted here that the rest of the suite cannot cover: that a
specification quoted *per bottle* is converted onto the per-100 g basis the
models use, that a liquid category produces liquid physics (per-100 ml, a
viscosity reading rather than a firmness), and that the seeded beverage case
converges on its plant fault through the closed loop.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.bootstrap import CASES
from app.core import brief as brief_module, engine, kb, population
from app.core.types import Formulation, Item
from app.service import AgentService
from app.store import Store


def beverage_case():
    return next(c for c in CASES if c["key"] == "beverage")


class SpecParsingTests(unittest.TestCase):
    def test_a_number_quoted_per_bottle_is_converted_to_per_100g(self) -> None:
        text = "Protein 20 g per bottle (10 g per 100 ml). 200 ml bottle."
        numbers = brief_module.parse_spec_numbers(text)
        self.assertIn("protein_g", numbers)
        # 20 g per 200 g serving -> 10 g per 100 g.
        self.assertAlmostEqual(numbers["protein_g"]["value"], 10.0, delta=0.01)
        self.assertEqual(numbers["protein_g"].get("basis"), "per serving")
        self.assertAlmostEqual(numbers["__unit_weight_g"]["value"], 200.0, delta=0.01)

    def test_a_number_quoted_per_100ml_is_not_converted(self) -> None:
        numbers = brief_module.parse_spec_numbers("Sugars not more than 6 g per 100 ml.")
        self.assertAlmostEqual(numbers["sugar_g"]["value"], 6.0, delta=0.01)
        self.assertNotIn("basis", numbers["sugar_g"])

    def test_the_beverage_case_brief_is_read_as_designed(self) -> None:
        brief = brief_module.build_brief(
            {
                "product_name": beverage_case()["name"],
                "category": "beverage",
                "spec_text": beverage_case()["spec_text"],
                "unit_weight_g": beverage_case()["unit_weight_g"],
            }
        )
        self.assertEqual(brief.category, "beverage")
        self.assertAlmostEqual(brief.unit_weight_g, 200.0, delta=0.01)
        protein = brief.target("protein_g")
        self.assertIsNotNone(protein)
        self.assertAlmostEqual(protein.target, 10.0, delta=0.05)
        shelf = brief.target("shelf_life_days")
        self.assertAlmostEqual(shelf.target, 180.0, delta=0.5)

    def test_beverage_category_is_inferred_from_a_written_brief(self) -> None:
        category, confidence, _ = brief_module.infer_category(
            "A ready-to-drink whey protein beverage in a 200 ml bottle."
        )
        self.assertEqual(category, "beverage")
        self.assertGreater(confidence, 0.0)


class LiquidPhysicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.formulation = Formulation(
            category="beverage",
            version=1,
            items=[
                Item("water", 55.0, "water"),
                Item("whey_protein_concentrate", 13.0, "protein"),
                Item("milk_toned", 20.0, "dairy"),
                Item("sucralose", 0.03, "sweetener"),
                Item("carrageenan", 0.3, "stabiliser"),
            ],
            params=kb.category("beverage").default_params(),
        )

    def test_a_beverage_is_reported_per_100ml_and_per_serving(self) -> None:
        brief = brief_module.build_brief(
            {"product_name": "RTD", "category": "beverage", "spec_text": "", "unit_weight_g": 200.0}
        )
        result = engine.predict(self.formulation, brief)
        self.assertTrue(result.details["is_liquid"])
        self.assertIsNotNone(result.details["per_100ml"])
        self.assertGreater(result.details["per_100ml"]["protein_g"], 0.0)
        # Density of a drink is above 1 g/ml, so per-100 ml exceeds per-100 g.
        self.assertGreater(result.values["protein_g"], 0.0)
        self.assertGreaterEqual(result.details["density_g_per_ml"], 1.0)

    def test_a_beverage_reports_viscosity_not_hardness(self) -> None:
        result = engine.predict(self.formulation)
        self.assertIn("consistency_index", result.values)
        # A drinkable product sits at the low end of the consistency scale.
        self.assertLess(result.values["consistency_index"], 40.0)
        self.assertAlmostEqual(result.values.get("hardness_n", 0.0), 0.0, delta=1e-6)

    def test_the_heat_treatment_drives_shelf_life(self) -> None:
        pasteurised = Formulation(
            category="beverage",
            version=1,
            items=list(self.formulation.items),
            params={**self.formulation.params, "heat_treat_temp_c": 75.0},
        )
        uht = Formulation(
            category="beverage",
            version=1,
            items=list(self.formulation.items),
            params={**self.formulation.params, "heat_treat_temp_c": 140.0},
        )
        low = engine.predict(pasteurised).values["shelf_life_days"]
        high = engine.predict(uht).values["shelf_life_days"]
        self.assertGreater(high, low, "a UHT treatment must outlast a pasteurisation")


class BeverageLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-bev-"))
        self.store = Store(self.tmp / "test.db")
        self.service = AgentService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self):
        case = beverage_case()
        return self.service.create_product(
            {
                "product_name": case["name"],
                "category": case["category"],
                "spec_text": case["spec_text"],
                "diet": case["diet"],
                "claims": list(case["claims"]),
                "unit_weight_g": case["unit_weight_g"],
                "plant": case["plant"],
            }
        )

    def test_designing_the_beverage_records_a_complete_formulation(self) -> None:
        created = self.create()
        self.assertEqual(created["brief"]["category"], "beverage")
        self.assertAlmostEqual(created["formulation"]["total_pct"], 100.0, delta=0.05)
        self.assertTrue(created["prediction"]["predictions"])

    def test_population_guidance_is_published_for_every_group(self) -> None:
        created = self.create()
        guide = self.service.population_guide(created["product_id"])
        self.assertEqual(len(guide["groups"]), len(population.profiles()))
        self.assertGreater(guide["serving"]["protein_g"], 0.0)
        self.assertTrue(guide["serving_label"].startswith("one"))

    def test_the_loop_corrects_the_heat_treatment_and_passes(self) -> None:
        created = self.create()
        result = self.service.closed_loop(created["product_id"], max_trials=6)
        self.assertTrue(
            result["success"],
            "beverage did not converge: "
            f"{[round(h['measured_objective'], 3) for h in result['history']]}",
        )


if __name__ == "__main__":
    unittest.main()
