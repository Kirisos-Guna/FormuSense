"""Writing the report: DOCX, HTML and Markdown from one set of blocks.

:mod:`app.report_sections` decides *what the report says*; this module decides
*what it says it in*. The same block list becomes

* a Word document (:class:`app.docx_writer.Docx`) in the college's format,
* a self-contained HTML file with the figures embedded, and
* a Markdown file that can be read or diffed in a terminal.

Nothing in the report is typed in twice. The context that feeds the chapters is
gathered here - the benchmark that was actually run, the record in the database,
the environment the report was built in, the source files themselves - and the
figures and code listings are drawn from that same context at report time. If
the numbers change, the prose, the tables and the pictures all change with them.
"""
from __future__ import annotations

import base64
import html
import io
import os
import platform
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "report"
FIGURES = ROOT / "app" / "data" / "figures"

#: One line per module, used by the module table in chapter 2. The size column
#: is counted from the files, so only the description lives here.
MODULE_PURPOSE: Dict[str, str] = {
    "app/core/kb.py": "Knowledge-base loader: ingredients, categories, process envelopes, limits, allergens",
    "app/core/types.py": "The vocabulary: Brief, KpiTarget, Formulation, Item, TrialResult",
    "app/core/kpi.py": "KPI registry, target construction, desirability kernel, status",
    "app/core/nutrition.py": "Composition by mass balance, energy, claim checking, label table",
    "app/core/physical.py": "Water activity, pH by titration, texture, stability and shelf life, processability",
    "app/core/uncertainty.py": "Per-KPI sigma, interval and confidence label",
    "app/core/cost.py": "Ingredient, process and packaging cost per kilogram and per unit",
    "app/core/engine.py": "Prediction orchestration and evaluation of a prediction against the brief",
    "app/core/brief.py": "Brief understanding: spec parsing, category, diet, allergens, claims, targets",
    "app/core/vision.py": "Image handling: offline measurement with Pillow, optional vision model",
    "app/core/formulate.py": "Slot-based formulation generation, reference-relative intent, constraint checking",
    "app/core/optimize.py": "Objective, levers, coordinate pattern search with paired moves",
    "app/core/surrogate.py": "Residual ridge regression with LOO validation and a bias-only mode",
    "app/core/doe.py": "Design of experiments for the factors that moved",
    "app/core/diagnose.py": "Residual analysis and ranked signature rules for probable causes",
    "app/core/reformulate.py": "Setpoint compensation, reformulation plan, pass probability",
    "app/core/process.py": "Unit operations, parameter table, in-process targets, CCPs, batch sheet",
    "app/plant.py": "The simulated plant: known faults, measurement noise drawn from the published sigma",
    "app/store.py": "SQLite record: products, versions, predictions, trials, diagnoses, plans, ledger, benchmarks",
    "app/service.py": "The agent: create, predict, run trial, analyse, diagnose, plan, accept, close the loop",
    "app/bootstrap.py": "The seeded demonstration cases and their plant faults",
    "app/benchmark.py": "Trial-efficiency benchmark: agent loop against one-factor-at-a-time",
    "app/docx_writer.py": "A .docx writer built on the standard library alone",
    "app/figures.py": "Report figures and syntax-highlighted code listings, drawn with Pillow and Pygments",
    "app/report_sections.py": "The chapters of this report, as data",
    "app/report_writers.py": "Writes the report as DOCX, HTML and Markdown",
    "app/server.py": "HTTP server: JSON API and the single-page interface",
}

#: The listings in chapter 5: (module, function, title, what to say about it).
LISTINGS: List[Tuple[str, str, str, str]] = [
    (
        "app/core/brief.py",
        "parse_spec_numbers",
        "Understanding the written specification",
        "This is where a brief stops being prose. Every number is captured with its "
        "comparator and its unit, and the two failure modes that matter are handled "
        "explicitly: a sentence-ending full stop must not swallow the value (\"0.90.\" is "
        "0.90), and a keyword is only ignored when it is followed by a different unit - "
        "so \"energy 425 kcal per 100 g\" does not lose its energy to the \"per 100 g\" "
        "that describes it.",
    ),
    (
        "app/core/nutrition.py",
        "analyse",
        "Composition and energy by mass balance",
        "The first-principles core. Composition is the sum of the ingredient contributions, "
        "moisture is carried separately because the physical models need it, and energy uses "
        "the Atwater factors. Nothing here is fitted: if a recipe's energy is wrong, the "
        "ingredient table is wrong, and the test suite asserts the closure.",
    ),
    (
        "app/core/physical.py",
        "water_activity",
        "Water activity: the control that decides shelf life",
        "Moisture alone does not predict spoilage; the water that is free to take part in "
        "chemistry does. This function separates free from bound water, applies a solute "
        "load in whichever regime the product sits in, and returns the value that the "
        "stability model, the microbial rules and the claim checks all read. Humectants and "
        "drying therefore affect it differently, which is what makes the reformulation "
        "advice specific.",
    ),
    (
        "app/core/formulate.py",
        "generate_optimised",
        "Generating the first formulation",
        "A seed recipe is generated from the category's slots, then handed to the optimiser "
        "and returned with its constraint report. Keeping generation and optimisation as two "
        "calls is what allows the interface to show what the brief alone produced, and then "
        "what the search made of it - two numbers that are often far apart, and that "
        "difference is itself a diagnosis of the brief.",
    ),
    (
        "app/core/optimize.py",
        "score_result",
        "The objective the search is trying to improve",
        "One function, and the whole design philosophy of the agent in it: priority-weighted "
        "desirability, minus a penalty for drifting away from each slot's intent (weighted "
        "down for precision slots such as acidulants and colours), minus a plausibility term "
        "that stops a recipe being nudged into a nonsense composition, minus constraint "
        "penalties. Nothing is optimised that is not quantified here.",
    ),
    (
        "app/core/uncertainty.py",
        "interval",
        "Publishing an answer with an interval, not a number",
        "Three contributions - the base analytical variation of that measurement, an absolute "
        "floor so a percentage error cannot vanish on a small value, and an extrapolation "
        "factor that widens the interval when the recipe sits outside the calibrated range. "
        "The same sigma is what makes a trial result surprising, and what makes a "
        "prediction falsifiable.",
    ),
    (
        "app/core/diagnose.py",
        "diagnose",
        "Ranking the probable causes of a disappointing trial",
        "Signature rules are evaluated over the residual pattern and the process actuals, "
        "each returning a cause, a category, a confidence and the evidence that triggered it, "
        "and the result is ranked with the recommended action attached. The model-bias "
        "signature is the honest fallback: when the residuals are small, consistently signed "
        "and spread across unrelated KPIs, the answer is \"the model is slightly wrong here\", "
        "not an invented process fault.",
    ),
    (
        "app/core/reformulate.py",
        "plan_reformulation",
        "Correcting the plant before rewriting the recipe",
        "The heart of the loop. Measured process offsets are applied as setpoint corrections "
        "first, so the optimiser is not asked to fix a machine error with a formulation "
        "change; only then are the remaining residuals addressed. The plan carries the dose "
        "deltas, the pass probability per target, the surrogate's weight and the design of "
        "experiments for the next trial.",
    ),
    (
        "app/service.py",
        "closed_loop",
        "Running the loop to a target",
        "Create, predict, run a trial, analyse, diagnose, plan, accept, repeat - with the gate "
        "that stops it: every measured hard target on target and a measured objective of at "
        "least 0.85. The function returns the whole history, which is why the trial counts in "
        "this report can be regenerated by anyone who runs it.",
    ),
    (
        "app/plant.py",
        "run_trial",
        "The simulated plant",
        "The plant applies its true parameters to the recipe - drying efficiency, thermal "
        "offsets, acid retention, sugar inversion, sodium carry-over, oxidation - and then "
        "adds analytical noise drawn from the published sigma of each KPI. Simulating is what "
        "makes a wrong diagnosis a defect that can be fixed rather than a plausible story, "
        "and it is what lets the intervals be tested for calibration.",
    ),
]


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def _version(module: str) -> str:
    try:
        imported = __import__(module)
        return str(getattr(imported, "__version__", "") or "-")
    except Exception:
        return "not installed"


def _git_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        value = (out.stdout or "").strip()
        return value or "not a repository"
    except Exception:
        return "not available"


def _processor() -> str:
    return platform.processor() or platform.machine() or "-"


def _memory_gb() -> str:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return f"{status.ullTotalPhys / (1024 ** 3):.1f} GB"
        return f"{os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (1024 ** 3):.1f} GB"
    except Exception:
        return "-"


def _disk_free() -> str:
    try:
        import shutil

        return f"{shutil.disk_usage(str(ROOT)).free / (1024 ** 3):.1f} GB free"
    except Exception:
        return "-"


def _file_size(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1024:.0f} KB"
    except Exception:
        return "-"


def _run_tests() -> str:
    """Run the project's test suite and return its tail, as report evidence."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900,
        )
        text = (out.stdout or "") + (out.stderr or "")
    except Exception as error:  # pragma: no cover - only on a broken environment
        return f"Test suite could not be run: {error}"
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 34:
        lines = lines[:6] + ["..."] + lines[-26:]
    return "\n".join(lines)


def gather_environment() -> Dict[str, str]:
    from .core import kb

    summary = kb.summarise_kb()
    return {
        "python": f"{platform.python_version()} ({'64-bit' if sys.maxsize > 2 ** 32 else '32-bit'})",
        "platform": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "processor": _processor(),
        "machine": platform.machine(),
        "memory": _memory_gb(),
        "disk": _disk_free(),
        "display": "1920 x 1080",
        "browser": "Microsoft Edge / Google Chrome (Chromium), any recent version",
        "sqlite": sqlite3.sqlite_version,
        "pillow": _version("PIL"),
        "pygments": _version("pygments"),
        "git": _git_version(),
        "ingredients_size": _file_size(ROOT / "app" / "data" / "ingredients.json"),
        "processes_size": _file_size(ROOT / "app" / "data" / "processes.json"),
        "limits_size": _file_size(ROOT / "app" / "data" / "limits.json"),
        "categories_defined": len(summary.get("categories") or []),
    }


def gather_modules() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for relative, purpose in MODULE_PURPOSE.items():
        path = ROOT / relative
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
        rows.append({"module": relative, "lines": lines, "purpose": purpose})
    return rows


def gather_schema(store: Any) -> Dict[str, Any]:
    """Read the database back: tables, columns, row counts, DDL and samples."""
    from . import store as store_module

    meanings = {
        ("products", "brief_json"): "The brief as understood: targets, tolerances, priorities, claims, open questions",
        ("products", "plant_json"): "The plant parameters of the line this product is made on",
        ("formulations", "version"): "Version number, incremented every time the recipe changes",
        ("formulations", "source"): "What produced this version: generated, reformulated, or accepted from a plan",
        ("predictions", "payload_json"): "The published values, the evaluation against every target and the conflicts at that moment",
        ("trials", "formulation_version"): "The version the batch was made from - the link between belief and measurement",
        ("trials", "measurements"): "What the laboratory reported, per KPI",
        ("trials", "process_actuals"): "What the machine actually did, against what the recipe asked for",
        ("analyses", "payload_json"): "Residuals in sigma units, systematic direction score, process deviations, objectives",
        ("diagnoses", "payload_json"): "Ranked causes with their category, confidence, evidence and recommended action",
        ("plans", "payload_json"): "Dose and parameter deltas, expected values with intervals, pass probability, DOE, rationale",
        ("benchmarks", "payload_json"): "A complete trials-to-target comparison, so an efficiency claim is traceable to one run",
        ("ledger", "kind"): "Event type, in order, with the payload that caused it",
    }
    purposes = {
        "products": "One row per product under development",
        "formulations": "Every formulation version, immutable",
        "predictions": "What was published for a version before any trial ran",
        "trials": "Physical trials, with measurements, sensory scores and process actuals",
        "analyses": "Trial analysis: residuals, deviations, objectives",
        "diagnoses": "Ranked probable causes for a trial result",
        "plans": "Reformulation plans and their designs of experiments",
        "benchmarks": "Trial-efficiency benchmark runs",
        "ledger": "Append-only activity log",
        "analyses_kind": "",
    }
    connection = store.connection if hasattr(store, "connection") else None
    tables: List[Dict[str, Any]] = []
    samples: Dict[str, Any] = {}
    created = connection is None
    if created:
        connection = sqlite3.connect(store.path if hasattr(store, "path") else store_module.DEFAULT_DB)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for name in names:
            info = list(connection.execute(f"PRAGMA table_info({name})"))
            count = list(connection.execute(f"SELECT COUNT(*) FROM {name}"))[0][0]
            columns = []
            for column in info:
                column_name = column[1]
                columns.append(
                    {
                        "name": column_name,
                        "type": column[2],
                        "meaning": meanings.get((name, column_name), ""),
                    }
                )
            tables.append(
                {
                    "name": name,
                    "columns": columns,
                    "columns_count": len(info),
                    "rows": count,
                    "purpose": purposes.get(name, ""),
                }
            )
        # Two small sample tables make the chapter concrete without pasting a whole dump.
        for name, caption, columns in (
            (
                "products",
                "Table 6.3  Sample rows from products",
                ["id", "name", "category", "created_at"],
            ),
            (
                "trials",
                "Table 6.4  Sample rows from trials (measurements shown as a count of KPIs measured)",
                ["id", "product_id", "formulation_version", "label", "batch_size_kg", "operator", "measurements"],
            ),
        ):
            try:
                rows = list(connection.execute(f"SELECT {', '.join(columns)} FROM {name} LIMIT 6"))
            except sqlite3.Error:
                continue
            body = []
            for row in rows:
                values = []
                for value in row:
                    text = str(value)
                    if value in (None, ""):
                        values.append("")
                    elif text.strip().startswith("{") or text.strip().startswith("["):
                        try:
                            import json as _json

                            values.append(f"{len(_json.loads(text))} fields")
                        except Exception:
                            values.append("json")
                    else:
                        values.append(text)
                body.append(values)
            samples[name] = {
                "caption": caption,
                "rows": [columns] + body,
            }
    finally:
        if created:
            connection.close()
    size_kb = None
    try:
        path = store.path if hasattr(store, "path") else store_module.DEFAULT_DB
        size_kb = Path(path).stat().st_size / 1024
    except Exception:
        pass
    return {
        "tables": tables,
        "ddl": store_module.SCHEMA.strip(),
        "samples": samples,
        "size_kb": size_kb,
    }


# --------------------------------------------------------------------------- #
# Figures, listings and the rest of the context
# --------------------------------------------------------------------------- #
def _pick_case_arm(benchmark: Dict[str, Any], needle: str = "namkeen") -> Dict[str, Any]:
    for arm in benchmark.get("arms") or []:
        if needle.lower() in str(arm.get("case", "")).lower() and arm.get("arm") == "agent":
            return arm
    arms = benchmark.get("arms") or []
    return arms[0] if arms else {}


def _figure_module() -> Optional[Any]:
    """The figure module, or ``None`` when the optional drawing stack is absent.

    Pillow and Pygments are extras (``requirements-optional.txt``): a report run
    without them still produces every chapter, it simply ships without figures
    rather than failing at import time.
    """
    try:
        from . import figures as figure_module
    except ImportError as exc:  # pragma: no cover - depends on the environment
        print(f"  note: figures disabled, drawing extras not installed ({exc})")
        return None
    return figure_module


def _draw_figures(store: Any, benchmark: Dict[str, Any], directory: Path) -> Dict[str, Path]:
    figure_module = _figure_module()
    if figure_module is None:
        return {}
    from .service import AgentService

    directory.mkdir(parents=True, exist_ok=True)
    service = AgentService(store)
    paths: Dict[str, Path] = {}
    paths["arch"] = figure_module.architecture_figure(directory / "arch.png")
    paths["loop"] = figure_module.loop_figure(directory / "loop.png")
    if benchmark:
        paths["bench"] = figure_module.benchmark_figure(benchmark, directory / "bench.png")
        series = [
            {
                "label": f"{arm['case'].split(' ')[0]} [{arm['arm']}]",
                "values": list(arm.get("objective_history") or []),
            }
            for arm in benchmark.get("arms") or []
            if arm.get("objective_history")
        ]
        if series:
            paths["traj"] = figure_module.trajectories_figure(
                series, directory / "traj.png", float(benchmark.get("pass_objective") or 0.85)
            )
    chosen = _pick_case_arm(benchmark)
    product_id = chosen.get("product_id")
    if product_id:
        from .core import kpi as kpi_registry

        view = service.product_view(int(product_id))
        trials = view.get("trials") or []
        if trials:
            analysis = trials[0].get("analysis") or {}
            rows = []
            for row in (analysis.get("residuals") or [])[:10]:
                rows.append(
                    {
                        **row,
                        "label": kpi_registry.kpi_label(str(row.get("kpi", ""))),
                    }
                )
            if rows:
                paths["res"] = figure_module.residual_figure(rows, directory / "res.png")
        version = view.get("formulation", {}).get("version") if view.get("formulation") else None
        record = (view.get("predictions_by_version") or {}).get(int(version)) if version else None
        evaluation = (record or {}).get("evaluation") or {}
        rows = evaluation.get("rows") or evaluation.get("targets") or []
        if not rows:
            # Older records, written before the evaluation was stored with the
            # prediction, are re-evaluated for the figure rather than skipped.
            try:
                evaluation = service.predict(int(product_id), store_record=False)["evaluation"]
                rows = evaluation.get("rows") or evaluation.get("targets") or []
            except Exception:
                rows = []
        if rows:
            failing = evaluation.get("failing") or []
            failing_ids = {
                (item.get("kpi") if isinstance(item, dict) else item) for item in failing
            }
            paths["des"] = figure_module.desirability_figure(
                [
                    {**row, "status": ("marginal" if row.get("kpi") in failing_ids else row.get("status", ""))}
                    for row in rows
                ],
                directory / "des.png",
            )
        accuracy = ((view.get("efficiency") or {}).get("prediction_accuracy") or {}).get("per_kpi") or []
        if accuracy:
            paths["acc"] = figure_module.accuracy_figure(accuracy, directory / "acc.png")
    conflicts = ((benchmark.get("conflict_demonstration") or {}).get("conflicts") or [])
    if conflicts:
        paths["conf"] = figure_module.conflict_figure(conflicts, directory / "conf.png")
    return paths


def _draw_listings(directory: Path) -> List[Dict[str, Any]]:
    figure_module = _figure_module()
    if figure_module is None:
        return []

    directory.mkdir(parents=True, exist_ok=True)
    listings: List[Dict[str, Any]] = []
    for index, (relative, function, title, note) in enumerate(LISTINGS, start=1):
        source = ROOT / relative
        if not source.is_file():
            continue
        span = figure_module.find_function_lines(source, function)
        if span is None:
            continue
        first, last = span
        limit = 44
        # Start at the code when the docstring is long enough to fill the listing
        # on its own, and always say which lines the reader is looking at.
        body_start = figure_module.function_body_start(source, function) if hasattr(
            figure_module, "function_body_start"
        ) else None
        start = first
        if body_start and body_start > first and (last - body_start + 1) >= 12:
            start = body_start
        end = min(last, start + limit - 1)
        path = directory / f"code_{index}_{function}.png"
        figure_module.code_image(
            source,
            path,
            first_line=start,
            last_line=last,
            title=f"{relative}::{function}()",
            max_lines=limit,
            font_size=9,
        )
        if start > first:
            span_text = f"lines {start}-{end} shown, docstring omitted (the function spans {first}-{last})"
        elif end < last:
            span_text = f"lines {first}-{end} of {first}-{last} shown ({last - first + 1} lines in all)"
        else:
            span_text = f"lines {first}-{last} of the file"
        listings.append(
            {
                "image": path,
                "title": title,
                "lines": (start, end, relative),
                "caption": f"Listing 5.{index}  {relative}, function {function}() ({span_text})",
                "note": note,
            }
        )
    return listings


def build_context(
    store: Any = None,
    benchmark: Optional[Dict[str, Any]] = None,
    figures_dir: Optional[Path] = None,
    run_tests: bool = True,
    student: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Gather everything the chapters need, from the record and the machine."""
    from .store import Store

    store = store or Store()
    if benchmark is None:
        benchmark = store.latest_benchmark() or {}
    figures_dir = Path(figures_dir or FIGURES)
    started = time.time()
    context: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "student": student,
        "benchmark": benchmark,
        "benchmark_output": _benchmark_text(benchmark),
        "environment": gather_environment(),
        "modules": gather_modules(),
        "schema": gather_schema(store),
        "figures": _draw_figures(store, benchmark, figures_dir),
        "listings": _draw_listings(figures_dir),
        "kb": _kb_summary(),
    }
    context["worked_case"] = _pick_case_arm(benchmark).get("case", "")
    context["project_report"] = _project_report(store, benchmark)
    if run_tests:
        context["environment"]["test_output"] = _run_tests()
    context["generated_in_seconds"] = round(time.time() - started, 1)
    return context


def _project_report(store: Any, benchmark: Dict[str, Any]) -> str:
    """The plain-text project report for the case the report works through."""
    from .report import product_report
    from .service import AgentService

    arm = _pick_case_arm(benchmark)
    product_id = arm.get("product_id")
    if not product_id:
        return "(no product in the record: run  python run.py --seed  first)"
    try:
        view = AgentService(store).product_view(int(product_id))
    except Exception as error:
        return f"(the project report could not be built: {error})"
    return product_report(view)


def _kb_summary() -> Dict[str, Any]:
    from .core import kb

    return kb.summarise_kb()


def _benchmark_text(benchmark: Dict[str, Any]) -> str:
    if not benchmark:
        return "No benchmark has been run yet. Run:  python run.py --benchmark"
    from .benchmark import format_benchmark

    return format_benchmark(benchmark)


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _document(meta: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> Any:
    from .docx_writer import Docx

    student = (meta.get("context") or {}).get("student") or {}
    document = Docx(
        title=meta.get("title", ""),
        author=student.get("name", ""),
        subject="Internship report",
    )
    document.extend(blocks)
    return document


def write_docx(path: Path, meta: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> Path:
    return _document(meta, blocks).save(Path(path))


def write_markdown(path: Path, meta: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for block in blocks:
        kind = block.get("kind", "body")
        text = str(block.get("text", ""))
        if kind == "titlepage":
            lines.append(f"**{text}**" if block.get("bold") else text)
        elif kind == "title":
            lines.append(f"# {text}")
        elif kind == "heading1":
            lines.append("")
            lines.append(f"# {text}")
        elif kind == "heading2":
            lines.append("")
            lines.append(f"## {text}")
        elif kind == "heading3":
            lines.append("")
            lines.append(f"### {text}")
        elif kind == "chapter":
            lines.append("")
            lines.append(f"# {text}")
        elif kind == "index":
            lines.append("")
            lines.append(f"## {block.get('title', 'INDEX')}")
            for chapter in meta.get("chapters") or []:
                lines.append(f"- Chapter {chapter['number']}. {chapter['title']}")
        elif kind == "bullet":
            lines.append(f"- {text}")
        elif kind == "caption":
            lines.append(f"*{text}*")
        elif kind == "placeholder":
            lines.append(f"> **To be supplied:** {text}")
        elif kind == "spacer":
            lines.append("")
        elif kind == "pagebreak":
            lines.append("\n---\n")
        elif kind == "image":
            if not block.get("path"):
                continue
            lines.append(f"![{block.get('caption', '')}]({Path(block['path']).name})")
            if block.get("caption"):
                lines.append(f"*{block['caption']}*")
        elif kind == "code":
            if block.get("caption"):
                lines.append(f"**{block['caption']}**")
            lines.append("```")
            lines.append(text)
            lines.append("```")
        elif kind == "table":
            if block.get("caption"):
                lines.append(f"**{block['caption']}**")
            rows = block.get("rows") or []
            if rows:
                lines.append("| " + " | ".join(str(cell) for cell in rows[0]) + " |")
                lines.append("|" + "|".join([" --- "] * len(rows[0])) + "|")
                for row in rows[1:]:
                    lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
            lines.append("")
        else:
            lines.append(text)
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _html_blocks(blocks: Sequence[Dict[str, Any]], meta: Dict[str, Any], embed: bool) -> str:
    parts: List[str] = []
    chapters = meta.get("chapters") or []
    for block in blocks:
        kind = block.get("kind", "body")
        text = str(block.get("text", ""))
        if kind == "titlepage":
            style = "font-weight:700;" if block.get("bold") else ""
            size = int(block.get("size", 28)) / 2
            parts.append(f'<p class="titlepage" style="{style}font-size:{size}pt">{html.escape(text)}</p>')
        elif kind in ("title", "heading1", "chapter"):
            tag = "h1" if kind != "chapter" else "h1"
            css = " class=\"divider\"" if kind == "chapter" else ""
            parts.append(f"<{tag}{css}>{html.escape(text)}</{tag}>")
        elif kind == "heading2":
            parts.append(f"<h2>{html.escape(text)}</h2>")
        elif kind == "heading3":
            parts.append(f"<h3>{html.escape(text)}</h3>")
        elif kind == "index":
            items = "".join(
                f'<li><a href="#chapter-{chapter["number"]}">Chapter {chapter["number"]}. '
                f'{html.escape(chapter["title"])}</a></li>'
                for chapter in chapters
            )
            parts.append(f'<h2>{html.escape(block.get("title", "INDEX"))}</h2><ol class="index">{items}</ol>')
        elif kind == "bullet":
            parts.append(f"<li>{html.escape(text)}</li>")
        elif kind == "caption":
            parts.append(f'<p class="caption">{html.escape(text)}</p>')
        elif kind == "placeholder":
            parts.append(f'<p class="placeholder">To be supplied: {html.escape(text)}</p>')
        elif kind == "spacer":
            parts.append("")
        elif kind == "pagebreak":
            parts.append('<div class="pagebreak"></div>')
        elif kind == "image":
            if not block.get("path"):
                continue
            path = Path(block["path"])
            if not path.is_file():
                continue
            if embed:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                source = f"data:image/png;base64,{data}"
            else:
                source = f"figures/{path.name}"
            parts.append(f'<figure><img src="{source}" alt="{html.escape(block.get("caption", ""))}"/>')
            if block.get("caption"):
                parts.append(f'<figcaption>{html.escape(block["caption"])}</figcaption>')
            parts.append("</figure>")
        elif kind == "code":
            if block.get("caption"):
                parts.append(f'<p class="caption">{html.escape(block["caption"])}</p>')
            parts.append(f"<pre>{html.escape(text)}</pre>")
        elif kind == "table":
            if block.get("caption"):
                parts.append(f'<p class="caption">{html.escape(block["caption"])}</p>')
            rows = block.get("rows") or []
            if not rows:
                continue
            head = "".join(f"<th>{html.escape(str(cell))}</th>" for cell in rows[0])
            body = "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
                for row in rows[1:]
            )
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        else:
            parts.append(f"<p>{html.escape(text)}</p>")
    return "\n".join(parts)


HTML_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 860px; padding: 48px 28px 96px;
       font-family: "Times New Roman", Times, serif; font-size: 12pt; line-height: 1.5;
       color: #14201a; background: #ffffff; }
h1 { font-size: 16pt; text-align: center; margin: 0 0 18px; }
h1.divider { page-break-before: always; margin-top: 200px; font-size: 20pt; letter-spacing: 2px; }
h2 { font-size: 13pt; margin: 26px 0 10px; }
h3 { font-size: 12pt; margin: 20px 0 8px; }
p { text-align: justify; margin: 0 0 12px; }
p.titlepage { text-align: center; margin: 6px 0; }
p.caption { text-align: center; font-style: italic; font-size: 10.5pt; color: #4b5a52; margin: 6px 0 14px; }
p.placeholder { border-left: 3px solid #b26a00; background: #fdf6e9; padding: 8px 12px;
                font-style: italic; text-align: left; }
ul, ol { margin: 0 0 12px 22px; padding: 0; }
li { margin: 0 0 6px; text-align: justify; }
ol.index { columns: 2; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 18px; font-size: 10.5pt; }
th, td { border: 1px solid #b9c4be; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #eef4f0; font-weight: bold; }
pre { font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 9.5pt; line-height: 1.35;
      background: #f6f8f6; border: 1px solid #dde5df; padding: 10px 12px; overflow-x: auto; }
figure { margin: 16px 0 22px; text-align: center; }
figure img { max-width: 100%; border: 0; }
figcaption { font-style: italic; font-size: 10.5pt; color: #4b5a52; margin-top: 6px; text-align: center; }
div.pagebreak { page-break-after: always; height: 0; }
@media print { body { max-width: none; padding: 0; } h1.divider { margin-top: 260px; } }
"""


def write_html(
    path: Path,
    meta: Dict[str, Any],
    blocks: Sequence[Dict[str, Any]],
    embed: bool = True,
    figures_dir: Optional[Path] = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _html_blocks(blocks, meta, embed)
    # The chapter anchors are added after rendering, so the index links resolve.
    for chapter in meta.get("chapters") or []:
        marker = f"<h1 class=\"divider\">CHAPTER {chapter['number']}</h1>"
        body = body.replace(marker, f'<h1 class="divider" id="chapter-{chapter["number"]}">CHAPTER {chapter["number"]}</h1>')
    document = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\"/>\n"
        f"<title>{html.escape(meta.get('title', 'Internship report'))}</title>\n"
        f"<style>{HTML_STYLE}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )
    path.write_text(document, encoding="utf-8")
    if not embed and figures_dir:
        # Not used by default: the HTML build is self-contained on purpose.
        pass
    return path


# --------------------------------------------------------------------------- #
def generate(
    out_dir: Optional[Path] = None,
    store: Any = None,
    benchmark: Optional[Dict[str, Any]] = None,
    run_tests: bool = True,
    student: Optional[Dict[str, str]] = None,
    formats: Sequence[str] = ("docx", "html", "md"),
    figures_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the report in every requested format and return where it was written."""
    from . import report_sections

    out_dir = Path(out_dir or DEFAULT_OUT)
    out_dir.mkdir(parents=True, exist_ok=True)
    context = build_context(
        store=store, benchmark=benchmark, figures_dir=figures_dir,
        run_tests=run_tests, student=student,
    )
    report = report_sections.build(context)
    # A figure whose data was not available is dropped rather than written as a
    # broken image block; the caption would be worse than useless without it.
    report["blocks"] = [
        block
        for block in report["blocks"]
        if not (block.get("kind") == "image" and not block.get("path"))
    ]
    written: Dict[str, Path] = {}
    if "docx" in formats:
        written["docx"] = write_docx(out_dir / "Internship_Report.docx", report, report["blocks"])
    if "html" in formats:
        written["html"] = write_html(out_dir / "Internship_Report.html", report, report["blocks"])
    if "md" in formats:
        written["md"] = write_markdown(out_dir / "Internship_Report.md", report, report["blocks"])
    return {
        "paths": written,
        "out_dir": out_dir,
        "blocks": len(report["blocks"]),
        "figures": len(context["figures"]),
        "listings": len(context["listings"]),
        "context": context,
    }
