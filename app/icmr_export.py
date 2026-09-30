"""The ICMR-NIN 2020 reference set, as a spreadsheet a team can open and hand in.

The population guidance in :mod:`app.core.population` is only as useful as the
reference values behind it, and those values live in ``app/data/dri_profiles.json``
- editable configuration, transcribed from the 2020 ICMR-NIN publication. A reader
outside the program has no way to see them, so this module writes the same set out
as a workbook and a CSV:

* **Population reference** - one row per group, one column per recorded field,
  straight from :func:`app.core.population.profiles`. The sheet is built *from the
  module*, never from a second copy of the numbers, so it cannot drift away from
  what the interface computes with.
* **Source & notes** - the citation, the units, the disclaimer, the 2020 key
  values, the acceptable protein-energy band, the practical upper intake that is
  not a toxicological limit, which rows the file marks as derived, and the command
  that regenerates the file.

Nothing is invented here: every value and every sentence about the data comes from
the data file's own ``_meta`` or from the profiles themselves.

Build it with::

    python run.py --icmr-export            # into report/
    python run.py --icmr-export --out DIR  # somewhere else
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .core import population

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "report"

BASENAME = "FormuSense_ICMR_NIN_2020_protein_reference"
XLSX_NAME = f"{BASENAME}.xlsx"
CSV_NAME = f"{BASENAME}.csv"

SHEET_REFERENCE = "Population reference"
SHEET_NOTES = "Source & notes"

COMMAND = "python run.py --icmr-export"

#: (header, fixed column width in characters). The cautions column is wide and
#: wrapped because it is the only prose on the sheet.
COLUMNS: Tuple[Tuple[str, float], ...] = (
    ("Group ID", 16),
    ("Population group", 24),
    ("Short label", 14),
    ("Age range", 11),
    ("Sex", 9),
    ("Life stage", 12),
    ("Reference body weight (kg)", 14),
    ("EAR (g protein/day)", 13),
    ("RDA (g protein/day)", 13),
    ("RDA (g/kg/day)", 12),
    ("Extra allowance (g/day)", 14),
    ("Suggested target 60+ (g/day)", 16),
    ("Derived", 9),
    ("Cautions", 62),
    ("Source", 22),
)

HEADER: Tuple[str, ...] = tuple(name for name, _ in COLUMNS)
WIDTHS: Tuple[float, ...] = tuple(width for _, width in COLUMNS)
#: The prose column, wrapped so the sheet stays readable at a glance.
WRAP_COLUMNS: Tuple[int, ...] = (13,)

NOTES_HEADER: Tuple[str, ...] = ("Item", "Detail")
NOTES_WIDTHS: Tuple[float, ...] = (30, 96)


def _blank(value: Any) -> Any:
    """Leave a cell empty when the data file records no value for it."""
    return "" if value is None else value


def reference_rows() -> List[List[Any]]:
    """The 15 groups as spreadsheet rows, in the order the module returns them.

    The values are the module's own (``DriProfile.as_dict()``), including the
    rounding the interface displays, so a reader comparing the sheet with the
    Populations tab sees the same figures.
    """
    rows: List[List[Any]] = []
    for entry in population.profiles():
        group = entry.as_dict()
        derived = bool(group["derived"])
        rows.append(
            [
                group["id"],
                group["label"],
                group["short_label"],
                group["age_range"],
                group["sex"],
                group["stage"],
                group["ref_weight_kg"],
                group["ear_g_day"],
                group["rda_g_day"],
                group["rda_g_kg_day"],
                _blank(group["additional_g_day"]),
                _blank(group["suggested_target_g_day"]),
                "yes" if derived else "no",
                "; ".join(group["cautions"]),
                "ICMR-NIN 2020" + (" (derived)" if derived else ""),
            ]
        )
    return rows


def notes_rows() -> List[List[Any]]:
    """The provenance sheet: everything a reader needs to trust the table."""
    meta = population.dataset_meta()
    groups = population.profiles()
    derived = [entry.label for entry in groups if entry.derived]
    stages = sorted({entry.stage for entry in groups})
    key_values = meta["key_values"]
    low, high = meta["protein_energy_pct_range"]

    rows: List[List[Any]] = [
        ("Dataset", meta["name"]),
        ("Version", meta["version"]),
        ("Standard", meta["source"]),
        (
            "What this is",
            f"Protein reference values for {len(groups)} population groups, held as "
            "editable configuration inside FormuSense. One serving's protein is "
            "expressed as a share of each group's daily requirement (%RDA), and this "
            "sheet is that reference table rather than a computed result.",
        ),
        ("Groups", f"{len(groups)} rows"),
        ("Life stages", ", ".join(stages)),
        (
            "Where it is used",
            "The Populations tab of the interface, the /api/populations reference "
            "endpoint, the consumption guidance stored with every product record in "
            "app/service.py, and the population slide of the presentation.",
        ),
        ("Disclaimer", meta["disclaimer"]),
    ]

    for name, text in meta["units"].items():
        rows.append((f"Unit: {name}", text))

    if "adult_rda_g_kg_day" in key_values:
        rows.append(
            (
                "Adult RDA (2020)",
                f"{key_values['adult_rda_g_kg_day']} g protein per kg body weight per day",
            )
        )
    if "adult_ear_g_kg_day" in key_values:
        rows.append(
            (
                "Adult EAR (2020)",
                f"{key_values['adult_ear_g_kg_day']} g protein per kg body weight per day",
            )
        )
    if key_values.get("note"):
        rows.append(("What changed in 2020", key_values["note"]))

    rows.extend(
        [
            (
                "Protein as a share of energy",
                f"{low:g}-{high:g}% of the day's energy is the band quoted for protein "
                "in a mixed diet. It describes the whole day, not one food: a product "
                "whose claim is protein density sits above it by design.",
            ),
            (
                "Practical upper intake",
                f"{meta['practical_upper_g_kg_day']:g} g protein per kg body weight per "
                "day (capped at 1.8 for children and teenagers). This is not a "
                "toxicological limit - protein has no established upper level - it is "
                "the point above which a serving should no longer be treated as routine "
                "for that group.",
            ),
            (
                "Extra allowance (g/day)",
                "Recorded only for pregnancy and lactation: the amount added above the "
                "non-pregnant requirement.",
            ),
            (
                "Suggested target 60+ (g/day)",
                "Not an ICMR figure. The data file appends a 1.0 g/kg/day target for "
                "adults 60+ from the sarcopenia evidence, used alongside - never instead "
                "of - the ICMR RDA, so a formulation team can see both.",
            ),
            (
                "Blank cells",
                "A blank cell means the data file records no value for that group, not "
                "a value of zero.",
            ),
            (
                "Derived rows",
                f"{len(derived)} of {len(groups)} ({', '.join(derived)}). The data file "
                "flags these as derived entries rather than values quoted as they stand; "
                "the application labels such a row 'ICMR-NIN 2020 (derived)'. Exported "
                "as recorded.",
            ),
            (
                "Edit the source",
                "The values are configuration: app/data/dri_profiles.json. Correct the "
                f"file and every number above changes with it.",
            ),
            ("Regenerate this file", COMMAND),
        ]
    )
    return rows


def sheets() -> List[Any]:
    """Both worksheets, ready to be written."""
    from .xlsx_writer import Sheet

    return [
        Sheet(
            SHEET_REFERENCE,
            HEADER,
            reference_rows(),
            widths=WIDTHS,
            wrap_columns=WRAP_COLUMNS,
        ),
        Sheet(
            SHEET_NOTES,
            NOTES_HEADER,
            notes_rows(),
            widths=NOTES_WIDTHS,
            wrap_columns=(1,),
            bold_columns=(0,),
            autofilter=False,
        ),
    ]


def write_csv(path: Any, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> Path:
    """Write the reference table as CSV.

    ``utf-8-sig`` because Excel on Windows reads a UTF-8 file without a byte-order
    mark as the local code page, which turns any accented character in a caution
    into noise; the mark is invisible to every other reader.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return target


def generate(out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Write both files and report where they went.

    Mirrors :func:`app.slides.generate` and :func:`app.report_writers.generate` so
    the launcher treats every deliverable the same way.
    """
    from .xlsx_writer import write as write_workbook

    target_dir = Path(out_dir or DEFAULT_OUT)
    target_dir.mkdir(parents=True, exist_ok=True)
    meta = population.dataset_meta()

    xlsx_path = write_workbook(
        target_dir / XLSX_NAME,
        sheets(),
        title=f"{meta['name']} (version {meta['version']})",
        creator="FormuSense",
        subject=meta["source"],
    )
    rows = reference_rows()
    csv_path = write_csv(target_dir / CSV_NAME, HEADER, rows)
    return {
        "dir": target_dir,
        "paths": {"xlsx": xlsx_path, "csv": csv_path},
        "groups": len(rows),
        "columns": len(HEADER),
        "notes": len(notes_rows()),
        "sheets": [SHEET_REFERENCE, SHEET_NOTES],
    }
