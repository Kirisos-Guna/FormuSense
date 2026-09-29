"""Tests for the population protein guidance.

These are known-value tests: the reference values are transcribed from ICMR-NIN
2020, so the arithmetic (share of RDA, servings to reach it) is checkable by hand
against the data file rather than against the implementation.
"""
from __future__ import annotations

import unittest

from app.core import population


class ProfileSetTests(unittest.TestCase):
    def test_every_population_group_named_in_the_brief_is_present(self) -> None:
        ids = {p.id for p in population.profiles()}
        for required in (
            "child_1_3",
            "adult_man",
            "adult_woman",
            "pregnant_woman",
            "senior_man_60",
            "senior_woman_60",
        ):
            self.assertIn(required, ids, f"missing population group {required}")

    def test_adult_reference_values_match_icmr_2020(self) -> None:
        man = population.profile("adult_man")
        woman = population.profile("adult_woman")
        assert man is not None and woman is not None
        self.assertAlmostEqual(man.rda_g_day, 54.0, delta=0.5)
        self.assertAlmostEqual(woman.rda_g_day, 46.0, delta=0.5)
        self.assertAlmostEqual(man.rda_g_kg_day, 0.83, delta=0.02)

    def test_source_is_recorded_and_not_medical_advice(self) -> None:
        note = population.source_note()
        self.assertIn("ICMR", note["source"])
        self.assertIn("not medical", note["disclaimer"].lower())


class GuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guide = population.guidance(20.0, serving_label="one 200 ml bottle")

    def row(self, group_id: str):
        return next(r for r in self.guide["groups"] if r["id"] == group_id)

    def test_share_of_rda_is_arithmetically_correct(self) -> None:
        woman = self.row("adult_woman")
        self.assertAlmostEqual(woman["pct_rda_per_serving"], 100.0 * 20.0 / 46.0, delta=0.2)
        self.assertAlmostEqual(woman["servings_for_rda"], 46.0 / 20.0, delta=0.02)

    def test_a_child_serving_is_flagged_when_it_is_a_large_share(self) -> None:
        child = self.row("child_1_3")
        self.assertGreater(child["pct_rda_per_serving"], 100.0)
        self.assertTrue(child["flags"], "a whole-day requirement in one serving was not flagged")

    def test_pregnancy_carries_its_cautions_and_flag(self) -> None:
        pregnant = self.row("pregnant_woman")
        self.assertGreater(pregnant["rda_g_day"], self.row("adult_woman")["rda_g_day"])
        self.assertTrue(any("pregnan" in c.lower() for c in pregnant["cautions"]))
        self.assertTrue(pregnant["flags"])

    def test_seniors_report_both_the_rda_and_the_suggested_target(self) -> None:
        senior = self.row("senior_man_60")
        self.assertAlmostEqual(senior["rda_g_day"], 54.0, delta=0.5)
        self.assertIsNotNone(senior["suggested_target_g_day"])
        self.assertGreater(senior["suggested_target_g_day"], senior["rda_g_day"])

    def test_protein_energy_share_is_checked_against_the_amdr(self) -> None:
        guide = population.guidance(20.0, energy_per_serving_kcal=120.0)
        # 20 g protein x 4 kcal = 80 kcal of 120 kcal = 66.7%: far above the AMDR.
        self.assertAlmostEqual(guide["protein_energy_pct"], 66.67, delta=0.1)
        self.assertFalse(guide["protein_energy_pct_in_range"])

    def test_selection_limits_the_rows_returned(self) -> None:
        guide = population.guidance(20.0, selected=["adult_man", "adult_woman"])
        self.assertEqual({r["id"] for r in guide["groups"]}, {"adult_man", "adult_woman"})


class ServingTests(unittest.TestCase):
    def test_serving_nutrients_scale_from_per_100g(self) -> None:
        class Fake:
            final = {"protein_g": 10.0, "energy_kcal": 60.0, "sugar_g": 5.0}

        rows = population.serving_nutrients(Fake(), 200.0)
        self.assertAlmostEqual(rows["protein_g"], 20.0, delta=1e-6)
        self.assertAlmostEqual(rows["energy_kcal"], 120.0, delta=1e-6)
        self.assertAlmostEqual(rows["sugar_g"], 10.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
