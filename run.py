#!/usr/bin/env python3
"""Launcher for the AI-Powered Food Product Development Agent (PS-1).

Runs on the Python standard library only::

    python run.py                # start on the default port (8770)
    python run.py --port 9000    # start on a specific port
    python run.py --open         # also open the UI in the default browser
    python run.py --seed         # re-seed the demonstration cases and exit
    python run.py --benchmark    # run the trial-efficiency benchmark and exit
    python run.py --report       # write the internship report and exit
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 8770


def main() -> int:
    parser = argparse.ArgumentParser(description="Food Product Development Agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
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
        "--out",
        default=None,
        help="directory for --report (default: report/)",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="skip the test-suite run while building the report",
    )
    args = parser.parse_args()

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

    from app.server import serve

    url = f"http://{args.host}:{args.port}/"
    print("=" * 68)
    print("  AI-Powered Food Product Development Agent  (PS-1 | Tiny Dot Foods)")
    print("=" * 68)
    print(f"  UI      : {url}")
    print("  API     : " + url + "api/health")
    print("  Stop    : Ctrl+C")
    print("=" * 68)
    if args.open:
        webbrowser.open(url)
    serve(args.host, args.port, banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
