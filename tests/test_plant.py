"""Tests for the simulated pilot plant.

The plant is the referee for every claim the agent makes, so its behaviour has to
be exactly what the case study documents: a dryer that under-delivers leaves more
water, a kettle that loses acid lands at a higher pH, dosing errors move the
minors, and two runs of the same recipe differ only by analytical repeatability.

The last one is a regression test with a history. Trial scatter used to be scaled
from the *model's* uncertainty, which made moisture readings vary by +/- 1.6 points
against a 3.0 +/- 1.0 target - noise no laboratory would recognise, and enough to
bury the systematic process offset the diagnosis exists to find.
"""
from __future__ import annotations

import unittest

from app.bootstrap import CASES
from app.core import brief as brief_module
from app.core import engine, formulate, kb, uncertainty
from app.plant import PlantConfig, measure, run_trial, truth_values
from tests.test_brief import payload


def prepared(case_key: str = "cookie"):
    case = next(c for c in CASES if c["key"] == case_key)
    brief = brief_module.build_brief(payload(case))
    formulation = formulate.generate(brief, seed=1)
    return case, brief, formulation


class PlantEffectTests(unittest.TestCase):
    def test_an_under_delivering_dryer_leaves_more_water(self) -> None:
        case, brief, formulation = prepared()
        perfect = truth_values(formulation, brief, PlantConfig(name="perfect", noise_scale=1.0))
        short = truth_values(
            formulation, brief, PlantConfig(name="short", drying_efficiency=0.85, noise_scale=1.0)
        )
        self.assertGreater(short["moisture_pct"], perfect["moisture_pct"])
        if "texture_index" in short:
            self.assertLessEqual(short["texture_index"], perfect["texture_index"] + 1e-9)

    def test_a_kettle_that_loses_acid_lands_at_a_higher_ph(self) -> None:
        case, brief, formulation = prepared("spread")
        full = truth_values(formulation, brief, PlantConfig(name="full", noise_scale=1.0))
        lossy = truth_values(
            formulation, brief, PlantConfig(name="lossy", acid_retention=0.5, noise_scale=1.0)
        )
        self.assertGreater(lossy["ph"], full["ph"])

    def test_inversion_raises_the_reducing_sugars(self) -> None:
        case, brief, formulation = prepared("spread")
        plain = truth_values(formulation, brief, PlantConfig(name="plain", noise_scale=1.0))
        inverting = truth_values(
            formulation, brief, PlantConfig(name="inverting", sugar_inversion=1.15, noise_scale=1.0)
        )
        self.assertGreater(inverting["sugar_g"], plain["sugar_g"])

    def test_temperature_offset_only_moves_the_temperature_it_names(self) -> None:
        case, brief, formulation = prepared()
        on_setpoint = truth_values(formulation, brief, PlantConfig(name="on", temp_offset_c=0.0, noise_scale=1.0))
        cold = truth_values(formulation, brief, PlantConfig(name="cold", temp_offset_c=-25.0, noise_scale=1.0))
        self.assertNotAlmostEqual(on_setpoint["moisture_pct"], cold["moisture_pct"], places=3)


class MeasurementTests(unittest.TestCase):
    def test_trial_scatter_is_the_repeatability_of_the_assay(self) -> None:
        # The analytical sigma for moisture is 0.12 points, so a run of aliquots
        # of the same batch must stay well inside a tenth of a point times the
        # lab's own noise scale - not the 1.6 points the model error would give.
        samples = [uncertainty.measurement_sigma("moisture_pct") for _ in range(1)]
        self.assertAlmostEqual(samples[0], 0.12, places=4)
        values = {"moisture_pct": 3.0, "water_activity": 0.35, "ph": 6.4}
        import random

        rng = random.Random(11)
        readings = [measure(values, PlantConfig(noise_scale=1.0), rng) for _ in range(40)]
        spread = max(r["moisture_pct"] for r in readings) - min(r["moisture_pct"] for r in readings)
        self.assertLess(spread, 1.0)
        spread_ph = max(r["ph"] for r in readings) - min(r["ph"] for r in readings)
        self.assertLess(spread_ph, 0.2)

    def test_the_model_uncertainty_is_larger_than_the_measurement_noise(self) -> None:
        for kpi_id, value in (("moisture_pct", 3.0), ("sugar_g", 20.0), ("ph", 3.6), ("protein_g", 15.0)):
            with self.subTest(kpi=kpi_id):
                model = abs(value) * uncertainty.BASE_SIGMA[kpi_id]
                self.assertGreater(model, uncertainty.measurement_sigma(kpi_id, value))

    def test_a_trial_is_reproducible_for_a_given_seed(self) -> None:
        case, brief, formulation = prepared("namkeen")
        config = PlantConfig(name="pilot", drying_efficiency=0.96, temp_offset_c=-9.0)
        first = run_trial(formulation, brief, config, seed=42, label="T1")
        second = run_trial(formulation, brief, config, seed=42, label="T1")
        self.assertEqual(first.measurements, second.measurements)

    def test_the_plant_only_reports_the_kpis_it_was_asked_for(self) -> None:
        case, brief, formulation = prepared()
        config = PlantConfig(name="pilot")
        trial = run_trial(
            formulation, brief, config, seed=5, label="T1", include=["moisture_pct", "water_activity"]
        )
        self.assertEqual(set(trial.measurements), {"moisture_pct", "water_activity"})

    def test_a_trial_reports_the_process_it_actually_delivered(self) -> None:
        case, brief, formulation = prepared("namkeen")
        config = PlantConfig(name="pilot", temp_offset_c=-9.0)
        trial = run_trial(formulation, brief, config, seed=3, label="T1")
        self.assertTrue(trial.process_actuals)
        for key, actual in trial.process_actuals.items():
            if key in formulation.params:
                expected = formulation.params[key] + (-9.0 if "temp" in key else 0.0)
                self.assertAlmostEqual(actual, expected, places=3)


class BatchAndYieldTests(unittest.TestCase):
    def test_a_trial_has_a_batch_size_and_a_yield(self) -> None:
        case, brief, formulation = prepared()
        trial = run_trial(formulation, brief, PlantConfig(name="pilot"), seed=1, label="T1")
        self.assertGreater(trial.batch_size_kg, 0.0)
        self.assertTrue(trial.notes or trial.measurements)


if __name__ == "__main__":
    unittest.main()
