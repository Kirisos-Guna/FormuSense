"""A small .docx writer: a Word file from the standard library alone.

The internship report has to be submitted as an *editable* Word document in the
college's format - Times New Roman 12, 1.5 line spacing, justified body text,
thin-bordered tables - and the machine it is written on has no ``python-docx``.
A .docx is a ZIP of XML parts, so this module writes the parts Word actually
needs and nothing else:

===================  =========================================================
``[Content_Types].xml``  what each part in the package is
``_rels/.rels``          the package relationships (document, properties)
``word/document.xml``    the report itself
``word/styles.xml``      Times New Roman 12, 1.5 spacing, the named styles used
``word/media/*.png``     the figures
``docProps/*.xml``       title, author and the application name
===================  =========================================================

Everything is expressed as *blocks* (:meth:`Docx.block`) so the same report
content can be written to DOCX, HTML or Markdown - see :mod:`app.report_writers`.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BODY_FONT = "Times New Roman"
HEADING_FONT = "Times New Roman"
CODE_FONT = "Consolas"
BODY_HALF_POINTS = 24  # 12 pt
LINE_SPACING = 360  # 1.5 lines, in twentieths of a point
TWIPS_PER_INCH = 1440
CONTENT_WIDTH_TWIPS = 9000  # A4 with one-inch margins
EMU_PER_INCH = 914400

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Default Extension="jpg" ContentType="image/jpeg"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


SETTINGS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:zoom w:percent="100"/>
<w:defaultTabStop w:val="708"/>
<w:updateFields w:val="true"/>
</w:settings>"""


def _styles_xml() -> str:
    """The report format, expressed once, as document defaults plus styles."""

    def paragraph_style(
        style_id: str,
        name: str,
        *,
        size: int = BODY_HALF_POINTS,
        bold: bool = False,
        italic: bool = False,
        font: str = BODY_FONT,
        align: str = "both",
        line: int = LINE_SPACING,
        before: int = 0,
        after: int = 120,
        page_break_before: bool = False,
        colour: str = "000000",
        outline: Optional[int] = None,
    ) -> str:
        # The child order inside w:pPr is fixed by the schema: keepNext and
        # pageBreakBefore come before spacing, which comes before jc, which comes
        # before outlineLvl. Word is strict about that order even where it looks
        # harmless.
        spacing = f'<w:spacing w:before="{before}" w:after="{after}" w:line="{line}" w:lineRule="auto"/>'
        # An outline level is what makes "CHAPTER 1" a chapter as far as Word is
        # concerned: it feeds the navigation pane and, more importantly, the
        # INDEX field on the front matter (see :meth:`Docx.index`).
        level = "" if outline is None else f'<w:outlineLvl w:val="{outline}"/>'
        return (
            f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
            f'<w:basedOn w:val="Normal"/><w:qFormat/>'
            "<w:pPr><w:keepNext w:val=\"true\"/>"
            f'{"<w:pageBreakBefore/>" if page_break_before else ""}'
            f"{spacing}<w:jc w:val=\"{align}\"/>{level}</w:pPr>"
            f"<w:rPr>"
            f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>'
            f'{"<w:b/>" if bold else ""}{"<w:i/>" if italic else ""}'
            f'<w:color w:val="{colour}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
            f"</w:rPr></w:style>"
        )

    styles = [
        paragraph_style("Normal", "Normal", align="both", after=120),
        paragraph_style("Body", "Body text", align="both", after=120),
        paragraph_style("TitlePage", "Title page line", size=28, bold=True, align="center", line=240, after=240),
        paragraph_style("Title", "Report title", size=32, bold=True, align="center", line=240, after=240),
        paragraph_style(
            "Heading1", "Chapter heading", size=28, bold=True, align="center", line=240,
            before=240, after=240, page_break_before=False, outline=0,
        ),
        paragraph_style(
            "ChapterDivider", "Chapter divider", size=36, bold=True, align="center",
            line=240, before=0, after=0, outline=0,
        ),
        paragraph_style(
            "Heading2", "Section heading", size=26, bold=True, align="left", line=240,
            before=200, after=120, outline=1,
        ),
        paragraph_style("Heading3", "Sub-section heading", size=24, bold=True, align="left", line=240, before=160, after=100),
        paragraph_style("Caption", "Figure and table caption", size=20, italic=True, align="center", line=240, after=200),
        paragraph_style("Code", "Code listing", size=16, font=CODE_FONT, align="left", line=240, after=60),
        paragraph_style("TableText", "Table cell", size=20, align="left", line=240, after=20),
        paragraph_style("TableCellHead", "Table heading cell", size=20, bold=True, align="left", line=240, after=20),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:styles {_NS}>"
        "<w:docDefaults>"
        f'<w:rPrDefault><w:rPr><w:rFonts w:ascii="{BODY_FONT}" w:hAnsi="{BODY_FONT}" w:cs="{BODY_FONT}"/>'
        f'<w:sz w:val="{BODY_HALF_POINTS}"/><w:szCs w:val="{BODY_HALF_POINTS}"/><w:lang w:val="en-IN"/></w:rPr></w:rPrDefault>'
        f'<w:pPrDefault><w:pPr><w:spacing w:line="{LINE_SPACING}" w:lineRule="auto" w:after="120"/>'
        '<w:jc w:val="both"/></w:pPr></w:pPrDefault>'
        "</w:docDefaults>"
        + "".join(styles)
        + "</w:styles>"
    )


def _esc(text: Any) -> str:
    """Escape text for XML, dropping characters the XML 1.0 grammar forbids."""
    value = "" if text is None else str(text)
    cleaned = "".join(ch for ch in value if ch in "\t\n\r" or 0x20 <= ord(ch) <= 0x10FFFF)
    return (
        cleaned.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _png_size(path: Path) -> Tuple[int, int]:
    """Width and height of a PNG, read from its header."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return (1200, 675)
    return struct.unpack(">II", header[16:24])


class Docx:
    """Accumulate report blocks, then write them as a Word document."""

    def __init__(self, title: str = "", author: str = "", subject: str = "") -> None:
        # Stored as metadata; ``title()`` below is the *block* helper, so the
        # document properties are kept under their own names.
        self.report_title = title
        self.author = author
        self.subject = subject
        self.blocks: List[Dict[str, Any]] = []
        self._media: List[Tuple[str, Path]] = []

    # ------------------------------------------------------------- primitives #
    def _paragraph(self, text: str, style: str = "Body", runs: Optional[List[str]] = None) -> str:
        if runs is not None:
            content = "".join(
                f'<w:r><w:rPr><w:rStyle w:val="{style if style != "Body" else "Normal"}"/></w:rPr>'
                f'<w:t xml:space="preserve">{_esc(chunk)}</w:t></w:r>'
                for chunk in runs
            )
        else:
            content = (
                f'<w:r><w:rPr><w:rStyle w:val="{style if style != "Body" else "Normal"}"/></w:rPr>'
                f'<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'
            )
        return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{content}</w:p>'

    def _run(self, text: str, *, bold: bool = False, italic: bool = False, size: int = BODY_HALF_POINTS,
             font: str = BODY_FONT) -> str:
        properties = (
            f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>'
            f'{"<w:b/>" if bold else ""}{"<w:i/>" if italic else ""}'
            f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        )
        return f'<w:r>{properties}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'

    # ------------------------------------------------------------- block API #
    def title(self, text: str) -> "Docx":
        self.blocks.append({"kind": "title", "text": text})
        return self

    def title_page_line(self, text: str, bold: bool = False, size: int = 28) -> "Docx":
        self.blocks.append({"kind": "titlepage", "text": text, "bold": bold, "size": size})
        return self

    def heading1(self, text: str) -> "Docx":
        self.blocks.append({"kind": "heading1", "text": text})
        return self

    def heading2(self, text: str) -> "Docx":
        self.blocks.append({"kind": "heading2", "text": text})
        return self

    def heading3(self, text: str) -> "Docx":
        self.blocks.append({"kind": "heading3", "text": text})
        return self

    def body(self, text: str) -> "Docx":
        self.blocks.append({"kind": "body", "text": text})
        return self

    def bullet(self, text: str, level: int = 0) -> "Docx":
        self.blocks.append({"kind": "bullet", "text": text, "level": level})
        return self

    def caption(self, text: str) -> "Docx":
        self.blocks.append({"kind": "caption", "text": text})
        return self

    def code(self, text: str, caption: str = "", lines_per_page: int = 0) -> "Docx":
        self.blocks.append({"kind": "code", "text": text, "caption": caption})
        return self

    def table(self, rows: Sequence[Sequence[Any]], header: bool = True, caption: str = "",
              widths: Optional[Sequence[float]] = None) -> "Docx":
        self.blocks.append(
            {"kind": "table", "rows": [list(row) for row in rows], "header": header,
             "caption": caption, "widths": list(widths) if widths else None}
        )
        return self

    def image(self, path: Path, caption: str = "", width_in: float = 6.2) -> "Docx":
        self.blocks.append({"kind": "image", "path": Path(path), "caption": caption, "width_in": width_in})
        return self

    def page_break(self) -> "Docx":
        self.blocks.append({"kind": "pagebreak"})
        return self

    def chapter_divider(self, text: str) -> "Docx":
        """A centred, page-filling "CHAPTER N" page, as the template asks for."""
        self.blocks.append({"kind": "chapter", "text": text})
        return self

    def index(self, title: str = "INDEX") -> "Docx":
        """A Word INDEX (table of contents) field.

        The field is written empty and left for Word to fill in: page numbers
        only exist once the document is paginated, and a hand-written table of
        contents would be wrong the moment a paragraph is edited. ``settings.xml``
        carries ``w:updateFields``, so Word builds it as soon as the file opens.
        """
        self.blocks.append({"kind": "index", "title": title})
        return self

    def placeholder(self, text: str) -> "Docx":
        """A visible marker for content the student has to supply themselves."""
        self.blocks.append({"kind": "placeholder", "text": text})
        return self

    def spacer(self, points: int = 200) -> "Docx":
        self.blocks.append({"kind": "spacer", "points": points})
        return self

    def block(self, item: Dict[str, Any]) -> "Docx":
        """Append a block from a report section (see :mod:`app.report_sections`)."""
        self.blocks.append(dict(item))
        return self

    def extend(self, items: Iterable[Dict[str, Any]]) -> "Docx":
        for item in items:
            self.block(item)
        return self

    # ----------------------------------------------------------- generation #
    def _table_xml(self, rows: Sequence[Sequence[Any]], header: bool, widths: Optional[Sequence[float]]) -> str:
        columns = max(len(row) for row in rows)
        if widths and len(widths) == columns:
            total = float(sum(widths)) or 1.0
            column_twips = [int(CONTENT_WIDTH_TWIPS * (width / total)) for width in widths]
        else:
            column_twips = [int(CONTENT_WIDTH_TWIPS / columns)] * columns
        borders = "".join(
            f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
            for side in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in column_twips)
        body_rows: List[str] = []
        for index, row in enumerate(rows):
            is_head = header and index == 0
            cells = []
            for column in range(columns):
                value = row[column] if column < len(row) else ""
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="EFEFEF"/>' if is_head else ""
                style = "TableCellHead" if is_head else "TableText"
                cells.append(
                    f'<w:tc><w:tcPr><w:tcW w:w="{column_twips[column]}" w:type="dxa"/>{shade}'
                    '<w:vAlign w:val="top"/></w:tcPr>'
                    + self._paragraph(str(value), style)
                    + "</w:tc>"
                )
            header_properties = "<w:trPr><w:tblHeader/></w:trPr>" if is_head else ""
            body_rows.append(f"<w:tr>{header_properties}{''.join(cells)}</w:tr>")
        return (
            "<w:tbl><w:tblPr>"
            '<w:tblW w:w="5000" w:type="pct"/>'
            f"<w:tblBorders>{borders}</w:tblBorders>"
            '<w:tblLayout w:type="fixed"/>'
            '<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar>'
            "</w:tblPr>"
            f"<w:tblGrid>{grid}</w:tblGrid>{''.join(body_rows)}</w:tbl>"
        )

    def _image_xml(self, path: Path, width_in: float, index: int) -> Tuple[str, str, str]:
        """Return (xml, relationship entry, media name) for one image."""
        name = f"image{index}.png"
        relationship_id = f"rIdImg{index}"
        pixel_width, pixel_height = _png_size(path)
        ratio = (pixel_height / pixel_width) if pixel_width else 0.56
        cx = int(width_in * EMU_PER_INCH)
        cy = int(width_in * EMU_PER_INCH * ratio)
        xml = (
            '<w:p><w:pPr><w:spacing w:before="120" w:after="60" w:line="240" w:lineRule="auto"/>'
            '<w:jc w:val="center"/></w:pPr>'
            "<w:r><w:drawing>"
            f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{index}" name="Figure {index}"/><wp:cNvGraphicFramePr/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            "<pic:pic>"
            f'<pic:nvPicPr><pic:cNvPr id="{index}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{relationship_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic></wp:inline>"
            "</w:drawing></w:r></w:p>"
        )
        relationship = (
            f'<Relationship Id="{relationship_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/{name}"/>'
        )
        return xml, relationship, name

    def render(self) -> Tuple[str, str, List[Tuple[str, Path]]]:
        """Build (document xml, document relationships, media files)."""
        parts: List[str] = []
        relationships: List[str] = [
            '<Relationship Id="rIdStyles" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>',
            '<Relationship Id="rIdSettings" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" '
            'Target="settings.xml"/>',
        ]
        media: List[Tuple[str, Path]] = []
        image_index = 0
        for item in self.blocks:
            kind = item.get("kind", "body")
            if kind == "title":
                parts.append(self._paragraph(item.get("text", ""), "Title"))
            elif kind == "titlepage":
                style = "TitlePage"
                text = item.get("text", "")
                runs = [self._run(text, bold=bool(item.get("bold")), size=int(item.get("size", 28)))]
                parts.append(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{"".join(runs)}</w:p>')
            elif kind == "chapter":
                parts.append(
                    '<w:p><w:pPr><w:pStyle w:val="ChapterDivider"/><w:pageBreakBefore/>'
                    f'</w:pPr>{self._run(item.get("text", ""), bold=True, size=36)}</w:p>'
                )
                # Push the heading itself onto the following page, so the divider
                # is a page of its own exactly as in the supplied template.
                parts.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            elif kind == "placeholder":
                parts.append(self._paragraph(str(item.get("text", "")), "Caption"))
            elif kind == "index":
                parts.append(
                    '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:pageBreakBefore/></w:pPr>'
                    f'{self._run(item.get("title", "INDEX"), bold=True, size=28)}</w:p>'
                )
                parts.append(
                    '<w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr>'
                    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                    '<w:r><w:instrText xml:space="preserve">TOC \\o "1-1" \\h \\z \\u</w:instrText></w:r>'
                    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                    + self._run(
                        "Right-click here and choose Update Field: Word will list every "
                        "CHAPTER heading with its page number."
                    )
                    + '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
                )
            elif kind == "heading1":
                parts.append(self._paragraph(item.get("text", ""), "Heading1"))
            elif kind == "heading2":
                parts.append(self._paragraph(item.get("text", ""), "Heading2"))
            elif kind == "heading3":
                parts.append(self._paragraph(item.get("text", ""), "Heading3"))
            elif kind == "body":
                parts.append(self._paragraph(item.get("text", ""), "Body"))
            elif kind == "bullet":
                level = int(item.get("level", 0))
                indent = 360 + level * 360
                parts.append(
                    f'<w:p><w:pPr><w:pStyle w:val="Body"/><w:spacing w:line="{LINE_SPACING}" w:lineRule="auto"/>'
                    f'<w:ind w:left="{indent}" w:hanging="260"/><w:jc w:val="both"/></w:pPr>'
                    + self._run("\u2022  " + str(item.get("text", "")))
                    + "</w:p>"
                )
            elif kind == "caption":
                parts.append(self._paragraph(item.get("text", ""), "Caption"))
            elif kind == "code":
                caption = item.get("caption") or ""
                if caption:
                    parts.append(self._paragraph(caption, "Caption"))
                for line in str(item.get("text", "")).splitlines() or [""]:
                    parts.append(self._paragraph(line, "Code"))
            elif kind == "spacer":
                parts.append(
                    '<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'
                )
            elif kind == "pagebreak":
                parts.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            elif kind == "table":
                if item.get("caption"):
                    parts.append(self._paragraph(item["caption"], "Caption"))
                parts.append(self._table_xml(item["rows"], bool(item.get("header", True)), item.get("widths")))
                parts.append('<w:p><w:pPr><w:spacing w:after="160" w:line="240" w:lineRule="auto"/></w:pPr></w:p>')
            elif kind == "image":
                if not item.get("path"):
                    continue
                path = Path(item["path"])
                if not path.is_file():
                    continue
                image_index += 1
                xml, relationship, name = self._image_xml(
                    path, float(item.get("width_in", 6.2)), image_index
                )
                parts.append(xml)
                relationships.append(relationship)
                media.append((name, path))
                if item.get("caption"):
                    parts.append(self._paragraph(item["caption"], "Caption"))
            else:
                parts.append(self._paragraph(item.get("text", ""), "Body"))

        section = (
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
            'w:header="720" w:footer="720" w:gutter="0"/>'
            '<w:cols w:space="708"/><w:docGrid w:linePitch="360"/></w:sectPr>'
        )
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {_NS}><w:body>{''.join(parts)}{section}</w:body></w:document>"
        )
        relationship_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(relationships)
            + "</Relationships>"
        )
        return document, relationship_xml, media

    def save(self, path: Path) -> Path:
        """Write the .docx package."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        document, relationships, media = self.render()
        core = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{_esc(self.report_title)}</dc:title>"
            f"<dc:creator>{_esc(self.author)}</dc:creator>"
            f"<cp:lastModifiedBy>{_esc(self.author)}</cp:lastModifiedBy>"
            f"<dc:subject>{_esc(self.subject)}</dc:subject>"
            "</cp:coreProperties>"
        )
        app = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>FormuSense report generator</Application>"
            f"<Company>{_esc(self.author)}</Company>"
            "</Properties>"
        )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", CONTENT_TYPES)
            package.writestr("_rels/.rels", PACKAGE_RELS)
            package.writestr("word/document.xml", document)
            package.writestr("word/styles.xml", _styles_xml())
            package.writestr("word/settings.xml", SETTINGS)
            package.writestr("word/_rels/document.xml.rels", relationships)
            package.writestr("docProps/core.xml", core)
            package.writestr("docProps/app.xml", app)
            for name, media_path in media:
                package.writestr(f"word/media/{name}", media_path.read_bytes())
        return path
