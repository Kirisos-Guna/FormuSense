"""An optional second reader for a specification, whose only output is questions.

The parser in :mod:`app.core.brief` is the authority on what a specification says. It
is deterministic, auditable and reproducible, and every number the system works from
comes out of it. It is also a rule-based reader of prose, and prose has a shape of
blindspot: a claim written as "no added sugar" rather than "reduced sugar", an
allergen named as a family, a pack size declared in a unit the category does not use.

A model is genuinely useful at exactly that class of miss. So this module asks for it
under a contract that makes it structurally unable to change the brief:

* it may name a claim or an allergen id that is already in the registry, and nothing
  else, so it cannot invent a claim the system has no rule for;
* it may raise questions, which land in ``Brief.open_questions`` - the list whose
  entire purpose is to record what a human still has to decide before the first trial;
* it may report that the declared pack unit disagrees with the category's, which is
  checkable against what the parser already extracted;
* it may not return a value of any kind - not a target, not a pack size, not a cost
  ceiling. There is no field for one, and :func:`apply_review` refuses the keys that
  are not on its list.

So the brief that comes out has exactly the numbers the parser computed, and better
questions. That is a strictly safer artefact than a brief a model wrote, and it is the
difference between the model being part of the record and being a liability in it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..ai import AiError, AiUnavailable
from ..ai import client as ai_client
from ..ai import prompts as ai_prompts
from ..config import AiSettings, ai_settings
from . import kb
from .types import Brief

#: Keys the review reply may carry. Anything else the model sends is dropped.
REVIEW_KEYS = ai_prompts.SPEC_REVIEW_KEYS

_MAX_QUESTIONS = 6
_MAX_EVIDENCE = 160
_MAX_QUESTION = 220


def _empty(note: str) -> Dict[str, Any]:
    """A review that did not happen, shaped like one that did.

    Every caller renders this, so returning the same shape for "no key", "budget
    spent" and "the provider failed" is what lets the interface show the reason in
    one place instead of three.
    """
    return {
        "reviewed": False,
        "note": note,
        "provider": None,
        "model": None,
        "cached": False,
        "ms": 0,
        "call_id": None,
        "found": {},
    }


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parsed_outline(brief: Brief) -> str:
    """What the parser found, in the terms the review is allowed to talk about.

    The model is shown the parser's reading as well as the raw text, and its
    instruction is to report only what is *missing* from it. Without this it would
    helpfully restate everything, and a review that repeats the parser is noise a
    technologist has to read through.
    """
    lines: List[str] = [
        "product_name: " + str(brief.product_name or ""),
        "category: " + str(brief.category or ""),
        "declared pack: " + str(brief.declared_unit_size if brief.declared_unit_size is not None else "not stated")
        + " " + str(brief.declared_unit or "g"),
        "diet: " + str(brief.diet or ""),
        "claims already applied: " + (", ".join(brief.claims) if brief.claims else "none"),
        "allergens already avoided: " + (", ".join(brief.allergens_to_avoid) if brief.allergens_to_avoid else "none"),
        "cost ceiling (INR per kg): "
        + (str(brief.cost_ceiling_inr_kg) if brief.cost_ceiling_inr_kg is not None else "not stated"),
    ]
    for target in brief.targets:
        lines.append(
            "target " + str(target.id) + ": " + str(target.target) + " " + str(target.unit or "")
            + " (tolerance " + str(target.tolerance) + ")"
        )
    for question in brief.open_questions:
        lines.append("open question already raised: " + str(question))
    return "\n".join(lines)


def _id_evidence_list(value: Any, allowed: Dict[str, Any], already: List[str]) -> List[Dict[str, str]]:
    """Filter a model's suggestions down to ids that exist and are not already known.

    A suggestion is kept only when its id is in the registry, so a model cannot add a
    claim or an allergen the system has no rule for - which is the mechanism that
    makes "the model cannot widen the interface" true rather than aspirational.
    """
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    seen = {str(item) for item in already}
    out: List[Dict[str, str]] = []
    for item in value[: _MAX_QUESTIONS * 2]:
        if isinstance(item, str):
            raw_id, evidence = item, ""
        elif isinstance(item, dict):
            raw_id = item.get("id") or item.get("claim") or item.get("allergen")
            evidence = item.get("evidence") or item.get("reason") or ""
        else:
            continue
        candidate = _clean(raw_id, 60).lower().replace(" ", "_").replace("-", "_")
        if candidate not in allowed or candidate in seen:
            continue
        seen.add(candidate)
        out.append({"id": candidate, "evidence": _clean(evidence, _MAX_EVIDENCE)})
        if len(out) >= _MAX_QUESTIONS:
            break
    return out


def _measure_mismatch(value: Any, brief: Brief) -> Optional[Dict[str, str]]:
    """A declared-unit disagreement, or ``None``.

    Kept to the two units the system actually stores, and only reported when it really
    is a disagreement: the expected unit comes from the category, not from the model.
    """
    if not isinstance(value, dict):
        return None
    expected = _clean(value.get("expected"), 8).lower()
    found = _clean(value.get("found"), 8).lower()
    if expected not in ("g", "ml") or found not in ("g", "ml") or expected == found:
        return None
    if expected != kb.category(brief.category).pack_unit:
        return None
    return {"found": found, "expected": expected, "evidence": _clean(value.get("evidence"), _MAX_EVIDENCE)}


def _accept(text: str, brief: Brief, claims: Dict[str, Any], allergens: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a review this system is willing to act on."""
    parsed = ai_client.parse_json_object(text)
    if not isinstance(parsed, dict):
        return {}
    found: Dict[str, Any] = {}
    missed_claims = _id_evidence_list(parsed.get("claims_missed"), claims, brief.claims)
    if missed_claims:
        found["claims_missed"] = missed_claims
    missed_allergens = _id_evidence_list(parsed.get("allergens_missed"), allergens, brief.allergens_to_avoid)
    if missed_allergens:
        found["allergens_missed"] = missed_allergens
    mismatch = _measure_mismatch(parsed.get("measure_mismatch"), brief)
    if mismatch:
        found["measure_mismatch"] = mismatch
    questions: List[str] = []
    raw_questions = parsed.get("open_questions")
    if isinstance(raw_questions, str):
        raw_questions = [raw_questions]
    if isinstance(raw_questions, list):
        for item in raw_questions[: _MAX_QUESTIONS * 2]:
            question = _clean(item, _MAX_QUESTION)
            if question and question not in questions:
                questions.append(question)
            if len(questions) >= _MAX_QUESTIONS:
                break
    if questions:
        found["open_questions"] = questions
    try:
        found["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        pass
    return found


def review(
    brief: Brief,
    *,
    settings: Optional[AiSettings] = None,
    cache: Any = None,
    product_id: Optional[int] = None,
    transport: Optional[ai_client.Transport] = None,
) -> Dict[str, Any]:
    """Ask a model what the parser missed, and return only questions and suggestions.

    Never raises. A provider that is down, a key that is absent and a budget that is
    spent all produce the same shape with ``reviewed`` false and a ``note`` saying
    which - because this runs in the middle of designing a product, and a design run
    that fails because an optional reader could not be reached would be a worse
    system than one with no reader at all.
    """
    config = settings or ai_settings()
    if not config.configured:
        return _empty("no AI provider key is configured")
    if not str(brief.spec_text or "").strip():
        return _empty("there is no specification text to review")
    budget = ai_client.usage_this_hour(cache, config)
    if budget.exceeded:
        return _empty("the hourly model budget is spent (" + str(budget.used) + "/" + str(budget.cap) + ")")

    claims = {str(rule.get("id")): rule for rule in kb.claim_rules()}
    allergens = kb.allergen_labels()
    prompt = ai_prompts.spec_review_prompt(
        brief.spec_text,
        _parsed_outline(brief),
        ", ".join(sorted(claims)),
        ", ".join(sorted(allergens)),
    )
    try:
        result = ai_client.chat(
            [{"role": "user", "content": prompt}],
            settings=config,
            cache=cache,
            product_id=product_id,
            transport=transport,
            kind="spec-review",
            # The reply is parsed, and a prose reply is indistinguishable from a review
            # that found nothing - which is a legitimate answer. So the format is asked
            # for twice before a review is reported as empty.
            expect_json=True,
        )
    except AiUnavailable as exc:
        return _empty("no AI provider key is configured (" + str(exc) + ")")
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
        "found": _accept(result.text, brief, claims, allergens),
    }


def apply_review(brief: Brief, outcome: Dict[str, Any]) -> List[str]:
    """Fold a review into the brief's open questions - and change nothing else.

    This function is the whole of the model's write access to the brief, and it writes
    to exactly one field. Targets, the pack size, the cost ceiling, the diet and the
    claims already parsed are not reachable from here, which is what makes the
    guarantee checkable: a test can run a brief through a hostile review and assert
    that every number is unchanged.

    The claim and allergen suggestions become *questions*, not claims. A human ticks a
    box in the interface if the suggestion is right, so the specification's claims
    remain something a person decided rather than something a model assumed.
    """
    found = outcome.get("found") or {}
    added: List[str] = []
    for item in found.get("claims_missed") or []:
        evidence = item.get("evidence") or "no quotation given"
        added.append(
            "The specification may also support the "
            + str(item.get("id"))
            + " claim (\""
            + evidence
            + "\"); confirm it before the first trial."
        )
    for item in found.get("allergens_missed") or []:
        evidence = item.get("evidence") or "no quotation given"
        added.append(
            "The specification may also mention "
            + str(item.get("id"))
            + " (\""
            + evidence
            + "\"); confirm whether it must be avoided."
        )
    mismatch = found.get("measure_mismatch")
    if isinstance(mismatch, dict):
        added.append(
            "The pack size is declared as "
            + str(mismatch.get("found"))
            + " where this category is stated in "
            + str(mismatch.get("expected"))
            + "; confirm the declared measure before the first trial."
        )
    for question in found.get("open_questions") or []:
        added.append(str(question))
    if found.get("claims_missed") or found.get("allergens_missed"):
        added.append(
            "Suggestions from the specification review are listed as questions rather "
            "than applied: tick the claims whose substantiation you have evidence for."
        )

    written: List[str] = []
    for text in added:
        if text and text not in brief.open_questions:
            brief.open_questions.append(text)
            written.append(text)
    return written
