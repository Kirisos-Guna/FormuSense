"""Reading an R&D document into text, with the standard library alone.

An R&D team already has the product on paper: a specification sheet, a development
report, a spreadsheet of targets. Asking them to retype it into a textarea is asking
them to introduce a transcription error, so the New product form takes the document
and reads the specification out of it.

What can be read here is a property of the standard library rather than a design
choice, and the boundary is worth stating plainly:

=================  ===============================================================
``.docx``          a ZIP of XML: paragraphs and table cells are read exactly
``.xlsx``          a ZIP of XML: shared strings resolved, every sheet by name
``.pptx``          a ZIP of XML: the text of each slide, in slide order
``.txt`` ``.csv``  decoded text, with the encoding sniffed rather than assumed
``.pdf``           *best effort*: the text operators inside the file's own content
                   streams, decompressed with zlib. A scan has no text to read, and
                   a font with no text mapping decodes to noise; both are reported
                   rather than passed on as if they were fine
``.doc`` ``.xls``  not readable - the old binary formats need a parser library
=================  ===============================================================

Nothing here interprets the document. It produces text, and
:mod:`app.core.document_brief` then reads that text with the same rule-based parser
the typed specification goes through, so a number in the record still traces to the
parser and not to a guess about what a document meant.
"""
from __future__ import annotations

import base64
import hashlib
import re
import zlib
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The one uploads directory, defined where the reference photographs already put
# theirs: the interface writes user files in a single place, and that place is
# git-ignored (see .gitignore) because it is record content rather than source.
from .vision import UPLOAD_DIR

#: Refused above this size. A document this large is not a specification sheet, and
#: the body travels to the server base64-encoded, so the ceiling is kept modest.
MAX_BYTES = 10 * 1024 * 1024

#: Text kept from one document. Long enough for a full specification and its tables,
#: short enough to sit in a textarea and in one model prompt.
MAX_CHARACTERS = 12000

DOCX_SUFFIXES = frozenset({".docx", ".docm", ".dotx"})
XLSX_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xltx"})
PPTX_SUFFIXES = frozenset({".pptx", ".pptm"})
TEXT_SUFFIXES = frozenset(
    {".txt", ".text", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".rst"}
)
PDF_SUFFIXES = frozenset({".pdf"})
LEGACY_SUFFIXES = frozenset({".doc", ".xls", ".ppt", ".odt", ".ods", ".rtf"})

#: The formats the upload names, in the order the form lists them: the ones an R&D team
#: actually sends first. ``None`` is a suffix the text reader accepts under many names.
FORMAT_ORDER: Tuple[Tuple[str, Optional[str]], ...] = (
    ("docx", ".docx"),
    ("xlsx", ".xlsx"),
    ("pdf", ".pdf"),
    ("text", None),
    ("pptx", ".pptx"),
)

SUPPORTED_SUFFIXES: Tuple[str, ...] = tuple(
    sorted(DOCX_SUFFIXES | XLSX_SUFFIXES | PPTX_SUFFIXES | TEXT_SUFFIXES | PDF_SUFFIXES)
)

FORMAT_LABELS: Dict[str, str] = {
    "docx": "Word document",
    "xlsx": "Excel workbook",
    "pptx": "PowerPoint deck",
    "pdf": "PDF (best effort)",
    "text": "Text, CSV or Markdown",
}


# --------------------------------------------------------------------------- #
# Data URL handling
# --------------------------------------------------------------------------- #
_DATA_URL = re.compile(
    r"^data:(?P<mime>[^,;]*)(?P<params>(?:;[^,;]*)*),(?P<data>.*)$", re.DOTALL
)


def decode_data_url(data_url: str) -> Optional[bytes]:
    """The bytes behind a browser data URL, or ``None`` if it is not one.

    :func:`app.core.vision.save_upload` reads images the same way but insists on an
    ``image/*`` MIME type, which is exactly right for a photograph and wrong for a
    specification sheet.
    """
    match = _DATA_URL.match(data_url or "")
    if not match:
        return None
    payload = match.group("data")
    try:
        if "base64" in match.group("params").lower():
            return base64.b64decode(payload, validate=False)
        from urllib.parse import unquote_to_bytes

        return unquote_to_bytes(payload)
    except Exception:  # noqa: BLE001 - a malformed payload is not an exception here
        return None


def save_document(name: str, data_url: str) -> Optional[str]:
    """Persist an uploaded document and return its path.

    The file name carries a digest of the contents, so uploading the same document
    twice overwrites one file instead of littering the directory, and the name can
    never escape it: everything but ``[A-Za-z0-9._-]`` is replaced.
    """
    payload = decode_data_url(data_url)
    if payload is None:
        return None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(name or "document")).name) or "document"
    stem, dot, suffix = safe.rpartition(".")
    if not dot:
        stem, suffix = safe, ""
    digest = hashlib.sha256(payload).hexdigest()[:8]
    target = UPLOAD_DIR / f"{stem[:60]}-{digest}{dot}{suffix}"
    target.write_bytes(payload)
    return str(target)


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
@dataclass
class Document:
    """A document, read. Everything the caller needs and nothing about files."""

    name: str
    format: str
    text: str
    sections: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def characters(self) -> int:
        return len(self.text)

    def as_dict(self, with_text: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "format": self.format,
            "format_label": FORMAT_LABELS.get(self.format, self.format),
            "characters": self.characters,
            "sections": [dict(section) for section in self.sections],
            "notes": list(self.notes),
            "truncated": self.truncated,
        }
        if with_text:
            payload["text"] = self.text
        return payload


def _xml_text(value: str) -> str:
    """Decode the character references Word and Excel really use."""
    text = value.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&apos;", "'").replace("&amp;", "&")
    text = re.sub(r"&#x([0-9A-Fa-f]+);", lambda m: chr(int(m.group(1), 16)), text)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def _tidy(text: str) -> str:
    """Line endings, trailing spaces and runs of blank lines - and nothing else."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_text(data: bytes) -> Tuple[str, Optional[str]]:
    """Decode a text file, reporting the encoding that was assumed."""
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "The file is not UTF-8; it was read as Windows-1252."
    except UnicodeDecodeError:
        return data.decode("latin-1"), "The file is not valid UTF-8; some characters may be wrong."


def _open_package(data: bytes, label: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(
            f"{label} is not a readable Office file. It may be renamed, or saved in one "
            "of the old binary formats - .doc, .xls and .ppt cannot be read without a "
            "parser library. Save it as .docx, .xlsx or .pdf and upload that."
        ) from None


# --------------------------------------------------------------------------- #
# Word
# --------------------------------------------------------------------------- #
# Word puts text in w:t runs, and everything that separates runs in the markup
# between them. Matching both in one pass keeps the paragraphs and the table cells
# in the order a reader sees them.
_WORD = re.compile(
    r"</w:p>|</w:tc>|</w:tr>|<w:br\s*/>|<w:tab\s*/>|<w:t(?:\s[^>]*)?>(.*?)</w:t>",
    re.DOTALL,
)


def _word_text(xml_bytes: bytes) -> str:
    raw = xml_bytes.decode("utf-8", "replace")
    out: List[str] = []
    for match in _WORD.finditer(raw):
        token = match.group(0)
        if token.startswith("<w:t"):
            out.append(_xml_text(match.group(1) or ""))
        elif token.startswith("<w:tab"):
            out.append("\t")
        elif token == "</w:tc>":
            # A table cell ends where the next one begins, and Word closes every cell
            # with a paragraph break. The pipe replaces that break rather than opening
            # a line of its own, so a table reads as rows of cells.
            while out and out[-1] == "\n":
                out.pop()
            out.append(" | ")
        elif token == "</w:tr>":
            # The last cell of a row does not need a separator after it.
            if out and out[-1] == " | ":
                out.pop()
            out.append("\n")
        else:
            out.append("\n")
    return "".join(out)


def _docx_sections(data: bytes, label: str) -> Tuple[List[Dict[str, str]], List[str]]:
    sections: List[Dict[str, str]] = []
    notes: List[str] = []
    with _open_package(data, label) as package:
        names = set(package.namelist())
        if "word/document.xml" not in names:
            raise ValueError(f"{label} does not look like a Word document (no body inside).")
        sections.append({"label": "Body", "text": _tidy(_word_text(package.read("word/document.xml")))})
        if "word/footnotes.xml" in names:
            text = _tidy(_word_text(package.read("word/footnotes.xml")))
            if text:
                sections.append({"label": "Footnotes", "text": text})
        if any(re.match(r"word/(header|footer)\d*\.xml$", name) for name in names):
            notes.append(
                "The document has a page header or footer. Only the body and the "
                "footnotes were read; check nothing important lives in the margin."
            )
    return sections, notes


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
_CELL = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.DOTALL)
_ROW = re.compile(r"<row\b[^>]*>(.*?)</row>", re.DOTALL)
_TAG_T = re.compile(r"<t(?:\s[^>]*)?>(.*?)</t>", re.DOTALL)
_SHARED_ITEM = re.compile(r"<si>(.*?)</si>", re.DOTALL)
_INLINE = re.compile(r"<is>(.*?)</is>", re.DOTALL)
_VALUE = re.compile(r"<v>(.*?)</v>", re.DOTALL)
_COLUMN_REF = re.compile(r"^([A-Z]+)\d+$")


def _attr(attrs: str, name: str) -> str:
    match = re.search(name + r'="([^"]*)"', attrs)
    return match.group(1) if match else ""


def _column_index(reference: str, fallback: int) -> int:
    match = _COLUMN_REF.match((reference or "").strip().upper())
    if not match:
        return fallback
    index = 0
    for character in match.group(1):
        index = index * 26 + (ord(character) - 64)
    return index


def _shared_strings(package: zipfile.ZipFile) -> List[str]:
    try:
        raw = package.read("xl/sharedStrings.xml").decode("utf-8", "replace")
    except KeyError:
        return []
    return [_xml_text("".join(_TAG_T.findall(item))) for item in _SHARED_ITEM.findall(raw)]


def _sheet_parts(package: zipfile.ZipFile) -> List[Tuple[str, str]]:
    """``[(sheet name, part path)]`` in workbook order, which is tab order."""
    try:
        workbook = package.read("xl/workbook.xml").decode("utf-8", "replace")
    except KeyError:
        return []
    relationships: Dict[str, str] = {}
    try:
        rels = package.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
    except KeyError:
        rels = ""
    for match in re.finditer(r'<Relationship\b[^>]*>', rels):
        tag = match.group(0)
        identifier, target = _attr(tag, "Id"), _attr(tag, "Target")
        if identifier and target:
            relationships[identifier] = target
    out: List[Tuple[str, str]] = []
    for match in re.finditer(r"<sheet\b[^>]*/?>", workbook):
        tag = match.group(0)
        target = relationships.get(_attr(tag, "r:id"), "")
        if not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        out.append((_xml_text(_attr(tag, "name")) or "Sheet", target))
    return out


def _sheet_text(package: zipfile.ZipFile, part: str, shared: Sequence[str]) -> str:
    try:
        raw = package.read(part).decode("utf-8", "replace")
    except KeyError:
        return ""
    lines: List[str] = []
    for row in _ROW.findall(raw):
        cells: Dict[int, str] = {}
        position = 0
        for attrs, body in _CELL.findall(row):
            position += 1
            column = _column_index(_attr(attrs, "r"), position)
            kind = _attr(attrs, "t")
            value = ""
            if kind == "inlineStr":
                inline = _INLINE.search(body or "")
                value = _xml_text("".join(_TAG_T.findall(inline.group(1)))) if inline else ""
            elif kind == "s":
                reference = _VALUE.search(body or "")
                try:
                    value = shared[int(reference.group(1))] if reference else ""
                except (TypeError, ValueError, IndexError):
                    value = ""
            else:
                reference = _VALUE.search(body or "")
                value = _xml_text(reference.group(1)) if reference else ""
            if value.strip():
                cells[column] = " ".join(value.split())
        if cells:
            width = max(cells)
            lines.append(" | ".join(cells.get(column, "") for column in range(1, width + 1)))
    return "\n".join(lines)


def _xlsx_sections(data: bytes, label: str) -> Tuple[List[Dict[str, str]], List[str]]:
    sections: List[Dict[str, str]] = []
    notes: List[str] = []
    with _open_package(data, label) as package:
        parts = _sheet_parts(package)
        if not parts and "xl/workbook.xml" not in set(package.namelist()):
            raise ValueError(f"{label} does not look like an Excel workbook (no sheets inside).")
        shared = _shared_strings(package)
        for name, part in parts:
            text = _tidy(_sheet_text(package, part, shared))
            if text:
                sections.append({"label": name, "text": text})
        if len(parts) > 1:
            notes.append(
                f"The workbook has {len(parts)} sheets; all of them were read, and each "
                "one is shown under its own name."
            )
        if not shared and parts and any(
            "xl/sharedStrings.xml" in name for name in package.namelist()
        ):  # pragma: no cover - defensive, a workbook that has both is unusual
            notes.append("Some cell text may be missing: the shared string table was unreadable.")
    return sections, notes


# --------------------------------------------------------------------------- #
# PowerPoint
# --------------------------------------------------------------------------- #
_SLIDE = re.compile(r"</a:p>|<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.DOTALL)


def _slide_text(xml_bytes: bytes) -> str:
    raw = xml_bytes.decode("utf-8", "replace")
    out: List[str] = []
    for match in _SLIDE.finditer(raw):
        out.append("\n" if match.group(1) is None else _xml_text(match.group(1)))
    return "".join(out)


def _pptx_sections(data: bytes, label: str) -> Tuple[List[Dict[str, str]], List[str]]:
    sections: List[Dict[str, str]] = []
    with _open_package(data, label) as package:
        slides = [
            name
            for name in package.namelist()
            if re.match(r"ppt/slides/slide\d+\.xml$", name)
        ]
        if not slides:
            raise ValueError(f"{label} does not look like a PowerPoint deck (no slides inside).")
        slides.sort(key=lambda name: int(re.search(r"(\d+)", name.rsplit("/", 1)[-1]).group(1)))
        for index, name in enumerate(slides, start=1):
            text = _tidy(_slide_text(package.read(name)))
            if text:
                sections.append({"label": f"Slide {index}", "text": text})
    return sections, []


# --------------------------------------------------------------------------- #
# PDF, best effort
# --------------------------------------------------------------------------- #
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_POSITION = re.compile(rb"(?<![A-Za-z])(T\*|Td|TD|ET|BT)(?![A-Za-z])")
_NUMBER = re.compile(rb"-?\d+(?:\.\d+)?")

_ESCAPES = {
    b"n": "\n",
    b"r": "\r",
    b"t": "\t",
    b"b": "\b",
    b"f": "\f",
    b"(": "(",
    b")": ")",
    b"\\": "\\",
}


def _pdf_literal(content: bytes, start: int) -> Tuple[str, int]:
    """Read one ``( ... )`` string, honouring escapes and nested parentheses."""
    buffer: List[str] = []
    depth = 1
    index = start + 1
    size = len(content)
    while index < size and depth:
        character = content[index : index + 1]
        if character == b"\\":
            following = content[index + 1 : index + 2]
            if following in _ESCAPES:
                buffer.append(_ESCAPES[following])
                index += 2
                continue
            if following.isdigit():
                digits = b""
                cursor = index + 1
                while cursor < size and len(digits) < 3 and content[cursor : cursor + 1].isdigit():
                    digits += content[cursor : cursor + 1]
                    cursor += 1
                buffer.append(chr(int(digits, 8) & 0xFF))
                index = cursor
                continue
            buffer.append(following.decode("latin-1"))
            index += 2
            continue
        if character == b"(":
            depth += 1
        elif character == b")":
            depth -= 1
            if not depth:
                index += 1
                break
        buffer.append(character.decode("latin-1"))
        index += 1
    return "".join(buffer), index


def _pdf_array(content: bytes, start: int) -> Tuple[str, int]:
    """Read a ``[ ... ]`` operand: its strings, with the kerning gaps as spaces.

    Inside a ``TJ`` array a number is a horizontal adjustment, in thousandths of an
    em. A large negative one is where the typesetter put a space, and honouring it is
    the difference between ``protein 12g`` and ``protein12g``.
    """
    buffer: List[str] = []
    index = start + 1
    size = len(content)
    while index < size and content[index : index + 1] != b"]":
        character = content[index : index + 1]
        if character == b"(":
            value, index = _pdf_literal(content, index)
            buffer.append(value)
            continue
        number = _NUMBER.match(content, index)
        if number:
            try:
                if float(number.group(0)) <= -100:
                    buffer.append(" ")
            except ValueError:  # pragma: no cover - the regex cannot produce this
                pass
            index = number.end()
            continue
        index += 1
    return "".join(buffer), index + 1


def _pdf_stream_text(content: bytes) -> str:
    """Every text operand in one content stream, in the order it is drawn."""
    out: List[str] = []
    index = 0
    size = len(content)
    while index < size:
        character = content[index : index + 1]
        if character == b"(":
            value, index = _pdf_literal(content, index)
            out.append(value)
            continue
        if character == b"[":
            value, index = _pdf_array(content, index)
            out.append(value)
            continue
        position = _POSITION.match(content, index)
        if position:
            out.append("\n")
            index = position.end()
            continue
        index += 1
    return "".join(out)


def _printable_share(data: bytes) -> float:
    """How much of a byte string looks like text rather than an image or a font."""
    sample = data[:4096]
    if not sample:
        return 0.0
    good = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    return good / len(sample)


def _readable_share(text: str) -> float:
    if not text:
        return 0.0
    good = sum(
        1
        for character in text
        if character.isalnum() or character.isspace() or character in ".,;:()%-+/&'\"*_=<>[]#@!"
    )
    return good / len(text)


def _pdf_sections(data: bytes, label: str) -> Tuple[List[Dict[str, str]], List[str]]:
    notes = [
        "PDF text is read from the file's own text operators, without a PDF library. "
        "Compare it with the document before you rely on it."
    ]
    pieces: List[str] = []
    for raw in _STREAM.findall(data):
        chunk = raw
        try:
            chunk = zlib.decompress(raw)
        except zlib.error:
            pass
        # An image or a font program also arrives as a stream, and decoding its bytes
        # as text would fill the form with noise. Binary streams are skipped.
        if _printable_share(chunk) < 0.7:
            continue
        text = _pdf_stream_text(chunk)
        if text.strip():
            pieces.append(text)
    text = _tidy("\n".join(pieces))
    if not text:
        notes.append(
            "No text could be read at all: the pages are probably a scan, which needs "
            "optical character recognition rather than a reader. Export the report as "
            ".docx or .xlsx, or paste the specification by hand."
        )
    elif _readable_share(text) < 0.8:
        notes.append(
            "Part of this text is stored in a form that cannot be decoded without a PDF "
            "library, so what is below may be incomplete or garbled - check it, and "
            "export the report as .docx or .xlsx if it looks wrong."
        )
    return [{"label": "Text", "text": text}], notes


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #
def extract(name: str, data: bytes) -> Document:
    """Read a document into a :class:`Document`, or raise ``ValueError`` saying why not.

    The failure modes are all deliberate: an empty upload, a file over the size the
    server will accept, a legacy Office format, a damaged package and a document with
    no text in it each raise with a sentence a person can act on, because the caller
    is an interface that has to show somebody what went wrong.
    """
    label = Path(str(name or "")).name or "the upload"
    suffix = Path(label).suffix.lower()
    if not data:
        raise ValueError(f"{label} is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(
            f"{label} is {len(data) / 1048576:.1f} MB and the limit is "
            f"{MAX_BYTES // 1048576} MB. Upload the specification section, or paste the text."
        )
    if suffix in LEGACY_SUFFIXES:
        raise ValueError(
            f"{suffix} is the old binary Office format, which cannot be read without a "
            "parser library. Save it as .docx, .xlsx or .pdf and upload that."
        )

    notes: List[str] = []
    if suffix in DOCX_SUFFIXES:
        kind = "docx"
        sections, extra = _docx_sections(data, label)
    elif suffix in XLSX_SUFFIXES:
        kind = "xlsx"
        sections, extra = _xlsx_sections(data, label)
    elif suffix in PPTX_SUFFIXES:
        kind = "pptx"
        sections, extra = _pptx_sections(data, label)
    elif suffix in PDF_SUFFIXES:
        kind = "pdf"
        sections, extra = _pdf_sections(data, label)
    elif suffix in TEXT_SUFFIXES or not suffix:
        kind = "text"
        text, note = _decode_text(data)
        sections, extra = [{"label": "Text", "text": _tidy(text)}], ([note] if note else [])
    else:
        raise ValueError(
            f"{suffix or 'That file'} cannot be read. Upload one of: "
            + ", ".join(SUPPORTED_SUFFIXES)
            + "."
        )
    notes.extend(extra)
    sections = [section for section in sections if section.get("text")]

    text = "\n\n".join(section["text"] for section in sections).strip()
    truncated = len(text) > MAX_CHARACTERS
    if truncated:
        text = text[:MAX_CHARACTERS].rstrip()
        notes.append(
            f"The document is longer than {MAX_CHARACTERS} characters; the first "
            f"{MAX_CHARACTERS} were read."
        )
    if not text.strip():
        raise ValueError(
            f"No readable text was found in {label}. " + " ".join(notes) if notes else
            f"No readable text was found in {label}."
        )
    return Document(name=label, format=kind, text=text, sections=sections, notes=notes, truncated=truncated)


def catalog_entry() -> Dict[str, Any]:
    """What the interface needs to offer the upload, taken from what is implemented.

    The interface does not hard-code the list of formats or the size limit: it renders
    what this says, so a form can never offer a file that nothing here can open.
    """
    formats = [
        {"id": kind, "label": FORMAT_LABELS[kind]}
        for kind, suffix in FORMAT_ORDER
        if suffix is None or suffix in SUPPORTED_SUFFIXES
    ]
    return {
        "accept": ",".join(SUPPORTED_SUFFIXES),
        "formats": formats,
        "limit_mb": MAX_BYTES // 1024 // 1024,
        "max_characters": MAX_CHARACTERS,
        "note": (
            "Read out of the document, then shown in this form for you to check. A "
            "PDF is read from its own text operators, so a scanned page has no text "
            "to read."
        ),
    }
