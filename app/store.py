"""Persistence: products, formulation versions, trials, diagnoses, plans, ledger.

SQLite through the standard library, on purpose: the whole system has to run from
a clean checkout with no installation, and a product development record is small
and relational.

Two parts of this schema are worth more than the rest:

* **The version ledger.** Every formulation version is stored with the source
  that produced it (generated, reformulated, accepted) and every prediction,
  trial, analysis, diagnosis and plan hangs off a product and a version number.
  That makes the project auditable: you can always answer "what did we believe,
  what did we make, what came back, what did we change, and why".
* **The efficiency ledger.** The trial-efficiency KPIs the whole problem
  statement is about - how many physical trials were needed, how many versions
  were consumed, whether the first version passed, how accurate the predictions
  turned out to be - are derived from the same rows rather than kept by hand, so
  they cannot drift from the record.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .core.types import Brief, Formulation, Item, TrialResult

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "formusense.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    plant_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS formulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, version)
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    formulation_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    objective REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    formulation_version INTEGER NOT NULL,
    label TEXT,
    measurements_json TEXT NOT NULL,
    sensory_json TEXT,
    process_json TEXT,
    batch_size_kg REAL,
    operator TEXT,
    trial_date TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    trial_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    trial_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    accepted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Store:
    """Thin, explicit data access layer."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # The HTTP server is threaded and opens one store per request, so two
        # requests can want the write lock at the same moment. Write-ahead
        # logging plus a busy timeout turns that from an exception into a wait.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- products #
    def create_product(
        self,
        brief: Brief,
        plant: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO products (name, category, brief_json, plant_json, created_at) VALUES (?,?,?,?,?)",
            (
                name or brief.product_name,
                brief.category,
                json.dumps(brief.as_dict()),
                json.dumps(plant or {}),
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def product(self, product_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "brief": json.loads(row["brief_json"]),
            "plant": json.loads(row["plant_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def products(self) -> List[Dict[str, Any]]:
        """Every product with the counts the UI and the benchmark need.

        The counts are computed in SQL rather than by loading the versions and
        trials of every product, because the product list is rendered on every
        page load and the records grow with use.
        """
        rows = self.conn.execute(
            """
            SELECT p.id, p.name, p.category, p.created_at,
                   (SELECT COUNT(*) FROM formulations f WHERE f.product_id = p.id) AS versions,
                   (SELECT MAX(f.version) FROM formulations f WHERE f.product_id = p.id) AS latest_version,
                   (SELECT COUNT(*) FROM trials t WHERE t.product_id = p.id) AS trials,
                   (SELECT COUNT(*) FROM plans pl WHERE pl.product_id = p.id) AS plans
            FROM products p ORDER BY p.id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def clear(self, product_id: Optional[int] = None) -> None:
        """Delete a product's records (used by the demo re-seed)."""
        tables = ("plans", "diagnoses", "analyses", "trials", "predictions", "formulations", "ledger", "products")
        for table in tables:
            if product_id is None:
                self.conn.execute(f"DELETE FROM {table}")
            elif table == "predictions":
                self.conn.execute(
                    "DELETE FROM predictions WHERE formulation_id IN "
                    "(SELECT id FROM formulations WHERE product_id=?)",
                    (product_id,),
                )
            elif table == "products":
                self.conn.execute("DELETE FROM products WHERE id=?", (product_id,))
            else:
                self.conn.execute(f"DELETE FROM {table} WHERE product_id=?", (product_id,))
        self.conn.commit()

    # -------------------------------------------------------- formulations #
    def save_formulation(self, product_id: int, formulation: Formulation, source: str) -> int:
        cursor = self.conn.execute(
            "INSERT OR REPLACE INTO formulations "
            "(product_id, version, payload_json, source, label, created_at) VALUES (?,?,?,?,?,?)",
            (
                product_id,
                formulation.version,
                json.dumps(formulation.as_dict()),
                source,
                formulation.label,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def formulation(self, product_id: int, version: Optional[int] = None) -> Optional[Formulation]:
        if version is None:
            row = self.conn.execute(
                "SELECT payload_json FROM formulations WHERE product_id=? ORDER BY version DESC LIMIT 1",
                (product_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT payload_json FROM formulations WHERE product_id=? AND version=?",
                (product_id, version),
            ).fetchone()
        if row is None:
            return None
        return Formulation.from_dict(json.loads(row["payload_json"]))

    def versions(self, product_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT version, source, label, created_at FROM formulations WHERE product_id=? ORDER BY version",
            (product_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_prediction(self, product_id: int, formulation: Formulation, payload: Dict[str, Any]) -> None:
        row = self.conn.execute(
            "SELECT id FROM formulations WHERE product_id=? AND version=?",
            (product_id, formulation.version),
        ).fetchone()
        if row is None:
            return
        self.conn.execute(
            "INSERT INTO predictions (formulation_id, payload_json, objective, created_at) VALUES (?,?,?,?)",
            (row["id"], json.dumps(payload), float(payload.get("objective") or 0.0), _now()),
        )
        self.conn.commit()

    def prediction_for_version(self, product_id: int, version: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT p.payload_json FROM predictions p JOIN formulations f ON f.id = p.formulation_id "
            "WHERE f.product_id=? AND f.version=? ORDER BY p.id DESC LIMIT 1",
            (product_id, version),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    # --------------------------------------------------------------- trials #
    def save_trial(self, product_id: int, trial: TrialResult) -> int:
        cursor = self.conn.execute(
            "INSERT INTO trials (product_id, formulation_version, label, measurements_json, sensory_json, "
            "process_json, batch_size_kg, operator, trial_date, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                product_id,
                trial.formulation_version,
                trial.label,
                json.dumps(trial.measurements),
                json.dumps(trial.sensory),
                json.dumps(trial.process_actuals),
                trial.batch_size_kg,
                trial.operator,
                trial.trial_date,
                trial.notes,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def trial(self, trial_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM trials WHERE id=?", (trial_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "product_id": row["product_id"],
            "formulation_version": row["formulation_version"],
            "label": row["label"],
            "measurements": json.loads(row["measurements_json"]),
            "sensory": json.loads(row["sensory_json"] or "{}"),
            "process_actuals": json.loads(row["process_json"] or "{}"),
            "batch_size_kg": row["batch_size_kg"],
            "operator": row["operator"],
            "trial_date": row["trial_date"],
            "notes": row["notes"],
            "created_at": row["created_at"],
        }

    def trials(self, product_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id FROM trials WHERE product_id=? ORDER BY formulation_version, id", (product_id,)
        ).fetchall()
        out = []
        for row in rows:
            trial = self.trial(row["id"])
            if trial:
                out.append(trial)
        return out

    def trial_samples(self, product_id: int) -> List[Tuple[Formulation, Dict[str, float]]]:
        """(formulation, measured) pairs for surrogate fitting."""
        samples: List[Tuple[Formulation, Dict[str, float]]] = []
        for trial in self.trials(product_id):
            formulation = self.formulation(product_id, trial["formulation_version"])
            if formulation is None:
                continue
            samples.append((formulation, dict(trial["measurements"])))
        return samples

    # ------------------------------------------------- analyses/diagnoses #
    def save_analysis(self, product_id: int, trial_id: int, payload: Dict[str, Any], kind: str = "analysis") -> int:
        table = "analyses" if kind == "analysis" else "diagnoses"
        cursor = self.conn.execute(
            f"INSERT INTO {table} (product_id, trial_id, payload_json, created_at) VALUES (?,?,?,?)",
            (product_id, trial_id, json.dumps(payload), _now()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def analysis_for_trial(self, trial_id: int, kind: str = "analysis") -> Optional[Dict[str, Any]]:
        table = "analyses" if kind == "analysis" else "diagnoses"
        row = self.conn.execute(
            f"SELECT payload_json FROM {table} WHERE trial_id=? ORDER BY id DESC LIMIT 1", (trial_id,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    # ---------------------------------------------------------------- plans #
    def save_plan(self, product_id: int, payload: Dict[str, Any], accepted: bool = False) -> int:
        cursor = self.conn.execute(
            "INSERT INTO plans (product_id, from_version, to_version, payload_json, accepted, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                product_id,
                int(payload.get("from_version", 0)),
                int(payload.get("to_version", 0)),
                json.dumps(payload),
                1 if accepted else 0,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["id"] = row["id"]
        payload["accepted"] = bool(row["accepted"])
        return payload

    def plans(self, product_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, from_version, to_version, accepted, created_at FROM plans WHERE product_id=? ORDER BY id DESC",
            (product_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_plan_accepted(self, plan_id: int) -> None:
        self.conn.execute("UPDATE plans SET accepted=1 WHERE id=?", (plan_id,))
        self.conn.commit()

    # --------------------------------------------------------------- ledger #
    def log(self, product_id: Optional[int], kind: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.conn.execute(
            "INSERT INTO ledger (product_id, kind, message, payload_json, created_at) VALUES (?,?,?,?,?)",
            (product_id, kind, message, json.dumps(payload or {}), _now()),
        )
        self.conn.commit()

    def entries(self, product_id: Optional[int] = None, limit: int = 200) -> List[Dict[str, Any]]:
        if product_id is None:
            rows = self.conn.execute(
                "SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM ledger WHERE product_id=? ORDER BY id DESC LIMIT ?", (product_id, limit)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "product_id": row["product_id"],
                "kind": row["kind"],
                "message": row["message"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------ benchmark #
    def save_benchmark(self, payload: Dict[str, Any]) -> int:
        cursor = self.conn.execute(
            "INSERT INTO benchmarks (payload_json, created_at) VALUES (?,?)", (json.dumps(payload), _now())
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def latest_benchmark(self) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT payload_json FROM benchmarks ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["payload_json"]) if row else None


def _cdf(value: float) -> float:
    import math

    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def prediction_accuracy(trials: Sequence[Dict[str, Any]], predictions: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """How well the agent's published predictions matched what the plant produced.

    Reported per KPI as a mean absolute percentage error over the trials, measured
    against the *prediction that was on record before each trial* - which is the
    only honest way to score a forecast.
    """
    errors: Dict[str, List[float]] = {}
    covered = 0
    total = 0
    for trial in trials:
        record = predictions.get(trial["formulation_version"])
        if not record:
            continue
        by_kpi = {row["id"]: row for row in record.get("predictions", [])}
        for kpi_id, measured in trial["measurements"].items():
            forecast = by_kpi.get(kpi_id)
            if forecast is None:
                continue
            predicted = float(forecast["value"])
            if abs(predicted) < 1e-9:
                continue
            errors.setdefault(kpi_id, []).append(abs(measured - predicted) / abs(predicted))
            total += 1
            if forecast["lo"] <= measured <= forecast["hi"]:
                covered += 1
    summary = []
    for kpi_id, values in sorted(errors.items(), key=lambda item: -len(item[1])):
        mape = 100.0 * sum(values) / len(values)
        summary.append({"kpi": kpi_id, "samples": len(values), "mape_pct": round(mape, 2)})
    overall = 100.0 * sum(sum(v) for v in errors.values()) / max(sum(len(v) for v in errors.values()), 1)
    return {
        "per_kpi": summary,
        "overall_mape_pct": round(overall, 2),
        "interval_coverage_pct": round(100.0 * covered / max(total, 1), 1),
        "samples": total,
    }
