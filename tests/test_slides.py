"""Tests for the presentation: the deck's content and the .pptx package.

The deck goes in front of a room, so what is tested here is what would embarrass
the work if it broke: that the file is a *valid* OOXML package PowerPoint will
open - every part parsable, every relationship resolving, every slide listed
exactly once, no duplicate shape ids - that the slides quote the record rather
than a hand-typed number, and that a deck still builds when the record is empty
instead of raising.
"""
from __future__ import annotations

import posixpath
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.dom.minidom import parseString
from xml.sax.saxutils import unescape

from app import report_sections, slides, slides_html
from app.core import population

REL_TAG = re.compile(r'<Relationship\b[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"')
OVERRIDE_TAG = re.compile(r'<Override PartName="([^"]+)"')
SLIDE_ID_TAG = re.compile(r'<p:sldId\b[^>]*id="(\d+)"[^>]*r:id="(rId\d+)"')
SHAPE_ID_TAG = re.compile(r'<p:cNvPr id="(\d+)"')
FRAME_TAG = re.compile(r'off x="(-?\d+)" y="(-?\d+)"/><a:ext cx="(\d+)" cy="(\d+)"')
BULLET_TAG = re.compile(r'<a:buChar')
ROUND_TAG = re.compile(r'prst="roundRect"')

EMU_PER_INCH = 914400
SLIDE_W_EMU = int(round(13.333 * EMU_PER_INCH))
SLIDE_H_EMU = 7 * EMU_PER_INCH + EMU_PER_INCH // 2
#: Rounding slop only: the accent bar is exactly the slide's height by design.
EMU_EPSILON = 2000


def _benchmark() -> dict:
    """A stored benchmark, shaped exactly like app.benchmark writes it."""
    return {
        "max_trials": 8,
        "pass_objective": 0.85,
        "arms": [],
        "comparison": [
            {
                "case": "Test snack case",
                "agent_trials": 3,
                "ofat_trials": None,
                "agent_final_objective": 0.951,
                "ofat_final_objective": 0.902,
                "trials_saved": 4,
            },
            {
                "case": "Test beverage case",
                "agent_trials": 2,
                "ofat_trials": None,
                "agent_final_objective": 0.985,
                "ofat_final_objective": 0.786,
                "trials_saved": 5,
            },
        ],
        "summary": {
            "agent_mean_trials_to_target": 2.5,
            "ofat_mean_trials_to_target": 7,
            "agent_successes": 2,
            "ofat_successes": 0,
            "cases": 2,
            "trials_saved_total": 9,
            "mean_trials_saved": 4.5,
        },
    }


def _slide_text(path: Path, number: int) -> str:
    with zipfile.ZipFile(path) as package:
        part = package.read(f"ppt/slides/slide{number}.xml").decode("utf-8")
    text = " ".join(re.findall(r"<a:t>(.*?)</a:t>", part, re.S))
    return unescape(text).replace("&quot;", '"')


def _slide_xml(path: Path, number: int) -> str:
    """The raw markup of one slide, for tests about shapes rather than words."""
    with zipfile.ZipFile(path) as package:
        return package.read(f"ppt/slides/slide{number}.xml").decode("utf-8")


def _all_text(path: Path) -> str:
    with zipfile.ZipFile(path) as package:
        names = sorted(
            name for name in package.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        parts = [package.read(name).decode("utf-8") for name in names]
    text = " ".join(" ".join(re.findall(r"<a:t>(.*?)</a:t>", part, re.S)) for part in parts)
    return unescape(text).replace("&quot;", '"')


def _relationships(path: Path, part: str) -> dict:
    """Id -> resolved target part name, for the .rels part belonging to ``part``.

    Passing an empty string reads the package root relationships. Targets are
    relative to the part's own folder, so they are resolved before comparison.
    """
    folder = posixpath.dirname(part)
    with zipfile.ZipFile(path) as package:
        name = (
            posixpath.join(folder, "_rels", posixpath.basename(part) + ".rels")
            if folder
            else "_rels/.rels"
        )
        xml = package.read(name).decode("utf-8")
    return {
        relation_id: posixpath.normpath(posixpath.join(folder, target))
        for relation_id, target in REL_TAG.findall(xml)
    }


def _sources() -> list:
    """Every part that owns a .rels part, plus the package root."""
    return [
        "",
        "ppt/presentation.xml",
        "ppt/slideMasters/slideMaster1.xml",
        "ppt/slideLayouts/slideLayout1.xml",
    ] + [f"ppt/slides/slide{number}.xml" for number in range(1, 8)]


class PackageTests(unittest.TestCase):
    """The file has to be a package that opens, not just a file that exists."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="formusense-slides-"))
        cls.result = slides.generate(
            out_dir=cls.tmp, benchmark=_benchmark(), run_tests=False
        )
        cls.path = cls.result["path"]

    def test_the_deck_is_written_with_seven_slides(self) -> None:
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.result["slides"], 7)
        self.assertEqual(len(self.result["outline"]), 7)
        self.assertGreater(self.result["bytes"], 5000)

    def test_every_part_is_well_formed_xml(self) -> None:
        with zipfile.ZipFile(self.path) as package:
            names = package.namelist()
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("ppt/presentation.xml", names)
            for name in names:
                if name.endswith((".xml", ".rels")):
                    parseString(package.read(name))

    def test_the_package_holds_exactly_the_parts_it_should(self) -> None:
        expected = {
            "[Content_Types].xml",
            "_rels/.rels",
            "docProps/core.xml",
            "ppt/presentation.xml",
            "ppt/_rels/presentation.xml.rels",
            "ppt/slideMasters/slideMaster1.xml",
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            "ppt/slideLayouts/slideLayout1.xml",
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            "ppt/theme/theme1.xml",
        }
        expected |= {f"ppt/slides/slide{n}.xml" for n in range(1, 8)}
        expected |= {f"ppt/slides/_rels/slide{n}.xml.rels" for n in range(1, 8)}
        with zipfile.ZipFile(self.path) as package:
            self.assertEqual(set(package.namelist()), expected)

    def test_every_part_is_declared_in_the_content_types(self) -> None:
        with zipfile.ZipFile(self.path) as package:
            content_types = package.read("[Content_Types].xml").decode("utf-8")
            declared = set(OVERRIDE_TAG.findall(content_types))
            names = set(package.namelist())
        self.assertIn("/ppt/presentation.xml", declared)
        self.assertIn("/ppt/slideMasters/slideMaster1.xml", declared)
        self.assertIn("/ppt/slideLayouts/slideLayout1.xml", declared)
        self.assertIn("/ppt/theme/theme1.xml", declared)
        for number in range(1, 8):
            self.assertIn(f"/ppt/slides/slide{number}.xml", declared)
        # Nothing is declared that the package does not actually contain.
        for part in declared:
            self.assertIn(part.lstrip("/"), names)

    def test_every_relationship_resolves_to_a_part_in_the_package(self) -> None:
        with zipfile.ZipFile(self.path) as package:
            names = set(package.namelist())
        for source in _sources():
            for relation_id, resolved in _relationships(self.path, source).items():
                self.assertIn(
                    resolved,
                    names,
                    f"{source or '[package]'} relationship {relation_id} points at a missing part",
                )

    def test_the_presentation_lists_each_slide_once(self) -> None:
        with zipfile.ZipFile(self.path) as package:
            presentation = package.read("ppt/presentation.xml").decode("utf-8")
        listed = SLIDE_ID_TAG.findall(presentation)
        self.assertEqual(len(listed), 7)
        self.assertEqual(len({r_id for _, r_id in listed}), 7, "duplicate slide relationship")
        self.assertEqual(len({number for number, _ in listed}), 7, "duplicate slide id")
        rels = _relationships(self.path, "ppt/presentation.xml")
        self.assertEqual(rels["rId1"], "ppt/slideMasters/slideMaster1.xml")
        for _, relation_id in listed:
            self.assertIn(relation_id, rels)
            self.assertIn(rels[relation_id], {f"ppt/slides/slide{n}.xml" for n in range(1, 8)})
        theme = [value for value in rels.values() if value.endswith("theme1.xml")]
        self.assertEqual(theme, ["ppt/theme/theme1.xml"])

    def test_the_master_and_layout_are_wired_to_each_other(self) -> None:
        master = _relationships(self.path, "ppt/slideMasters/slideMaster1.xml")
        self.assertIn("ppt/slideLayouts/slideLayout1.xml", master.values())
        layout = _relationships(self.path, "ppt/slideLayouts/slideLayout1.xml")
        self.assertIn("ppt/slideMasters/slideMaster1.xml", layout.values())
        for number in range(1, 8):
            slide = _relationships(self.path, f"ppt/slides/slide{number}.xml")
            self.assertIn("ppt/slideLayouts/slideLayout1.xml", slide.values())

    def test_every_text_body_holds_at_least_one_paragraph(self) -> None:
        # The schema requires it, including on the decorative shapes.
        with zipfile.ZipFile(self.path) as package:
            for number in range(1, 8):
                part = package.read(f"ppt/slides/slide{number}.xml").decode("utf-8")
                bodies = re.findall(r"<p:txBody>(.*?)</p:txBody>", part, re.S)
                self.assertTrue(bodies)
                for body in bodies:
                    self.assertRegex(body, r"<a:p[ >]")

    def test_shape_ids_are_unique_within_a_slide(self) -> None:
        # PowerPoint refuses to open a slide that reuses a shape id.
        with zipfile.ZipFile(self.path) as package:
            for number in range(1, 8):
                part = package.read(f"ppt/slides/slide{number}.xml").decode("utf-8")
                ids = SHAPE_ID_TAG.findall(part)
                self.assertEqual(len(ids), len(set(ids)), f"slide {number} reuses a shape id")

    def test_no_shape_is_drawn_off_the_slide(self) -> None:
        # Every block measures itself, so a slide that outgrows its canvas is a
        # layout bug - and it is invisible in a file nobody can open here.
        with zipfile.ZipFile(self.path) as package:
            for number in range(1, 8):
                part = package.read(f"ppt/slides/slide{number}.xml").decode("utf-8")
                frames = FRAME_TAG.findall(part)
                self.assertTrue(frames, f"slide {number} draws nothing")
                for x, y, cx, cy in frames:
                    left, top = int(x), int(y)
                    right, bottom = left + int(cx), top + int(cy)
                    self.assertGreaterEqual(left, 0, f"slide {number}: shape off the left edge")
                    self.assertGreaterEqual(top, 0, f"slide {number}: shape off the top")
                    self.assertLessEqual(
                        right, SLIDE_W_EMU + EMU_EPSILON,
                        f"slide {number}: shape runs past the right edge",
                    )
                    self.assertLessEqual(
                        bottom, SLIDE_H_EMU + EMU_EPSILON,
                        f"slide {number}: shape runs past the bottom",
                    )

    def test_the_slides_fill_the_canvas_rather_than_bunching_at_the_top(self) -> None:
        # The lowest shape on every content slide should be close to the footer,
        # which is what the block layout's slack distribution is for.
        with zipfile.ZipFile(self.path) as package:
            for number in range(2, 8):
                part = package.read(f"ppt/slides/slide{number}.xml").decode("utf-8")
                bottoms = [
                    int(y) + int(cy) for _, y, _, cy in FRAME_TAG.findall(part)
                ]
                self.assertGreater(
                    max(bottoms), int(6.0 * EMU_PER_INCH),
                    f"slide {number} leaves a band of empty space",
                )


class ContentTests(unittest.TestCase):
    """What the slides actually say."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="formusense-slides-"))
        cls.path = slides.generate(
            out_dir=cls.tmp, benchmark=_benchmark(), run_tests=False
        )["path"]
        cls.text = _all_text(cls.path)

    def test_the_team_and_every_member_is_named(self) -> None:
        self.assertIn(report_sections.TEAM["name"], self.text)
        for member in report_sections.TEAM["members"]:
            self.assertIn(member, self.text)

    def test_the_footer_numbers_every_slide_and_leaves_no_placeholder(self) -> None:
        self.assertNotIn("{n}", self.text)
        self.assertNotIn("{total}", self.text)
        # The title slide carries no number, as a deck's title slide normally does not.
        self.assertNotIn("Slide", _slide_text(self.path, 1))
        for number in range(2, 8):
            self.assertIn(f"Slide {number} of 7", _slide_text(self.path, number))

    def test_the_benchmark_slide_quotes_the_stored_benchmark(self) -> None:
        text = _slide_text(self.path, 4)
        self.assertIn("Test snack case", text)
        self.assertIn("Test beverage case", text)
        self.assertIn("not reached", text)
        self.assertIn("4.5", text)   # mean trials saved, from the summary
        self.assertIn("2.5", text)   # agent mean trials to target
        self.assertIn("2 of 2", text)

    def test_the_population_slide_quotes_the_reference_set(self) -> None:
        text = _slide_text(self.path, 6)
        self.assertIn(str(len(population.profiles())), text)
        self.assertIn(slides.POPULATION_SHORT_SOURCE, text)

    def test_the_beverage_slide_quotes_the_briefs_own_numbers(self) -> None:
        # The case's specification text says 20 g of protein per bottle; the slide
        # must carry that number, not one typed into this module.
        text = _slide_text(self.path, 6)
        self.assertIn("20 g", text)
        self.assertIn("200 ml", text)

    def test_no_content_slide_is_a_bullet_list(self) -> None:
        # The deck's job is to be read from the back of a room, so the visual
        # blocks carry the content and bullets are the fallback, not the default.
        for number in range(2, 8):
            self.assertEqual(
                BULLET_TAG.findall(_slide_xml(self.path, number)), [],
                f"slide {number} is still a bullet list",
            )

    def test_each_slide_uses_the_visual_blocks_it_is_meant_to(self) -> None:
        expected = {
            1: ("Chip",),            # KPI chips under the team line
            2: ("Badge",),           # the problem, as a numbered flow
            3: ("Badge",),           # the pipeline, as a numbered flow
            4: ("Card", "Track"),    # KPI row plus the trial-efficiency chart
            5: ("Card", "Column"),   # held-out metrics plus the three foundations
            6: ("Card", "Track"),    # product KPIs plus the per-population chart
            7: ("Card", "Column"),   # verification, deliverables and limits
        }
        for number, names in expected.items():
            part = _slide_xml(self.path, number)
            for name in names:
                self.assertIn(f'name="{name}"', part, f"slide {number} has no {name}")


class EmptyRecordTests(unittest.TestCase):
    """A deck still has to build before anything has been run."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="formusense-slides-"))
        cls.path = slides.generate(
            out_dir=cls.tmp, benchmark={}, run_tests=False
        )["path"]
        cls.text = _all_text(cls.path)

    def test_seven_slides_are_still_produced(self) -> None:
        with zipfile.ZipFile(self.path) as package:
            parts = [n for n in package.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        self.assertEqual(len(parts), 7)

    def test_an_empty_benchmark_is_reported_as_missing_rather_than_invented(self) -> None:
        text = _slide_text(self.path, 4)
        self.assertIn("No benchmark is stored", text)
        self.assertIn("python run.py --benchmark", text)

    def test_the_suite_is_not_claimed_when_it_was_not_run(self) -> None:
        self.assertNotIn("tests green", self.text)


class PreviewTests(unittest.TestCase):
    """The browser rendition is drawn from the package, so it checks the artefact."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="formusense-preview-"))
        cls.path = slides.generate(
            out_dir=cls.tmp, benchmark=_benchmark(), run_tests=False
        )["path"]
        cls.rendered = slides_html.write(cls.path)
        cls.html = cls.rendered.read_text(encoding="utf-8")

    def test_a_rendition_is_written_next_to_the_deck(self) -> None:
        self.assertTrue(self.rendered.is_file())
        self.assertEqual(self.rendered.suffix, ".html")
        self.assertEqual(self.rendered.stem, self.path.stem)

    def test_every_slide_is_drawn_once(self) -> None:
        for number in range(1, 8):
            self.assertIn(f'data-slide="{number}"', self.html)
        self.assertEqual(self.html.count('class="slide"'), 7)

    def test_the_shapes_carry_the_coordinates_from_the_file(self) -> None:
        # Slide size 13.333 x 7.5 in at 96 dpi, and the accent bar is 0.2 in wide.
        self.assertIn("width: 1280px", self.html)
        self.assertIn("height: 720px", self.html)
        self.assertIn("width:19.2px", self.html)

    def test_a_slide_is_scaled_to_fit_the_panel_rather_than_cropped(self) -> None:
        self.assertEqual(self.html.count('class="frame"'), 7)
        self.assertIn("transformOrigin", self.html)
        self.assertIn("addEventListener('resize'", self.html)

    def test_the_words_on_the_slides_reach_the_rendition(self) -> None:
        self.assertIn(report_sections.TEAM["name"], self.html)
        self.assertIn("Trial-Efficiency Benchmark", self.html)
        self.assertIn("not reached", self.html)
        self.assertIn("180 days", self.html)

    def test_the_rendition_is_a_whole_document_with_escaped_text(self) -> None:
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", self.html)
        self.assertNotIn("NaN", self.html)
        self.assertNotIn("None", self.html)

    def test_the_rendition_says_it_is_a_rendition(self) -> None:
        self.assertIn("browser rendition of the PowerPoint package", self.html)


class FactTests(unittest.TestCase):
    """The fact gathering, without writing a package."""

    def test_the_benchmark_facts_keep_the_recorded_numbers(self) -> None:
        facts = slides._benchmark_facts(_benchmark())
        self.assertEqual(facts["rows"][0][:4], ["Test snack case", "3 trials", "not reached", "4"])
        self.assertEqual(facts["rows"][0][4], "0.951 vs 0.902")
        self.assertEqual(facts["agent_mean"], 2.5)
        self.assertEqual(facts["cases"], 2)

    def test_the_benchmark_facts_also_keep_the_numbers_a_chart_needs(self) -> None:
        facts = slides._benchmark_facts(_benchmark())
        self.assertEqual(facts["entries"][0]["agent"], 3)
        self.assertIsNone(facts["entries"][0]["ofat"])
        self.assertEqual(facts["entries"][1]["ofat"], None)
        self.assertEqual(facts["max_trials"], 8)

    def test_the_population_chart_is_drawn_from_the_guidance_module(self) -> None:
        block = slides._population_chart(
            slides._population_facts(), {"protein_g_per_serving": 20.0}
        )
        self.assertIsNotNone(block)
        self.assertEqual(block["kind"], "bars")
        self.assertGreaterEqual(block["max"], 100.0)
        self.assertTrue(block["rows"])
        for row in block["rows"]:
            self.assertTrue(row["series"][0]["caption"].endswith("%"))
        # One row per life stage, so fifteen groups do not become fifteen bars.
        self.assertLess(len(block["rows"]), len(population.profiles()))

    def test_the_database_facts_report_the_configured_dialect(self) -> None:
        facts = slides._database_facts(None)
        self.assertIn(facts["dialect"], ("sqlite", "postgresql"))
        self.assertTrue(facts["url"])
        self.assertIn("postgres:16", facts.get("images", []))

    def test_the_ci_facts_are_read_from_the_workflow_file(self) -> None:
        jobs = slides._ci_jobs()
        self.assertEqual(jobs, ["offline", "learning", "postgres"])

    def test_a_built_deck_reports_its_outline(self) -> None:
        result = slides.build(benchmark=_benchmark(), run_tests=False)
        self.assertEqual(result["slides"], 7)
        self.assertEqual(result["outline"][0], "FormuSense")
        self.assertEqual(result["outline"][-1], "Verification, Deliverables and Honest Limits")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
