"""Tests for the offline learning pipeline.

The dataset is built small (two categories, few variants) so the suite stays
quick; the point is to exercise the protocol - feature shape, a leak-free grouped
split, out-of-sample metrics against baselines, and a model that round-trips
through JSON.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.core import kb
from app.core.types import Formulation, Item
from app.ml import dataset as dataset_module, evaluate, features, registry, train


class FeatureTests(unittest.TestCase):
    def test_feature_vector_is_category_agnostic_and_fixed_length(self) -> None:
        names = features.feature_names()
        cookie = Formulation(
            category="cookie", version=1,
            items=[Item("atta", 60.0, "structure"), Item("sugar", 30.0, "sweetener"), Item("water", 10.0, "water")],
            params=kb.category("cookie").default_params(),
        )
        beverage = Formulation(
            category="beverage", version=1,
            items=[Item("water", 80.0, "water"), Item("whey_protein_concentrate", 20.0, "protein")],
            params=kb.category("beverage").default_params(),
        )
        self.assertEqual(len(features.extract(cookie)), len(names))
        self.assertEqual(len(features.extract(beverage)), len(names))

    def test_group_shares_are_recorded(self) -> None:
        formulation = Formulation(
            category="cookie", version=1,
            items=[Item("atta", 50.0, "structure"), Item("sweetener_placeholder", 50.0, "sweetener")],
            params=kb.category("cookie").default_params(),
        )
        row = features.row_features(formulation)
        self.assertAlmostEqual(row["group:grain"], 0.5, delta=1e-6)


class SplitTests(unittest.TestCase):
    def test_a_grouped_split_never_leaks_a_group_across_folds(self) -> None:
        rows = [
            {"group": f"plant-{i % 6}", "objective": 0.5, "passed": i % 2 == 0, "features": {}}
            for i in range(60)
        ]
        train_rows, val_rows, test_rows = evaluate.group_split(rows, seed=3)
        train_groups = {r["group"] for r in train_rows}
        val_groups = {r["group"] for r in val_rows}
        test_groups = {r["group"] for r in test_rows}
        self.assertFalse(train_groups & test_groups)
        self.assertFalse(train_groups & val_groups)
        self.assertFalse(val_groups & test_groups)
        self.assertTrue(train_rows and val_rows and test_rows)
        self.assertEqual(evaluate.split_kind(rows), "group")


class MetricTests(unittest.TestCase):
    def test_perfect_predictions_score_perfectly(self) -> None:
        reg = evaluate.regression_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(reg["rmse"], 0.0, delta=1e-9)
        self.assertAlmostEqual(reg["r2"], 1.0, delta=1e-9)
        clf = evaluate.classification_metrics([1.0, 0.0, 1.0, 0.0], [0.9, 0.1, 0.8, 0.2])
        self.assertAlmostEqual(clf["roc_auc"], 1.0, delta=1e-9)
        self.assertAlmostEqual(clf["accuracy"], 1.0, delta=1e-9)

    def test_roc_auc_is_chance_for_a_constant_score(self) -> None:
        clf = evaluate.classification_metrics([1.0, 0.0, 1.0, 0.0], [0.5, 0.5, 0.5, 0.5])
        self.assertAlmostEqual(clf["roc_auc"], 0.5, delta=1e-9)


class TrainAndRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="formusense-ml-"))
        rows = dataset_module.build_acceptance(
            variants_per_category=12, seed=5, categories=["cookie", "beverage"]
        )
        cls.ref = dataset_module.write_dataset(
            rows,
            name="acceptance",
            version="test",
            provenance={"source": "unit test"},
            root=cls.tmp / "datasets",
        )
        # Fit one shared bundle up front: the registry tests read the latest
        # bundle, and unittest orders methods alphabetically, so a bundle written
        # by another test method would not necessarily exist yet.
        cls.training = train.train_acceptance(
            dataset_ref=cls.ref, version="shared", models_root=cls.tmp / "models"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_dataset_round_trips_with_a_manifest(self) -> None:
        rows = dataset_module.load_rows(self.ref)
        self.assertGreater(len(rows), 0)
        manifest = dataset_module.manifest(self.ref)
        self.assertEqual(manifest["rows"], len(rows))
        self.assertTrue(manifest["content_sha256"])

    def test_training_produces_out_of_sample_metrics_and_saves_a_bundle(self) -> None:
        report = train.train_acceptance(
            dataset_ref=self.ref, version="test", models_root=self.tmp / "models"
        )
        metrics = report["metrics"]
        self.assertEqual(metrics["split"], "group")
        test = metrics["test"]
        self.assertIn("rmse", test["regression"])
        self.assertIn("roc_auc", test["classification"])
        # The model must be compared against a baseline, whatever the outcome.
        self.assertIn("baseline_regression", test)
        self.assertIn("baseline_classification", test)
        self.assertIn("beats_baseline", metrics)
        self.assertTrue(Path(report["path"]).is_file())

    def test_a_saved_bundle_predicts_a_formulation(self) -> None:
        bundle = registry.latest_bundle(name="acceptance", root=self.tmp / "models")
        self.assertIsNotNone(bundle)
        assert bundle is not None
        formulation = Formulation(
            category="beverage", version=1,
            items=[Item("water", 70.0, "water"), Item("whey_protein_concentrate", 30.0, "protein")],
            params=kb.category("beverage").default_params(),
        )
        result = bundle.predict(formulation)
        self.assertIn("objective", result)
        self.assertGreaterEqual(result["pass_probability"], 0.0)
        self.assertLessEqual(result["pass_probability"], 1.0)

    def test_bundle_round_trips_through_json(self) -> None:
        bundle = registry.latest_bundle(name="acceptance", root=self.tmp / "models")
        assert bundle is not None
        again = registry.ModelBundle.from_dict(bundle.as_dict())
        self.assertEqual(again.feature_names, bundle.feature_names)
        self.assertEqual(len(again.ridge.coefficients), len(bundle.ridge.coefficients))


if __name__ == "__main__":
    unittest.main()
