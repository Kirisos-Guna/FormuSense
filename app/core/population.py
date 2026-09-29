"""Population protein guidance: what one serving means, group by group.

A formulation is developed against a specification, but a product is *eaten* by
people whose protein requirement depends on age, sex, pregnancy and age again.
This module turns the product's per-serving nutrition into an answer for each
population group in :mod:`app.data.dri_profiles.json`:

* protein delivered by one serving,
* that serving as a share of the group's daily requirement (% RDA),
* how many servings would be needed to reach the requirement,
* how close a serving comes to a practical upper intake,
* the cautions that apply to that group.

The reference values are ICMR-NIN 2020, transcribed and editable in the data
file; nothing here is invented, and the report cites the source. The output is
product-development guidance, not medical advice - the module says so in the
payload it returns, so the UI and the report cannot present it otherwise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DRI_FILE = "dri_profiles.json"

PROTEIN_KCAL_PER_G = 4.0


@dataclass(frozen=True)
class DriProfile:
    """One population group's protein reference values."""

    id: str
    label: str
    short_label: str
    age_range: str
    sex: str
    stage: str
    ref_weight_kg: float
    ear_g_day: float
    rda_g_day: float
    rda_g_kg_day: float
    derived: bool = False
    additional_g_day: Optional[float] = None
    suggested_target_g_kg_day: Optional[float] = None
    cautions: tuple = ()

    @property
    def suggested_target_g_day(self) -> Optional[float]:
        if self.suggested_target_g_kg_day is None:
            return None
        return self.suggested_target_g_kg_day * self.ref_weight_kg

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "short_label": self.short_label,
            "age_range": self.age_range,
            "sex": self.sex,
            "stage": self.stage,
            "ref_weight_kg": self.ref_weight_kg,
            "ear_g_day": self.ear_g_day,
            "rda_g_day": self.rda_g_day,
            "rda_g_kg_day": self.rda_g_kg_day,
            "additional_g_day": self.additional_g_day,
            "suggested_target_g_day": (
                None if self.suggested_target_g_day is None else round(self.suggested_target_g_day, 1)
            ),
            "derived": self.derived,
            "cautions": list(self.cautions),
        }


@lru_cache(maxsize=1)
def _raw() -> Dict[str, Any]:
    with open(DATA_DIR / DRI_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def profiles() -> List[DriProfile]:
    out: List[DriProfile] = []
    for record in _raw()["groups"]:
        out.append(
            DriProfile(
                id=record["id"],
                label=record["label"],
                short_label=record.get("short_label", record["label"]),
                age_range=record.get("age_range", ""),
                sex=record.get("sex", "any"),
                stage=record.get("stage", "adult"),
                ref_weight_kg=float(record["ref_weight_kg"]),
                ear_g_day=float(record["ear_g_day"]),
                rda_g_day=float(record["rda_g_day"]),
                rda_g_kg_day=float(record.get("rda_g_kg_day") or 0.0),
                derived=bool(record.get("derived", False)),
                additional_g_day=record.get("additional_g_day"),
                suggested_target_g_kg_day=record.get("suggested_target_g_kg_day"),
                cautions=tuple(record.get("cautions") or ()),
            )
        )
    return out


def profile(profile_id: str) -> Optional[DriProfile]:
    for entry in profiles():
        if entry.id == profile_id:
            return entry
    return None


def source_note() -> Dict[str, Any]:
    meta = _raw()["_meta"]
    return {
        "source": meta["source"],
        "disclaimer": meta["disclaimer"],
        "version": meta["version"],
        "key_values": meta.get("key_values", {}),
        "protein_energy_pct_range": meta.get("protein_energy_pct_range", [10.0, 15.0]),
        "practical_upper_g_kg_day": float(meta.get("practical_upper_g_kg_day", 2.0)),
    }


def practical_upper_g_day(entry: DriProfile) -> float:
    """Practical daily upper intake for a group, in grams of protein.

    Not a toxicological limit: protein has no established UL. It is the level
    above which a development team should stop treating a serving as routine for
    that group - 2 g/kg/day for healthy adults, and scaled down for children.
    """
    upper_kg = source_note()["practical_upper_g_kg_day"]
    if entry.stage in ("child", "teen"):
        upper_kg = min(upper_kg, 1.8)
    return upper_kg * entry.ref_weight_kg


def serving_nutrients(composition: Any, serving_g: float) -> Dict[str, float]:
    """Scale a per-100 g composition to one serving."""
    factor = max(float(serving_g), 0.0) / 100.0
    final = getattr(composition, "final", {}) or {}
    return {
        "serving_g": round(float(serving_g), 2),
        "protein_g": round(float(final.get("protein_g", 0.0)) * factor, 3),
        "energy_kcal": round(float(final.get("energy_kcal", 0.0)) * factor, 2),
        "sugar_g": round(float(final.get("sugar_g", 0.0)) * factor, 3),
        "fat_g": round(float(final.get("fat_g", 0.0)) * factor, 3),
        "fibre_g": round(float(final.get("fibre_g", 0.0)) * factor, 3),
        "sodium_mg": round(float(final.get("sodium_mg", 0.0)) * factor, 2),
    }


def _energy_share_note(
    protein_energy_pct: Optional[float], band: List[float]
) -> Optional[str]:
    """Explain the protein energy share without calling a protein product a failure.

    The 10-15% figure is an acceptable *range for a whole diet*, so a product built
    to be protein-dense sits above it by design. Saying so is more useful than
    tagging the product as out of range.
    """
    if protein_energy_pct is None:
        return None
    low, high = float(band[0]), float(band[1])
    share = f"Protein supplies {protein_energy_pct:.1f}% of this product's energy"
    if protein_energy_pct > high:
        return (
            f"{share}, above the {low:.0f}-{high:.0f}% band quoted for protein in a "
            "mixed diet - expected for a product whose claim is protein density. "
            "The band describes the whole day's intake, not one food."
        )
    if protein_energy_pct < low:
        return (
            f"{share}, below the {low:.0f}-{high:.0f}% band quoted for protein in a "
            "mixed diet: this product is not protein-led."
        )
    return f"{share}, inside the {low:.0f}-{high:.0f}% band quoted for protein in a mixed diet."


def _reading(entry: DriProfile, pct: float, servings: Optional[float]) -> str:
    if pct >= 45.0:
        return "a substantial part of the daily requirement in one serving"
    if pct >= 20.0:
        return "a useful contribution towards the daily requirement"
    if pct >= 10.0:
        return "a modest contribution; several servings are needed"
    return "a small contribution to the daily requirement"


def guidance(
    protein_per_serving_g: float,
    serving_label: str = "one serving",
    energy_per_serving_kcal: Optional[float] = None,
    sugar_per_serving_g: Optional[float] = None,
    sodium_per_serving_mg: Optional[float] = None,
    selected: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Per-group consumption guidance for a product's per-serving protein.

    Returns a payload that is safe to render directly: every row carries the
    reference value it is compared against, so a number can never appear without
    the standard it came from.
    """
    protein = max(float(protein_per_serving_g), 0.0)
    protein_energy_pct = None
    if energy_per_serving_kcal:
        protein_energy_pct = round(
            100.0 * protein * PROTEIN_KCAL_PER_G / max(float(energy_per_serving_kcal), 1e-6), 2
        )
    note = source_note()
    rows: List[Dict[str, Any]] = []
    wanted = set(selected) if selected else None
    for entry in profiles():
        if wanted is not None and entry.id not in wanted:
            continue
        upper = practical_upper_g_day(entry)
        pct_rda = 100.0 * protein / entry.rda_g_day if entry.rda_g_day > 0 else 0.0
        pct_ear = 100.0 * protein / entry.ear_g_day if entry.ear_g_day > 0 else 0.0
        servings = entry.rda_g_day / protein if protein > 1e-9 else None
        pct_upper = 100.0 * protein / upper if upper > 0 else 0.0
        suggested_pct = None
        if entry.suggested_target_g_day:
            suggested_pct = round(100.0 * protein / entry.suggested_target_g_day, 1)
        cautions = list(entry.cautions)
        flags: List[str] = []
        if entry.stage in ("child", "teen") and pct_rda >= 50.0:
            flags.append(
                "one serving exceeds half this group's daily protein requirement - "
                "review the portion size for this group"
            )
        if entry.stage == "pregnancy":
            flags.append("not suitable as a self-prescribed supplement during pregnancy")
        rows.append(
            {
                "id": entry.id,
                "label": entry.label,
                "short_label": entry.short_label,
                "age_range": entry.age_range,
                "sex": entry.sex,
                "stage": entry.stage,
                "ref_weight_kg": entry.ref_weight_kg,
                "rda_g_day": round(entry.rda_g_day, 1),
                "ear_g_day": round(entry.ear_g_day, 1),
                "rda_g_kg_day": round(entry.rda_g_kg_day, 2),
                "rda_source": "ICMR-NIN 2020" + (" (derived)" if entry.derived else ""),
                "protein_per_serving_g": round(protein, 2),
                "pct_rda_per_serving": round(pct_rda, 1),
                "pct_ear_per_serving": round(pct_ear, 1),
                "servings_for_rda": None if servings is None else round(servings, 2),
                "suggested_target_g_day": (
                    None if entry.suggested_target_g_day is None
                    else round(entry.suggested_target_g_day, 1)
                ),
                "pct_suggested_target_per_serving": suggested_pct,
                "practical_upper_g_day": round(upper, 1),
                "pct_of_practical_upper_per_serving": round(pct_upper, 1),
                "reading": _reading(entry, pct_rda, servings),
                "flags": flags,
                "cautions": cautions,
            }
        )
    return {
        "serving_label": serving_label,
        "protein_per_serving_g": round(protein, 3),
        "energy_per_serving_kcal": (
            None if energy_per_serving_kcal is None else round(float(energy_per_serving_kcal), 1)
        ),
        "sugar_per_serving_g": (
            None if sugar_per_serving_g is None else round(float(sugar_per_serving_g), 2)
        ),
        "sodium_per_serving_mg": (
            None if sodium_per_serving_mg is None else round(float(sodium_per_serving_mg), 1)
        ),
        "protein_energy_pct": protein_energy_pct,
        "protein_energy_pct_range": note["protein_energy_pct_range"],
        "protein_energy_pct_in_range": (
            None if protein_energy_pct is None
            else bool(
                note["protein_energy_pct_range"][0]
                <= protein_energy_pct
                <= note["protein_energy_pct_range"][1]
            )
        ),
        "protein_energy_pct_note": _energy_share_note(
            protein_energy_pct, note["protein_energy_pct_range"]
        ),
        "source": note["source"],
        "disclaimer": note["disclaimer"],
        "groups": rows,
    }


def guide_for_composition(
    composition: Any,
    serving_g: float,
    serving_label: str = "one serving",
    selected: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper: composition + serving size -> full guidance payload."""
    serving = serving_nutrients(composition, serving_g)
    payload = guidance(
        serving["protein_g"],
        serving_label=serving_label,
        energy_per_serving_kcal=serving["energy_kcal"],
        sugar_per_serving_g=serving["sugar_g"],
        sodium_per_serving_mg=serving["sodium_mg"],
        selected=selected,
    )
    payload["serving"] = serving
    return payload


def summary() -> Dict[str, Any]:
    rows = profiles()
    return {
        "groups": len(rows),
        "stages": sorted({p.stage for p in rows}),
        "source": source_note()["source"],
        "protein_energy_pct_range": source_note()["protein_energy_pct_range"],
    }
