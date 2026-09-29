"""An HTML rendition of the generated deck, so it can be reviewed in a browser.

The deck is an OOXML package and the machine that builds it has no PowerPoint, so
the only way to look at what was produced is to draw it. This module reads the
``.pptx`` that was just written - **not** the data behind it - and lays every shape
out at the coordinates, sizes, colours and fonts recorded in the file. That
distinction matters: what gets reviewed is the artefact itself, so a shape that
landed in the wrong place or a run that lost its colour is visible here.

It is a review aid, not a PowerPoint renderer. Text metrics differ from Office's,
so a long line may wrap differently, and it draws only the shapes this project
emits: text boxes, filled panels and bars, rounded cards, and one table.
"""
from __future__ import annotations

import html
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

EMU_PER_INCH = 914400.0
CSS_PX_PER_INCH = 96.0
DEFAULT_SIZE_CENTIPOINTS = 1800

STYLE = """
:root { color-scheme: light; }
body { margin: 0; padding: 28px 18px 60px; background: #eef1ee;
       font-family: Calibri, "Segoe UI", Arial, sans-serif; color: #172320; }
header { max-width: 1280px; margin: 0 auto 22px; }
header h1 { font-size: 20px; margin: 0 0 6px; }
header p { margin: 0; font-size: 13px; color: #5c6b62; }
.frame { max-width: 1280px; margin: 0 auto 26px; }
.slide { position: relative; width: 1280px; height: 720px;
         background: #ffffff; border: 1px solid #d6dfd9; border-radius: 3px;
         box-shadow: 0 2px 10px rgba(23, 35, 32, 0.08); overflow: hidden; }
.caption { max-width: 1280px; margin: 0 auto 6px; font-size: 12px; color: #5c6b62; }
.shape { position: absolute; overflow: hidden; }
.shape p { margin: 0; }
.table { border-collapse: collapse; table-layout: fixed; }
.table td { vertical-align: middle; }
"""

#: A slide is 13.333 in wide, which is wider than most review panels; the script
#: scales each one down to the space available so nothing is cropped.
FIT_SCRIPT = """
<script>
(function () {
  var BASE_W = 1280, BASE_H = 720;
  function fit() {
    var available = document.documentElement.clientWidth - 56;
    var scale = Math.min(1, Math.max(0.35, available / BASE_W));
    document.querySelectorAll('.frame').forEach(function (frame) {
      var slide = frame.firstElementChild;
      slide.style.transform = 'scale(' + scale + ')';
      slide.style.transformOrigin = 'top left';
      frame.style.height = (BASE_H * scale) + 'px';
      frame.style.width = (BASE_W * scale) + 'px';
    });
  }
  window.addEventListener('resize', fit);
  fit();
})();
</script>
"""


# --------------------------------------------------------------------------- #
# Unit conversion and small readers
# --------------------------------------------------------------------------- #
def _px(emu: Optional[str]) -> float:
    """EMU to CSS pixels at 96 dpi, which is what a browser lays out in."""
    try:
        return float(emu or 0) / EMU_PER_INCH * CSS_PX_PER_INCH
    except (TypeError, ValueError):
        return 0.0


def _font_px(centipoints: Optional[str]) -> float:
    try:
        points = float(centipoints) / 100.0
    except (TypeError, ValueError):
        points = DEFAULT_SIZE_CENTIPOINTS / 100.0
    return round(points * CSS_PX_PER_INCH / 72.0, 2)


def _colour(node: Optional[ElementTree.Element]) -> Optional[str]:
    if node is None:
        return None
    fill = node.find(f".//{A}srgbClr")
    if fill is None:
        return None
    value = fill.get("val")
    return f"#{value}" if value else None


def _xfrm(node: ElementTree.Element) -> Tuple[float, float, float, float]:
    frame = node.find(f"{P}spPr/{A}xfrm")
    if frame is None:
        frame = node.find(f"{P}xfrm")
    if frame is None:
        return (0.0, 0.0, 0.0, 0.0)
    offset = frame.find(f"{A}off")
    extent = frame.find(f"{A}ext")
    return (
        _px(offset.get("x") if offset is not None else "0"),
        _px(offset.get("y") if offset is not None else "0"),
        _px(extent.get("cx") if extent is not None else "0"),
        _px(extent.get("cy") if extent is not None else "0"),
    )


def _inline_style(left: float, top: float, width: float, height: float,
                  fill: Optional[str], geometry: Optional[str] = None,
                  outline: Optional[str] = None) -> str:
    # In DrawingML the shape's extent includes its insets, so border-box is the
    # faithful box model here - without it every text box would be drawn wider
    # and taller than the slide says.
    style = (
        f"left:{left:.1f}px;top:{top:.1f}px;"
        f"width:{width:.1f}px;height:{height:.1f}px;box-sizing:border-box;"
    )
    if fill:
        style += f"background:{fill};"
    if geometry == "roundRect":
        style += "border-radius:10px;"
    if outline:
        style += f"border:1px solid {outline};"
    return style


def _paragraph_html(paragraph: ElementTree.Element) -> str:
    """One DrawingML paragraph as HTML, with its bullet and run formatting."""
    properties = paragraph.find(f"{A}pPr")
    align = "left"
    indent_left = 0.0
    bullet = ""
    if properties is not None:
        if properties.get("algn") == "ctr":
            align = "center"
        elif properties.get("algn") == "r":
            align = "right"
        indent_left = _px(properties.get("marL"))
        marker = properties.find(f"{A}buChar")
        if marker is not None:
            bullet = marker.get("char") or "\u2022"

    pieces: List[str] = []
    for run in paragraph.findall(f"{A}r"):
        node = run.find(f"{A}t")
        text = node.text if node is not None and node.text else ""
        if not text:
            continue
        properties_node = run.find(f"{A}rPr")
        style = []
        weight = "600" if properties_node is not None and properties_node.get("b") == "1" else "400"
        style.append(f"font-weight:{weight}")
        if properties_node is not None and properties_node.get("i") == "1":
            style.append("font-style:italic")
        style.append(f"font-size:{_font_px(properties_node.get('sz') if properties_node is not None else None)}px")
        colour = _colour(properties_node)
        if colour:
            style.append(f"color:{colour}")
        family = properties_node.find(f"{A}latin") if properties_node is not None else None
        if family is not None and family.get("typeface"):
            style.append(f"font-family:'{html.escape(family.get('typeface', ''))}'")
        pieces.append(f'<span style="{";".join(style)}">{html.escape(text)}</span>')

    if not pieces:
        return ""
    body = (html.escape(bullet) + "&nbsp;&nbsp;") if bullet else ""
    padding = f"padding-left:{indent_left:.1f}px;" if indent_left else ""
    return f'<p style="text-align:{align};{padding}">{body}{"".join(pieces)}</p>'


def _text_box_html(shape: ElementTree.Element) -> str:
    left, top, width, height = _xfrm(shape)
    body = shape.find(f"{P}txBody")
    if body is None:
        return ""
    fill = _colour(shape.find(f"{P}spPr"))
    paragraphs = "".join(_paragraph_html(p) for p in body.findall(f"{A}p"))
    preset = shape.find(f"{P}spPr/{A}prstGeom")
    outline_node = shape.find(f"{P}spPr/{A}ln/{A}solidFill")
    style = _inline_style(
        left,
        top,
        width,
        height,
        fill,
        preset.get("prst") if preset is not None else None,
        _colour(outline_node) if outline_node is not None else None,
    )

    # The file's own body properties decide the inset and the vertical anchor, so
    # the rendition moves when the deck does rather than being independently right.
    properties = body.find(f"{A}bodyPr")
    if properties is not None:
        style += (
            f"padding:{_px(properties.get('tIns')):.1f}px {_px(properties.get('rIns')):.1f}px "
            f"{_px(properties.get('bIns')):.1f}px {_px(properties.get('lIns')):.1f}px;"
        )
        if properties.get("anchor") == "ctr":
            style += "display:flex;flex-direction:column;justify-content:center;"

    if not paragraphs.strip():
        # A decorative bar: drawn as a block, nothing to read.
        return f'<div class="shape" style="{style}"></div>'
    return f'<div class="shape" style="{style}">{paragraphs}</div>'


def _table_html(frame: ElementTree.Element) -> str:
    left, top, width, height = _xfrm(frame)
    table = frame.find(f".//{A}tbl")
    if table is None:
        return ""
    grid = [float(column.get("w") or 0) for column in table.findall(f"{A}tblGrid/{A}gridCol")]
    total = sum(grid) or 1.0
    scaled = [max(_px(str(value)) / _px(str(total)) * width, 8.0) for value in grid]

    rows: List[str] = []
    for index, row in enumerate(table.findall(f"{A}tr")):
        cells: List[str] = []
        for position, cell in enumerate(row.findall(f"{A}tc")):
            fill = _colour(cell.find(f"{A}tcPr")) or ("#ffffff" if index % 2 == 0 else "#e7f3ec")
            body = cell.find(f"{A}txBody")
            content = ""
            if body is not None:
                content = "".join(
                    _paragraph_html(p) for p in body.findall(f"{A}p")
                )
            column_width = scaled[position] if position < len(scaled) else 0.0
            cells.append(
                f'<td style="width:{column_width:.1f}px;background:{fill};'
                f'padding:4px 8px;">{content}</td>'
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    body_rows = "".join(rows)
    return (
        f'<div class="shape" style="{_inline_style(left, top, width, height, None, "rect")}">'
        f'<table class="table" style="width:{width:.1f}px;height:{height:.1f}px;">'
        f"<tbody>{body_rows}</tbody></table></div>"
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def slide_parts(path: Path) -> List[str]:
    """The slide XML parts, in presentation order."""
    import re

    with zipfile.ZipFile(path) as package:
        names = [
            name
            for name in package.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ]
        names.sort(key=lambda name: int(re.findall(r"\d+", name)[-1]))
        return [package.read(name).decode("utf-8") for name in names]


def render(path: Path, source_name: Optional[str] = None) -> str:
    """Draw the deck as a standalone HTML document."""
    path = Path(path)
    slides: List[str] = []
    for index, part in enumerate(slide_parts(path), start=1):
        root = ElementTree.fromstring(part)
        layers: List[str] = []
        tree = root.find(f".//{P}spTree")
        if tree is not None:
            for child in tree:
                if child.tag == f"{P}sp":
                    layers.append(_text_box_html(child))
                elif child.tag == f"{P}graphicFrame":
                    layers.append(_table_html(child))
        slides.append(
            f'<div class="caption">Slide {index}</div>'
            f'<div class="frame"><div class="slide" data-slide="{index}">'
            f'{"".join(layers)}</div></div>'
        )
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\"/>\n"
        f"<title>{html.escape(source_name or path.name)} - rendition</title>\n"
        f"<style>{STYLE}</style>\n</head>\n<body>\n"
        "<header>"
        f"<h1>{html.escape(source_name or path.name)}</h1>"
        "<p>A browser rendition of the PowerPoint package, drawn from the shapes and "
        "coordinates stored in the file. Text wrapping here is the browser's, not "
        "PowerPoint's, so treat it as a layout check rather than a proof of the final "
        "slide.</p>"
        "</header>\n"
        + "\n".join(slides)
        + "\n" + FIT_SCRIPT + "</body>\n</html>\n"
    )


def write(path: Path, out: Optional[Path] = None) -> Path:
    """Write the rendition next to the deck it describes."""
    path = Path(path)
    target = Path(out) if out else path.with_suffix(".html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(path), encoding="utf-8")
    return target
