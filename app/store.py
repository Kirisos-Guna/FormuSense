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
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import settings as _settings, sqlite_path_from_url
from .core.types import Brief, Formulation, Item, TrialResult
from .db import apply_migrations, connect

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "formusense.db"

# The reference DDL is single-sourced from the SQLite migration file, so the
# schema printed in the report and the schema created at runtime cannot drift.
SQLITE_MIGRATIONS = Path(__file__).resolve().parent / "db" / "migrations" / "sqlite"


def _load_schema() -> str:
    """Every SQLite migration, concatenated, for the report's schema appendix.

    Reading the directory rather than one filename is what keeps the published
    schema in step with the schema the application actually creates: adding a
    migration adds it to the appendix, and nobody has to remember to.
    """
    try:
        files = sorted(SQLITE_MIGRATIONS.glob("*.sql"))
        return "\n\n".join(path.read_text(encoding="utf-8").rstrip() for path in files)
    except OSError:  # pragma: no cover - only if data files were stripped
        return ""


SCHEMA = _load_schema()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Store:
    """Thin, explicit data access layer."""

    def __init__(self, path: Optional[Path] = None, url: Optional[str] = None) -> None:
        # Precedence: an explicit url, then an explicit path (the test suite and
        # the report writers both pass one), then the environment, then the
        # bundled SQLite file. The default path needs nothing installed.
        if url is None:
            if path is not None:
                url = f"sqlite:///{Path(path).as_posix()}"
            else:
                url = _settings().db_url
        self.db_url = url
        self.conn = connect(url)
        self.dialect = getattr(self.conn, "dialect", "sqlite")
        resolved = sqlite_path_from_url(url)
        self.path = Path(resolved) if resolved is not None else Path(path or DEFAULT_DB)
        # The HTTP server is threaded and opens one store per request, so two
        # requests can want the write lock at the same moment. The SQLite backend
        # sets write-ahead logging and a busy timeout for exactly that reason.
        self.migrations = apply_migrations(self.conn, self.dialect)

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

    # -------------------------------------------------------- the model layer #
    def save_ai_call(
        self,
        product_id: Optional[int],
        kind: str,
        provider: str,
        model: str,
        prompt_hash: str,
        text: str,
        cached: bool = False,
        ms: int = 0,
        usage: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Record one model reply: the cache entry and the audit line, at once.

        The reply is stored rather than a pointer to it, because the cache has to
        work when the provider does not. A cached description is the only copy of a
        call that was already paid for, and asking again can legitimately return a
        different answer - which would leave the product's own record contradicting
        the report written from it.
        """
        cursor = self.conn.execute(
            "INSERT INTO ai_calls (product_id, kind, provider, model, prompt_hash, text, cached, ms, usage_json, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                product_id,
                kind,
                provider,
                model,
                prompt_hash,
                text,
                1 if cached else 0,
                int(ms),
                json.dumps(usage or {}),
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def ai_call_by_hash(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        """The most recent reply to this exact request, or ``None`` for a miss."""
        row = self.conn.execute(
            "SELECT * FROM ai_calls WHERE prompt_hash=? ORDER BY id DESC LIMIT 1", (prompt_hash,)
        ).fetchone()
        if row is None:
            return None
        try:
            usage = json.loads(row["usage_json"] or "{}")
        except (TypeError, ValueError):
            usage = {}
        return {
            "id": row["id"],
            "provider": row["provider"],
            "model": row["model"],
            "text": row["text"],
            "usage": usage if isinstance(usage, dict) else {},
            "created_at": row["created_at"],
        }

    def attach_ai_calls(self, product_id: int, ids: Sequence[int]) -> int:
        """Point rows recorded before the product existed at the product, now.

        The image description happens before the product row is written, because the
        hints it produces belong in the brief that row stores. Recording the call
        against no product and adopting it a moment later keeps both properties: the
        hints are in the brief, and the call is in the product's history rather than
        orphaned in the table.
        """
        wanted = [int(i) for i in ids if i]
        if not wanted:
            return 0
        placeholders = ",".join("?" for _ in wanted)
        cursor = self.conn.execute(
            "UPDATE ai_calls SET product_id=? WHERE id IN (" + placeholders + ")",
            (product_id, *wanted),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def latest_ai_call(self, product_id: int, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The most recent model reply for a product, optionally of one kind.

        The text is included here, unlike in :meth:`ai_calls`: this is read to show the
        description again, so the reply is what is wanted rather than its metadata.
        """
        if kind:
            row = self.conn.execute(
                "SELECT * FROM ai_calls WHERE product_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                (product_id, kind),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM ai_calls WHERE product_id=? ORDER BY id DESC LIMIT 1", (product_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "provider": row["provider"],
            "model": row["model"],
            "text": row["text"],
            "cached": bool(row["cached"]),
            "created_at": row["created_at"],
        }

    def ai_calls_since(self, since: str) -> int:
        """Paid calls recorded at or after ``since``, an ISO-8601 timestamp.

        Cache hits are excluded. They cost nothing, so counting them would make the
        hourly ceiling punish exactly the traffic the cache exists to absorb.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM ai_calls WHERE created_at >= ? AND cached = 0", (since,)
        ).fetchone()
        return int(row["n"] or 0) if row is not None else 0

    def ai_calls(self, product_id: Optional[int] = None, limit: int = 40) -> List[Dict[str, Any]]:
        """The model's history, newest first, without the reply text.

        The text is left out deliberately: it is the bulk of the row, and this is
        read to answer "which model, how long ago, how slow" rather than to show the
        description again.
        """
        columns = "id, product_id, kind, provider, model, cached, ms, created_at"
        if product_id is None:
            rows = self.conn.execute(
                "SELECT " + columns + " FROM ai_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT " + columns + " FROM ai_calls WHERE product_id=? ORDER BY id DESC LIMIT ?",
                (product_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "product_id": row["product_id"],
                "kind": row["kind"],
                "provider": row["provider"],
                "model": row["model"],
                "cached": bool(row["cached"]),
                "ms": int(row["ms"] or 0),
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
