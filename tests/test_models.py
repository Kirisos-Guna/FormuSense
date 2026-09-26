"""Tests for the first-principles models: nutrition, water activity, pH, scoring.

These are the numbers the whole product is judged on, so the properties tested
here are the ones a food technologist would check by hand: the mass balance
closes, Atwater energy follows from the composition, water activity falls as
solids rise, a stronger acid dose gives a lower pH, and the desirability band is
the band the brief declared.
"""
from __future__ import annotations

import unittest

from app.bootstrap import CASES
from app.core import brief as brief_module
from app.core import engine, formulate, kb, kpi as kpi_registry, nutrition, physical
from app.core.types import Formulation, Item
from tests.test_brief import payload


def mix(**parts: float) -> Formulation:
    return Formulation(
        category="cookie",
        version=1,
        items=[Item(ingredient_id, pct, "") for ingredient_id, pct in parts.items()],
    )


class MassBalanceTests(unittest.TestCase):
    def test_the_recipe_total_is_carried_through_the_analysis(self) -> None:
        formulation = mix(atta=50.0, sugar=20.0, rice_bran_oil=10.0, water=20.0)
        comp = nutrition.analyse(formulation, 3.0)
        self.assertAlmostEqual(comp.total_pct, formulation.total_pct, places=3)
        # The mix splits into water and dry matter; the dry matter is what the
        # macros are expressed on before the process removes water.
        self.assertAlmostEqual(comp.moisture_mix + comp.mix_dry_matter, 100.0, places=2)
        self.assertAlmostEqual(comp.moisture_mix, 26.07, places=1)

    def test_drying_concentrates_everything_else(self) -> None:
        formulation = mix(atta=50.0, sugar=20.0, rice_bran_oil=10.0, water=20.0)
        wet = nutrition.analyse(formulation, 20.0)
        dry = nutrition.analyse(formulation, 3.0)
        self.assertGreater(dry.final["protein_g"], wet.final["protein_g"])
        self.assertGreater(dry.final["energy_kcal"], wet.final["energy_kcal"])

    def test_energy_follows_atwater_from_the_same_composition(self) -> None:
        formulation = mix(atta=60.0, sugar=25.0, rice_bran_oil=15.0)
        comp = nutrition.analyse(formulation, 3.0)
        manual = (
            4.0 * comp.final["protein_g"]
            + 9.0 * comp.final["fat_g"]
            + 4.0 * comp.final["carb_g"]
            + 2.0 * comp.final["fibre_g"]
        )
        self.assertAlmostEqual(comp.final["energy_kcal"], manual, delta=1.0)

    def test_more_sugar_means_more_sugar_on_the_label(self) -> None:
        low = nutrition.analyse(mix(atta=80.0, sugar=20.0), 3.0)
        high = nutrition.analyse(mix(atta=60.0, sugar=40.0), 3.0)
        self.assertGreater(high.final["sugar_g"], low.final["sugar_g"])


class PhysicalPropertyTests(unittest.TestCase):
    def category(self):
        return kb.category("cookie")

    def test_a_hotter_oven_leaves_less_moisture(self) -> None:
        cool, _ = physical.predict_moisture("cookie", 16.0, {"bake_temp_c": 160.0, "bake_time_min": 12.0})
        hot, _ = physical.predict_moisture("cookie", 16.0, {"bake_temp_c": 200.0, "bake_time_min": 12.0})
        self.assertLess(hot, cool)

    def test_a_longer_bake_leaves_less_moisture(self) -> None:
        short, _ = physical.predict_moisture("cookie", 16.0, {"bake_temp_c": 175.0, "bake_time_min": 6.0})
        long, _ = physical.predict_moisture("cookie", 16.0, {"bake_temp_c": 175.0, "bake_time_min": 20.0})
        self.assertLess(long, short)

    def test_water_activity_falls_as_the_solids_rise(self) -> None:
        dilute = nutrition.analyse(mix(atta=20.0, sugar=10.0, water=70.0), 40.0)
        concentrated = nutrition.analyse(mix(atta=30.0, sugar=50.0, water=20.0), 25.0)
        self.assertLess(physical.water_activity(concentrated)[0], physical.water_activity(dilute)[0])

    def test_a_stronger_acid_dose_gives_a_lower_ph(self) -> None:
        # A realistic dose range for a biscuit: acid is used in tenths of a percent.
        weak = nutrition.analyse(mix(atta=80.0, sugar=18.0, citric_acid=0.02), 3.0)
        strong = nutrition.analyse(mix(atta=80.0, sugar=18.0, citric_acid=0.20), 3.0)
        self.assertLess(physical.predict_ph(strong, "cookie")[0], physical.predict_ph(weak, "cookie")[0])

    def test_stability_gets_worse_as_water_activity_rises(self) -> None:
        low = physical.predict_stability(nutrition.analyse(mix(atta=80.0, water=20.0), 3.0), 0.30, 6.5, "cookie")[0]
        high = physical.predict_stability(nutrition.analyse(mix(atta=80.0, water=20.0), 3.0), 0.80, 6.5, "cookie")[0]
        self.assertGreater(high["mould_risk"], low["mould_risk"])
        self.assertLess(high["shelf_life_days"], low["shelf_life_days"])


class DesirabilityTests(unittest.TestCase):
    """The scoring rules every part of the loop reads its verdict from."""

    def band(self):
        return kpi_registry.make_target("moisture_pct", 3.0, 1.0, 1.0, True)

    def test_band_targets_peak_on_the_target(self) -> None:
        target = self.band()
        self.assertGreater(
            kpi_registry.desirability(target, 3.0), kpi_registry.desirability(target, 3.5)
        )
        self.assertGreater(
            kpi_registry.desirability(target, 3.5), kpi_registry.desirability(target, 5.0)
        )

    def test_the_declared_tolerance_is_the_pass_band(self) -> None:
        # The brief says plus/minus 1.0, so the edge of that band is a pass.
        target = self.band()
        self.assertTrue(kpi_registry.is_on_target(target, 4.0))
        self.assertTrue(kpi_registry.is_on_target(target, 2.0))
        self.assertFalse(kpi_registry.is_on_target(target, 4.4))
        self.assertFalse(kpi_registry.is_on_target(target, 1.5))

    def test_one_sided_targets_do_not_fail_by_overshooting(self) -> None:
        target = kpi_registry.make_target("protein_g", 12.0, 1.2, 1.3, True)
        target.direction = "higher"
        self.assertTrue(kpi_registry.is_on_target(target, 11.5))
        self.assertTrue(kpi_registry.is_on_target(target, 18.0))
        self.assertFalse(kpi_registry.is_on_target(target, 9.0))

    def test_desirability_keeps_a_gradient_far_from_the_target(self) -> None:
        # The optimiser needs a slope even far outside the band, or it stalls.
        target = self.band()
        scores = [kpi_registry.desirability(target, value) for value in (3.0, 4.0, 6.0, 10.0, 20.0)]
        for earlier, later in zip(scores, scores[1:]):
            self.assertGreater(earlier, later)
        self.assertGreater(scores[-1], 0.0)


class PredictionTests(unittest.TestCase):
    def test_predictions_carry_intervals_that_contain_their_value(self) -> None:
        brief = brief_module.build_brief(payload(CASES[1]))
        result = formulate.generate_optimised(brief, seed=3)
        prediction = engine.predict(result.formulation, brief)
        self.assertTrue(prediction.predictions)
        for row in prediction.predictions:
            self.assertLessEqual(row.lo, row.value + 1e-9)
            self.assertGreaterEqual(row.hi, row.value - 1e-9)
            self.assertGreater(row.confidence, 0.0)
            self.assertTrue(row.method)

    def test_the_evaluation_agrees_with_the_desirability_of_each_row(self) -> None:
        brief = brief_module.build_brief(payload(CASES[0]))
        prediction = engine.predict(formulate.generate_optimised(brief, seed=1).formulation, brief)
        evaluation = engine.evaluate_against_brief(prediction, brief)
        self.assertEqual(len(evaluation["rows"]), len(brief.targets))
        for row in evaluation["rows"]:
            target = brief.target(row["kpi"])
            # The row is rounded for transport, so compare at that precision.
            self.assertAlmostEqual(
                row["desirability"], kpi_registry.desirability(target, row["value"]), places=4
            )
        self.assertGreaterEqual(evaluation["objective"], 0.0)
        self.assertLessEqual(evaluation["objective"], 1.0)

    def test_the_objective_rises_as_a_formulation_moves_onto_its_targets(self) -> None:
        brief = brief_module.build_brief(payload(CASES[1]))
        good = formulate.generate_optimised(brief, seed=1).formulation
        off = Formulation(
            category="cookie",
            version=1,
            items=[
                Item("atta", 40.0, "structure"),
                Item("sugar", 40.0, "sweetener"),
                Item("water", 20.0, "water"),
            ],
        )
        good_score = engine.evaluate_against_brief(engine.predict(good, brief), brief)["objective"]
        off_score = engine.evaluate_against_brief(engine.predict(off, brief), brief)["objective"]
        self.assertGreater(good_score, off_score)


if __name__ == "__main__":
    unittest.main()
