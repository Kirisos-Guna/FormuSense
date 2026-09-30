"""Tests for the ICMR-NIN 2020 reference export.

The export is not allowed to be a second copy of the reference values: it is built
from :mod:`app.core.population`, and these tests compare every exported row with
what that module returns. If a value in ``app/data/dri_profiles.json`` changes, the
spreadsheet has to change with it, or the suite says so.

Nothing here needs Excel, ``openpyxl`` or a network: a workbook is a ZIP of XML
parts, so the package is opened as one and read back with ``ElementTree``.
"""
from __future__ import annotations

import csv
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from app import icmr_export
from app.core import population
from app.xlsx_writer import Sheet, column_letter, write

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

REQUIRED_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/worksheets/sheet1.xml",
    "xl/worksheets/sheet2.xml",
    "xl/styles.xml",
    "docProps/core.xml",
    "docProps/app.xml",
)


def sheet_cells(path: Path, index: int = 1) -> Dict[str, Any]:
    """One written sheet as {cell reference: value}, references included.

    Reading by reference rather than by position is deliberate: a blank cell is
    left out of the document, and only the reference says which column the cells
    after it belong to.
    """
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read(f"xl/worksheets/sheet{index}.xml"))
    cells: Dict[str, Any] = {}
    for cell in root.iter(f"{NS}c"):
        reference = cell.get("r", "")
        kind = cell.get("t")
        if kind == "inlineStr":
            text = cell.find(f"{NS}is/{NS}t")
            cells[reference] = "" if text is None else (text.text or "")
        elif kind == "b":
            cells[reference] = cell.find(f"{NS}v").text == "1"
        else:
            number = float(cell.find(f"{NS}v").text)
            cells[reference] = int(number) if number.is_integer() else number
    return cells


def sheet_rows(cells: Dict[str, Any]) -> List[List[Any]]:
    """The same sheet as ordered rows, gaps in a row filled with ``None``."""
    numbered: Dict[int, Dict[str, Any]] = {}
    for reference, value in cells.items():
        column = "".join(character for character in reference if character.isalpha())
        row = int(reference[len(column) :])
        index = 0
        for character in column:
            index = index * 26 + (ord(character) - 64)
        numbered.setdefault(row, {})[index] = value
    width = max((max(values) for values in numbered.values()), default=0)
    return [
        [numbered[row].get(column) for column in range(1, width + 1)]
        for row in sorted(numbered)
    ]


class WorkbookPackageTests(unittest.TestCase):
    """The writer itself: what Excel needs, and what it refuses to open."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_package_carries_every_part_excel_needs(self) -> None:
        path = write(
            self.dir / "book.xlsx",
            [Sheet("One", ("A", "B"), [[1, "text"]]), Sheet("Two", ("C",), [])],
        )
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            self.assertTrue(set(REQUIRED_PARTS).issubset(set(names)), names)
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    ET.fromstring(package.read(name))

    def test_numbers_are_numbers_and_text_is_text(self) -> None:
        path = write(
            self.dir / "book.xlsx",
            [Sheet("Numbers", ("whole", "decimal", "text", "flag", "absent"),
                    [[54, 12.9, "54 g", True, None]])],
        )
        cells = sheet_cells(path)
        self.assertIsInstance(cells["A2"], int)
        self.assertEqual(cells["A2"], 54)
        self.assertIsInstance(cells["B2"], float)
        self.assertAlmostEqual(cells["B2"], 12.9, places=6)
        self.assertIsInstance(cells["C2"], str)
        self.assertEqual(cells["C2"], "54 g")
        self.assertIs(cells["D2"], True)
        # A cell with no value is not in the document at all.
        self.assertNotIn("E2", cells)

    def test_escaping_keeps_the_markup_out_of_the_document(self) -> None:
        path = write(
            self.dir / "book.xlsx",
            [Sheet("Text", ("value",), [["R&D <lab> \"quoted\"\x07"]]), ],
        )
        cells = sheet_cells(path)
        self.assertEqual(cells["A2"], 'R&D <lab> "quoted" ')

    def test_a_whole_float_loses_its_pointless_decimals(self) -> None:
        path = write(self.dir / "book.xlsx", [Sheet("N", ("v", "w"), [[65.0, 0.83]])])
        with zipfile.ZipFile(path) as package:
            xml = package.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("<v>65</v>", xml)
        self.assertIn("<v>0.83</v>", xml)

    def test_a_sheet_name_excel_would_refuse_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Sheet("x" * 32, ("A",))
        with self.assertRaises(ValueError):
            Sheet("Reference/summary", ("A",))
        with self.assertRaises(ValueError):
            Sheet("", ("A",))
        with self.assertRaises(ValueError):
            write(self.dir / "book.xlsx", [Sheet("Same", ("A",)), Sheet("same", ("A",))])

    def test_a_row_that_does_not_match_the_header_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Sheet("Rows", ("A", "B"), [[1]])

    def test_column_letters_run_past_z(self) -> None:
        self.assertEqual(column_letter(1), "A")
        self.assertEqual(column_letter(15), "O")
        self.assertEqual(column_letter(26), "Z")
        self.assertEqual(column_letter(27), "AA")
        self.assertEqual(column_letter(28), "AB")


class ReferenceExportTests(unittest.TestCase):
    """The delivered workbook and CSV, against the module they came from."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.result = icmr_export.generate(Path(cls._tmp.name))
        cls.xlsx: Path = cls.result["paths"]["xlsx"]
        cls.csv: Path = cls.result["paths"]["csv"]
        cls.reference = sheet_rows(sheet_cells(cls.xlsx, 1))
        cls.notes = sheet_rows(sheet_cells(cls.xlsx, 2))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def row_for(self, group_id: str) -> List[Any]:
        index = list(icmr_export.HEADER).index("Group ID")
        for row in self.reference[1:]:
            if row[index] == group_id:
                return row
        raise AssertionError(f"{group_id} is not in the exported sheet")

    def column(self, name: str) -> int:
        return list(icmr_export.HEADER).index(name)

    def test_both_files_are_written_and_named_after_the_standard(self) -> None:
        self.assertTrue(self.xlsx.is_file())
        self.assertTrue(self.csv.is_file())
        self.assertEqual(self.xlsx.name, "FormuSense_ICMR_NIN_2020_protein_reference.xlsx")
        self.assertEqual(self.csv.name, "FormuSense_ICMR_NIN_2020_protein_reference.csv")

    def test_the_workbook_has_the_two_documented_sheets(self) -> None:
        self.assertEqual(
            self.result["sheets"], ["Population reference", "Source & notes"]
        )
        with zipfile.ZipFile(self.xlsx) as package:
            workbook = ET.fromstring(package.read("xl/workbook.xml"))
        names = [sheet.get("name") for sheet in workbook.iter(f"{NS}sheet")]
        self.assertEqual(names, ["Population reference", "Source & notes"])

    def test_the_header_row_is_the_documented_columns(self) -> None:
        self.assertEqual(self.reference[0], list(icmr_export.HEADER))
        self.assertEqual(self.reference[0][self.column("Cautions")], "Cautions")

    def test_every_group_in_the_module_is_exported_once_and_in_order(self) -> None:
        ids = [entry.id for entry in population.profiles()]
        exported = [row[self.column("Group ID")] for row in self.reference[1:]]
        self.assertEqual(exported, ids)
        self.assertEqual(len(exported), len(set(exported)))

    def test_every_exported_value_equals_the_module(self) -> None:
        for entry in population.profiles():
            group = entry.as_dict()
            row = self.row_for(entry.id)
            self.assertEqual(row[self.column("Population group")], group["label"])
            self.assertEqual(row[self.column("Age range")], group["age_range"])
            self.assertEqual(row[self.column("Reference body weight (kg)")], group["ref_weight_kg"])
            self.assertEqual(row[self.column("EAR (g protein/day)")], group["ear_g_day"])
            self.assertEqual(row[self.column("RDA (g protein/day)")], group["rda_g_day"])
            self.assertEqual(row[self.column("RDA (g/kg/day)")], group["rda_g_kg_day"])
            self.assertEqual(
                row[self.column("Derived")], "yes" if group["derived"] else "no"
            )
            self.assertEqual(row[self.column("Cautions")], "; ".join(group["cautions"]))

    def test_the_adult_values_are_the_icmr_2020_ones(self) -> None:
        man = self.row_for("adult_man")
        woman = self.row_for("adult_woman")
        self.assertAlmostEqual(man[self.column("RDA (g protein/day)")], 54.0)
        self.assertAlmostEqual(woman[self.column("RDA (g protein/day)")], 46.0)
        self.assertAlmostEqual(man[self.column("RDA (g/kg/day)")], 0.83)
        self.assertAlmostEqual(man[self.column("EAR (g protein/day)")], 43.0)

    def test_a_group_without_an_extra_allowance_leaves_that_cell_empty(self) -> None:
        child = self.row_for("child_1_3")
        self.assertIsNone(child[self.column("Extra allowance (g/day)")])
        self.assertIsNone(child[self.column("Suggested target 60+ (g/day)")])
        pregnant = self.row_for("pregnant_woman")
        self.assertAlmostEqual(pregnant[self.column("Extra allowance (g/day)")], 15.0)
        lactating = self.row_for("lactating_woman")
        self.assertAlmostEqual(lactating[self.column("Extra allowance (g/day)")], 25.0)

    def test_the_senior_target_is_exported_where_the_file_records_one(self) -> None:
        senior = self.row_for("senior_man_60")
        self.assertAlmostEqual(senior[self.column("Suggested target 60+ (g/day)")], 65.0)
        self.assertAlmostEqual(senior[self.column("RDA (g protein/day)")], 54.0)

    def test_a_derived_row_says_so_in_its_own_source_cell(self) -> None:
        derived = [
            entry.id for entry in population.profiles() if entry.derived
        ]
        self.assertEqual(len(derived), 8)
        for group_id in derived:
            self.assertEqual(self.row_for(group_id)[self.column("Source")], "ICMR-NIN 2020 (derived)")
        self.assertEqual(self.row_for("adult_man")[self.column("Source")], "ICMR-NIN 2020")

    def test_blank_cells_keep_the_columns_after_them_in_place(self) -> None:
        """Only the cell reference says where a value sits once a cell is skipped."""
        cells = sheet_cells(self.xlsx, 1)
        self.assertNotIn("K2", cells)  # child_1_3 has no extra allowance
        self.assertEqual(cells["M2"], "no")  # ...so the Derived column is still M
        self.assertIn("K13", cells)  # pregnant_woman's allowance is recorded
        self.assertEqual(cells["K13"], 15)

    def test_the_notes_sheet_carries_the_citation_the_units_and_the_disclaimer(self) -> None:
        details = {row[0]: row[1] for row in self.notes[1:]}
        meta = population.dataset_meta()
        self.assertEqual(details["Dataset"], meta["name"])
        self.assertEqual(details["Version"], meta["version"])
        self.assertEqual(details["Standard"], meta["source"])
        self.assertEqual(details["Disclaimer"], meta["disclaimer"])
        for name, text in meta["units"].items():
            self.assertEqual(details[f"Unit: {name}"], text)
        self.assertIn("ICMR-NIN (2020)", details["Standard"])
        self.assertIn("not medical", details["Disclaimer"].lower())

    def test_the_notes_sheet_says_how_to_rebuild_it_and_what_a_blank_means(self) -> None:
        details = {row[0]: row[1] for row in self.notes[1:]}
        self.assertEqual(details["Regenerate this file"], icmr_export.COMMAND)
        self.assertIn("no value", details["Blank cells"])
        self.assertIn("not a toxicological limit", details["Practical upper intake"])
        self.assertIn("10-15%", details["Protein as a share of energy"])

    def test_the_csv_is_the_same_table_as_the_sheet(self) -> None:
        with open(self.csv, "r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], list(icmr_export.HEADER))
        expected = [
            ["" if value is None else str(value) for value in row]
            for row in icmr_export.reference_rows()
        ]
        self.assertEqual(rows[1:], expected)
        self.assertEqual(len(rows), len(population.profiles()) + 1)

    def test_the_csv_opens_as_utf8_where_excel_expects_a_mark(self) -> None:
        self.assertTrue(self.csv.read_bytes().startswith(b"\xef\xbb\xbf"))


class ExportCliTests(unittest.TestCase):
    def test_generate_reports_what_it_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = icmr_export.generate(Path(directory))
            self.assertEqual(result["groups"], len(population.profiles()))
            self.assertEqual(result["columns"], len(icmr_export.HEADER))
            self.assertEqual(set(result["paths"]), {"xlsx", "csv"})
            for path in result["paths"].values():
                self.assertGreater(Path(path).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
