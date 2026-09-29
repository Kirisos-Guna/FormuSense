"""Train/validation/test protocol and metrics.

The single most important line in this module is the split: rows are split by
**group** (the plant instance a row was measured on), not at random. Splitting
rows at random would let the same plant's systematic offset appear in both the
training and the test set, and a model that had memorised that offset would score
beautifully while learning nothing that transfers. A grouped split asks the
honest question: does this generalise to a plant the model has never seen?

Metrics are reported out of sample. Training-set numbers are never reported as
results.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence, Tuple

MIN_GROUPS_FOR_GROUPED_SPLIT = 5


def group_split(
    rows: Sequence[Dict[str, Any]],
    val_fraction: float = 0.2,
    test_fraction: float = 0.2,
    seed: int = 13,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split rows into (train, validation, test) by group where possible."""
    groups = sorted({str(row.get("group")) for row in rows})
    rng = random.Random(seed)
    if len(groups) < MIN_GROUPS_FOR_GROUPED_SPLIT:
        # Too few groups to hold one out: fall back to a row split, and say which
        # one was used so the report can state it rather than imply the stronger
        # protocol.
        shuffled = list(rows)
        rng.shuffle(shuffled)
        n_test = max(1, int(round(len(shuffled) * test_fraction)))
        n_val = max(1, int(round(len(shuffled) * val_fraction)))
        test = shuffled[:n_test]
        val = shuffled[n_test : n_test + n_val]
        train = shuffled[n_test + n_val :]
        return train, val, test

    rng.shuffle(groups)
    n_test = max(1, int(round(len(groups) * test_fraction)))
    n_val = max(1, int(round(len(groups) * val_fraction)))
    test_groups = set(groups[:n_test])
    val_groups = set(groups[n_test : n_test + n_val])
    train = [r for r in rows if str(r.get("group")) not in test_groups | val_groups]
    val = [r for r in rows if str(r.get("group")) in val_groups]
    test = [r for r in rows if str(r.get("group")) in test_groups]
    if not train or not val or not test:
        # Degenerate group layout; fall back rather than return an empty fold.
        return group_split(rows, val_fraction, test_fraction, seed + 1)
    return train, val, test


def split_kind(rows: Sequence[Dict[str, Any]]) -> str:
    groups = {str(row.get("group")) for row in rows}
    return "group" if len(groups) >= MIN_GROUPS_FOR_GROUPED_SPLIT else "row"


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def regression_metrics(targets: Sequence[float], predictions: Sequence[float]) -> Dict[str, float]:
    n = len(targets)
    if n == 0:
        return {"samples": 0, "rmse": 0.0, "mae": 0.0, "r2": 0.0}
    errors = [p - t for t, p in zip(targets, predictions)]
    mse = sum(e * e for e in errors) / n
    mae = sum(abs(e) for e in errors) / n
    mean_target = sum(targets) / n
    ss_tot = sum((t - mean_target) ** 2 for t in targets)
    ss_res = sum(e * e for e in errors)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return {
        "samples": n,
        "rmse": round(mse ** 0.5, 4),
        "mae": round(mae, 4),
        "r2": round(r2, 4),
    }


def _roc_auc(labels: Sequence[float], scores: Sequence[float]) -> float:
    """Rank-based ROC-AUC (Mann-Whitney U), tie-aware."""
    positives = [s for y, s in zip(labels, scores) if y >= 0.5]
    negatives = [s for y, s in zip(labels, scores) if y < 0.5]
    if not positives or not negatives:
        return 0.0
    ordered = sorted(zip(scores, labels), key=lambda pair: pair[0])
    ranks: Dict[int, float] = {}
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[index][0]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[position] = average_rank
        index = end + 1
    positive_rank_sum = sum(ranks[i] for i, (_, y) in enumerate(ordered) if y >= 0.5)
    n_pos = len(positives)
    n_neg = len(negatives)
    return (positive_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def classification_metrics(
    labels: Sequence[float], probabilities: Sequence[float], threshold: float = 0.5
) -> Dict[str, Any]:
    n = len(labels)
    if n == 0:
        return {"samples": 0, "accuracy": 0.0, "roc_auc": 0.0, "brier": 0.0, "base_rate": 0.0}
    tp = fp = tn = fn = 0
    brier = 0.0
    for y, p in zip(labels, probabilities):
        predicted = 1.0 if p >= threshold else 0.0
        actual = 1.0 if y >= 0.5 else 0.0
        brier += (p - actual) ** 2
        if predicted >= 0.5 and actual >= 0.5:
            tp += 1
        elif predicted >= 0.5 and actual < 0.5:
            fp += 1
        elif predicted < 0.5 and actual < 0.5:
            tn += 1
        else:
            fn += 1
    accuracy = (tp + tn) / n
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "samples": n,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "roc_auc": round(_roc_auc(labels, probabilities), 4),
        "brier": round(brier / n, 4),
        "base_rate": round(sum(1 for y in labels if y >= 0.5) / n, 4),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "threshold": threshold,
    }


def calibration_table(
    labels: Sequence[float], probabilities: Sequence[float], bins: int = 5
) -> List[Dict[str, Any]]:
    """Observed pass rate per predicted-probability bin."""
    if not labels:
        return []
    edges = [i / bins for i in range(bins + 1)]
    rows: List[Dict[str, Any]] = []
    for index in range(bins):
        lo, hi = edges[index], edges[index + 1]
        selected = [
            (y, p) for y, p in zip(labels, probabilities)
            if (lo <= p < hi) or (index == bins - 1 and p >= hi)
        ]
        if not selected:
            continue
        predicted = sum(p for _, p in selected) / len(selected)
        observed = sum(1 for y, _ in selected if y >= 0.5) / len(selected)
        rows.append(
            {
                "bin": f"{lo:.2f}-{hi:.2f}",
                "n": len(selected),
                "mean_predicted": round(predicted, 3),
                "observed_rate": round(observed, 3),
            }
        )
    return rows
