"""Tests for the document reader.

Every format here is built by hand in the test - a .docx, .xlsx and .pptx are ZIPs of
XML, and a PDF is a header, an object and a Flate-compressed content stream - which is
exactly what the reader has to cope with in the wild without a parsing library. The
cases that matter are the ones a person will hit: a document whose table must survive
as a row of cells, a PDF that is a scan and has no text to read, and a file that is not
what its extension claims.
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from app.core import documents
from app.xlsx_writer import Sheet, write


def zip_of(parts: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name, text in parts.items():
            package.writestr(name, text)
    return buffer.getvalue()


def docx(body: str) -> bytes:
    return zip_of({"word/document.xml": f'<?xml version="1.0"?><w:document>{body}</w:document>'})


def paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def pdf(stream: bytes, compressed: bool = True, extra: str = "") -> bytes:
    blob = zlib.compress(stream) if compressed else stream
    filter_ = "/Filter/FlateDecode" if compressed else ""
    head = f"%PDF-1.4\n{extra}4 0 obj<</Length {len(blob)}{filter_}>>stream\n".encode()
    return head + blob + b"\nendstream\nendobj\ntrailer<</Root 1 0 R>>\n"


def data_url(name: str, payload: bytes, mime: str = "application/octet-stream") -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


class WordTests(unittest.TestCase):
    def test_paragraphs_and_tables_are_read_in_order(self) -> None:
        document = documents.extract(
            "report.docx",
            docx(
                paragraph("High protein ragi cookie")
                + paragraph("Protein 15 g per 100 g &amp; 40 g pack")
                + "<w:tbl><w:tr>"
                "<w:tc>" + paragraph("Moisture") + "</w:tc>"
                "<w:tc>" + paragraph("3.5 %") + "</w:tc>"
                "</w:tr></w:tbl>"
            ),
        )
        self.assertEqual(document.format, "docx")
        self.assertEqual(
            document.text,
            "High protein ragi cookie\n"
            "Protein 15 g per 100 g & 40 g pack\n"
            "Moisture | 3.5 %",
        )

    def test_a_footnote_and_a_header_are_both_accounted_for(self) -> None:
        document = documents.extract(
            "report.docx",
            zip_of(
                {
                    "word/document.xml": paragraph("Targets"),
                    "word/footnotes.xml": paragraph("Confirmed with QA on 12 May."),
                    "word/header1.xml": paragraph("Internal use only"),
                }
            ),
        )
        self.assertIn("Confirmed with QA", document.text)
        self.assertIn("Footnotes", [section["label"] for section in document.sections])
        self.assertTrue(any("header" in note for note in document.notes))

    def test_a_deleted_paragraph_does_not_come_back(self) -> None:
        """Tracked deletions live in w:delText, and a specification is read as it stands."""
        document = documents.extract(
            "report.docx",
            docx(paragraph("Protein 12 g") + "<w:p><w:del><w:r><w:delText>Protein 9 g</w:delText></w:r></w:del></w:p>"),
        )
        self.assertIn("Protein 12 g", document.text)
        self.assertNotIn("Protein 9 g", document.text)


class ExcelTests(unittest.TestCase):
    def test_a_workbook_written_here_reads_back_with_its_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.xlsx"
            write(
                path,
                [
                    Sheet("Targets", ("Nutrient", "Target", "Unit"), [["Protein", 15.0, "g"], ["Moisture", 3.5, "%"]]),
                    Sheet("Costs", ("Item", "INR/kg"), [["Flour", 48.0]]),
                ],
            )
            document = documents.extract("targets.xlsx", path.read_bytes())
        self.assertEqual(document.format, "xlsx")
        self.assertEqual([section["label"] for section in document.sections], ["Targets", "Costs"])
        self.assertIn("Protein | 15 | g", document.text)
        self.assertIn("Flour | 48", document.text)
        self.assertTrue(any("2 sheets" in note for note in document.notes))

    def test_an_empty_cell_does_not_shift_the_columns_after_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.xlsx"
            write(path, [Sheet("Sheet1", ("A", "B", "C"), [["first", None, "third"]])])
            document = documents.extract("gaps.xlsx", path.read_bytes())
        self.assertIn("first |  | third", document.text)


class DeckTests(unittest.TestCase):
    def test_slides_are_read_in_slide_order(self) -> None:
        def slide(text: str) -> str:
            return f'<p:sld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>'

        document = documents.extract(
            "deck.pptx",
            zip_of(
                {
                    "ppt/slides/slide10.xml": slide("Slide ten"),
                    "ppt/slides/slide2.xml": slide("Slide two"),
                    "ppt/slides/slide1.xml": slide("Slide one"),
                }
            ),
        )
        self.assertEqual([section["label"] for section in document.sections], ["Slide 1", "Slide 2", "Slide 3"])
        self.assertLess(document.text.index("Slide one"), document.text.index("Slide two"))
        self.assertLess(document.text.index("Slide two"), document.text.index("Slide ten"))


class PdfTests(unittest.TestCase):
    CONTENT = (
        b"BT /F1 12 Tf 50 700 Td (High protein ragi cookie) Tj 0 -14 Td (40 g pack. Protein 15 g per 100 g.) Tj ET"
    )

    def test_a_compressed_text_stream_is_decompressed_and_read(self) -> None:
        document = documents.extract("report.pdf", pdf(self.CONTENT))
        self.assertIn("High protein ragi cookie", document.text)
        self.assertIn("40 g pack", document.text)
        # The Td between the two lines is where the newline comes from.
        self.assertIn("\n", document.text)
        self.assertTrue(any("without a PDF library" in note for note in document.notes))

    def test_kerning_in_a_TJ_array_becomes_a_space(self) -> None:
        document = documents.extract(
            "report.pdf",
            pdf(b"BT [(Protein) -250 (15 g per 100 g)] TJ ET", compressed=False),
        )
        self.assertIn("Protein 15 g per 100 g", document.text)

    def test_an_escaped_parenthesis_survives(self) -> None:
        document = documents.extract(
            "report.pdf", pdf(rb"BT (Net weight 40 g \(approx\)) Tj ET", compressed=False)
        )
        self.assertIn("(approx)", document.text)

    def test_a_scan_says_so_instead_of_returning_nothing(self) -> None:
        with self.assertRaises(ValueError) as caught:
            documents.extract("scan.pdf", pdf(b"\x00\x01\x02\x03 binary image data \xff\xfe", compressed=False))
        self.assertIn("scan", str(caught.exception).lower())

    def test_an_image_stream_inside_a_pdf_is_not_read_as_text(self) -> None:
        image = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4
        document = documents.extract(
            "mixed.pdf", pdf(self.CONTENT, compressed=True) + pdf(image, compressed=False)
        )
        self.assertIn("High protein ragi cookie", document.text)
        self.assertNotIn("\xff\xd8", document.text)


class TextTests(unittest.TestCase):
    def test_a_utf8_file_with_a_byte_order_mark_reads_cleanly(self) -> None:
        document = documents.extract("spec.txt", "\ufeffProtein 15 g\n".encode("utf-8"))
        self.assertEqual(document.text, "Protein 15 g")
        self.assertEqual(document.notes, [])

    def test_a_windows_encoded_file_is_read_and_the_assumption_is_reported(self) -> None:
        document = documents.extract("spec.csv", "Café protein 15 g".encode("cp1252"))
        self.assertIn("Café", document.text)
        self.assertTrue(any("Windows-1252" in note for note in document.notes))

    def test_blank_line_runs_and_trailing_spaces_are_cleaned(self) -> None:
        document = documents.extract("spec.txt", b"Line one   \n\n\n\nLine two\n")
        self.assertEqual(document.text, "Line one\n\nLine two")


class RefusalTests(unittest.TestCase):
    def test_an_empty_upload_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            documents.extract("empty.docx", b"")

    def test_a_legacy_office_format_is_named_as_the_reason(self) -> None:
        with self.assertRaises(ValueError) as caught:
            documents.extract("old.doc", b"whatever")
        self.assertIn(".doc", str(caught.exception))

    def test_an_unknown_extension_lists_what_will_work(self) -> None:
        with self.assertRaises(ValueError) as caught:
            documents.extract("report.pages", b"whatever")
        message = str(caught.exception)
        self.assertIn(".docx", message)
        self.assertIn(".pdf", message)

    def test_a_renamed_file_is_refused_rather_than_read_as_noise(self) -> None:
        with self.assertRaises(ValueError):
            documents.extract("report.docx", b"this is not a zip file at all")

    def test_a_file_over_the_limit_is_refused_with_its_size(self) -> None:
        with mock.patch.object(documents, "MAX_BYTES", 1024):
            with self.assertRaises(ValueError) as caught:
                documents.extract("big.docx", b"x" * 2048)
        self.assertIn("MB", str(caught.exception))

    def test_a_long_document_is_cut_and_says_so(self) -> None:
        with mock.patch.object(documents, "MAX_CHARACTERS", 40):
            document = documents.extract("spec.txt", b"word " * 60)
        self.assertTrue(document.truncated)
        # The cut lands on the last whole word inside the limit, never mid-word or on a
        # trailing space.
        self.assertLessEqual(len(document.text), 40)
        self.assertGreater(len(document.text), 30)
        self.assertFalse(document.text.endswith(" "))
        self.assertTrue(any("longer than 40" in note for note in document.notes))


class DataUrlTests(unittest.TestCase):
    def test_any_mime_type_is_accepted_not_only_images(self) -> None:
        payload = b"Protein 15 g"
        self.assertEqual(
            documents.decode_data_url(data_url("spec.doc", payload, "application/msword")),
            payload,
        )

    def test_something_that_is_not_a_data_url_is_none(self) -> None:
        self.assertIsNone(documents.decode_data_url("https://example.com/spec.docx"))
        self.assertIsNone(documents.decode_data_url(""))

    def test_saving_a_document_keeps_one_file_per_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(documents, "UPLOAD_DIR", Path(directory)):
                first = documents.save_document("../../etc/passwd.docx", data_url("x", b"abc"))
                again = documents.save_document("passwd.docx", data_url("x", b"abc"))
                other = documents.save_document("passwd.docx", data_url("x", b"abcd"))
            self.assertIsNotNone(first)
            self.assertEqual(first, again)
            self.assertNotEqual(first, other)
            # The name can never walk out of the uploads directory.
            self.assertEqual(Path(first).parent.name, Path(directory).name)


class CatalogTests(unittest.TestCase):
    def test_the_form_is_told_what_it_may_offer(self) -> None:
        entry = documents.catalog_entry()
        for suffix in (".docx", ".xlsx", ".pdf", ".txt", ".csv"):
            self.assertIn(suffix, entry["accept"])
        self.assertGreaterEqual(entry["limit_mb"], 1)
        self.assertEqual(
            [item["id"] for item in entry["formats"]],
            ["docx", "xlsx", "pdf", "text", "pptx"],
        )
        self.assertIn("scanned", entry["note"])


if __name__ == "__main__":
    unittest.main()
