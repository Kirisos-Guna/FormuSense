"""Figures for the report, drawn with Pillow and Pygments.

The report has to show evidence, not screenshots of a terminal. Every figure here
is produced from the *record* - the benchmark JSON, the stored trials, the residual
rows, the source files themselves - so a figure can always be regenerated and can
never drift away from the numbers in the text.

Charts are drawn at twice the final resolution and reduced with a Lanczos filter,
which is the cheapest way to get text and hairlines that survive a printed page.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

SCALE = 2
INK = (23, 35, 28)
MUTED = (92, 107, 98)
LINE = (214, 223, 217)
GRID = (235, 240, 236)
ACCENT = (31, 122, 77)
WARN = (178, 106, 0)
BAD = (179, 38, 30)
NEUTRAL = (154, 168, 160)
PANEL = (255, 255, 255)
BAND = (231, 243, 236)

FONT_CANDIDATES = {
    "sans": ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"],
    "sans_bold": ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "DejaVuSans-Bold.ttf"],
    "serif": ["C:/Windows/Fonts/times.ttf", "DejaVuSerif.ttf"],
    "mono": ["C:/Windows/Fonts/consola.ttf", "DejaVuSansMono.ttf", "Courier New.ttf"],
    "mono_bold": ["C:/Windows/Fonts/consolab.ttf", "DejaVuSansMono-Bold.ttf"],
}


def _font(size: int, kind: str = "sans") -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES.get(kind, FONT_CANDIDATES["sans"]):
        try:
            return ImageFont.truetype(candidate, size * SCALE)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _canvas(width: int, height: int, background: Tuple[int, int, int] = PANEL) -> Image.Image:
    return Image.new("RGB", (width * SCALE, height * SCALE), background)


def _save(image: Image.Image, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((image.width // SCALE, image.height // SCALE), Image.LANCZOS).save(path)
    return path


def _text(draw: ImageDraw.ImageDraw, xy: Tuple[float, float], value: str, font, fill=INK, anchor: str = "la") -> None:
    draw.text((xy[0] * SCALE, xy[1] * SCALE), str(value), font=font, fill=fill, anchor=anchor)


def _line(draw: ImageDraw.ImageDraw, points: Sequence[Tuple[float, float]], fill=LINE, width: int = 1) -> None:
    draw.line([(x * SCALE, y * SCALE) for x, y in points], fill=fill, width=max(1, width * SCALE))


def _rect(draw: ImageDraw.ImageDraw, box: Tuple[float, float, float, float], fill=None, outline=None, width: int = 1) -> None:
    draw.rectangle(
        [box[0] * SCALE, box[1] * SCALE, box[2] * SCALE, box[3] * SCALE],
        fill=fill,
        outline=outline,
        width=max(1, width * SCALE),
    )


def _ellipse(draw: ImageDraw.ImageDraw, box: Tuple[float, float, float, float], fill=None, outline=None, width: int = 2) -> None:
    draw.ellipse(
        [box[0] * SCALE, box[1] * SCALE, box[2] * SCALE, box[3] * SCALE],
        fill=fill,
        outline=outline,
        width=max(1, width * SCALE),
    )


class Frame:
    """A plot area with a linear x and y mapping, ticks and axis titles."""

    def __init__(
        self,
        draw: ImageDraw.ImageDraw,
        left: float,
        top: float,
        right: float,
        bottom: float,
        x_domain: Tuple[float, float],
        y_domain: Tuple[float, float],
    ) -> None:
        self.draw = draw
        self.left, self.top, self.right, self.bottom = left, top, right, bottom
        self.x_domain = x_domain
        self.y_domain = y_domain

    def x(self, value: float) -> float:
        low, high = self.x_domain
        span = (high - low) or 1.0
        return self.left + (value - low) / span * (self.right - self.left)

    def y(self, value: float) -> float:
        low, high = self.y_domain
        span = (high - low) or 1.0
        return self.bottom - (value - low) / span * (self.bottom - self.top)

    def grid(self, y_ticks: Sequence[float], label_format: str = "{:.2f}") -> None:
        for value in y_ticks:
            y = self.y(value)
            _line(self.draw, [(self.left, y), (self.right, y)], fill=GRID)
            _text(self.draw, (self.left - 8, y), label_format.format(value), _font(10), MUTED, anchor="rm")

    def frame(self) -> None:
        _line(self.draw, [(self.left, self.top), (self.right, self.top)], fill=LINE)
        _line(self.draw, [(self.left, self.bottom), (self.right, self.bottom)], fill=LINE)
        _line(self.draw, [(self.left, self.top), (self.left, self.bottom)], fill=LINE)


def _ticks(low: float, high: float, count: int = 5) -> List[float]:
    span = high - low
    if span <= 0:
        return [low]
    return [low + span * index / count for index in range(count + 1)]


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def benchmark_figure(report: Dict[str, Any], path: Path) -> Path:
    """Trials to target, agent against one-factor-at-a-time, per product.

    Laid out by hand rather than through :class:`Frame`: two bars per row plus a
    value label needs the vertical rhythm to be exact, and a generic axis mapping
    put the second row on top of the summary line.
    """
    rows = list(report.get("comparison") or [])
    budget = int(report.get("max_trials") or 6)
    header, row_height, footer = 66, 54, 84
    height = header + row_height * max(len(rows), 1) + footer
    image = _canvas(760, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), "Physical trials needed to reach the target", _font(14, "sans_bold"))
    _text(draw, (28, 40), "agent loop vs one-factor-at-a-time, same plant, same starting formulation, same gate",
          _font(10), MUTED)

    censored = budget + 1
    values = [(row.get("agent_trials") or censored) for row in rows]
    values += [(row.get("ofat_trials") or censored) for row in rows]
    maximum = max(values + [budget]) * 1.05
    left, right = 230, 470
    bar_height = 15

    # A light ruler along the top so bar lengths can be read off.
    for tick in _ticks(0.0, maximum, 4):
        x = left + (right - left) * tick / maximum
        _line(draw, [(x, header - 14), (x, height - footer + 6)], fill=GRID)
        _text(draw, (x, header - 24), f"{tick:.0f}", _font(9), MUTED, anchor="mm")

    for index, row in enumerate(rows):
        top = header + row_height * index + 4
        label = str(row.get("case", ""))
        if len(label) > 32:
            label = label[:31] + "\u2026"
        _text(draw, (left - 12, top + 14), label, _font(10), INK, anchor="rm")
        for offset, (value, colour, name) in enumerate(
            (
                (row.get("agent_trials"), ACCENT, "agent"),
                (row.get("ofat_trials"), NEUTRAL, "one-factor-at-a-time"),
            )
        ):
            bar_top = top + offset * (bar_height + 5)
            drawn = (right - left) * ((value or censored) / maximum)
            _rect(draw, (left, bar_top, left + drawn, bar_top + bar_height), fill=colour)
            text = f"{name} {value if value is not None else 'not reached'}"
            _text(draw, (left + drawn + 6, bar_top + 7), text, _font(9), colour)

    summary = report.get("summary") or {}
    line_one = (
        f"Mean trials to target: agent {summary.get('agent_mean_trials_to_target')} vs "
        f"{summary.get('ofat_mean_trials_to_target')} one-factor-at-a-time. "
        f"Products reaching target: {summary.get('agent_successes')}/{summary.get('cases')} vs "
        f"{summary.get('ofat_successes')}/{summary.get('cases')}."
    )
    line_two = (
        f"Mean saving {summary.get('mean_trials_saved')} trials per product "
        f"(an arm that never reaches target is charged {censored} trials, the budget plus one)."
    )
    _text(draw, (28, height - footer + 26), line_one, _font(10), INK)
    _text(draw, (28, height - footer + 42), line_two, _font(10), MUTED)
    return _save(image, path)


def trajectories_figure(series: Sequence[Dict[str, Any]], path: Path, pass_bar: float = 0.85) -> Path:
    """Measured objective by trial, one line per product, with the pass bar."""
    height = 380
    image = _canvas(760, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), "Measured objective, trial by trial", _font(14, "sans_bold"))
    _text(draw, (28, 40), "the pass gate is every hard target inside tolerance and an objective of at least 0.85",
          _font(10), MUTED)
    values = [value for item in series for value in item.get("values", [])]
    low = min(values + [pass_bar]) - 0.03
    high = max(values + [pass_bar]) + 0.02
    frame = Frame(draw, 70, 70, 720, height - 70, (0.0, max(len(item.get("values", [])) for item in series) - 1 or 1), (low, high))
    frame.grid(_ticks(low, high, 4))
    _line(draw, [(frame.left, frame.y(pass_bar)), (frame.right, frame.y(pass_bar))], fill=BAD, width=1)
    _text(draw, (frame.right, frame.y(pass_bar) - 12), "pass bar 0.85", _font(10), BAD, anchor="ra")

    palette = [ACCENT, (31, 95, 139), WARN, (120, 80, 160), (176, 78, 128)]
    for index, item in enumerate(series):
        colour = palette[index % len(palette)]
        points = [(frame.x(position), frame.y(value)) for position, value in enumerate(item.get("values", []))]
        if len(points) > 1:
            _line(draw, points, fill=colour, width=2)
        for position, point in enumerate(points):
            _ellipse(draw, (point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3), fill=PANEL, outline=colour)
            _text(draw, (point[0], point[1] - 12), f"{item['values'][position]:.3f}", _font(9), colour, anchor="mm")
        label = str(item.get("label", ""))
        _text(draw, (frame.x(len(item.get("values", [])) - 1) + 8, frame.y(item.get("values", [0])[-1])),
              label if len(label) <= 22 else label[:21] + "\u2026", _font(10), colour)
    for position in range(0, max(len(item.get("values", [])) for item in series)):
        _text(draw, (frame.x(position), frame.bottom + 14), f"T{position + 1}", _font(10), MUTED, anchor="mm")
    _text(draw, (frame.left - 46, (frame.top + frame.bottom) / 2), "objective", _font(11), MUTED, anchor="mm")
    return _save(image, path)


def residual_figure(rows: Sequence[Dict[str, Any]], path: Path, title: str = "Residuals against the published interval") -> Path:
    """Predicted interval and the value the laboratory reported, per KPI."""
    rows = list(rows)[:12]
    height = 90 + 30 * max(len(rows), 1)
    image = _canvas(760, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), title, _font(14, "sans_bold"))
    _text(draw, (28, 40), "grey: 95% interval published before the trial | green: reported inside it | red: outside",
          _font(10), MUTED)
    for index, row in enumerate(rows):
        sigma = float(row.get("sigma") or 0.0)
        predicted = float(row.get("predicted") or 0.0)
        measured = float(row.get("measured") or 0.0)
        low, high = predicted - 1.96 * sigma, predicted + 1.96 * sigma
        pad = max((high - low) * 0.25, 1e-6)
        span_low = min(low - pad * 0.5, measured)
        span_high = max(high + pad * 0.5, measured)
        centre_y = 82 + index * 30
        left, right = 210, 660
        span = (span_high - span_low) or 1.0

        def x(value: float) -> float:
            return left + (value - span_low) / span * (right - left)

        _text(draw, (left - 10, centre_y), str(row.get("label", "")), _font(10), INK, anchor="rm")
        _line(draw, [(x(low), centre_y), (x(high), centre_y)], fill=LINE, width=6)
        _ellipse(draw, (x(predicted) - 4, centre_y - 4, x(predicted) + 4, centre_y + 4), fill=NEUTRAL)
        inside = low <= measured <= high
        colour = ACCENT if inside else BAD
        _ellipse(draw, (x(measured) - 5, centre_y - 5, x(measured) + 5, centre_y + 5), fill=colour)
        _text(draw, (right + 8, centre_y), f"{measured:.2f}", _font(10), colour)
        if not inside:
            _text(draw, (right + 8, centre_y + 11), "outside", _font(9), BAD)
    return _save(image, path)


def desirability_figure(rows: Sequence[Dict[str, Any]], path: Path, title: str = "Targets and how close the recipe gets") -> Path:
    """Horizontal desirability bars with the target value printed next to each."""
    rows = list(rows)[:12]
    height = 90 + 26 * max(len(rows), 1)
    image = _canvas(760, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), title, _font(14, "sans_bold"))
    _text(draw, (28, 40), "bar length is desirability: 100% is dead on target, 0% is not producible",
          _font(10), MUTED)
    for index, row in enumerate(rows):
        centre_y = 76 + index * 26
        _text(draw, (210, centre_y), str(row.get("label", "")), _font(10), INK, anchor="rm")
        score = max(0.0, min(1.0, float(row.get("desirability") or 0.0)))
        status = str(row.get("status") or "")
        colour = ACCENT if status == "on-target" else (WARN if status == "marginal" else BAD)
        _rect(draw, (218, centre_y - 5, 640, centre_y + 5), fill=GRID)
        _rect(draw, (218, centre_y - 5, 218 + 422 * score, centre_y + 5), fill=colour)
        _text(draw, (648, centre_y), f"{score * 100:.0f}%", _font(10), colour)
        target = row.get("target")
        value = row.get("value")
        _text(
            draw,
            (720, centre_y),
            f"{value:.2f} / {target:.2f}" if isinstance(target, (int, float)) and isinstance(value, (int, float)) else "",
            _font(9),
            MUTED,
            anchor="ra",
        )
    _text(draw, (218, height - 26), "value / target", _font(9), MUTED)
    return _save(image, path)


def accuracy_figure(per_kpi: Sequence[Dict[str, Any]], path: Path, title: str = "Prediction error by KPI") -> Path:
    """Mean absolute percentage error of the predictions that preceded each trial."""
    rows = sorted(per_kpi, key=lambda row: -float(row.get("mape_pct") or 0))[:12]
    height = 100 + 22 * max(len(rows), 1)
    image = _canvas(760, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), title, _font(14, "sans_bold"))
    _text(draw, (28, 40), "mean absolute error of each KPI's forecast, scored against the run that followed it",
          _font(10), MUTED)
    maximum = max([float(row.get("mape_pct") or 0) for row in rows] + [1.0])
    for index, row in enumerate(rows):
        centre_y = 76 + index * 22
        _text(draw, (250, centre_y), str(row.get("kpi", "")), _font(10), INK, anchor="rm")
        _rect(draw, (258, centre_y - 4, 640, centre_y + 4), fill=GRID)
        width = 382 * float(row.get("mape_pct") or 0) / maximum
        _rect(draw, (258, centre_y - 4, 258 + width, centre_y + 4), fill=ACCENT)
        _text(draw, (648, centre_y), f"{float(row.get('mape_pct') or 0):.1f}%", _font(10), MUTED)
        _text(draw, (712, centre_y), f"n={row.get('samples', 0)}", _font(9), MUTED, anchor="ra")
    return _save(image, path)


def conflict_figure(conflicts: Sequence[Dict[str, Any]], path: Path, title: str = "A brief that cannot be satisfied as written") -> Path:
    """Specified target against the best the formulation can achieve."""
    rows = list(conflicts)[:6]
    height = 90 + 34 * max(len(rows), 1)
    image = _canvas(760, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), title, _font(14, "sans_bold"))
    _text(draw, (28, 40), "the agent reports the gap instead of quietly missing the target",
          _font(10), MUTED)
    for index, row in enumerate(rows):
        centre_y = 78 + index * 34
        _text(draw, (200, centre_y), str(row.get("label", "")), _font(10), INK, anchor="rm")
        target = float(row.get("target") or 0)
        achieved = float(row.get("achieved") or 0)
        maximum = max(target, achieved) * 1.2 or 1.0
        _text(draw, (210, centre_y - 8), f"asked for {target:.1f}", _font(10), MUTED)
        _rect(draw, (210, centre_y - 4, 210 + 420 * target / maximum, centre_y + 4), fill=NEUTRAL)
        _text(draw, (210, centre_y + 18), f"best achievable {achieved:.1f}", _font(10), BAD)
        _rect(draw, (210, centre_y + 8, 210 + 420 * achieved / maximum, centre_y + 16), fill=BAD)
    return _save(image, path)


def loop_figure(path: Path) -> Path:
    """The agent loop, drawn as a diagram for the project description."""
    width, height = 760, 250
    image = _canvas(width, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), "The development loop", _font(14, "sans_bold"))
    stages = [
        ("Brief", "spec text,\nimages"),
        ("Design v1", "slot fill,\nmass balance"),
        ("Predict", "value +\ninterval"),
        ("Trial", "plant makes\nthe batch"),
        ("Analyse", "residuals,\nz-scores"),
        ("Diagnose", "ranked\ncauses"),
        ("Reformulate", "fit, correct,\nre-optimise"),
        ("Accept", "next\nversion"),
    ]
    box_width, box_height = 86, 54
    gap = (width - 56 - len(stages) * box_width) / max(len(stages) - 1, 1)
    top = 80
    centres: List[Tuple[float, float]] = []
    for index, (label, detail) in enumerate(stages):
        left = 28 + index * (box_width + gap)
        _rect(draw, (left, top, left + box_width, top + box_height), fill=PANEL, outline=ACCENT, width=1)
        _text(draw, (left + box_width / 2, top + 9), label, _font(10, "sans_bold"), INK, anchor="mm")
        y = top + 26
        for line in detail.split("\n"):
            _text(draw, (left + box_width / 2, y), line, _font(8), MUTED, anchor="mm")
            y += 11
        centres.append((left + box_width / 2, top + box_height))
        if index:
            previous = centres[index - 1][0] + box_width / 2
            _line(draw, [(previous + 2, top + box_height / 2), (left - 6, top + box_height / 2)], fill=MUTED, width=1)
            _text(draw, ((previous + left) / 2, top + box_height / 2 - 14), ">", _font(10), MUTED, anchor="mm")
    # The feedback edge: a trial that misses returns to reformulation.
    _line(draw, [(centres[-1][0], top + box_height + 6), (centres[-1][0], top + 110),
                 (centres[-3][0], top + 110), (centres[-3][0], top + box_height + 6)], fill=BAD, width=1)
    _text(draw, ((centres[-1][0] + centres[-3][0]) / 2, top + 104),
          "if the product misses, the residual drives the next version", _font(9), BAD, anchor="mm")
    _text(draw, (28, top + 130), "Every arrow is a stored record: the brief, the prediction with its interval, the trial, "
                                 "the diagnosis, the plan and the version that was accepted.", _font(10), MUTED)
    return _save(image, path)


def architecture_figure(path: Path) -> Path:
    """Module map, for the project description chapter."""
    width, height = 760, 330
    image = _canvas(width, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (28, 20), "How the code is arranged", _font(14, "sans_bold"))
    layers = [
        ("Interface", "app/web/index.html, app.js, charts.js  |  app/server.py (JSON API)", ACCENT),
        ("Orchestration", "app/service.py  |  app/bootstrap.py  |  app/benchmark.py", (31, 95, 139)),
        ("Agent core", "brief  |  vision  |  formulate  |  optimize  |  surrogate  |  doe  |  diagnose  |  reformulate  |  process", WARN),
        ("Models", "kb  |  nutrition  |  physical  |  cost  |  uncertainty  |  kpi  |  engine", (120, 80, 160)),
        ("World & record", "app/plant.py (the plant)  |  app/store.py (SQLite version ledger)", (176, 78, 128)),
    ]
    top = 60
    for label, detail, colour in layers:
        _rect(draw, (28, top, width - 28, top + 42), fill=PANEL, outline=colour, width=1)
        _rect(draw, (28, top, 34, top + 42), fill=colour)
        _text(draw, (46, top + 8), label, _font(11, "sans_bold"), colour)
        _text(draw, (46, top + 24), detail, _font(9), MUTED)
        top += 52
    _text(draw, (28, top + 4), "Nothing in the agent core writes to the database directly: every change goes through the "
                               "service, so the record is always the sequence of calls that produced it.", _font(10), MUTED)
    return _save(image, path)


# --------------------------------------------------------------------------- #
# Code listings, rendered with Pygments
# --------------------------------------------------------------------------- #
def code_image(
    source_path: Path,
    path: Path,
    first_line: int = 1,
    last_line: Optional[int] = None,
    title: str = "",
    max_lines: int = 46,
    font_size: int = 9,
) -> Path:
    """A syntax-highlighted listing of part of a source file, as a PNG."""
    from pygments import lex
    from pygments.lexers import PythonLexer
    from pygments.styles import get_style_by_name
    from pygments.token import Token

    source = Path(source_path).read_text(encoding="utf-8").splitlines()
    last = last_line or len(source)
    last = min(last, first_line + max_lines - 1, len(source))
    excerpt = source[first_line - 1 : last]
    style = get_style_by_name("friendly")
    colour_for = style.style_for_token

    def token_colour(token) -> Tuple[int, int, int]:
        entry = colour_for(token)
        value = entry.get("color") if isinstance(entry, dict) else None
        if not value:
            return INK
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))

    mono = _font(font_size, "mono")
    line_height = font_size + 5
    characters = max([len(line) for line in excerpt] + [40])
    width = 90 + int(characters * font_size * 0.62) + 40
    height = 58 + line_height * len(excerpt)
    image = _canvas(width, height)
    draw = ImageDraw.Draw(image)
    _text(draw, (18, 12), title or str(source_path), _font(10, "sans_bold"))
    _text(draw, (18, 28), f"lines {first_line}-{last} of {Path(source_path).name}", _font(8), MUTED)
    _line(draw, [(18, 46), (width - 18, 46)], fill=GRID)
    y = 52
    for offset, line in enumerate(excerpt):
        number = first_line + offset
        _text(draw, (18, y + 1), f"{number:4d}", _font(font_size, "mono"), (170, 180, 174))
        x = 66.0
        for token, value in lex(line + "\n", PythonLexer()):
            value = value.rstrip("\n")
            if not value:
                continue
            _text(draw, (x, y), value, mono, token_colour(token))
            x += len(value) * font_size * 0.62
        y += line_height
    return _save(image, path)


def find_function_lines(source_path: Path, name: str) -> Optional[Tuple[int, int]]:
    """Locate a function by name, returning its true (first line, last line).

    Parsed with :mod:`ast` rather than guessed by indentation, because a
    multi-line signature closes its bracket at the ``def``'s own indent level -
    which an indentation heuristic reads as the end of the function and silently
    truncates the listing to the signature. The parser already knows where the
    function ends.
    """
    import ast

    source = Path(source_path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return (node.lineno, node.end_lineno or node.lineno)
    return None


def function_body_start(source_path: Path, name: str) -> Optional[int]:
    """The first line of a function's *code*, past a leading docstring.

    A long docstring is worth reading in the source and not worth 40 lines of a
    report listing, so the listing starts at the code and says that the docstring
    was left out.
    """
    import ast

    source = Path(source_path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if not node.body:
                return node.lineno
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(getattr(first, "value", None), ast.Constant)
                and isinstance(first.value.value, str)
            ):
                doc_end = first.end_lineno or first.lineno
                if doc_end - first.lineno >= 5:
                    return doc_end + 1
            return node.lineno
    return None
