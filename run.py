#!/usr/bin/env python3
"""Launcher for the AI-Powered Food Product Development Agent (PS-1).

Runs on the Python standard library only::

    python run.py                # start on the default port (8770)
    python run.py --port 9000    # start on a specific port
    python run.py --open         # also open the UI in the default browser
    python run.py --seed         # re-seed the demonstration cases and exit
    python run.py --benchmark    # run the trial-efficiency benchmark and exit
    python run.py --report       # write the internship report and exit
    python run.py --slides       # write the presentation (PPTX) and exit
    python run.py --slides --preview  # ...and an HTML rendition to look at it in a browser
    python run.py --build-dataset  # build the acceptance dataset (offline, no API key)
    python run.py --train          # train and evaluate the acceptance model
    python run.py --train-report   # print the metrics of the latest trained model
    python run.py --db-migrate     # apply pending database migrations
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def seeds_on_start() -> bool:
    """Whether to seed the demonstration cases before serving.

    Off unless ``FORMUSENSE_SEED_ON_START`` is set: a local run must never rewrite
    the record somebody is working in. A deployment sets it, because a fresh
    container starts with an empty database, and the first visitor should not be
    the one who discovers that.
    """
    value = os.environ.get("FORMUSENSE_SEED_ON_START", "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def bootstrap_on_start() -> str:
    """Seed the record if the deployment asked for it, and describe what happened."""
    from app.bootstrap import ensure_seeded

    result = ensure_seeded()
    if result["seeded"]:
        return f"empty, seeded {result['products']} demonstration products"
    return f"{result['products']} demonstration products already on record"


def build_parser() -> argparse.ArgumentParser:
    """The command line.

    ``--host`` and ``--port`` default to the settings rather than to constants, so
    the interface a hosting platform asks for is followed without the container
    having to pass flags (``PORT``, see ``app/config.py``). An explicit flag still
    wins, which keeps every local invocation exactly as it was.
    """
    from app.config import settings

    current = settings()
    parser = argparse.ArgumentParser(description="Food Product Development Agent")
    parser.add_argument("--host", default=current.host)
    parser.add_argument("--port", type=int, default=current.port)
    parser.add_argument("--open", action="store_true", help="open the UI in a browser")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="re-seed the local database with the demo case studies, then exit",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="run the trial-efficiency benchmark against the demo cases, then exit",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="write the internship report (DOCX, HTML, Markdown) from the record, then exit",
    )
    parser.add_argument(
        "--slides",
        action="store_true",
        help="write the presentation (PPTX) from the record, then exit",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="also write an HTML rendition of the presentation, to review it in a browser",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="directory for --report and --slides (default: report/)",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="skip the test-suite run while building the report or the slides",
    )
    parser.add_argument(
        "--build-dataset",
        action="store_true",
        help="build the acceptance-model dataset from the simulated plant, then exit",
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="train and evaluate the acceptance model on the dataset, then exit",
    )
    parser.add_argument(
        "--train-report",
        action="store_true",
        help="print the metrics of the most recently trained model, then exit",
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=60,
        help="formulation variants per category when building the dataset (default: 60)",
    )
    parser.add_argument(
        "--dataset-seed",
        type=int,
        default=7,
        help="seed for dataset generation (default: 7)",
    )
    parser.add_argument(
        "--model-version",
        default="v1",
        help="version tag for a trained model bundle (default: v1)",
    )
    parser.add_argument(
        "--db-migrate",
        action="store_true",
        help="apply pending database migrations and report the schema status, then exit",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.db_migrate:
        from app.db.migrate import migration_status
        from app.store import Store

        store = Store()
        try:
            status = migration_status(store.conn, store.dialect)
            print(f"  database : {store.db_url}")
            print(f"  dialect  : {store.dialect}")
            print(f"  applied  : {', '.join(status['applied']) or 'none'}")
            print(f"  pending  : {', '.join(status['pending']) or 'none'}")
        finally:
            store.close()
        return 0

    if args.build_dataset:
        from app.ml.dataset import build_and_write

        print(f"Building the acceptance dataset ({args.variants} variants per category)...")
        ref = build_and_write(variants_per_category=args.variants, seed=args.dataset_seed)
        print(f"  dataset : {ref.name}/{ref.version}")
        print(f"  rows    : {ref.rows_path}")
        return 0

    if args.train:
        from app.ml.train import format_training_report, train_acceptance

        print("Training the acceptance model (no network, no API key)...")
        report = train_acceptance(version=args.model_version)
        print(format_training_report(report))
        return 0

    if args.train_report:
        from app.ml.registry import latest_bundle
        from app.ml.train import format_training_report

        bundle = latest_bundle()
        if bundle is None:
            print("No trained model found. Run: python run.py --build-dataset && python run.py --train")
            return 1
        print(
            format_training_report(
                {
                    "name": bundle.name,
                    "version": bundle.version,
                    "dataset": bundle.dataset,
                    "metrics": bundle.metrics,
                    "drivers": bundle.driver_notes(),
                }
            )
        )
        return 0

    if args.seed:
        from app.bootstrap import seed_all

        result = seed_all(force=True)
        print(f"Seeded {result['products']} products.")
        for case in result["cases"]:
            print(
                f"  #{case['product_id']:<3d} {case['key']:<20s} "
                f"objective {case['objective']:.3f}  conflicts {case['conflicts']}"
            )
        return 0

    if args.benchmark:
        from app.benchmark import format_benchmark, run_benchmark

        print("Running the benchmark on the seeded cases (this takes a few minutes)...")
        report = run_benchmark()
        print(format_benchmark(report))
        return 0

    if args.report:
        from pathlib import Path as _Path

        from app.report_writers import generate

        print("Building the report from the record...")
        result = generate(
            out_dir=_Path(args.out) if args.out else None,
            run_tests=not args.no_tests,
        )
        print(f"  blocks   : {result['blocks']}")
        print(f"  figures  : {result['figures']}")
        print(f"  listings : {result['listings']}")
        for kind, path in result["paths"].items():
            print(f"  {kind:8s} : {path}")
        return 0

    if args.slides:
        from pathlib import Path as _Path

        from app.slides import generate

        print("Building the presentation from the record...")
        result = generate(
            out_dir=_Path(args.out) if args.out else None,
            run_tests=not args.no_tests,
        )
        print(f"  slides   : {result['slides']}")
        for title in result["outline"]:
            print(f"    - {title}")
        print(f"  pptx     : {result['path']}")
        if args.preview:
            from app.slides_html import write as write_preview

            print(f"  html     : {write_preview(result['path'])}")
        return 0

    from app.server import serve

    # Seeding happens before the port is opened, so a visitor never catches the
    # application halfway through populating itself.
    seeded = bootstrap_on_start() if seeds_on_start() else None

    url = f"http://{args.host}:{args.port}/"
    print("=" * 68)
    print("  AI-Powered Food Product Development Agent  (PS-1 | Tiny Dot Foods)")
    print("=" * 68)
    print(f"  UI      : {url}")
    print("  API     : " + url + "api/health")
    if seeded is not None:
        print(f"  Record  : {seeded}")
    print("  Stop    : Ctrl+C")
    print("=" * 68)
    if args.open:
        webbrowser.open(url)
    serve(args.host, args.port, banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
