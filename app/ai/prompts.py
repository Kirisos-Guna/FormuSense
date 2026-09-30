"""The prompts, in one place, because what the agent asks a model is reviewable.

Three rules run through every prompt here.

**JSON on a fixed schema.** Each task names the keys it wants and forbids anything
else. A caller then validates the reply against those keys rather than trusting it,
so a model that improvises cannot widen the interface between it and the code.

**The keys are almost never numbers.** The vision prompt describes appearance; the
review prompt asks which claims a specification supports; the document prompt
transcribes a name, a category, a diet and registry ids out of a document the team
already wrote; the record prompt answers from what is already stored. None of them may
write a value that a model produces, because a number from a language model is
indistinguishable from a measurement once it is in the record - and this system's
entire claim to credibility is that its numbers are reproducible and traceable to a
method.

The exception proves the rule. ``DOCUMENT_KEYS`` allows exactly one number, a *declared*
pack size, and only when the rule-based parser found none in the same text; it is
returned with the words it was read from, it is labelled in the interface as read by a
model rather than parsed by a rule, and it reaches the record only if the person
prefills-and-confirms it. Every other figure the brief holds is still read by the
parser out of the specification text on screen.

**The answer is bounded by what it was given.** The record prompt is told that the
context it receives is the whole of what exists, so "the record does not say" is the
correct answer to a question the record cannot answer, rather than a plausible
invention.
"""
from __future__ import annotations

#: Appended to every task. The no-markdown-fence rule is not cosmetic: several
#: providers otherwise wrap the object in prose, and the parser would then be
#: guessing where the JSON starts.
JSON_RULE = (
    "Respond with a single JSON object and nothing else - no prose before or after it, "
    "no markdown code fence. Use exactly the keys named above, and omit any you cannot "
    "answer from what you were given. Set confidence honestly: a low number is more "
    "useful than a confident guess."
)


def compose(task: str) -> str:
    """The instruction the model actually receives: the task, then the contract."""
    return task.strip() + "\n\n" + JSON_RULE


# --------------------------------------------------------------------------- #
# Image understanding
# --------------------------------------------------------------------------- #
#: The keys the vision reply is allowed to contain. Anything else is dropped, and a
#: key that names a composition value is dropped deliberately: the offline Pillow
#: path in ``app.core.vision`` measures appearance, and the models in ``app.core``
#: predict composition. A vision model sits between them as a description, not as a
#: third source of facts.
VISION_KEYS = (
    "product_form",
    "surface_finish",
    "dominant_colour",
    "visible_inclusions",
    "shape_and_size_notes",
    "apparent_defects",
    "process_hypothesis",
    "confidence",
)

VISION_PROMPT = compose(
    """
You are a food product development scientist looking at a reference photograph of a
product that has to be reverse-engineered. Describe only what the image supports.

Keys:
- product_form: short phrase, e.g. "round rotary-moulded biscuit".
- surface_finish: short phrase, e.g. "matte, lightly dusted".
- dominant_colour: short phrase.
- visible_inclusions: list of short strings; empty list if none are visible.
- shape_and_size_notes: one sentence on shape, relative size and any scale cue. Do
  not state a weight, a volume or a dimension in millimetres: the image has no scale
  bar, so any figure would be invented.
- apparent_defects: list of short strings; empty list if the product looks sound.
- process_hypothesis: one sentence, e.g. "baked rotary-moulded biscuit, wire-cut".
- confidence: a number between 0 and 1 for the description as a whole.
"""
)


# --------------------------------------------------------------------------- #
# Specification review
# --------------------------------------------------------------------------- #
#: The keys the review reply may contain. All of them are advisory: the caller folds
#: the questions into the brief's open list and shows the claims as unticked
#: suggestions. Nothing here can change a target, a pack size or a claim that is
#: already on the brief.
SPEC_REVIEW_KEYS = (
    "claims_missed",
    "allergens_missed",
    "measure_mismatch",
    "open_questions",
    "confidence",
)

SPEC_REVIEW_PROMPT_TEMPLATE = compose(
    """
You are reviewing a food product specification for a formulation team. A rule-based
parser has already read the specification that follows, and its reading is shown to
you. Your job is to find only what the parser appears to have missed.

Keys:
- claims_missed: list of objects with "id" and "evidence". "id" must be one of the
  claim ids listed in the request and nothing else; "evidence" is the words from the
  specification that support it.
- allergens_missed: list of objects with "id" and "evidence", using only the allergen
  ids listed in the request.
- measure_mismatch: either null, or an object with "found", "expected" and "evidence",
  when the specification declares a pack size in a different unit from the one the
  category is stated in.
- open_questions: list of short strings. Each must be a question a food technologist
  would have to answer before the first trial, not a restatement of the text.
- confidence: a number between 0 and 1.

Do not restate what the parser already found, and do not propose a target value,
a pack size or a cost ceiling. If the parser's reading is complete, return empty
lists and null.
"""
)


def spec_review_prompt(spec_text: str, parsed: str, claim_ids: str, allergen_ids: str) -> str:
    """The review request: the raw text, then the parser's reading of it."""
    return (
        "SPECIFICATION TEXT" + "\n"
        + spec_text.strip()
        + "\n\nPARSER READING\n"
        + parsed.strip()
        + "\n\nCLAIM IDS AVAILABLE\n"
        + claim_ids.strip()
        + "\n\nALLERGEN IDS AVAILABLE\n"
        + allergen_ids.strip()
    )


# --------------------------------------------------------------------------- #
# Reading an uploaded document
# --------------------------------------------------------------------------- #
#: The keys a document reading may contain. This prompt is a *transcription* task, and
#: the rule is kept honest by what the keys allow: a name, a category, a diet, ids that
#: already exist in the claim and allergen registries, the words a declared pack size
#: was read from, and a list of what the document does not say. There is no key for a
#: target, a property or an ingredient, because the parser reads the specification text
#: itself and every number in the brief has to come from there.
DOCUMENT_KEYS = (
    "product_name",
    "category",
    "diet",
    "claims",
    "allergens_avoided",
    "pack_size",
    "pack_unit",
    "pack_evidence",
    "missing",
    "confidence",
)

DOCUMENT_PROMPT_TEMPLATE = compose(
    """
An R&D team sent the document below instead of filling in a form. A rule-based parser
has already read it, and its reading is shown to you. Report only what the parser did
not find, quoting the document's own words as evidence.

Keys:
- product_name: the product's name, as the document writes it, if the document names one.
- category: exactly one id from the category ids listed in the request, or omit it.
- diet: one of "vegetarian", "vegan" or "any", only if the document states it.
- claims: list of objects with "id" and "evidence". "id" must be one of the claim ids
  listed in the request; "evidence" is the words from the document that support it.
- allergens_avoided: list of objects with "id" and "evidence", using only the allergen
  ids listed in the request.
- pack_size: a number, only if the document states the pack or serving size and the
  parser's reading does not already show one. pack_unit: "g" or "ml", matching it.
- pack_evidence: the exact words the pack size was read from.
- missing: list of short strings - the facts a formulation team needs that this
  document does not state at all. Notes for a human, not guesses.
- confidence: a number between 0 and 1 for the reading as a whole.

Do not propose a target value, a measured property (protein, sugar, moisture, cost) or
an ingredient: the parser reads the specification text and the models predict the
properties. If the document does not state something, put it in "missing" instead of
filling it in.
"""
)


def document_prompt(
    text: str, category_ids: str, claim_ids: str, allergen_ids: str, parsed: str
) -> str:
    """The document, then the parser's reading of it, then the vocabularies it may use."""
    return (
        "DOCUMENT TEXT" + "\n"
        + text.strip()
        + "\n\nPARSER READING\n"
        + parsed.strip()
        + "\n\nCATEGORY IDS AVAILABLE\n"
        + category_ids.strip()
        + "\n\nCLAIM IDS AVAILABLE\n"
        + claim_ids.strip()
        + "\n\nALLERGEN IDS AVAILABLE\n"
        + allergen_ids.strip()
    )


# --------------------------------------------------------------------------- #
# Questions about the record
# --------------------------------------------------------------------------- #
ASK_SYSTEM_PROMPT = compose(
    """
You are the assistant inside a food product development record. You will be given an
extract of one product's record - its brief, its current formulation, the prediction
on file, its trials and their residuals, and any open reformulation plan - followed
by a question.

Answer only from the extract. It is the whole of what exists: there is no other
context for you to draw on, and nothing to look up.

Keys:
- answer: the answer, in at most six sentences, written for a food technologist.
  Quote the numbers from the extract rather than describing them.
- citations: list of short strings naming the parts of the record you used, chosen
  from the section names in the extract, e.g. "formulation", "target: protein",
  "trial 2 residuals", "diagnosis", "plan".
- found: true when the extract contains what was asked, false when it does not.
- confidence: a number between 0 and 1.

If the extract does not contain the answer, say so plainly in "answer", set "found"
to false, and return an empty "citations" list. Never estimate, average, interpolate
or infer a value that is not written in the extract.
"""
)


def ask_user_prompt(context: str, question: str) -> str:
    """The record extract, then the question. Context first, so the question is last."""
    return (
        "PRODUCT RECORD EXTRACT" + "\n" + context.strip()
        + "\n\nQUESTION\n" + question.strip()
    )
