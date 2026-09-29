"""Versioned datasets for the acceptance model.

A model is only as honest as its dataset, so the dataset is a first-class,
versioned artefact with a recorded provenance:

* ``datasets/<name>/<version>/rows.jsonl`` - one JSON object per row.
* ``datasets/<name>/<version>/manifest.json`` - how it was produced, with the
  seed, the row count and a SHA-256 of the rows, so a result can be reproduced
  or shown to be a different build.

Two ways in:

* :func:`build_acceptance` generates the seed dataset from the *simulated plant*
  in :mod:`app.plant`: for each category it designs a formulation, jitters it
  and the process, and labels every variant with what that plant would actually
  measure. It is synthetic, and the manifest says so in as many words - that is
  the point of recording provenance rather than implying the numbers came from a
  factory.
* :func:`load_csv` reads an external dataset with the same columns, for when
  real sensory or plant data is available. The schema is documented here and
  validated on load; nothing is merged silently.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .. import plant as plant_module
from ..core import brief as brief_module, kb, kpi as kpi_registry
from ..core.types import Formulation, Item
from .features import FEATURE_VERSION, feature_names, row_features

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATASET_ROOT = DATA_DIR / "datasets"
PASS_OBJECTIVE = 0.85
DEFAULT_NAME = "acceptance"
DEFAULT_VERSION = "v1"

# How many independent plant instances a generated dataset models. Splits are
# grouped by plant, so the model is never asked to memorise one plant's offset
# and then tested on the same offset.
PLANTS_PER_CATEGORY = 4


@dataclass
class DatasetRef:
    name: str
    version: str
    path: Path

    @property
    def rows_path(self) -> Path:
        return self.path / "rows.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version, "path": str(self.path)}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def _sample_plant(rng: random.Random, category: str, index: int) -> plant_module.PlantConfig:
    return plant_module.PlantConfig(
        name=f"sim-{category}-{index}",
        drying_efficiency=rng.uniform(0.86, 1.0),
        temp_offset_c=rng.uniform(-10.0, 0.5),
        acid_retention=rng.uniform(0.70, 1.0),
        sugar_inversion=rng.uniform(1.0, 1.08),
        sodium_carry=rng.uniform(0.96, 1.08),
        oxidation_factor=rng.uniform(1.0, 1.15),
        noise_scale=rng.uniform(1.0, 1.25),
    )


def _jitter(formulation: Formulation, rng: random.Random) -> Formulation:
    """A neighbouring formulation, inside the knowledge-base inclusion limits."""
    table = kb.ingredients()
    items: List[Item] = []
    for item in formulation.items:
        ing = table.get(item.ingredient_id)
        if ing is None:
            continue
        value = item.pct + rng.uniform(-0.25, 0.25) * max(item.pct, 0.5)
        lo = 0.0 if not ing.min_pct else ing.min_pct * 0.5
        value = min(max(value, lo), ing.hard_max_pct)
        items.append(Item(item.ingredient_id, value, item.slot))
    total = sum(i.pct for i in items) or 1.0
    items = [Item(i.ingredient_id, i.pct * 100.0 / total, i.slot) for i in items]

    try:
        category = kb.category(formulation.category)
    except KeyError:
        params = dict(formulation.params)
    else:
        params = {}
        for parameter in category.parameters:
            base = float(formulation.params.get(parameter.id, parameter.default))
            span = (parameter.max - parameter.min) or 1.0
            value = base + rng.uniform(-0.15, 0.15) * span
            params[parameter.id] = min(max(value, parameter.min), parameter.max)
    return Formulation(
        category=formulation.category,
        version=1,
        items=items,
        params=params,
        label=formulation.label,
    )


def build_acceptance(
    variants_per_category: int = 60,
    seed: int = 7,
    categories: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Build the seed dataset from the simulated plant.

    For each category: design one formulation, then generate ``variants_per_category``
    neighbours, run each through a simulated plant and record what the laboratory
    measured. The label is therefore the *measured* outcome, not the model's own
    opinion of the recipe.
    """
    from ..core import formulate

    rng = random.Random(seed)
    rows: List[Dict[str, Any]] = []
    wanted = list(categories) if categories else sorted(kb.categories().keys())
    for category in wanted:
        brief = brief_module.build_brief(
            {
                "product_name": f"{kb.category(category).label} dataset base",
                "category": category,
                "spec_text": "",
            }
        )
        # A deterministic per-category offset. ``hash()`` is randomised per
        # process for strings, which would make two builds with the same seed
        # produce different datasets and quietly break reproducibility.
        category_seed = seed + (sum(ord(ch) for ch in category) % 97)
        base = formulate.generate_optimised(brief, seed=category_seed).formulation
        plant_index = 0
        for index in range(variants_per_category):
            # A new plant every `per_plant` variants, so a group split keeps a
            # plant instance on one side of the train/test boundary.
            per_plant = max(1, variants_per_category // PLANTS_PER_CATEGORY)
            if index % per_plant == 0:
                plant_index += 1
                config = _sample_plant(rng, category, plant_index)
            variant = _jitter(base, rng)
            truth = plant_module.truth_values(variant, brief, config)
            measured = plant_module.measure(truth, config, rng)
            objective = kpi_registry.overall_desirability(
                brief.targets, {k: float(v) for k, v in measured.items()}
            )
            hard_total = 0
            hard_on = 0
            for target in brief.targets:
                if not target.hard or target.id not in measured:
                    continue
                hard_total += 1
                if kpi_registry.is_on_target(target, float(measured[target.id])):
                    hard_on += 1
            passed = hard_total > 0 and hard_on == hard_total and objective >= PASS_OBJECTIVE
            rows.append(
                {
                    "group": config.name,
                    "category": category,
                    "features": row_features(variant),
                    "objective": round(float(objective), 4),
                    "passed": bool(passed),
                    "hard_on": hard_on,
                    "hard_total": hard_total,
                }
            )
    return rows


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _digest(rows: Sequence[Dict[str, Any]]) -> str:
    payload = "\n".join(json.dumps(row, sort_keys=True) for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_dataset(
    rows: Sequence[Dict[str, Any]],
    name: str = DEFAULT_NAME,
    version: str = DEFAULT_VERSION,
    provenance: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> DatasetRef:
    directory = Path(root or DATASET_ROOT) / name / version
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "rows.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    manifest = {
        "name": name,
        "version": version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": len(rows),
        "feature_version": FEATURE_VERSION,
        "feature_names": feature_names(),
        "content_sha256": _digest(rows),
        "provenance": provenance or {"source": "unspecified"},
    }
    with open(directory / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    return DatasetRef(name=name, version=version, path=directory)


def load_rows(ref: DatasetRef) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(ref.rows_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def manifest(ref: DatasetRef) -> Dict[str, Any]:
    if not ref.manifest_path.is_file():
        return {}
    with open(ref.manifest_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve(
    name: str = DEFAULT_NAME,
    version: Optional[str] = None,
    root: Optional[Path] = None,
) -> Optional[DatasetRef]:
    base = Path(root or DATASET_ROOT) / name
    if not base.is_dir():
        return None
    if version:
        candidate = base / version
        return DatasetRef(name, version, candidate) if candidate.is_dir() else None
    versions = sorted([p.name for p in base.iterdir() if p.is_dir()])
    if not versions:
        return None
    chosen = versions[-1]
    return DatasetRef(name, chosen, base / chosen)


def build_and_write(
    variants_per_category: int = 60,
    seed: int = 7,
    name: str = DEFAULT_NAME,
    version: str = DEFAULT_VERSION,
    root: Optional[Path] = None,
) -> DatasetRef:
    rows = build_acceptance(variants_per_category=variants_per_category, seed=seed)
    return write_dataset(
        rows,
        name=name,
        version=version,
        provenance={
            "source": "simulated plant",
            "generator": "app.ml.dataset.build_acceptance",
            "note": (
                "Synthetic dataset: each row is a formulation variant measured on a "
                "simulated pilot plant (app.plant), labelled with the laboratory "
                "outcome. Not factory data; suitable for developing and comparing the "
                "learning pipeline, and replaceable by load_csv with real plant data."
            ),
            "seed": seed,
            "variants_per_category": variants_per_category,
            "pass_objective": PASS_OBJECTIVE,
        },
        root=root,
    )


# --------------------------------------------------------------------------- #
# External data
# --------------------------------------------------------------------------- #
EXTERNAL_COLUMNS_REQUIRED = ("category", "objective", "passed")


def load_csv(path: Path, group_column: str = "group") -> List[Dict[str, Any]]:
    """Load an external dataset with the same schema as a generated one.

    Columns: ``category``, ``objective``, ``passed``, an optional ``group`` (used
    for a leak-free split; defaults to the row's category), and one column per
    feature name from :func:`app.ml.features.feature_names`.
    """
    names = set(feature_names())
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in EXTERNAL_COLUMNS_REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"external dataset is missing required columns: {', '.join(missing)}")
        for record in reader:
            features = {name: float(record.get(name) or 0.0) for name in names}
            category = str(record.get("category") or "")
            rows.append(
                {
                    "group": str(record.get(group_column) or category),
                    "category": category,
                    "features": features,
                    "objective": float(record.get("objective") or 0.0),
                    "passed": str(record.get("passed")).strip().lower() in ("1", "true", "yes", "y"),
                    "hard_on": int(float(record.get("hard_on") or 0)),
                    "hard_total": int(float(record.get("hard_total") or 0)),
                }
            )
    if not rows:
        raise ValueError(f"external dataset {path} contained no rows")
    return rows
