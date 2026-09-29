"""The presentation, as data: seven slides, built from the same record as the report.

The deck is not a second document to keep in step with the code. Every number on
every slide is read at build time - the benchmark rows from the stored benchmark,
the model's metrics from the trained bundle, the database facts from the live
settings and migration bookkeeping, the beverage's figures from its own brief -
which is why the slides cannot drift away from what the application actually
does. What cannot be counted (who is presenting, what the problem statement asks)
is written once, here.

A slide is composed rather than written out: each one is a title plus a stack of
*blocks* - a row of KPI cards, a numbered flow, a bar chart, side-by-side panels,
a callout - and ``app.pptx_writer`` measures and draws them. Bullet lists are the
last resort, not the default, because a room reads a chart and a card row faster
than it reads a page of prose.

Build it with::

    python run.py --slides

and read the result in PowerPoint, Google Slides or WPS. ``app/pptx_writer``
does the packaging; this module only decides what goes on each slide.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import report_sections
from .config import settings
from .pptx_writer import Deck

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "report"
FILENAME = "Technostatic_Wings_FormuSense_Slides.pptx"

FOOTER = "Technostatic Wings  |  FormuSense  |  Problem Statement PS-1, Tiny Dot Foods"

#: The full citation lives in ``app/data/dri_profiles.json``; a slide cannot hold
#: it, so this is the short form of the same standard.
POPULATION_SHORT_SOURCE = "ICMR-NIN 2020 protein RDA and EAR"

PASS_GATE_NOTE = "same cases, same trial budget, same pass gate"


# --------------------------------------------------------------------------- #
# Reading the record
# --------------------------------------------------------------------------- #
def _benchmark_facts(benchmark: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The trial-efficiency comparison, as stored.

    ``rows`` is the table the report uses; ``entries`` is the same data kept as
    numbers so a slide can chart it. Both come from one read of the record.
    """
    benchmark = benchmark or {}
    rows: List[List[str]] = []
    entries: List[Dict[str, Any]] = []
    for row in benchmark.get("comparison") or []:
        agent = row.get("agent_trials")
        ofat = row.get("ofat_trials")
        rows.append(
            [
                str(row.get("case", "")),
                f"{agent} trials" if agent else "not reached",
                f"{ofat} trials" if ofat else "not reached",
                str(row.get("trials_saved", "")),
                "%.3f vs %.3f"
                % (float(row.get("agent_final_objective") or 0.0),
                   float(row.get("ofat_final_objective") or 0.0)),
            ]
        )
        entries.append(
            {
                "case": str(row.get("case", "")),
                "agent": agent,
                "ofat": ofat,
                "saved": row.get("trials_saved"),
                "agent_objective": row.get("agent_final_objective"),
                "ofat_objective": row.get("ofat_final_objective"),
            }
        )
    summary = benchmark.get("summary") or {}
    return {
        "rows": rows,
        "entries": entries,
        "pass_objective": benchmark.get("pass_objective"),
        "agent_mean": summary.get("agent_mean_trials_to_target"),
        "ofat_mean": summary.get("ofat_mean_trials_to_target"),
        "mean_saved": summary.get("mean_trials_saved"),
        "agent_successes": summary.get("agent_successes"),
        "ofat_successes": summary.get("ofat_successes"),
        "cases": summary.get("cases"),
        "max_trials": benchmark.get("max_trials"),
    }


def _model_facts() -> Dict[str, Any]:
    """What the newest trained bundle measured out of sample."""
    try:
        from .ml.registry import latest_bundle
    except ImportError:  # pragma: no cover - the package always ships together
        return {"available": False}
    bundle = latest_bundle()
    if bundle is None:
        return {"available": False}
    metrics = bundle.metrics or {}
    test = metrics.get("test") or {}
    regression = test.get("regression") or {}
    baseline_regression = test.get("baseline_regression") or {}
    classification = test.get("classification") or {}
    baseline_classification = test.get("baseline_classification") or {}
    rows = metrics.get("rows") or {}
    dataset = bundle.dataset or {}
    return {
        "available": True,
        "name": bundle.name,
        "version": bundle.version,
        "created_at": bundle.created_at,
        "dataset_rows": dataset.get("rows"),
        "dataset_version": dataset.get("version"),
        "manifest": dataset.get("sha256") or dataset.get("manifest"),
        "split": metrics.get("split"),
        "train": rows.get("train"),
        "validation": rows.get("validation"),
        "test": rows.get("test"),
        "rmse": regression.get("rmse"),
        "baseline_rmse": baseline_regression.get("rmse"),
        "r2": regression.get("r2"),
        "roc_auc": classification.get("roc_auc"),
        "baseline_roc_auc": baseline_classification.get("roc_auc"),
        "brier": classification.get("brier"),
        "baseline_brier": baseline_classification.get("brier"),
        "beats_baseline": metrics.get("beats_baseline") or {},
    }


def _database_facts(store: Any) -> Dict[str, Any]:
    """Dialect, driver and migration bookkeeping, as the running app sees them."""
    from .db import driver_available, migration_status

    current = settings()
    facts: Dict[str, Any] = {
        "dialect": "postgresql" if current.is_postgres else "sqlite",
        "url": current.redacted_db_url(),
        "applied": [],
        "pending": [],
    }
    try:
        available, detail = driver_available(current.db_url)
        facts["driver"] = detail if available else f"missing ({detail})"
    except Exception:  # pragma: no cover - a driver probe must never break a deck
        facts["driver"] = "unknown"
    if store is not None:
        try:
            status = migration_status(store.conn, store.dialect)
            facts["applied"] = list(status.get("applied") or [])
            facts["pending"] = list(status.get("pending") or [])
        except Exception:  # pragma: no cover - read-only or locked record
            pass
    compose = ROOT / "docker-compose.yml"
    if compose.is_file():
        images = re.findall(r"^\s*image:\s*(\S+)$", compose.read_text(encoding="utf-8"), re.M)
        facts["images"] = images
    return facts


def _beverage_facts(benchmark: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The ready-to-drink case: its own specification, parsed by the agent."""
    from .bootstrap import CASES
    from .core import brief as brief_module

    case = next((entry for entry in CASES if entry.get("key") == "beverage"), None)
    if case is None:  # pragma: no cover - the case ships with the project
        return {}
    numbers = brief_module.parse_spec_numbers(str(case.get("spec_text", "")))
    pack = numbers.get("__pack_size") or {}
    protein_per_100 = (numbers.get("protein_g") or {}).get("value")
    facts: Dict[str, Any] = {
        "name": case.get("name", ""),
        "spec_text": case.get("spec_text", ""),
        "pack_size": pack.get("value"),
        "pack_unit": pack.get("unit") or "ml",
        "protein_g_per_100": protein_per_100,
        "protein_g_per_serving": (numbers.get("protein_g") or {}).get("per_serving"),
        "sugar_g_per_100": (numbers.get("sugar_g") or {}).get("value"),
        "fat_g_per_100": (numbers.get("fat_g") or {}).get("value"),
        "cost_inr_kg": (numbers.get("cost_inr_kg") or {}).get("value"),
        "shelf_life_days": (numbers.get("shelf_life_days") or {}).get("value"),
        "temp_offset_c": (case.get("plant") or {}).get("temp_offset_c"),
    }
    # How the loop actually did on it, from the stored benchmark rather than here.
    name = str(case.get("name", ""))
    for row in (benchmark or {}).get("comparison") or []:
        if str(row.get("case", "")).startswith(name):
            facts["trials_to_target"] = row.get("agent_trials")
            facts["trials_saved"] = row.get("trials_saved")
            break
    return facts


def _population_facts() -> Dict[str, Any]:
    from .core import population

    note = population.source_note()
    return {
        "groups": len(population.profiles()),
        "source": note.get("source", ""),
        "short_source": POPULATION_SHORT_SOURCE,
        "amdr": note.get("protein_energy_pct_range"),
        "upper_g_kg_day": note.get("practical_upper_g_kg_day"),
    }


def _population_chart(
    population_facts: Dict[str, Any], product: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """One bottle, as a share of each population group's daily protein need.

    The figures come from the same module the product page and the report use, so
    the slide cannot disagree with either. One group is drawn per life stage -
    fifteen bars would be a table, not a chart - and the chart is scaled to a
    round ceiling above the tallest bar so the axis stays readable.
    """
    protein = product.get("protein_g_per_serving") or product.get("protein_g_per_100")
    if not protein:
        return None
    from .core import population

    try:
        payload = population.guidance(float(protein), serving_label="one bottle")
    except Exception:  # pragma: no cover - the reference set ships with the project
        return None
    chosen: Dict[str, Dict[str, Any]] = {}
    for row in payload.get("groups") or []:
        stage = str(row.get("stage") or row.get("id") or "")
        best = chosen.get(stage)
        if best is None or float(row.get("pct_rda_per_serving") or 0.0) > float(
            best.get("pct_rda_per_serving") or 0.0
        ):
            chosen[stage] = row
    selected = sorted(
        chosen.values(), key=lambda row: float(row.get("pct_rda_per_serving") or 0.0)
    )
    if not selected:
        return None
    highest = max(float(row.get("pct_rda_per_serving") or 0.0) for row in selected)
    ceiling = max(100.0, math.ceil(highest / 25.0) * 25.0)
    bars = []
    for row in selected:
        share = float(row.get("pct_rda_per_serving") or 0.0)
        label = str(row.get("short_label") or row.get("label") or "")
        if row.get("age_range"):
            label = f"{label} ({row['age_range']})"
        bars.append(
            {
                "label": label,
                "series": [{"value": round(share, 1), "caption": f"{share:.0f}%"}],
            }
        )
    return {
        "kind": "bars",
        "max": ceiling,
        "label_width": 3.7,
        "rows": bars,
        "caption": (
            "One bottle's protein as a share of that group's daily requirement - "
            f"{population_facts['groups']} groups, {population_facts['short_source']}. "
            "Product-development guidance, not medical advice."
        ),
    }


def _test_facts(run_tests: bool) -> Dict[str, Any]:
    """Run the suite once and read its verdict, the same evidence the report uses."""
    if not run_tests:
        return {"ran": False}
    from .report_writers import _run_tests

    text = _run_tests()
    match = re.search(r"Ran (\d+) tests?", text)
    skipped = re.search(r"skipped=(\d+)", text)
    return {
        "ran": True,
        "count": int(match.group(1)) if match else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "passed": "FAILED" not in text,
    }


def _ci_jobs() -> List[str]:
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        return []
    text = workflow.read_text(encoding="utf-8")
    jobs_block = text.split("jobs:", 1)[-1]
    return re.findall(r"^  ([a-z][\w-]*):", jobs_block, re.M)


def _kb_facts() -> Dict[str, Any]:
    from .core import kb

    summary = kb.summarise_kb()
    return {
        "ingredients": summary.get("ingredients", 0),
        "categories": len(summary.get("categories") or []),
        "claims": len(summary.get("claims") or []),
    }


def facts(
    store: Any = None,
    benchmark: Optional[Dict[str, Any]] = None,
    run_tests: bool = True,
) -> Dict[str, Any]:
    """Everything the slides quote, gathered once.

    With no record passed in, the stored one is opened and read - a deck built by
    the CLI has to show the benchmark that was actually run, not fall back to
    "nothing stored" because nobody handed it a connection.
    """
    opened = None
    if store is None and benchmark is None:
        try:
            from .store import Store

            opened = Store()
            store = opened
        except Exception:  # pragma: no cover - a deck must build without a record
            store = None
    try:
        if benchmark is None and store is not None:
            try:
                benchmark = store.latest_benchmark() or {}
            except Exception:  # pragma: no cover - an empty record is not an error
                benchmark = {}
        gathered = _gather(store=store, benchmark=benchmark, run_tests=run_tests)
    finally:
        if opened is not None:
            opened.close()
    return gathered


def _gather(
    store: Any = None,
    benchmark: Optional[Dict[str, Any]] = None,
    run_tests: bool = True,
) -> Dict[str, Any]:
    return {
        "benchmark": _benchmark_facts(benchmark),
        "model": _model_facts(),
        "database": _database_facts(store),
        "beverage": _beverage_facts(benchmark),
        "population": _population_facts(),
        "tests": _test_facts(run_tests),
        "ci": _ci_jobs(),
        "kb": _kb_facts(),
        "team": dict(report_sections.TEAM),
    }


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
def _plain(value: Any, suffix: str = "", digits: int = 4, keep: int = 0) -> str:
    """A number as a slide would print it: no trailing zeros, no lost precision.

    ``keep`` sets the minimum number of decimals, so a ROC-AUC of 1 reads as
    "1.00" rather than "1" while a quantity of 20 g stays "20".
    """
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}{suffix}"
    text = f"{number:.{digits}f}"
    if "." in text:
        text = text.rstrip("0")
        whole, _, fraction = text.partition(".")
        if len(fraction) < keep:
            fraction = fraction.ljust(keep, "0")
        text = f"{whole}.{fraction}" if fraction else whole
    return f"{text}{suffix}"


def _card(value: Any, label: str, note: str = "") -> Dict[str, Any]:
    return {"value": str(value), "label": str(label), "note": str(note)}


# --------------------------------------------------------------------------- #
# The seven slides
# --------------------------------------------------------------------------- #
def _slide_1(f: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    tests = f["tests"]
    kb = f["kb"]
    benchmark = f["benchmark"]
    if tests.get("ran") and tests.get("passed") and tests.get("count"):
        footnote = (
            "Python standard library only  .  no API key, no network call  .  "
            "every number on these slides read from the record at build time"
        )
        chips = [
            (f"{kb['ingredients']}", f"ingredients, {kb['categories']} product categories"),
            ("0", "API keys and network calls"),
            (str(tests["count"]), "automated tests, all green"),
        ]
    else:
        footnote = (
            f"Python standard library only  .  no API key, no network  .  "
            f"{kb['ingredients']} ingredients, {kb['categories']} product categories"
        )
        chips = [
            (f"{kb['ingredients']}", f"ingredients, {kb['categories']} product categories"),
            ("0", "API keys and network calls"),
        ]
    if benchmark.get("entries"):
        chips.append(
            (_plain(benchmark.get("mean_saved")), "trials saved per product")
        )
    return "title", {
        "kicker": "PROBLEM STATEMENT PS-1  |  TINY DOT FOODS",
        "title": "FormuSense",
        "subtitle": "AI-Powered Food Product Development Agent",
        "promise": (
            "An offline agent that takes a food product from a written brief to a "
            "validated formulation"
        ),
        "team": f"TEAM  {f['team'].get('name', '').upper()}",
        "members": list(f["team"].get("members") or []),
        "chips": chips,
        "footnote": footnote,
    }


def _slide_2(_: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    return "slide", {
        "title": "The Problem We Addressed (PS-1)",
        "lead": (
            "A new food product is rarely right the first time, and the gap is "
            "closed today by physical trials."
        ),
        "blocks": [
            {
                "kind": "steps",
                "items": [
                    {
                        "label": "A brief arrives",
                        "text": "Targets and tolerances, claims, diet and allergens - "
                                "written down, but not yet a recipe.",
                    },
                    {
                        "label": "The first recipe misses something",
                        "text": "Texture, pH, water activity, cost or shelf life: "
                                "rarely all of it at once, and never obviously.",
                    },
                    {
                        "label": "The gap is closed by trials",
                        "text": "Each one costs material, machine time, analytical "
                                "work and shelf space.",
                    },
                    {
                        "label": "Weeks pass per product",
                        "text": "The answer arrives only after the budget has been "
                                "spent.",
                    },
                    {
                        "label": "So PS-1 is not \"design a product\"",
                        "text": "It is: stop paying for trials a model could have "
                                "avoided.",
                    },
                ],
            },
            {
                "kind": "callout",
                "text": "What we delivered: a working agent that carries a product "
                        "from a written specification to a validated formulation.",
                "bold": True,
            },
        ],
    }


def _slide_3(f: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    kb = f["kb"]
    return "slide", {
        "title": "How the Agent Works",
        "body_size": 13,
        "blocks": [
            {
                "kind": "steps",
                "items": [
                    {
                        "label": "Understand",
                        "text": f"The specification becomes targets with tolerances "
                                f"and priorities, read against {kb['ingredients']} "
                                "ingredients.",
                    },
                    {
                        "label": "Design",
                        "text": "Ingredients fill the category's functional slots and "
                                "are reconciled to 100 %, acidulants dosed against pH.",
                    },
                    {
                        "label": "Predict",
                        "text": "Mass balance, energy, water activity, pH, texture, "
                                "stability and cost - each with a stated uncertainty.",
                    },
                    {
                        "label": "Optimise",
                        "text": "Coordinate pattern search over the recipe and the "
                                "process together, with an accepted-move log.",
                    },
                    {
                        "label": "Trial and diagnose",
                        "text": "A simulated plant with real line faults; residuals in "
                                "sigma units, a probable cause named with its evidence.",
                    },
                    {
                        "label": "Correct and record",
                        "text": "Measured offsets become setpoint corrections before "
                                "the recipe changes; every version is stored.",
                    },
                ],
            },
        ],
    }


def _slide_4(f: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    benchmark = f["benchmark"]
    if not benchmark["entries"]:
        return "slide", {
            "title": "Evidence: Trial-Efficiency Benchmark",
            "blocks": [
                {
                    "kind": "callout",
                    "text": "No benchmark is stored in this record yet.",
                    "bold": True,
                },
                {
                    "kind": "bullets",
                    "items": [
                        "Run \"python run.py --benchmark\" and rebuild the deck: the "
                        "slides read the stored result rather than quoting one.",
                    ],
                },
            ],
        }
    gate = _plain(benchmark.get("pass_objective"))
    cases = benchmark.get("cases") or len(benchmark["entries"])
    budget = benchmark.get("max_trials")
    chart = []
    for entry in benchmark["entries"]:
        agent = entry.get("agent")
        ofat = entry.get("ofat")
        chart.append(
            {
                "label": entry["case"],
                "series": [
                    {
                        "value": float(agent or 0.0),
                        "caption": f"{agent} trials" if agent else "not reached",
                    },
                    {
                        "value": float(ofat or 0.0),
                        "caption": f"{ofat} trials" if ofat else "not reached",
                    },
                ],
            }
        )
    caption = (
        f"Trials to reach the pass gate ({gate}). {PASS_GATE_NOTE.capitalize()}"
        + (f"; bars scale to the {budget}-trial budget" if budget else "")
        + ". Solid is the agent, grey is one-factor-at-a-time."
    )
    return "slide", {
        "title": "Evidence: Trial-Efficiency Benchmark",
        "blocks": [
            {
                "kind": "cards",
                "items": [
                    _card(_plain(benchmark["agent_mean"]), "trials to target, agent",
                          f"mean over {cases} products"),
                    _card(_plain(benchmark["ofat_mean"]),
                          "trials, one-factor-at-a-time", "same budget, same gate"),
                    _card(_plain(benchmark["mean_saved"]), "trials saved per product",
                          "the cost this work removes"),
                ],
            },
            {
                "kind": "bars",
                "label_width": 3.4,
                "max": budget,
                "rows": chart,
                "caption": caption,
            },
            {
                "kind": "callout",
                "text": "Products reaching target: agent "
                        f"{benchmark['agent_successes']} of {cases}, "
                        f"one-factor-at-a-time {benchmark['ofat_successes']} of "
                        f"{cases} - measured from the stored record, re-runnable with "
                        "\"python run.py --benchmark\".",
                "bold": True,
            },
        ],
    }


def _slide_5(f: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    database = f["database"]
    model = f["model"]
    engine = "SQLite" if database["dialect"] == "sqlite" else "PostgreSQL"
    migration_count = len(database.get("applied") or []) + len(database.get("pending") or [])
    database_lines = [
        f"{engine} is the zero-install default; PostgreSQL is reached by "
        "configuration.",
        "Forward-only numbered SQL migrations per dialect, tracked in a "
        "schema_migrations table.",
        "One record layer behind the dialect adapter, with the driver imported "
        "lazily.",
    ]
    if model.get("available"):
        learning_lines = [
            f"A versioned dataset of {model['dataset_rows']} rows with a seeded, "
            "SHA-256 manifest.",
            "Ridge regression for the objective, calibrated logistic regression for "
            "pass/fail.",
            f"Split by plant instance, so no plant leaks across folds "
            f"({model['train']} / {model['validation']} / {model['test']}).",
        ]
    else:
        learning_lines = [
            "The pipeline trains offline with no API key: \"python run.py "
            "--build-dataset\", then \"--train\".",
            "No trained bundle is stored in this record yet, so the held-out "
            "figures are not quoted.",
            "Nothing in the pipeline reaches the network.",
        ]
    operation_lines = [
        (
            f"Docker: \"docker compose up --build\" runs against "
            f"{', '.join(database['images'])}."
            if database.get("images")
            else "No compose file in this checkout; run the app directly."
        ),
        f"Schema migrations known to the runner: {migration_count or 'n/a'}.",
        "Pillow and Pygments stay optional extras; the standard library is the "
        "requirement.",
    ]
    blocks: List[Dict[str, Any]] = [
        {
            "kind": "cards",
            "items": [],
        },
        {
            "kind": "columns",
            "items": [
                {"title": "Database", "lines": database_lines},
                {"title": "Offline learning", "lines": learning_lines},
                {"title": "Operation", "lines": operation_lines},
            ],
        },
    ]
    if model.get("available"):
        blocks[0]["items"] = [
            _card(_plain(model["rmse"], digits=4, keep=4),
                  "RMSE, held-out test", f"naive baseline {_plain(model['baseline_rmse'])}"),
            _card(_plain(model["roc_auc"], digits=2, keep=2), "ROC-AUC",
                  f"baseline {_plain(model['baseline_roc_auc'], digits=2, keep=2)}"),
            _card(_plain(model["brier"], digits=3, keep=3), "Brier score",
                  f"baseline {_plain(model['baseline_brier'], digits=3, keep=3)}"),
            _card(model["dataset_rows"], "dataset rows",
                  f"split {model['train']} / {model['validation']} / {model['test']}"),
        ]
    else:
        blocks[0]["items"] = [
            _card(engine, "database engine", "configuration selects PostgreSQL"),
            _card(database.get("driver", "unknown"), "driver", "imported lazily"),
            _card("0", "API keys", "nothing in the pipeline calls out"),
        ]
    return "slide", {
        "title": "Production Foundations: Real Database, Offline Learning",
        "title_size": 24,
        "blocks": blocks,
    }


def _slide_6(f: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    product = f["beverage"]
    population = f["population"]
    cards: List[Dict[str, Any]] = []
    if product:
        unit = product.get("pack_size")
        unit_name = product.get("pack_unit") or "ml"
        protein = product.get("protein_g_per_serving") or product.get("protein_g_per_100")
        note = f"{_plain(unit)} {unit_name} bottle, ready to drink" if unit else "per serving"
        cards.append(_card(f"{_plain(protein)} g", "protein per bottle", note))
        cards.append(
            _card(f"{_plain(product.get('sugar_g_per_100'))} g",
                  "sugars per 100 ml, ceiling",
                  f"fat {_plain(product.get('fat_g_per_100'))} g per 100 ml at most")
        )
        cards.append(
            _card(_plain(product.get("shelf_life_days"), " days"), "ambient shelf life",
                  "UHT and aseptic fill")
        )
    cards.append(
        _card(population["groups"], "population groups", population["short_source"])
    )
    blocks: List[Dict[str, Any]] = [{"kind": "cards", "items": cards}]
    chart = _population_chart(population, product)
    if chart:
        blocks.append(chart)
    else:
        blocks.append(
            {
                "kind": "bullets",
                "items": [
                    "Every product - the protein bar included - gets a Populations "
                    "view: protein per serving, the share of the daily requirement, "
                    "how many servings reach it, and the cautions that apply.",
                ],
            }
        )
    if product and product.get("trials_to_target"):
        offset = product.get("temp_offset_c")
        offset_text = (
            f" (the line's heat exchanger ran {abs(offset):g} degC below setpoint)"
            if offset else ""
        )
        blocks.append(
            {
                "kind": "callout",
                "text": "The closed loop diagnosed the plant fault on this product "
                        f"and cleared the gate in {product['trials_to_target']} "
                        f"trials{offset_text}.",
            }
        )
    return "slide", {
        "title": "A New Product, and Consumption Guidance for Every Population",
        "title_size": 24,
        "blocks": blocks,
    }


def _slide_7(f: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    tests = f["tests"]
    ci = f["ci"]
    cards: List[Dict[str, Any]] = []
    if tests.get("ran") and tests.get("count"):
        skipped = tests.get("skipped") or 0
        cards.append(
            _card(
                tests["count"],
                "automated tests green" if tests.get("passed") else "automated tests FAILING",
                (
                    f"{skipped} skipped: the live PostgreSQL round trip"
                    if skipped else "no network, no API key needed to run them"
                ),
            )
        )
    else:
        cards.append(
            _card("not run", "test suite",
                  "python -m unittest discover -s tests -t .")
        )
    cards.append(
        _card(len(ci) or "n/a", "CI jobs on every push",
              ", ".join(ci) if ci else "no workflow found")
    )
    cards.append(
        _card("0", "packages required",
              "Pillow and Pygments are optional extras")
    )
    return "slide", {
        "title": "Verification, Deliverables and Honest Limits",
        "title_size": 26,
        "blocks": [
            {"kind": "cards", "items": cards},
            {
                "kind": "columns",
                "items": [
                    {
                        "title": "Deliverables",
                        "lines": [
                            "The agent with a browser interface and a relational "
                            "record.",
                            "An offline trained model over a versioned dataset.",
                            "A report and this deck, generated from the same record.",
                        ],
                    },
                    {
                        "title": "Honest limits",
                        "lines": [
                            "The plant is simulated by design: a wrong diagnosis is a "
                            "bug, not luck.",
                            "Ingredient data are literature values, not supplier "
                            "certificates.",
                            "Microbiology, full rheology and consumer acceptance are "
                            "out of scope.",
                        ],
                    },
                ],
            },
        ],
    }


def build(
    store: Any = None,
    benchmark: Optional[Dict[str, Any]] = None,
    run_tests: bool = True,
) -> Dict[str, Any]:
    """Assemble the deck and report what it is made of."""
    f = facts(store=store, benchmark=benchmark, run_tests=run_tests)
    deck = Deck(
        title=f"FormuSense, PS-1 presentation - {f['team'].get('name', '')}",
        author=", ".join(f["team"].get("members") or []),
        subject="Internship presentation (PS-1)",
        footer=FOOTER,
    )
    outline: List[str] = []
    for builder in (_slide_1, _slide_2, _slide_3, _slide_4, _slide_5, _slide_6, _slide_7):
        kind, payload = builder(f)
        if kind == "title":
            deck.title_slide(**payload)
        else:
            deck.slide(**payload)
        outline.append(payload["title"])
    return {"deck": deck, "slides": len(deck.slides), "outline": outline, "facts": f}


def generate(
    out_dir: Optional[Path] = None,
    store: Any = None,
    benchmark: Optional[Dict[str, Any]] = None,
    run_tests: bool = True,
    filename: str = FILENAME,
) -> Dict[str, Any]:
    """Build the deck and write the .pptx. Returns where it was written."""
    result = build(store=store, benchmark=benchmark, run_tests=run_tests)
    target = Path(out_dir or DEFAULT_OUT) / filename
    result["deck"].save(target)
    return {
        "path": target,
        "slides": result["slides"],
        "outline": result["outline"],
        "bytes": target.stat().st_size,
    }
