"""A small .xlsx writer: an Excel workbook from the standard library alone.

The reference data the application develops against has to leave the program in a
form a formulation team can open, sort and hand in, and the machine that builds it
has no ``openpyxl``. An .xlsx is a ZIP of XML parts, exactly like the .docx that
:mod:`app.docx_writer` and the .pptx that :mod:`app.pptx_writer` already write by
hand, so this module writes the parts Excel actually needs:

==============================  ===============================================
``[Content_Types].xml``         what each part in the package is
``_rels/.rels``                 the package relationship (the workbook)
``xl/workbook.xml``             the sheet list and the tab order
``xl/_rels/workbook.xml.rels``  the sheet parts and the style table
``xl/worksheets/sheetN.xml``    one part per sheet: rows, cells, freeze, filter
``xl/styles.xml``               the header fill, the bold font, the wrap
``docProps/core.xml``           title and author, for the file's properties
``docProps/app.xml``            the application name
==============================  ===============================================

Only what is used here is implemented, and deliberately:

* text is written as an *inline* string, so there is no shared-string table to
  keep in step with the cells;
* numbers are written as numbers, so a column can be summed in Excel rather than
  being text that happens to look numeric;
* a cell whose value is ``None`` or ``""`` is left out of the document
  altogether, which is how a spreadsheet says "the source records no value";
* a sheet name is validated the way Excel validates it (at most 31 characters,
  none of ``[]:*?/\\``, not a duplicate), because Excel refuses to open a file
  that breaks the rule.

The palette is the one the report's figures and the deck use, so a workbook looks
like it came from the same project.

Use it as::

    from app.xlsx_writer import Sheet, write

    write(
        "out.xlsx",
        [Sheet("Reference", ("Group", "RDA"), [["adult_man", 54.0]])],
        title="Reference values",
    )
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: The same greens the figures, the report and the deck are drawn with.
ACCENT = "1F7A4D"
INK = "172320"

FONT = "Calibri"

#: Cell style indexes into ``cellXfs`` in ``xl/styles.xml``.
STYLE_PLAIN = 0
STYLE_HEADER = 1
STYLE_WRAP = 2
STYLE_BOLD_WRAP = 3

#: Excel's own limits, enforced here rather than discovered by the person who
#: cannot open the file.
SHEET_NAME_LIMIT = 31
ILLEGAL_NAME_CHARS = set("[]:*?/\\")

MAX_COLUMNS = 16384

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f]")

DEFAULT_WIDTH = 14.0
ROW_HEIGHT = 15.0


def _esc(value: Any) -> str:
    """Escape text for XML content, dropping characters XML cannot carry."""
    text = _CONTROL.sub(" ", str(value).replace("\r\n", "\n").replace("\r", "\n"))
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _number(value: Any) -> str:
    """A number as OOXML writes it: plain decimal, never exponent notation.

    ``<v>`` holds a decimal literal, so ``1e-05`` would not be read back as the
    value that was written; anything that formats that way falls back to a fixed
    representation, and a whole float is written without a pointless ``.0``.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    text = repr(number)
    if "e" in text or "E" in text:
        text = f"{number:.15f}".rstrip("0").rstrip(".")
    return text


def column_letter(index: int) -> str:
    """1 -> ``A``, 27 -> ``AA``: the spreadsheet column name for a 1-based index."""
    if index < 1 or index > MAX_COLUMNS:
        raise ValueError(f"column {index} is outside the spreadsheet grid")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _check_sheet_name(name: str, seen: Iterable[str]) -> None:
    """Raise :class:`ValueError` for a name Excel would refuse to open."""
    if not str(name).strip():
        raise ValueError("a sheet needs a name")
    if len(name) > SHEET_NAME_LIMIT:
        raise ValueError(
            f"sheet name {name!r} is {len(name)} characters; Excel allows {SHEET_NAME_LIMIT}"
        )
    bad = ILLEGAL_NAME_CHARS.intersection(name)
    if bad:
        raise ValueError(f"sheet name {name!r} contains {''.join(sorted(bad))!r}")
    if name.lower() in {existing.lower() for existing in seen}:
        raise ValueError(f"two sheets are both called {name!r}")


@dataclass
class Sheet:
    """One worksheet: a header row, the rows under it, and how they are shown.

    ``widths`` are column widths in characters; ``wrap_columns`` and
    ``bold_columns`` are 0-based column indexes whose cells are wrapped and set in
    bold, which is what keeps a long cautions column readable and a notes sheet's
    labels distinct from its values.
    """

    name: str
    header: Sequence[str]
    rows: Sequence[Sequence[Any]] = field(default_factory=list)
    widths: Sequence[float] = ()
    wrap_columns: Sequence[int] = ()
    bold_columns: Sequence[int] = ()
    freeze_header: bool = True
    autofilter: bool = True

    def __post_init__(self) -> None:
        _check_sheet_name(self.name, ())
        width = len(self.header)
        for number, row in enumerate(self.rows, start=2):
            if len(row) != width:
                raise ValueError(
                    f"sheet {self.name!r}: row {number} has {len(row)} values, "
                    f"the header has {width}"
                )

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def columns(self) -> int:
        return len(self.header)

    def last_cell(self) -> str:
        return f"{column_letter(max(self.columns, 1))}{len(self.rows) + 1}"

    def column_width(self, index: int) -> float:
        if index < len(self.widths):
            return float(self.widths[index])
        return DEFAULT_WIDTH

    def cell_style(self, column: int, is_header: bool) -> int:
        if is_header:
            return STYLE_HEADER
        if column in self.bold_columns:
            # The bold style wraps as well: a bold column is a label column, and a
            # label that overflows its width is worse than one on two lines.
            return STYLE_BOLD_WRAP
        return STYLE_WRAP if column in self.wrap_columns else STYLE_PLAIN

    def cell_xml(self, column: int, row: int, value: Any, style: int) -> str:
        """One ``<c>`` element, or an empty string for a cell with no value."""
        if value is None or value == "":
            return ""
        reference = f"{column_letter(column)}{row}"
        style_attribute = f' s="{style}"' if style else ""
        if isinstance(value, bool):
            return f'<c r="{reference}"{style_attribute} t="b"><v>{_number(value)}</v></c>'
        if isinstance(value, (int, float)):
            return f'<c r="{reference}"{style_attribute}><v>{_number(value)}</v></c>'
        return (
            f'<c r="{reference}"{style_attribute} t="inlineStr">'
            f'<is><t xml:space="preserve">{_esc(value)}</t></is></c>'
        )

    def sheet_xml(self, selected: bool = False) -> str:
        rows: List[str] = []
        if self.header:
            cells = "".join(
                self.cell_xml(column, 1, title, STYLE_HEADER)
                for column, title in enumerate(self.header, start=1)
            )
            rows.append(f'<row r="1" ht="{ROW_HEIGHT}" customHeight="1">{cells}</row>')
        for index, values in enumerate(self.rows, start=2):
            cells = "".join(
                self.cell_xml(column, index, value, self.cell_style(column - 1, False))
                for column, value in enumerate(values, start=1)
            )
            rows.append(f'<row r="{index}">{cells}</row>')

        columns = "".join(
            f'<col min="{index}" max="{index}" width="{self.column_width(index - 1):g}" '
            'customWidth="1"/>'
            for index in range(1, self.columns + 1)
        )

        pane = ""
        selection = ""
        if self.freeze_header and self.header:
            pane = (
                '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            )
            selection = '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
        tab = ' tabSelected="1"' if selected else ""
        sheet_views = (
            f'<sheetViews><sheetView{tab} workbookViewId="0">{pane}{selection}'
            "</sheetView></sheetViews>"
        )
        filter_xml = ""
        if self.autofilter and self.header and self.rows:
            filter_xml = f'<autoFilter ref="A1:{self.last_cell()}"/>'

        return (
            DECLARATION
            + f'<worksheet xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
            f'<dimension ref="A1:{self.last_cell()}"/>'
            + sheet_views
            + f'<sheetFormatPr defaultRowHeight="{ROW_HEIGHT:g}"/>'
            + (f"<cols>{columns}</cols>" if columns else "")
            + f"<sheetData>{''.join(rows)}</sheetData>"
            + filter_xml
            + '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" '
            'header="0.3" footer="0.3"/>'
            "</worksheet>"
        )


def _styles_xml() -> str:
    """The style table: two fonts, the header fill, and the alignments used."""
    return (
        DECLARATION
        + f'<styleSheet xmlns="{MAIN_NS}">'
        '<fonts count="3">'
        f'<font><sz val="11"/><color rgb="FF{INK}"/><name val="{FONT}"/><family val="2"/></font>'
        f'<font><b/><sz val="11"/><color rgb="FF{INK}"/><name val="{FONT}"/><family val="2"/></font>'
        f'<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="{FONT}"/><family val="2"/></font>'
        "</fonts>"
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{ACCENT}"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" '
        'applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
        '<alignment vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" '
        'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '<dxfs count="0"/>'
        '<tableStyles count="0" defaultTableStyle="TableStyleMedium2" '
        'defaultPivotStyle="PivotStyleLight16"/>'
        "</styleSheet>"
    )


class Workbook:
    """A set of sheets, saved as one .xlsx package."""

    def __init__(
        self,
        sheets: Sequence[Sheet],
        title: Optional[str] = None,
        creator: str = "FormuSense",
        subject: str = "FormuSense workbook",
    ) -> None:
        if not sheets:
            raise ValueError("a workbook needs at least one sheet")
        seen: List[str] = []
        for sheet in sheets:
            _check_sheet_name(sheet.name, seen)
            seen.append(sheet.name)
        self.sheets: List[Sheet] = list(sheets)
        self.title = title or self.sheets[0].name
        self.creator = creator
        self.subject = subject

    def content_types(self) -> str:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(self.sheets) + 1)
        )
        return (
            DECLARATION
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + overrides
            + '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>"
        )

    def package_rels(self) -> str:
        return (
            DECLARATION
            + f'<Relationships xmlns="{PKG_REL_NS}">'
            f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/>'
            f'<Relationship Id="rId3" Type="{REL_NS}/extended-properties" '
            'Target="docProps/app.xml"/>'
            "</Relationships>"
        )

    def workbook_xml(self) -> str:
        entries = "".join(
            f'<sheet name="{_esc(sheet.name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, sheet in enumerate(self.sheets, start=1)
        )
        return (
            DECLARATION
            + f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
            '<fileVersion appName="xl" lastEdited="5" lowestEdited="5" rupBuild="9302"/>'
            "<workbookPr/>"
            '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" '
            'windowHeight="18000" activeTab="0"/></bookViews>'
            f"<sheets>{entries}</sheets>"
            '<calcPr calcId="0"/>'
            "</workbook>"
        )

    def workbook_rels(self) -> str:
        sheets = "".join(
            f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(self.sheets) + 1)
        )
        return (
            DECLARATION
            + f'<Relationships xmlns="{PKG_REL_NS}">'
            + sheets
            + f'<Relationship Id="rIdStyles" Type="{REL_NS}/styles" Target="styles.xml"/>'
            "</Relationships>"
        )

    def core_xml(self) -> str:
        return (
            DECLARATION
            + '<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{_esc(self.title)}</dc:title>"
            f"<dc:creator>{_esc(self.creator)}</dc:creator>"
            f"<cp:lastModifiedBy>{_esc(self.creator)}</cp:lastModifiedBy>"
            f"<dc:subject>{_esc(self.subject)}</dc:subject>"
            "</cp:coreProperties>"
        )

    def app_xml(self) -> str:
        return (
            DECLARATION
            + '<Properties '
            'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>FormuSense workbook writer</Application>"
            f"<Company>{_esc(self.creator)}</Company>"
            "</Properties>"
        )

    def save(self, path: Any) -> Path:
        """Write the .xlsx package and return where it landed."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", self.content_types())
            package.writestr("_rels/.rels", self.package_rels())
            package.writestr("xl/workbook.xml", self.workbook_xml())
            package.writestr("xl/_rels/workbook.xml.rels", self.workbook_rels())
            for index, sheet in enumerate(self.sheets, start=1):
                package.writestr(
                    f"xl/worksheets/sheet{index}.xml", sheet.sheet_xml(selected=index == 1)
                )
            package.writestr("xl/styles.xml", _styles_xml())
            package.writestr("docProps/core.xml", self.core_xml())
            package.writestr("docProps/app.xml", self.app_xml())
        return target


def write(
    path: Any,
    sheets: Sequence[Sheet],
    title: Optional[str] = None,
    creator: str = "FormuSense",
    subject: str = "FormuSense workbook",
) -> Path:
    """Write a one-off workbook: :class:`Workbook` with fewer names to type."""
    return Workbook(sheets, title=title, creator=creator, subject=subject).save(path)
