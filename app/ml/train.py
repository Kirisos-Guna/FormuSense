"""Training entry point: fit, validate on held-out data, save, report.

The protocol, stated plainly because the report repeats it:

1. Load the versioned dataset.
2. Split it by group into train / validation / test.
3. Fit the ridge regressor and the logistic classifier on **train only**.
4. Choose nothing on the test set; calibrate the classifier and read the
   validation metrics on the **validation** fold.
5. Report the final numbers on the **test** fold, alongside the two baselines.

Nothing is fitted on the test fold, and the baselines are scored on the same
rows, so "the model beats the baseline" is a like-for-like statement.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import dataset as dataset_module, evaluate
from .features import matrix_from_rows
from .models import LogisticRegressor, MajorityBaseline, MeanBaseline, RidgeRegressor
from .registry import DEFAULT_NAME, ModelBundle, save_bundle


def _labels(rows, key: str) -> List[float]:
    return [float(row.get(key) or 0.0) for row in rows]


def train_acceptance(
    dataset_ref: Optional[dataset_module.DatasetRef] = None,
    name: str = DEFAULT_NAME,
    version: str = "v1",
    seed: int = 13,
    val_fraction: float = 0.2,
    test_fraction: float = 0.2,
    dataset_root: Optional[Path] = None,
    models_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Train, validate and save the acceptance model. Returns its report.

    ``dataset_root`` locates the dataset to read and ``models_root`` where the
    bundle is written; both default to the packaged data directories. They are
    separate arguments because they are separate stores - a run may train from a
    frozen dataset onto scratch space.
    """
    ref = dataset_ref or dataset_module.resolve(name=DEFAULT_NAME, root=dataset_root)
    if ref is None or not ref.rows_path.is_file():
        raise FileNotFoundError(
            "No dataset found. Build one first with: python run.py --build-dataset"
        )
    rows = dataset_module.load_rows(ref)
    manifest = dataset_module.manifest(ref)
    train, val, test = evaluate.group_split(
        rows, val_fraction=val_fraction, test_fraction=test_fraction, seed=seed
    )

    x_train = matrix_from_rows(train)
    x_val = matrix_from_rows(val)
    x_test = matrix_from_rows(test)
    y_train, y_val, y_test = (_labels(train, "objective"), _labels(val, "objective"), _labels(test, "objective"))
    c_train, c_val, c_test = (_labels(train, "passed"), _labels(val, "passed"), _labels(test, "passed"))

    ridge = RidgeRegressor().fit(x_train, y_train)
    logistic = LogisticRegressor().fit(x_train, c_train)
    logistic.calibrate(x_val, c_val)

    mean_baseline = MeanBaseline().fit(y_train)
    rate_baseline = MajorityBaseline().fit(c_train)

    # Metric set on the validation fold, used to sanity-check before saving.
    validation = {
        "regression": evaluate.regression_metrics(y_val, ridge.predict(x_val)),
        "classification": evaluate.classification_metrics(c_val, logistic.predict_proba(x_val)),
    }

    # The numbers that get reported: the untouched test fold.
    model_regression = evaluate.regression_metrics(y_test, ridge.predict(x_test))
    model_classification = evaluate.classification_metrics(c_test, logistic.predict_proba(x_test))
    baseline_regression = evaluate.regression_metrics(y_test, mean_baseline.predict(x_test))
    baseline_classification = evaluate.classification_metrics(
        c_test, rate_baseline.predict_proba(x_test)
    )
    calibration = evaluate.calibration_table(c_test, logistic.predict_proba(x_test))

    metrics: Dict[str, Any] = {
        "split": evaluate.split_kind(rows),
        "rows": {"train": len(train), "validation": len(val), "test": len(test)},
        "validation": validation,
        "test": {
            "regression": model_regression,
            "classification": model_classification,
            "baseline_regression": baseline_regression,
            "baseline_classification": baseline_classification,
        },
        "calibration": calibration,
        "beats_baseline": {
            "rmse": model_regression["rmse"] < baseline_regression["rmse"],
            "roc_auc": model_classification["roc_auc"] > baseline_classification["roc_auc"],
            "brier": model_classification["brier"] < baseline_classification["brier"],
        },
    }

    bundle = ModelBundle(
        name=name,
        version=version,
        feature_names=dataset_module.feature_names(),
        dataset={
            "name": ref.name,
            "version": ref.version,
            "rows": len(rows),
            "content_sha256": manifest.get("content_sha256", ""),
            "provenance": manifest.get("provenance", {}),
        },
        metrics=metrics,
        train_config={
            "seed": seed,
            "val_fraction": val_fraction,
            "test_fraction": test_fraction,
            "ridge_target": "objective",
            "classifier": "logistic+platt",
        },
        ridge=ridge,
        logistic=logistic,
    )
    path = save_bundle(bundle, root=models_root)
    return {
        "name": name,
        "version": version,
        "path": str(path),
        "dataset": bundle.dataset,
        "metrics": metrics,
        "drivers": bundle.driver_notes(),
    }


def format_training_report(report: Dict[str, Any]) -> str:
    """Plain-text rendering, used by the CLI and the report."""
    metrics = report.get("metrics", {})
    test = metrics.get("test", {})
    reg = test.get("regression", {})
    base_reg = test.get("baseline_regression", {})
    clf = test.get("classification", {})
    base_clf = test.get("baseline_classification", {})
    rows = metrics.get("rows", {})
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("  ACCEPTANCE MODEL - training report (all numbers out of sample)")
    lines.append("=" * 78)
    lines.append(f"  model      : {report.get('name')}/{report.get('version')}")
    lines.append(f"  dataset    : {report.get('dataset', {}).get('name')}/{report.get('dataset', {}).get('version')}"
                 f" ({report.get('dataset', {}).get('rows')} rows)")
    lines.append(f"  split      : by {metrics.get('split')} -> "
                 f"train {rows.get('train')}, val {rows.get('validation')}, test {rows.get('test')}")
    lines.append("-" * 78)
    lines.append("  objective (ridge regression)")
    lines.append(f"    model    RMSE {reg.get('rmse')}  MAE {reg.get('mae')}  R2 {reg.get('r2')}")
    lines.append(f"    baseline RMSE {base_reg.get('rmse')}  MAE {base_reg.get('mae')}  R2 {base_reg.get('r2')}")
    lines.append("  pass/fail (calibrated logistic regression)")
    lines.append(f"    model    acc {clf.get('accuracy')}  ROC-AUC {clf.get('roc_auc')}  "
                 f"Brier {clf.get('brier')}  base rate {clf.get('base_rate')}")
    lines.append(f"    baseline acc {base_clf.get('accuracy')}  ROC-AUC {base_clf.get('roc_auc')}  "
                 f"Brier {base_clf.get('brier')}")
    beats = metrics.get("beats_baseline", {})
    lines.append(f"    beats baseline: RMSE {beats.get('rmse')}, ROC-AUC {beats.get('roc_auc')}, "
                 f"Brier {beats.get('brier')}")
    lines.append("-" * 78)
    drivers = report.get("drivers") or []
    if drivers:
        lines.append("  strongest drivers of the objective (standardised coefficients)")
        for row in drivers:
            lines.append(f"    {row['feature']:28s} {row['objective_coefficient']:+.4f}")
    lines.append("=" * 78)
    return "\n".join(lines)
