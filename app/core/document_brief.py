"""What a document proposes for the New product form: a reading a human confirms.

The parser in :mod:`app.core.brief` is the authority on what a specification says. It
is deterministic, auditable and reproducible, and every number the system works from
comes out of it. So this module does not interpret a document - :mod:`app.core.documents`
already turned it into text, and this runs that same parser over it and turns the
result into the fields the New product form asks for.

That means the feature has a floor and a ceiling, and both are deliberate.

**The floor: it works with no key, no network and no model.** Category, diet, claims,
allergens, the declared pack size and every numeric target are read by the rules that
already read a typed specification. A deployment with no model configured still gets
the whole upload path.

**The ceiling: a model may transcribe, never invent.** When a key is configured and the
run asks for one, the model is shown the document and the parser's reading, and asked
only for what the parser did not find - a product name, a category, a diet, claim ids
that exist in the registry, allergen ids that exist in the registry, and a declared
pack size the rules did not recognise. It is told not to propose a target value or a
property, and there is no field for one. Whatever it does suggest lands in the form,
labelled with where it came from, for a person to confirm before anything is designed.

The specification text handed to the form is the document's own words. That is what
keeps the guarantee checkable: whatever the model says about a document, the numbers
the brief ends up holding are the ones the parser read out of the text on screen.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ai import AiError, AiUnavailable
from ..ai import client as ai_client
from ..ai import prompts as ai_prompts
from ..config import AiSettings, ai_settings
from . import brief as brief_module
from . import kb
from . import kpi as kpi_registry
from .documents import Document

#: The specification text the form receives. The document may be longer; the rest is
#: kept in the review panel rather than pasted into the field.
MAX_SPEC_TEXT = 6000
MAX_NAME = 80
MAX_MISSING = 8
MAX_EVIDENCE = 160
MAX_SUGGESTION = 6
MAX_PACK = 10000.0

DIETS = ("vegetarian", "vegan", "any")

#: Lines that are a document's furniture rather than its product name.
_HEADING_HINTS = (
    "specification",
    "product development",
    "development report",
    "product brief",
    "confidential",
    "internal use",
    "prepared by",
    "version",
    "revision",
    "date:",
    "page ",
    "table of contents",
)


def _clean(value: Any, limit: int = MAX_EVIDENCE) -> str:
    return " ".join(str(value or "").split())[:limit]


def _category_label(category_id: str) -> str:
    if not category_id:
        return ""
    entry = kb.categories().get(category_id)
    return entry.label if entry else category_id


def _claim_label(claim_id: str) -> str:
    for rule in kb.claim_rules():
        if str(rule.get("id")) == claim_id:
            return _clean(rule.get("label") or claim_id, 60)
    return claim_id


def _allergen_label(allergen_id: str) -> str:
    return _clean(kb.allergen_labels().get(allergen_id, allergen_id), 60)


def _found(field: str, value: str, how: str, evidence: str = "") -> Dict[str, str]:
    entry = {"field": field, "value": value, "how": how}
    if evidence:
        entry["evidence"] = evidence
    return entry


# --------------------------------------------------------------------------- #
# The offline reading
# --------------------------------------------------------------------------- #
def _suggested_name(text: str) -> str:
    """The document's own first line, when it reads like a product name.

    A title is what a development report usually opens with, and it is checkable at a
    glance - the person sees it in the name field and can overwrite it. A line that
    carries numbers, or that is document furniture, is skipped rather than guessed at.
    """
    for line in text.splitlines()[:5]:
        candidate = " ".join(line.split()).strip(" .:-*#_")
        if not (3 <= len(candidate) <= 70):
            continue
        lowered = candidate.lower()
        if any(hint in lowered for hint in _HEADING_HINTS):
            continue
        if re.search(r"\d", candidate):
            continue
        if len(candidate.split()) > 8:
            continue
        return candidate
    return ""


def rules_reading(text: str) -> Dict[str, Any]:
    """Everything the rule-based parser can say about a document's text.

    One call per parser, no cleverness on top: if the offline reading has a gap, the
    gap belongs to the parser and the form shows it as a missing field rather than
    this module filling it with a plausible guess.
    """
    category, confidence, hits = brief_module.infer_category(text, fallback="")
    pack = brief_module.declared_pack_size(text)
    numbers = {
        key: value
        for key, value in brief_module.parse_spec_numbers(text).items()
        if not key.startswith("__")
    }
    return {
        "category": category,
        "category_confidence": confidence,
        "category_hits": list(hits),
        "diet": brief_module.infer_diet(text, fallback=""),
        "claims": brief_module.claims_in_text(text),
        "allergens": brief_module.allergens_in_text(text),
        "pack": pack,
        "numbers": numbers,
        "name": _suggested_name(text),
    }


def _missing(reading: Dict[str, Any], fields: Dict[str, Any], pack_from_model: bool) -> List[str]:
    """What the document does not say, in the terms the form uses.

    This is the list a technologist reads first: every KPI the category cares about
    that the document does not state is one they will have to decide before the first
    trial, and naming them is more useful than silently falling back to a default.
    """
    out: List[str] = []
    if fields.get("unit_weight_g") is None:
        out.append("The pack or serving size is not stated.")
    elif pack_from_model:
        out.append(
            "The pack size came from the model's reading, not from the parser's - check "
            "it against the document."
        )
    if not fields.get("category"):
        out.append("The category is not named clearly enough to infer; it is set to the first one.")
    if not fields.get("product_name"):
        out.append("The document does not name the product.")
    # The targets come before the softer gaps: a missing figure is what a technologist
    # has to decide before the first trial, and the list is capped.
    category = str(fields.get("category") or "")
    if category:
        for kpi_id in kpi_registry.kpis_for_category(category):
            if kpi_id in reading["numbers"]:
                continue
            out.append(f"{kpi_registry.kpi_label(kpi_id)} is not stated in the document.")
    if not fields.get("claims"):
        out.append("No claim this system can substantiate is stated in the document.")
    if not fields.get("allergens_to_avoid"):
        out.append("No allergen is declared as avoided, so nothing is excluded on allergen grounds.")
    if len(out) > MAX_MISSING:
        extra = len(out) - MAX_MISSING
        out = out[:MAX_MISSING]
        out.append(f"...and {extra} more that the document does not state.")
    return out


# --------------------------------------------------------------------------- #
# The optional model reading
# --------------------------------------------------------------------------- #
def _empty(note: str) -> Dict[str, Any]:
    """A model reading that did not happen, shaped like one that did."""
    return {
        "reviewed": False,
        "note": note,
        "provider": None,
        "model": None,
        "cached": False,
        "ms": 0,
        "found": {},
    }


def _id_evidence(value: Any, allowed: Sequence[str], already: Sequence[str]) -> List[Dict[str, str]]:
    """Keep only ids that exist in the registry and are not already known."""
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    known = {str(item) for item in already}
    out: List[Dict[str, str]] = []
    for item in list(value)[: MAX_SUGGESTION * 2]:
        if isinstance(item, str):
            raw_id, evidence = item, ""
        elif isinstance(item, dict):
            raw_id = item.get("id") or item.get("claim") or item.get("allergen")
            evidence = item.get("evidence") or item.get("reason") or ""
        else:
            continue
        candidate = _clean(raw_id, 60).lower().replace(" ", "_").replace("-", "_")
        if candidate not in allowed or candidate in known:
            continue
        known.add(candidate)
        out.append({"id": candidate, "evidence": _clean(evidence)})
        if len(out) >= MAX_SUGGESTION:
            break
    return out


def _accept(reply: str, categories: Sequence[str], claims: Sequence[str], allergens: Sequence[str], reading: Dict[str, Any], needs_pack: bool) -> Dict[str, Any]:
    """The part of a document reading this system is willing to put on the form."""
    parsed = ai_client.parse_json_object(reply)
    if not isinstance(parsed, dict):
        return {}
    found: Dict[str, Any] = {}

    name = _clean(parsed.get("product_name"), MAX_NAME)
    if name:
        found["product_name"] = name

    category = _clean(parsed.get("category"), 40).lower().replace(" ", "_").replace("-", "_")
    if category in categories and not reading["category"]:
        found["category"] = category

    diet = _clean(parsed.get("diet"), 20).lower()
    if diet in DIETS and not reading["diet"]:
        found["diet"] = diet

    missed_claims = _id_evidence(parsed.get("claims"), claims, [])
    if missed_claims:
        found["claims"] = missed_claims
    avoided = _id_evidence(parsed.get("allergens_avoided") or parsed.get("allergens"), allergens, [])
    if avoided:
        found["allergens_avoided"] = avoided

    if needs_pack:
        unit = _clean(parsed.get("pack_unit"), 8).lower()
        try:
            size = float(parsed.get("pack_size"))
        except (TypeError, ValueError):
            size = None
        if unit in ("g", "ml") and size is not None and 0 < size <= MAX_PACK:
            found["pack_size"] = size
            found["pack_unit"] = unit
            evidence = _clean(parsed.get("pack_evidence"))
            if evidence:
                found["pack_evidence"] = evidence

    questions: List[str] = []
    raw_missing = parsed.get("missing")
    if isinstance(raw_missing, str):
        raw_missing = [raw_missing]
    if isinstance(raw_missing, (list, tuple)):
        for item in list(raw_missing)[: MAX_SUGGESTION * 2]:
            text = _clean(item, 200)
            if text and text not in questions:
                questions.append(text)
            if len(questions) >= MAX_SUGGESTION:
                break
    if questions:
        found["missing"] = questions
    try:
        found["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        pass
    return found


def _outline(reading: Dict[str, Any], fields: Dict[str, Any]) -> str:
    """The parser's reading, so the model reports what is missing rather than repeating it."""
    lines = [
        "product_name: " + (fields.get("product_name") or "not found"),
        "category: " + (reading["category"] or "not found"),
        "diet: " + (reading["diet"] or "not found"),
        "declared pack: " + (
            f"{reading['pack'][0]:g} {reading['pack'][1]}" if reading["pack"] else "not found"
        ),
        "claims found: " + (", ".join(reading["claims"]) or "none"),
        "allergens avoided: " + (", ".join(reading["allergens"]) or "none"),
        "targets found: " + (", ".join(sorted(reading["numbers"])) or "none"),
    ]
    return "\n".join(lines)


def read_with_model(
    document: Document,
    reading: Dict[str, Any],
    fields: Dict[str, Any],
    *,
    settings: Optional[AiSettings] = None,
    cache: Any = None,
    product_id: Optional[int] = None,
    transport: Optional[ai_client.Transport] = None,
) -> Dict[str, Any]:
    """Ask a model what the parser missed. Never raises, and never writes to a record.

    A provider that is down, a key that is absent and a budget that is spent all return
    the same shape with a note saying which, because an upload that failed because an
    optional reader could not be reached would be worse than no reader at all.
    """
    config = settings or ai_settings()
    if not config.configured:
        return _empty("no model is configured, so the document was read by rule alone")
    budget = ai_client.usage_this_hour(cache, config)
    if budget.exceeded:
        return _empty(f"the hourly model budget is spent ({budget.used}/{budget.cap})")
    categories = sorted(kb.categories())
    claims = sorted({str(rule.get("id")) for rule in kb.claim_rules()})
    allergens = sorted(kb.allergen_labels())
    prompt = ai_prompts.document_prompt(
        document.text,
        ", ".join(categories),
        ", ".join(claims),
        ", ".join(allergens),
        _outline(reading, fields),
    )
    try:
        result = ai_client.chat(
            [
                # The instructions travel as a system message and the document as the
                # user message. Sending only the data leaves the model to guess what was
                # wanted, and a guess parsed as a reading is worse than no reading.
                {"role": "system", "content": ai_prompts.DOCUMENT_PROMPT_TEMPLATE},
                {"role": "user", "content": prompt},
            ],
            settings=config,
            cache=cache,
            product_id=product_id,
            transport=transport,
            kind="document-read",
            expect_json=True,
        )
    except AiUnavailable as exc:
        return _empty("no model is configured (" + str(exc) + ")")
    except AiError as exc:
        outcome = _empty("the model could not be reached: " + str(exc))
        outcome["error"] = str(exc)
        return outcome
    return {
        "reviewed": True,
        "note": "",
        "provider": result.provider,
        "model": result.model,
        "cached": result.cached,
        "ms": result.ms,
        "call_id": result.call_id,
        "found": _accept(
            result.text,
            categories,
            claims,
            allergens,
            reading,
            needs_pack=fields.get("unit_weight_g") is None,
        ),
    }


def _merge(fields: Dict[str, Any], found: List[Dict[str, str]], outcome: Dict[str, Any]) -> bool:
    """Fill only what the rules left empty, and record where each value came from.

    Returns whether a pack size came from the model, because that one is called out in
    the list of things to check: every other field is a name or a label the document
    contains, and this is the only number.
    """
    suggested = outcome.get("found") or {}
    pack_from_model = False

    if not fields.get("product_name") and suggested.get("product_name"):
        fields["product_name"] = suggested["product_name"]
        found.append(_found("Product name", suggested["product_name"], "model", "named in the document"))

    if not fields.get("category") and suggested.get("category"):
        fields["category"] = suggested["category"]
        found.append(
            _found("Category", _category_label(suggested["category"]), "model", "the document's own words")
        )

    if not fields.get("diet") and suggested.get("diet"):
        fields["diet"] = suggested["diet"]
        found.append(_found("Diet", suggested["diet"], "model", "stated in the document"))

    for item in suggested.get("claims") or []:
        claim_id = item["id"]
        if claim_id in fields["claims"]:
            continue
        fields["claims"].append(claim_id)
        found.append(_found("Claim", _claim_label(claim_id), "model", item.get("evidence", "")))

    for item in suggested.get("allergens_avoided") or []:
        allergen_id = item["id"]
        if allergen_id in fields["allergens_to_avoid"]:
            continue
        fields["allergens_to_avoid"].append(allergen_id)
        found.append(
            _found("Avoids", _allergen_label(allergen_id), "model", item.get("evidence", ""))
        )

    if fields.get("unit_weight_g") is None and suggested.get("pack_size"):
        fields["unit_weight_g"] = suggested["pack_size"]
        fields["unit"] = suggested["pack_unit"]
        pack_from_model = True
        found.append(
            _found(
                "Pack size",
                f"{suggested['pack_size']:g} {suggested['pack_unit']}",
                "model",
                suggested.get("pack_evidence", "read from the document"),
            )
        )
    return pack_from_model


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #
def propose(
    document: Document,
    *,
    product_name: str = "",
    use_model: bool = False,
    settings: Optional[AiSettings] = None,
    cache: Any = None,
    product_id: Optional[int] = None,
    transport: Optional[ai_client.Transport] = None,
) -> Dict[str, Any]:
    """Read a document into the fields the New product form asks for.

    The result is a *proposal*: nothing here writes to the record, and the interface
    puts every value in the form for a person to confirm or correct. The specification
    text is the document's own words, capped for the field, so the numbers the parser
    will read are the numbers on screen.
    """
    text = document.text
    reading = rules_reading(text)
    fields: Dict[str, Any] = {
        "product_name": _clean(product_name, MAX_NAME) or reading["name"],
        "spec_text": text[:MAX_SPEC_TEXT].rstrip(),
        "category": reading["category"] or None,
        "diet": reading["diet"] or None,
        "unit_weight_g": reading["pack"][0] if reading["pack"] else None,
        "unit": reading["pack"][1] if reading["pack"] else None,
        "claims": list(reading["claims"]),
        "allergens_to_avoid": list(reading["allergens"]),
    }
    found: List[Dict[str, str]] = []
    if fields["product_name"] and not _clean(product_name, MAX_NAME):
        found.append(_found("Product name", fields["product_name"], "rules", "the first line of the document"))
    if fields["category"]:
        evidence = ", ".join(reading["category_hits"][:3])
        found.append(
            _found(
                "Category",
                _category_label(fields["category"]),
                "rules",
                (f"the words: {evidence}" if evidence else "the document's own words")
                + f" (confidence {reading['category_confidence']:.0%})",
            )
        )
    if fields["diet"]:
        found.append(_found("Diet", fields["diet"], "rules", "stated in the document"))
    if fields["unit_weight_g"] is not None:
        found.append(
            _found(
                "Pack size",
                f"{fields['unit_weight_g']:g} {fields['unit']}",
                "rules",
                "read from the text",
            )
        )
    for claim_id in list(fields["claims"]):
        found.append(_found("Claim", _claim_label(claim_id), "rules", "matched in the document"))
    for allergen_id in list(fields["allergens_to_avoid"]):
        found.append(_found("Avoids", _allergen_label(allergen_id), "rules", "matched in the document"))

    outcome = _empty("the model was not asked for this run")
    pack_from_model = False
    if use_model:
        outcome = read_with_model(
            document,
            reading,
            fields,
            settings=settings,
            cache=cache,
            product_id=product_id,
            transport=transport,
        )
        pack_from_model = _merge(fields, found, outcome)

    missing = _missing(reading, fields, pack_from_model)
    for question in (outcome.get("found") or {}).get("missing") or []:
        if question not in missing:
            missing.append(question)

    notes = list(document.notes)
    if pack_from_model:
        notes.append(
            "The parser did not recognise a pack size in the document; the one in the "
            "form was read by the model and has not been checked by a rule."
        )
    return {
        "document": document.as_dict(),
        "fields": fields,
        "found": found,
        "missing": missing[:MAX_MISSING],
        "notes": notes,
        "model": outcome,
        "confidence": (outcome.get("found") or {}).get("confidence") or reading["category_confidence"],
    }
