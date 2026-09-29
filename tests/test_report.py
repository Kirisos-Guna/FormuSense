"""Tests for the report: the chapters, the writers and the .docx package.

The report is a deliverable, so it is tested like one. These tests check the
things that would embarrass the work if they broke: that all eight chapters are
present, that every block is a block, that a table keeps its rows in the right
order, that the Word file is a valid package with parseable XML, and that a long
listing is trimmed visibly rather than silently.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.dom.minidom import parseString

from app import report_sections, report_writers

KEY = {"OPENROUTER_API_KEY": "sk-or-test-abcdefghij"}


def _text(value) -> str:
    """Every string inside a report, whatever shape the block happens to be.

    A report is a nested structure of ``text``, ``rows``, ``lines`` and ``cells``, so
    a check that has to see all of the prose cannot assume one block layout: it walks
    the structure instead.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return ""


def _synthetic_context() -> dict:
    return {
        "student": {"name": "Test Candidate", "register_number": "RA0000000000"},
        "benchmark": {
            "pass_objective": 0.85,
            "summary": {
                "cases": 1,
                "agent_mean_trials_to_target": 3,
                "ofat_mean_trials_to_target": 7,
                "agent_successes": 1,
                "ofat_successes": 0,
                "trials_saved_total": 4,
                "mean_trials_saved": 4,
            },
            "comparison": [
                {
                    "case": "Test case",
                    "agent_trials": 3,
                    "ofat_trials": None,
                    "agent_final_objective": 0.95,
                    "ofat_final_objective": 0.9,
                    "trials_saved": 4,
                }
            ],
            "arms": [
                {"arm": "agent", "case": "Test case", "product_id": 1, "final_truth_objective": 0.95},
                {"arm": "one-factor-at-a-time", "case": "Test case", "final_truth_objective": 0.9},
            ],
            "conflict_demonstration": {
                "conflicts": [
                    {
                        "kpi": "fibre_g",
                        "label": "Dietary fibre",
                        "target": 4.0,
                        "achieved": 10.2,
                        "desirability": 0.09,
                        "recommendation": "No lever moves this KPI.",
                    }
                ]
            },
        },
        "figures": {},
        "listings": [],
        "environment": {"python": "3.14.6 (64-bit)", "sqlite": "3.50.4"},
        "modules": [{"module": "app/core/kb.py", "lines": 340, "purpose": "Knowledge base"}],
        "kb": {"ingredients": 95, "categories": ["Cookie / biscuit"], "groups": {}},
        "schema": {
            "tables": [
                {
                    "name": "products",
                    "columns": [{"name": "id", "type": "INTEGER", "meaning": ""}],
                    "columns_count": 1,
                    "rows": 3,
                    "purpose": "One row per product",
                }
            ],
            "ddl": "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY);",
            "samples": {"products": {"caption": "Table 6.3", "rows": [["id"], ["1"]]}},
        },
        "project_report": "SPECIFICATION\n  protein >= 15 g",
    }


class ChapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = report_sections.build(_synthetic_context())
        self.blocks = self.report["blocks"]

    def test_all_eight_chapters_are_present(self) -> None:
        dividers = [b["text"] for b in self.blocks if b["kind"] == "chapter"]
        self.assertEqual(dividers, [f"CHAPTER {number}" for number in range(1, 9)])

    def test_every_block_is_a_block(self) -> None:
        for block in self.blocks:
            self.assertIsInstance(block, dict, "a block was appended as a list")
            self.assertIn("kind", block)

    def test_front_matter_has_the_placeholders_and_the_index(self) -> None:
        kinds = [b["kind"] for b in self.blocks]
        self.assertIn("index", kinds)
        placeholders = [b["text"] for b in self.blocks if b["kind"] == "placeholder"]
        self.assertTrue(any("completion letter" in text for text in placeholders))
        # The student's own details come from the context, and the markers stay
        # visible when they have not been filled in.
        title_page = " ".join(
            str(b.get("text", "")) for b in self.blocks if b["kind"] == "titlepage"
        )
        self.assertIn("Test Candidate", title_page)
        self.assertIn("RA0000000000", title_page)
        self.assertIn("[Student name]", report_sections.STUDENT["name"])

    def test_missing_figures_are_dropped_not_broken(self) -> None:
        blocks = [b for b in self.blocks if b["kind"] == "image"]
        self.assertEqual(blocks, [], "a figure without a path should not survive build()")

    def test_table_rows_keep_their_order(self) -> None:
        table = next(
            b for b in self.blocks
            if b["kind"] == "table" and b.get("caption", "").startswith("Table 3.2")
        )
        self.assertEqual(table["rows"][0][0], "Case")
        self.assertEqual(table["rows"][1][0], "Test case")
        self.assertEqual(table["rows"][1][1], "3")

    def test_benchmark_numbers_reach_the_prose(self) -> None:
        prose = " ".join(b.get("text", "") for b in self.blocks if b["kind"] == "body")
        self.assertIn("4", prose)  # mean trials saved
        self.assertIn("3", prose)

    def test_section_headings_are_numbered_under_the_chapter(self) -> None:
        headings = [b["text"] for b in self.blocks if b["kind"] == "heading2"]
        self.assertTrue(any(text.startswith("3.") for text in headings))
        self.assertTrue(any(text.startswith("6.") for text in headings))

    def test_excerpt_trims_and_says_so(self) -> None:
        long_text = "\n".join(f"line {index}" for index in range(200))
        trimmed = report_sections._excerpt(long_text, max_lines=10)
        self.assertEqual(len(trimmed.splitlines()), 12)  # 10 kept, blank, note
        self.assertIn("trimmed", trimmed)
        short = "one\ntwo"
        self.assertEqual(report_sections._excerpt(short, max_lines=10), short)


class WriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = report_sections.build(_synthetic_context())
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_markdown_carries_the_headings_and_the_tables(self) -> None:
        path = report_writers.write_markdown(
            self.directory / "report.md", self.report, self.report["blocks"]
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("# CHAPTER 3", text)
        self.assertIn("## 3.3  Searching for a better recipe", text)
        self.assertIn("| Case | Agent trials |", text)
        self.assertIn("Chapter 8. References", text)

    def test_html_is_self_contained(self) -> None:
        path = report_writers.write_html(
            self.directory / "report.html", self.report, self.report["blocks"]
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", text)
        self.assertIn('id="chapter-8"', text)
        self.assertIn("<title>", text)

    def test_docx_is_a_valid_word_package(self) -> None:
        path = report_writers.write_docx(
            self.directory / "report.docx", self.report, self.report["blocks"]
        )
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            for required in (
                "[Content_Types].xml",
                "_rels/.rels",
                "word/document.xml",
                "word/styles.xml",
                "word/settings.xml",
                "word/_rels/document.xml.rels",
            ):
                self.assertIn(required, names)
            for name in names:
                if name.endswith((".xml", ".rels")):
                    parseString(package.read(name))
            document = package.read("word/document.xml").decode("utf-8")
            settings = package.read("word/settings.xml").decode("utf-8")
        # Every chapter divider is a page of its own, and the INDEX is a live field.
        self.assertEqual(document.count("ChapterDivider"), 8)
        self.assertIn("TOC", document)
        self.assertIn("updateFields", settings)

    def test_docx_survives_a_missing_figure(self) -> None:
        blocks = [
            {"kind": "image", "path": None, "caption": "no figure"},
            {"kind": "body", "text": "after the missing figure"},
        ]
        path = report_writers.write_docx(self.directory / "bare.docx", self.report, blocks)
        with zipfile.ZipFile(path) as package:
            document = package.read("word/document.xml").decode("utf-8")
        self.assertIn("after the missing figure", document)


class GenerateTests(unittest.TestCase):
    """End-to-end on the real record, in a temporary directory."""

    def test_generate_writes_markdown_from_the_record(self) -> None:
        from app.store import Store

        store = Store()
        try:
            with tempfile.TemporaryDirectory() as temp:
                result = report_writers.generate(
                    out_dir=Path(temp),
                    store=store,
                    benchmark={},
                    run_tests=False,
                    formats=("md",),
                )
                self.assertIn("md", result["paths"])
                text = result["paths"]["md"].read_text(encoding="utf-8")
                self.assertIn("CHAPTER 1", text)
                self.assertIn("Knowledge base and data files", text)
                self.assertGreater(result["blocks"], 100)
        finally:
            store.close()


class VendorNeutralityTests(unittest.TestCase):
    """The deliverable describes the model layer without naming the platform.

    The platform is named where a reader who wants it looks: the code, the environment
    variables and the deployment configuration. The report is a submitted document, so
    it describes the endpoint by what it is and still prints the models that would
    answer, because those are the part of the claim somebody can check.
    """

    def test_the_environment_row_describes_the_endpoint_not_the_platform(self) -> None:
        with mock.patch.dict(os.environ, dict(KEY), clear=True):
            environment = report_writers._ai_environment()
        self.assertNotIn("openrouter", _text(environment).lower())
        self.assertEqual(environment["ai_provider"], "OpenAI-compatible gateway")
        # The models stay: a table that named no model would be an appendix nobody
        # could check anything against.
        self.assertNotEqual(environment["ai_model"], "-")
        self.assertIn("/", environment["ai_model"])

    def test_no_chapter_names_the_platform(self) -> None:
        context = _synthetic_context()
        with mock.patch.dict(os.environ, dict(KEY), clear=True):
            context["environment"] = {**context["environment"], **report_writers._ai_environment()}
        built = _text(report_sections.build(context)).lower()
        self.assertNotIn("openrouter", built)
        self.assertIn("openai-compatible", built)
        self.assertIn(context["environment"]["ai_model"].lower(), built)

    def test_without_a_key_the_report_says_offline_rather_than_a_vendor(self) -> None:
        for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            with self.subTest(name=name):
                with mock.patch.dict(os.environ, {name: ""}, clear=True):
                    environment = report_writers._ai_environment()
                self.assertEqual(environment["ai_provider"], "not configured")
                self.assertEqual(environment["ai_model"], "-")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
