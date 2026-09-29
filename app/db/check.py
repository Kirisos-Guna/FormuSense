"""A check for the database a deployment actually talks to.

``python run.py --db-check`` walks the operations the record layer performs - the
migration, every insert, every read back, the upsert, and the delete - and reports
each one, so a PostgreSQL deployment can be verified before an interface is pointed
at it.

It exists because SQLite and PostgreSQL are close enough to pass a casual look and
different enough to fail on a fresh server, and because a failure inside a hosted
job is one line of traceback in a log nobody outside the repository can open. This
names the operation that broke, which is the difference between a diagnosis and a
guess.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.brief import build_brief
from ..core.types import Formulation, Item, TrialResult
from ..store import Store

CHECK_BRIEF = {"product_name": "record check", "category": "cookie", "spec_text": ""}


def _formulation() -> Formulation:
    return Formulation(
        category="cookie",
        version=1,
        items=[Item("wheat_flour", 60.0), Item("sugar", 40.0)],
        params={"oven_temp_c": 170.0},
        label="record check",
        notes=["written by the database check"],
    )


def _trial() -> TrialResult:
    return TrialResult(
        label="record check",
        formulation_version=1,
        measurements={"moisture_pct": 4.0, "protein_pct": 15.0},
        sensory={"crispness": 7.0},
        process_actuals={"oven_temp_c": 168.0},
        operator="database check",
        notes="written by the database check",
    )


def check(url: Optional[str] = None) -> Dict[str, Any]:
    """Run every record-layer operation once and report how each one went.

    The first failure stops the walk, because everything after it depends on what
    came before: a report that says "the insert into trials failed, after products
    and formulations worked" is the useful shape, not a list of fourteen cascading
    errors.
    """
    state: Dict[str, Any] = {}
    store: Optional[Store] = None

    def open_store() -> str:
        nonlocal store
        store = Store(url=url) if url else Store()
        state["store"] = store
        state["brief"] = build_brief(dict(CHECK_BRIEF))
        applied = ", ".join(store.migrations["applied"]) or "none pending"
        return f"{store.dialect}: {applied}"

    def insert_product() -> str:
        state["product_id"] = store.create_product(state["brief"])  # type: ignore[union-attr]
        product_id = state["product_id"]
        if not isinstance(product_id, int) or product_id <= 0:
            raise AssertionError(f"the id read back after the insert is not a usable id: {product_id!r}")
        return f"product {product_id}"

    def read_product() -> str:
        record = store.product(state["product_id"])  # type: ignore[union-attr]
        if not record:
            raise AssertionError("the product written a moment ago cannot be read back")
        return str(record["name"])

    def upsert_formulation() -> str:
        # Twice on purpose: the second write takes the ON CONFLICT path on
        # PostgreSQL and the INSERT OR REPLACE path on SQLite.
        formulation = _formulation()
        store.save_formulation(state["product_id"], formulation, "generated")  # type: ignore[union-attr]
        store.save_formulation(state["product_id"], formulation, "generated")  # type: ignore[union-attr]
        versions = store.versions(state["product_id"])  # type: ignore[union-attr]
        if len(versions) != 1:
            raise AssertionError(f"the same version was stored {len(versions)} times")
        return f"{len(versions)} version, written twice"

    def insert_prediction() -> str:
        store.save_prediction(  # type: ignore[union-attr]
            state["product_id"], _formulation(), {"objective": 0.9, "predictions": []}
        )
        record = store.prediction_for_version(state["product_id"], 1)  # type: ignore[union-attr]
        if not record:
            raise AssertionError("the prediction cannot be read back")
        return f"objective {record.get('objective')}"

    def insert_trial() -> str:
        state["trial_id"] = store.save_trial(state["product_id"], _trial())  # type: ignore[union-attr]
        if store.trial(state["trial_id"]) is None:  # type: ignore[union-attr]
            raise AssertionError("the trial written a moment ago cannot be read back")
        return f"trial {state['trial_id']}"

    def insert_evidence() -> str:
        store.save_analysis(state["product_id"], state["trial_id"], {"summary": "recorded"})  # type: ignore[union-attr]
        store.save_analysis(  # type: ignore[union-attr]
            state["product_id"], state["trial_id"], {"cause": "oven offset"}, kind="diagnosis"
        )
        analysis = store.analysis_for_trial(state["trial_id"])  # type: ignore[union-attr]
        diagnosis = store.analysis_for_trial(state["trial_id"], kind="diagnosis")  # type: ignore[union-attr]
        if not analysis or not diagnosis:
            raise AssertionError("the analysis or the diagnosis cannot be read back")
        return f"analysis {analysis['summary']}, diagnosis {diagnosis['cause']}"

    def insert_plan() -> str:
        state["plan_id"] = store.save_plan(state["product_id"], {"from_version": 1, "to_version": 2})  # type: ignore[union-attr]
        store.mark_plan_accepted(state["plan_id"])  # type: ignore[union-attr]
        stored = store.plan(state["plan_id"])  # type: ignore[union-attr]
        if not stored or not stored.get("accepted"):
            raise AssertionError("the plan cannot be read back, or did not stay accepted")
        return f"plan {state['plan_id']}, accepted"

    def append_ledger() -> str:
        store.log(state["product_id"], "check", "the database check ran here")  # type: ignore[union-attr]
        entries = store.entries(state["product_id"])  # type: ignore[union-attr]
        if not entries:
            raise AssertionError("the ledger entry cannot be read back")
        return f"{len(entries)} entry"

    def store_benchmark() -> str:
        benchmark_id = store.save_benchmark({"summary": {"agent_successes": 0, "ofat_successes": 0}})  # type: ignore[union-attr]
        if not store.latest_benchmark():  # type: ignore[union-attr]
            raise AssertionError("the benchmark cannot be read back")
        # The benchmark table has no product to hang off, so clear() cannot take this
        # row back and it is removed by hand: a check must not leave a made-up
        # benchmark behind as the latest one, which is the record the interface reads.
        store.conn.execute("DELETE FROM benchmarks WHERE id=?", (benchmark_id,))  # type: ignore[union-attr]
        store.conn.commit()  # type: ignore[union-attr]
        return "one benchmark written, read back, and taken out again"

    def list_products() -> str:
        rows = store.products()  # type: ignore[union-attr]
        if not any(row["id"] == state["product_id"] for row in rows):
            raise AssertionError("the product is missing from the product list")
        return f"{len(rows)} product(s)"

    def delete_records() -> str:
        store.clear(state["product_id"])  # type: ignore[union-attr]
        if store.product(state["product_id"]) is not None:  # type: ignore[union-attr]
            raise AssertionError("the product is still there after clear()")
        return "the product and its records are gone"

    plan: List[Tuple[str, Callable[[], str]]] = [
        ("open the store and apply the migrations", open_store),
        ("insert a product and get its id back", insert_product),
        ("read the product back", read_product),
        ("write the same formulation version twice (the upsert)", upsert_formulation),
        ("insert a prediction and read it back", insert_prediction),
        ("insert a trial and read it back", insert_trial),
        ("insert an analysis and a diagnosis, read both back", insert_evidence),
        ("insert a plan, accept it, read it back", insert_plan),
        ("append a ledger entry and read the ledger", append_ledger),
        ("store a benchmark and read it back", store_benchmark),
        ("list the products", list_products),
        ("delete the product's records", delete_records),
    ]

    steps: List[Dict[str, Any]] = []
    failure: Optional[Dict[str, str]] = None
    for name, action in plan:
        try:
            detail = action()
        except Exception as exc:
            failure = {
                "step": name,
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            steps.append({"step": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
            break
        steps.append({"step": name, "ok": True, "detail": detail})
    if store is not None:
        try:
            store.close()
        except Exception:  # pragma: no cover - a connection that is already gone
            pass
    return {
        "ok": failure is None,
        "dialect": getattr(store, "dialect", "unknown"),
        "url": getattr(store, "db_url", url or ""),
        "steps": steps,
        "failure": failure,
    }
