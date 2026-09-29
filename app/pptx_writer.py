"""A small .pptx writer: a PowerPoint deck from the standard library alone.

The internship has to be shown to a room, and the presentation is a build
artefact of the same record as the report: the numbers on the slides are read
from the database and the trained model, not retyped. A .pptx is a ZIP of XML
parts, exactly like the .docx that :mod:`app.docx_writer` already writes by hand,
so this module writes the parts PowerPoint needs and nothing else:

=============================  ==================================================
``[Content_Types].xml``        what each part in the package is
``_rels/.rels``                the package relationships (the presentation)
``ppt/presentation.xml``       slide size and the slide list
``ppt/slideMasters/…``         one master, so text has a default look
``ppt/slideLayouts/…``         one blank layout, because every slide is placed
``ppt/theme/theme1.xml``       the palette, taken from the report's figures
``ppt/slides/slideN.xml``      one part per slide
``docProps/core.xml``          title and author, for the file's properties
=============================  ==================================================

Why hand-rolled rather than ``python-pptx``: the project's whole claim is that
Python is the only requirement, and this keeps that true. The trade is that only
what is used here is implemented - text boxes, filled bands, a rule, and a table.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

EMU_PER_INCH = 914400

SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
MARGIN_IN = 0.8
CONTENT_WIDTH_IN = SLIDE_WIDTH_IN - MARGIN_IN - 0.55
CONTENT_TOP_IN = 1.78
CONTENT_BOTTOM_IN = 6.6

TITLE_SIZE = 28
BODY_SIZE = 15
MONO_SIZE = 11.5
TABLE_SIZE = 11.5
FOOTER_SIZE = 9.5

# The same values the report's figures are drawn with, so the deck and the
# document look like they came from one project.
ACCENT = "1F7A4D"
INK = "172320"
MUTED = "5C6B62"
RULE = "D6DFD9"
BAND = "E7F3EC"
PAPER = "FFFFFF"
#: The second series in a chart: the comparison arm, deliberately quiet so the
#: agent's bar is the one the eye lands on.
NEUTRAL_BAR = "9AA8A0"

SANS_FONT = "Calibri"
HEADING_FONT = "Calibri Light"
MONO_FONT = "Consolas"

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"

PML_NS = f'xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"'

DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

CONTENT_TYPES_HEAD = (
    DECLARATION
    + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
    '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
    '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
)

PACKAGE_RELS = (
    DECLARATION
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>"
)

SLIDE_RELS = (
    DECLARATION
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    "</Relationships>"
)

MASTER_RELS = (
    DECLARATION
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
    "</Relationships>"
)

LAYOUT_RELS = (
    DECLARATION
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    "</Relationships>"
)

GROUP_SHAPE = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
)


# --------------------------------------------------------------------------- #
# Scalars and escaping
# --------------------------------------------------------------------------- #
def inch(value: float) -> int:
    """Inches to EMU, which is what every DrawingML coordinate is quoted in."""
    return int(round(value * EMU_PER_INCH))


def centipoints(points: float) -> int:
    """Points to centipoints, which is what every font size is quoted in."""
    return int(round(points * 100))


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


# --------------------------------------------------------------------------- #
# DrawingML primitives
# --------------------------------------------------------------------------- #
def _run(text: str, size: float, bold: bool, color: str, font: str, italic: bool = False) -> str:
    attributes = f'lang="en-US" sz="{centipoints(size)}" dirty="0"'
    if bold:
        attributes += ' b="1"'
    if italic:
        attributes += ' i="1"'
    return (
        f"<a:r><a:rPr {attributes}>"
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        f'<a:latin typeface="{font}"/></a:rPr><a:t>{_esc(text)}</a:t></a:r>'
    )


def _paragraph(
    text: str,
    size: float = BODY_SIZE,
    bold: bool = False,
    color: str = INK,
    font: str = SANS_FONT,
    bullet: Optional[str] = None,
    align: str = "l",
    space_after: float = 8.0,
    line_spacing: int = 100,
    italic: bool = False,
) -> str:
    properties = f'<a:pPr algn="{align}"'
    children = ""
    if line_spacing != 100:
        children += f'<a:lnSpc><a:spcPct val="{line_spacing * 1000}"/></a:lnSpc>'
    if space_after:
        children += f'<a:spcAft><a:spcPts val="{centipoints(space_after)}"/></a:spcAft>'
    if bullet:
        properties += ' marL="205740" indent="-205740"'
        children += f'<a:buFont typeface="Arial"/><a:buChar char="{bullet}"/>'
    else:
        children += "<a:buNone/>"
    properties += ">" + children + "</a:pPr>"
    if text == "":
        return f"<a:p>{properties}</a:p>"
    return f"<a:p>{properties}{_run(text, size, bold, color, font, italic)}</a:p>"


def _text_box(
    shape_id: int,
    name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    paragraphs: Sequence[str],
    fill: Optional[str] = None,
    anchor: str = "t",
    insets: Tuple[float, float, float, float] = (0.12, 0.05, 0.12, 0.05),
) -> str:
    left, top, right, bottom = (inch(value) for value in insets)
    paint = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
    # A text body must hold at least one paragraph, even when the shape is only
    # a coloured bar, so the decorative shapes carry an empty one.
    content = "".join(paragraphs) or "<a:p><a:pPr><a:buNone/></a:pPr></a:p>"
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{_esc(name)}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{inch(x)}" y="{inch(y)}"/>'
        f'<a:ext cx="{inch(width)}" cy="{inch(height)}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{paint}</p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" lIns="{left}" tIns="{top}" '
        f'rIns="{right}" bIns="{bottom}" anchor="{anchor}"><a:normAutofit/></a:bodyPr>'
        f"<a:lstStyle/>{content}</p:txBody></p:sp>"
    )


def _cell(text: str, width_emu: int, fill: str, color: str, bold: bool, size: float) -> str:
    body = _paragraph(
        text, size=size, bold=bold, color=color, align="l", space_after=0
    )
    return (
        f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{body}</a:txBody>'
        f'<a:tcPr marL="91440" marR="91440" marT="45720" marB="45720" anchor="ctr">'
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill></a:tcPr></a:tc>'
    )


def _table(
    shape_id: int,
    name: str,
    x: float,
    y: float,
    width: float,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    weights: Sequence[float],
    row_height: float = 0.34,
) -> Tuple[str, float]:
    """A real DrawingML table. Returns the shape and the height it needs."""
    total_weight = float(sum(weights)) or 1.0
    columns = [int(inch(width) * (weight / total_weight)) for weight in weights]
    height = row_height * (len(rows) + 1)

    def row(cells: Sequence[str], fill: str, color: str, bold: bool) -> str:
        return (
            f'<a:tr h="{inch(row_height)}">'
            + "".join(
                _cell(value, columns[index], fill, color, bold, TABLE_SIZE)
                for index, value in enumerate(cells)
            )
            + "</a:tr>"
        )

    body = row(list(headers), ACCENT, PAPER, True)
    for index, values in enumerate(rows):
        body += row(list(values), PAPER if index % 2 == 0 else BAND, INK, False)

    shape = (
        f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{shape_id}" name="{_esc(name)}"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr>'
        "<p:nvPr/></p:nvGraphicFramePr>"
        f'<p:xfrm><a:off x="{inch(x)}" y="{inch(y)}"/>'
        f'<a:ext cx="{inch(width)}" cy="{inch(height)}"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        f'<a:tbl><a:tblPr firstRow="1"/><a:tblGrid>'
        + "".join(f'<a:gridCol w="{column}"/>' for column in columns)
        + f"</a:tblGrid>{body}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    return shape, height


# --------------------------------------------------------------------------- #
# Slide blocks: the visual vocabulary a slide is composed from
# --------------------------------------------------------------------------- #
# A slide is a stack of blocks. Each one measures itself, then draws itself into
# the height it was handed, so a slide can carry a table, a row of KPI cards and
# a chart without the caller doing arithmetic.
CARD_GAP_IN = 0.18
STEP_ROW_IN = 0.68
BAR_ROW_IN = 0.36
BAR_ROW_PAIR_IN = 0.5
CAPTION_IN = 0.34

#: Blocks that read as a panel or a chart, so they can absorb spare height by
#: growing their rows rather than leaving a gap under the content.
STRETCH_KINDS = {"cards", "columns", "steps", "bars"}


def _lines(text: str, characters: int) -> int:
    return max(1, -(-len(text) // max(characters, 1)))


def _characters(width_in: float, size: float) -> int:
    """How many characters of ``size`` points fit across ``width_in`` inches.

    Calibri averages a little under half its point size per character; the 0.74
    factor is deliberately pessimistic so a line is measured as wrapping early
    rather than as fitting when it does not.
    """
    return max(12, int(width_in * 96 / (size * 0.74)))


def block_height(block: Dict[str, Any]) -> float:
    """How much vertical room a block wants, in inches."""
    kind = block.get("kind", "bullets")
    if kind == "bullets":
        size = float(block.get("size", BODY_SIZE))
        characters = _characters(CONTENT_WIDTH_IN, size)
        items = block.get("items") or []
        return 0.30 * sum(_lines(text, characters) for text in items) + 0.06 * len(items)
    if kind == "table":
        return 0.34 * (len(block.get("rows") or []) + 1)
    if kind == "cards":
        return 1.15
    if kind == "columns":
        items = block.get("items") or []
        count = max(len(items), 1)
        column_width = (CONTENT_WIDTH_IN - CARD_GAP_IN * (count - 1)) / count
        characters = _characters(column_width - 0.36, 11.5)
        wrapped = [
            1 + sum(_lines(str(line), characters) for line in (column.get("lines") or []))
            for column in items
        ] or [3]
        return 0.34 + 0.29 + 0.24 * max(wrapped)
    if kind == "steps":
        return STEP_ROW_IN * len(block.get("items") or [])
    if kind == "bars":
        rows = block.get("rows") or []
        paired = any(len(row.get("series") or []) > 1 for row in rows)
        height = (BAR_ROW_PAIR_IN if paired else BAR_ROW_IN) * len(rows)
        return height + (CAPTION_IN if block.get("caption") else 0.0)
    if kind == "callout":
        return 0.54 + 0.24 * _lines(block.get("text", ""), 150)
    return 0.6


def _panel(
    shape_id: int,
    name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str,
    rounded: bool = True,
    border: Optional[str] = None,
) -> str:
    """A filled panel with no text of its own, used as a card or a bar track."""
    preset = "roundRect" if rounded else "rect"
    edge = ""
    if border:
        edge = f'<a:ln w="9525"><a:solidFill><a:srgbClr val="{border}"/></a:solidFill></a:ln>'
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{_esc(name)}"/>'
        '<p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{inch(x)}" y="{inch(y)}"/>'
        f'<a:ext cx="{inch(width)}" cy="{inch(height)}"/></a:xfrm>'
        f'<a:prstGeom prst="{preset}"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>{edge}</p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:buNone/></a:pPr></a:p>'
        "</p:txBody></p:sp>"
    )


def _slide_part(shapes: Sequence[str]) -> str:
    return (
        DECLARATION
        + f"<p:sld {PML_NS}><p:cSld><p:spTree>{GROUP_SHAPE}"
        + "".join(shapes)
        + "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def slide_master() -> str:
    return (
        DECLARATION
        + f'<p:sldMaster {PML_NS}><p:cSld><p:bg><p:bgPr>'
        '<a:solidFill><a:schemeClr val="lt1"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        f"<p:spTree>{GROUP_SHAPE}</p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        "<p:txStyles>"
        '<p:titleStyle><a:lvl1pPr algn="l"><a:defRPr sz="2800" b="1">'
        '<a:solidFill><a:schemeClr val="dk2"/></a:solidFill>'
        f'<a:latin typeface="{HEADING_FONT}"/></a:defRPr></a:lvl1pPr></p:titleStyle>'
        '<p:bodyStyle><a:lvl1pPr marL="205740" indent="-205740">'
        '<a:buFont typeface="Arial"/><a:buChar char="&#8226;"/><a:defRPr sz="1500">'
        '<a:solidFill><a:schemeClr val="dk2"/></a:solidFill></a:defRPr></a:lvl1pPr>'
        "</p:bodyStyle>"
        '<p:otherStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:otherStyle>'
        "</p:txStyles></p:sldMaster>"
    )


def slide_layout() -> str:
    return (
        DECLARATION
        + f'<p:sldLayout {PML_NS} type="blank" preserve="1" showMasterSp="0">'
        f'<p:cSld name="Blank"><p:spTree>{GROUP_SHAPE}</p:spTree></p:cSld>'
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )


def _solid_fill() -> str:
    return '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'


def _line(width: int) -> str:
    return (
        f'<a:ln w="{width}" cap="flat" cmpd="sng" algn="ctr">{_solid_fill()}'
        '<a:prstDash val="solid"/></a:ln>'
    )


def theme() -> str:
    """A theme is required by the master; only the palette here is load-bearing."""
    return (
        DECLARATION
        + f'<a:theme xmlns:a="{_A}" name="FormuSense"><a:themeElements>'
        '<a:clrScheme name="FormuSense">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        f'<a:dk2><a:srgbClr val="{INK}"/></a:dk2>'
        '<a:lt2><a:srgbClr val="F4F7F4"/></a:lt2>'
        f'<a:accent1><a:srgbClr val="{ACCENT}"/></a:accent1>'
        '<a:accent2><a:srgbClr val="2E8B57"/></a:accent2>'
        '<a:accent3><a:srgbClr val="B26A00"/></a:accent3>'
        '<a:accent4><a:srgbClr val="B3261E"/></a:accent4>'
        f'<a:accent5><a:srgbClr val="{MUTED}"/></a:accent5>'
        '<a:accent6><a:srgbClr val="9AA8A0"/></a:accent6>'
        f'<a:hlink><a:srgbClr val="{ACCENT}"/></a:hlink>'
        f'<a:folHlink><a:srgbClr val="{MUTED}"/></a:folHlink>'
        "</a:clrScheme>"
        '<a:fontScheme name="FormuSense">'
        f'<a:majorFont><a:latin typeface="{HEADING_FONT}"/><a:ea typeface=""/>'
        '<a:cs typeface=""/></a:majorFont>'
        f'<a:minorFont><a:latin typeface="{SANS_FONT}"/><a:ea typeface=""/>'
        '<a:cs typeface=""/></a:minorFont>'
        "</a:fontScheme>"
        '<a:fmtScheme name="FormuSense">'
        f"<a:fillStyleLst>{_solid_fill()}{_solid_fill()}{_solid_fill()}</a:fillStyleLst>"
        f"<a:lnStyleLst>{_line(6350)}{_line(12700)}{_line(19050)}</a:lnStyleLst>"
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        f"<a:bgFillStyleLst>{_solid_fill()}{_solid_fill()}{_solid_fill()}</a:bgFillStyleLst>"
        "</a:fmtScheme></a:themeElements><a:objectDefaults/>"
        "<a:extraClrSchemeLst/></a:theme>"
    )


def _block_bars(shape_id: int, x: float, y: float, width: float, height: float,
                block: Dict[str, Any]) -> Tuple[List[str], int]:
    """A drawn bar chart: labelled rows, a track, a fill and the value in words.

    A native chart part would be a second package inside the package (a chart, a
    colour/style sidecar and an embedded workbook). Bars drawn from the same
    rectangles as everything else stay inspectable in the XML and keep the file
    a single, small artefact.
    """
    rows = block.get("rows") or []
    caption = str(block.get("caption") or "")
    label_width = float(block.get("label_width", 3.4))
    value_width = 1.6
    track_left = x + label_width + 0.1
    track_width = width - label_width - value_width - 0.2
    values = [float(series.get("value") or 0.0) for row in rows for series in (row.get("series") or [])]
    overall = float(block.get("max") or (max(values) if values else 1.0)) or 1.0
    paired = any(len(row.get("series") or []) > 1 for row in rows)
    row_height = (height - (CAPTION_IN if caption else 0.0)) / max(len(rows), 1)
    # Bars stay a fraction of their row, so a chart that has been given more room
    # grows with the slide instead of floating in it.
    bar_height = min(0.22 if not paired else 0.15, row_height * (0.34 if not paired else 0.24))
    bar_height = max(bar_height, 0.05)
    gap = 0.05 if paired else 0.0
    colours = [ACCENT, NEUTRAL_BAR]

    shapes: List[str] = []
    for index, row in enumerate(rows):
        top = y + index * row_height
        # Metrics on different scales can share one chart by scaling each row to
        # its own ceiling; the caption says so when that is what happened.
        scale = float(row.get("max") or overall) or 1.0
        shapes.append(
            _text_box(shape_id, "BarLabel", x, top, label_width, row_height,
                      [_paragraph(str(row.get("label", "")), size=11.5, space_after=0,
                                  line_spacing=95)], anchor="ctr")
        )
        shape_id += 1
        series = row.get("series") or []
        block_height_in = bar_height * len(series) + gap * (len(series) - 1)
        first = top + (row_height - block_height_in) / 2
        for position, entry in enumerate(series):
            bar_top = first + position * (bar_height + gap)
            shapes.append(_panel(shape_id, "Track", track_left, bar_top, track_width, bar_height,
                                 BAND, rounded=False))
            shape_id += 1
            value = float(entry.get("value") or 0.0)
            fraction = max(0.0, min(1.0, value / scale))
            if fraction > 0.001:
                shapes.append(
                    _panel(shape_id, "Bar", track_left, bar_top, max(track_width * fraction, 0.03),
                           bar_height, colours[position % len(colours)], rounded=False)
                )
                shape_id += 1
            caption_text = str(entry.get("caption") or _format_number(value))
            # The value sits in a box taller than its bar and centred on it, and
            # carries no vertical inset, so a 10 pt caption is never the thing
            # that decides the bar's height.
            caption_box = 0.24 if not paired else 0.2
            shapes.append(
                _text_box(
                    shape_id, "BarValue", track_left + track_width + 0.08,
                    bar_top - (caption_box - bar_height) / 2, value_width, caption_box,
                    [_paragraph(caption_text, size=10, bold=True, space_after=0)],
                    anchor="ctr", insets=(0.04, 0.0, 0.04, 0.0),
                )
            )
            shape_id += 1
    if caption:
        shapes.append(
            _text_box(shape_id, "BarCaption", x, y + height - CAPTION_IN + 0.02, width,
                      CAPTION_IN - 0.04,
                      [_paragraph(caption, size=10, color=MUTED, space_after=0, line_spacing=95)])
        )
        shape_id += 1
    return shapes, shape_id


def _format_number(value: float, decimals: int = 4) -> str:
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def _block_callout(shape_id: int, x: float, y: float, width: float, height: float,
                   block: Dict[str, Any]) -> Tuple[List[str], int]:
    """One tinted strip carrying the sentence the slide wants remembered."""
    tone = block.get("tone", "band")
    fill = BAND if tone == "band" else PAPER
    border = None if tone == "band" else RULE
    shapes = [_panel(shape_id, "Callout", x, y, width, height, fill, border=border)]
    shape_id += 1
    shapes.append(
        _text_box(shape_id, "CalloutText", x + 0.22, y, width - 0.44, height,
                  [_paragraph(str(block.get("text", "")), size=13, bold=bool(block.get("bold")),
                              color=INK, space_after=0, line_spacing=100)],
                  anchor="ctr")
    )
    return shapes, shape_id + 1


def _block_cards(shape_id: int, x: float, y: float, width: float, height: float,
                 block: Dict[str, Any]) -> Tuple[List[str], int]:
    """A row of KPI cards: one big number, its label, and a line of context."""
    items = block.get("items") or []
    count = max(len(items), 1)
    card_width = (width - CARD_GAP_IN * (count - 1)) / count
    shapes: List[str] = []
    for index, card in enumerate(items):
        left = x + index * (card_width + CARD_GAP_IN)
        shapes.append(_panel(shape_id, "Card", left, y, card_width, height, BAND, border=RULE))
        shape_id += 1
        paragraphs = [
            _paragraph(str(card.get("value", "")), size=26, bold=True, color=ACCENT,
                       font=HEADING_FONT, space_after=2),
            _paragraph(str(card.get("label", "")), size=12.5, bold=True, space_after=2),
        ]
        if card.get("note"):
            paragraphs.append(
                _paragraph(str(card["note"]), size=10.5, color=MUTED, space_after=0)
            )
        shapes.append(
            _text_box(shape_id, "CardText", left + 0.14, y + 0.1, card_width - 0.28,
                      height - 0.2, paragraphs, anchor="ctr")
        )
        shape_id += 1
    return shapes, shape_id


def _block_columns(shape_id: int, x: float, y: float, width: float, height: float,
                   block: Dict[str, Any]) -> Tuple[List[str], int]:
    """Side-by-side panels, each with a heading and a few short lines."""
    items = block.get("items") or []
    count = max(len(items), 1)
    column_width = (width - CARD_GAP_IN * (count - 1)) / count
    shapes: List[str] = []
    for index, column in enumerate(items):
        left = x + index * (column_width + CARD_GAP_IN)
        shapes.append(_panel(shape_id, "Column", left, y, column_width, height, PAPER, border=RULE))
        shape_id += 1
        paragraphs = [
            _paragraph(str(column.get("title", "")), size=14, bold=True, color=ACCENT,
                       font=HEADING_FONT, space_after=7)
        ]
        for line in column.get("lines") or []:
            paragraphs.append(_paragraph(str(line), size=11.5, space_after=6, line_spacing=95))
        shapes.append(
            _text_box(shape_id, "ColumnText", left + 0.16, y + 0.12, column_width - 0.32,
                      height - 0.24, paragraphs)
        )
        shape_id += 1
    return shapes, shape_id


def _block_steps(shape_id: int, x: float, y: float, width: float, height: float,
                 block: Dict[str, Any]) -> Tuple[List[str], int]:
    """A numbered sequence: the pipeline reads as a flow, not as a list."""
    items = block.get("items") or []
    row = height / max(len(items), 1)
    badge = min(0.44, row - 0.14)
    shapes: List[str] = []
    for index, step in enumerate(items):
        top = y + index * row
        shapes.append(
            _text_box(shape_id, "Band", x, top + 0.03, width, row - 0.09, [], fill=BAND)
        )
        shape_id += 1
        shapes.append(_panel(shape_id, "Badge", x + 0.1, top + (row - badge) / 2, badge, badge, ACCENT))
        shape_id += 1
        shapes.append(
            _text_box(shape_id, "BadgeNumber", x + 0.1, top + (row - badge) / 2, badge, badge,
                      [_paragraph(str(index + 1), size=13, bold=True, color=PAPER, align="ctr",
                                  space_after=0)], anchor="ctr")
        )
        shape_id += 1
        label_left = x + badge + 0.28
        paragraphs = [
            _paragraph(str(step.get("label", "")), size=13, bold=True, space_after=1)
        ]
        if step.get("text"):
            paragraphs.append(
                _paragraph(str(step["text"]), size=11, color=MUTED, space_after=0, line_spacing=95)
            )
        shapes.append(
            _text_box(shape_id, "StepText", label_left, top, width - (label_left - x) - 0.14,
                      row, paragraphs, anchor="ctr")
        )
        shape_id += 1
    return shapes, shape_id


#: Block kind -> the function that draws it into the rectangle it was given.
BLOCK_DRAWERS = {
    "cards": _block_cards,
    "columns": _block_columns,
    "steps": _block_steps,
    "bars": _block_bars,
    "callout": _block_callout,
}


# --------------------------------------------------------------------------- #
# The deck
# --------------------------------------------------------------------------- #
class Deck:
    """Accumulate slides, then write them as a .pptx package.

    Only the shapes the presentation actually uses exist: a title, an accent
    bar, a rule, a text box of bullets, a tinted band for monospaced lines, and
    a table. Everything else a deck might need (images, charts, notes) is left
    out on purpose rather than half-implemented.
    """

    def __init__(
        self,
        title: str = "",
        author: str = "",
        subject: str = "",
        footer: str = "",
    ) -> None:
        self.deck_title = title
        self.author = author
        self.subject = subject
        # ``{n}`` and ``{total}`` are resolved when the package is written, so
        # the numbering is right even though slides are added one at a time.
        self.footer = footer
        self.slides: List[str] = []

    # ------------------------------------------------------------- primitives #
    def _chrome(self, shapes: List[str], title: str, title_size: float) -> None:
        shapes.append(_text_box(2, "AccentBar", 0.0, 0.0, 0.2, SLIDE_HEIGHT_IN, [], fill=ACCENT))
        shapes.append(
            _text_box(
                3,
                "SlideTitle",
                MARGIN_IN,
                0.42,
                CONTENT_WIDTH_IN,
                0.95,
                [_paragraph(title, size=title_size, bold=True, font=HEADING_FONT, space_after=0)],
            )
        )
        shapes.append(_text_box(4, "Rule", MARGIN_IN, 1.52, CONTENT_WIDTH_IN, 0.03, [], fill=RULE))

    def _footer_shape(self, shape_id: int, text: str) -> str:
        return _text_box(
            shape_id,
            "Footer",
            MARGIN_IN,
            CONTENT_BOTTOM_IN + 0.22,
            CONTENT_WIDTH_IN,
            0.34,
            [_paragraph(text, size=FOOTER_SIZE, color=MUTED, space_after=0)],
        )

    # ------------------------------------------------------------ slide types #
    def title_slide(
        self,
        title: str,
        subtitle: str = "",
        kicker: str = "",
        promise: str = "",
        team: str = "",
        members: Sequence[str] = (),
        footnote: str = "",
        chips: Sequence[Sequence[str]] = (),
    ) -> "Deck":
        """The opening slide: what the project is, and who is presenting it."""
        shapes = [
            _text_box(2, "AccentBar", 0.0, 0.0, 0.2, SLIDE_HEIGHT_IN, [], fill=ACCENT),
        ]
        if kicker:
            shapes.append(
                _text_box(
                    3, "Kicker", MARGIN_IN, 1.0, CONTENT_WIDTH_IN, 0.4,
                    [_paragraph(kicker, size=12, bold=True, color=ACCENT, space_after=0)],
                )
            )
        heading = [_paragraph(title, size=44, bold=True, font=HEADING_FONT, space_after=2)]
        if subtitle:
            heading.append(_paragraph(subtitle, size=20, color=MUTED, font=HEADING_FONT, space_after=0))
        shapes.append(
            _text_box(4, "Title", MARGIN_IN, 1.5, CONTENT_WIDTH_IN, 1.6, heading)
        )
        shapes.append(_text_box(5, "Rule", MARGIN_IN, 3.3, 4.2, 0.04, [], fill=ACCENT))
        if promise:
            shapes.append(
                _text_box(
                    6, "Promise", MARGIN_IN, 3.6, CONTENT_WIDTH_IN, 0.9,
                    [_paragraph(promise, size=17, space_after=0, line_spacing=105)],
                )
            )
        credits: List[str] = []
        if team:
            credits.append(_paragraph(team, size=12, bold=True, color=ACCENT, space_after=6))
        if members:
            credits.append(
                _paragraph("   .   ".join(members), size=16, space_after=0)
            )
        if credits:
            shapes.append(_text_box(7, "Team", MARGIN_IN, 4.75, CONTENT_WIDTH_IN, 1.0, credits))
        if chips:
            entries = [
                {"value": str(chip[0]), "label": str(chip[1])} for chip in chips
            ]
            height = 1.0
            room = CONTENT_WIDTH_IN
            card_width = (room - CARD_GAP_IN * (len(entries) - 1)) / len(entries)
            cursor = 5.3
            for index, entry in enumerate(entries):
                left = MARGIN_IN + index * (card_width + CARD_GAP_IN)
                shapes.append(_panel(9 + index, "Chip", left, cursor, card_width, height, BAND))
                shapes.append(
                    _text_box(
                        9 + index + 10, "ChipText", left + 0.12, cursor, card_width - 0.24, height,
                        [
                            _paragraph(entry["value"], size=22, bold=True, color=ACCENT,
                                       font=HEADING_FONT, align="ctr", space_after=1),
                            _paragraph(entry["label"], size=11, color=INK, align="ctr",
                                       space_after=0, line_spacing=95),
                        ],
                        anchor="ctr",
                    )
                )
        if footnote:
            shapes.append(
                _text_box(
                    30, "Footnote", MARGIN_IN, 6.6, CONTENT_WIDTH_IN, 0.4,
                    [_paragraph(footnote, size=11, color=MUTED, space_after=0)],
                )
            )
        self.slides.append(_slide_part(shapes))
        return self

    def slide(
        self,
        title: str,
        blocks: Sequence[Dict[str, Any]] = (),
        lead: str = "",
        title_size: float = TITLE_SIZE,
        body_size: float = BODY_SIZE,
    ) -> "Deck":
        """A content slide: a title, an optional lead-in, then a stack of blocks.

        Each block measures itself and is drawn into the height it is handed. If
        the stack asks for more room than the slide has, every block is scaled
        down together, so content never runs off the bottom of the slide. If it
        asks for less, the gaps open out and the last block that can stretch -
        a card row, a chart, a column pair - takes up the remainder, so a slide
        is composed rather than bunched under its title.
        """
        shapes: List[str] = []
        self._chrome(shapes, title, title_size)
        shape_id = 5
        cursor = CONTENT_TOP_IN

        if lead:
            shapes.append(
                _text_box(
                    shape_id, "Lead", MARGIN_IN, cursor, CONTENT_WIDTH_IN, 0.5,
                    [_paragraph(lead, size=body_size - 1, color=MUTED, italic=True,
                                space_after=0, line_spacing=98)],
                )
            )
            shape_id += 1
            cursor += 0.6

        blocks = [dict(block) for block in blocks]
        for block in blocks:
            block.setdefault("size", body_size)
        available = CONTENT_BOTTOM_IN - cursor
        count = len(blocks)
        wanted = [block_height(block) for block in blocks]
        total = sum(wanted)
        gap = 0.16
        room = list(wanted)
        if count and total > available - gap * (count - 1):
            scale = max(0.55, (available - gap * (count - 1)) / total)
            room = [max(height * scale, 0.4) for height in wanted]
        elif count:
            slack = available - total
            if count > 1:
                gap += min(0.5, max(0.0, (slack - gap * (count - 1)) / (count - 1)))
            leftover = max(available - total - gap * (count - 1), 0.0)
            for index in range(count - 1, -1, -1):
                if blocks[index].get("kind", "bullets") in STRETCH_KINDS:
                    room[index] = wanted[index] + leftover
                    break

        for block, height in zip(blocks, room):
            kind = block.get("kind", "bullets")
            if kind == "table":
                weights = list(block.get("weights") or [1.0] * len(block["headers"]))
                shape, _ = _table(
                    shape_id, "Table", MARGIN_IN, cursor, CONTENT_WIDTH_IN,
                    block["headers"], block["rows"], weights,
                    row_height=min(0.34, height / (len(block["rows"]) + 1)),
                )
                shapes.append(shape)
                shape_id += 1
            elif kind in BLOCK_DRAWERS:
                drawn, shape_id = BLOCK_DRAWERS[kind](
                    shape_id, MARGIN_IN, cursor, CONTENT_WIDTH_IN, height, block
                )
                shapes.extend(drawn)
            else:
                shapes.append(
                    _text_box(
                        shape_id, "Bullets", MARGIN_IN, cursor, CONTENT_WIDTH_IN, height,
                        [
                            _paragraph(text, size=float(block.get("size", body_size)),
                                       bullet="\u2022", space_after=9, line_spacing=98)
                            for text in block.get("items") or []
                        ],
                    )
                )
                shape_id += 1
            cursor += height + gap

        if self.footer:
            shapes.append(
                self._footer_shape(
                    shape_id,
                    f"{self.footer}          Slide {{n}} of {{total}}",
                )
            )
        self.slides.append(_slide_part(shapes))
        return self

    # ------------------------------------------------------------------ output #
    def presentation(self) -> str:
        count = len(self.slides)
        return (
            DECLARATION
            + f'<p:presentation {PML_NS}>'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            "<p:sldIdLst>"
            + "".join(
                f'<p:sldId id="{255 + index}" r:id="rId{index + 1}"/>'
                for index in range(1, count + 1)
            )
            + "</p:sldIdLst>"
            f'<p:sldSz cx="{inch(SLIDE_WIDTH_IN)}" cy="{inch(SLIDE_HEIGHT_IN)}"/>'
            f'<p:notesSz cx="{inch(7.5)}" cy="{inch(10.0)}"/></p:presentation>'
        )

    def presentation_rels(self) -> str:
        count = len(self.slides)
        entries = [
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        ]
        for index in range(1, count + 1):
            entries.append(
                f'<Relationship Id="rId{index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{index}.xml"/>'
            )
        entries.append(
            f'<Relationship Id="rId{count + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>'
        )
        return DECLARATION + (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(entries)
            + "</Relationships>"
        )

    def content_types(self) -> str:
        overrides = "".join(
            f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for index in range(1, len(self.slides) + 1)
        )
        return CONTENT_TYPES_HEAD + overrides + "</Types>"

    def core_properties(self) -> str:
        return (
            DECLARATION
            + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{_esc(self.deck_title)}</dc:title>"
            f"<dc:creator>{_esc(self.author)}</dc:creator>"
            f"<dc:subject>{_esc(self.subject)}</dc:subject>"
            f"<cp:lastModifiedBy>{_esc(self.author)}</cp:lastModifiedBy>"
            "</cp:coreProperties>"
        )

    def _resolved(self) -> List[str]:
        total = len(self.slides)
        return [
            part.replace("{n}", str(index)).replace("{total}", str(total))
            for index, part in enumerate(self.slides, start=1)
        ]

    def save(self, path: Path) -> Path:
        """Write the .pptx package."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", self.content_types())
            package.writestr("_rels/.rels", PACKAGE_RELS)
            package.writestr("docProps/core.xml", self.core_properties())
            package.writestr("ppt/presentation.xml", self.presentation())
            package.writestr("ppt/_rels/presentation.xml.rels", self.presentation_rels())
            package.writestr("ppt/slideMasters/slideMaster1.xml", slide_master())
            package.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", MASTER_RELS)
            package.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout())
            package.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", LAYOUT_RELS)
            package.writestr("ppt/theme/theme1.xml", theme())
            for index, part in enumerate(self._resolved(), start=1):
                package.writestr(f"ppt/slides/slide{index}.xml", part)
                package.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", SLIDE_RELS)
        return path
