"""Tests for turning a document into proposed form fields.

Two properties matter here, and both are asserted directly.

**The rules come first.** With no key at all - which is how the deployment runs today -
the category, diet, claims, allergens and pack size are read out of the document by the
same parser that reads a typed specification.

**A model may transcribe, not invent.** It fills only what the rules left blank, its
category, diet and ids are checked against the registries the system already has, an
unknown claim id is dropped, and the one number it may return - a declared pack size -
is taken only when the parser found none and is labelled as read by a model. The
specification text handed to the form is always the document's own words, so the
figures the brief will hold are the ones the parser reads off the screen.
"""
from __future__ import annotations

import json
import unittest

from app.config import ai_settings
from app.core import document_brief, documents

SPEC = (
    "High protein ragi cookie\n"
    "Specification: protein 15 g per 100 g, sugar not more than 12 g per 100 g, "
    "moisture 3.5 %, shelf life 180 days, cost not more than INR 240 per kg. 40 g pack.\n"
    "Gluten free and no soy. Vegetarian. High protein claim.\n"
)


def document(text: str, name: str = "spec.txt"):
    return documents.extract(name, text.encode("utf-8"))


def configured(keys=None):
    """Settings with a key injected, so no test depends on the machine's environment."""
    return ai_settings({"keys": {"openrouter": "sk-or-test-abcdefghij"} if keys is None else keys})


def transport_for(reply, seen=None):
    """A transport that answers with a fixed reply and never dials out."""

    def transport(url, headers, body, timeout):
        if seen is not None:
            seen.append(json.loads(body.decode("utf-8")))
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return json.dumps(
            {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": text}}]}
        ).encode("utf-8")

    return transport


class RulesOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = document_brief.propose(document(SPEC))

    def test_the_form_is_filled_without_any_model(self) -> None:
        fields = self.proposal["fields"]
        self.assertEqual(fields["product_name"], "High protein ragi cookie")
        self.assertEqual(fields["category"], "cookie")
        self.assertEqual(fields["diet"], "vegetarian")
        self.assertAlmostEqual(fields["unit_weight_g"], 40.0)
        self.assertEqual(fields["unit"], "g")
        self.assertIn("high_protein", fields["claims"])
        self.assertIn("gluten", fields["allergens_to_avoid"])
        self.assertIn("soy", fields["allergens_to_avoid"])

    def test_the_specification_text_is_the_document_s_own_words(self) -> None:
        self.assertEqual(self.proposal["fields"]["spec_text"], document(SPEC).text)

    def test_every_filled_value_says_it_came_from_a_rule(self) -> None:
        self.assertTrue(self.proposal["found"])
        for item in self.proposal["found"]:
            self.assertEqual(item["how"], "rules")
            self.assertTrue(item["field"])
            self.assertTrue(item["value"])
        labelled = {item["field"] for item in self.proposal["found"]}
        self.assertIn("Category", labelled)
        self.assertIn("Pack size", labelled)

    def test_the_model_note_says_it_was_not_asked(self) -> None:
        self.assertFalse(self.proposal["model"]["reviewed"])
        self.assertIn("not asked", self.proposal["model"]["note"])

    def test_the_gaps_are_named_for_the_person_filling_the_form(self) -> None:
        missing = " ".join(self.proposal["missing"])
        self.assertIn("not stated", missing)
        self.assertIn("Sodium", missing)
        self.assertLessEqual(len(self.proposal["missing"]), document_brief.MAX_MISSING)

    def test_a_document_that_says_almost_nothing_reports_that(self) -> None:
        proposal = document_brief.propose(document("Some notes about a project.\n"))
        self.assertIsNone(proposal["fields"]["category"])
        self.assertIsNone(proposal["fields"]["unit_weight_g"])
        missing = " ".join(proposal["missing"])
        self.assertIn("pack or serving size is not stated", missing)
        self.assertIn("category is not named", missing)

    def test_the_product_name_skips_a_title_block(self) -> None:
        text = (
            "Product Development Report\n"
            "Version 3 dated 12 May 2026\n"
            "High protein ragi cookie\n"
            "Protein 15 g per 100 g\n"
        )
        proposal = document_brief.propose(document(text))
        self.assertEqual(proposal["fields"]["product_name"], "High protein ragi cookie")

    def test_a_name_the_caller_already_has_wins(self) -> None:
        proposal = document_brief.propose(document(SPEC), product_name="Ragi cookie v2")
        self.assertEqual(proposal["fields"]["product_name"], "Ragi cookie v2")
        self.assertNotIn("Product name", {item["field"] for item in proposal["found"]})

    def test_the_specification_text_is_capped_for_the_field(self) -> None:
        long_text = SPEC + ("More specification prose. " * 600)
        proposal = document_brief.propose(document(long_text))
        self.assertEqual(len(proposal["fields"]["spec_text"]), document_brief.MAX_SPEC_TEXT)


class ModelTests(unittest.TestCase):
    def test_a_model_may_not_change_what_the_rules_already_read(self) -> None:
        reply = {
            "product_name": "Something else entirely",
            "category": "beverage",
            "diet": "vegan",
            "claims": [{"id": "low_sugar", "evidence": "sugar not more than 12 g"}],
            "missing": ["Shelf life is not stated."],
            "confidence": 0.6,
        }
        proposal = document_brief.propose(
            document(SPEC),
            use_model=True,
            settings=configured(),
            transport=transport_for(reply),
        )
        fields = proposal["fields"]
        self.assertEqual(fields["product_name"], "High protein ragi cookie")
        self.assertEqual(fields["category"], "cookie")
        self.assertEqual(fields["diet"], "vegetarian")
        self.assertAlmostEqual(fields["unit_weight_g"], 40.0)
        # A claim the rules did not find is added, and it is labelled as the model's.
        self.assertIn("low_sugar", fields["claims"])
        added = [item for item in proposal["found"] if item["how"] == "model"]
        self.assertEqual([item["field"] for item in added], ["Claim"])
        self.assertIn("Shelf life is not stated.", proposal["model"]["found"]["missing"])

    def test_an_unknown_id_is_dropped_rather_than_added(self) -> None:
        reply = {
            "claims": [{"id": "quantum_protein", "evidence": "nonsense"}],
            "allergens_avoided": [{"id": "lunar_dust", "evidence": "nonsense"}],
        }
        proposal = document_brief.propose(
            document("A cookie. 40 g pack. Protein 15 g per 100 g.\n"),
            use_model=True,
            settings=configured(),
            transport=transport_for(reply),
        )
        self.assertNotIn("quantum_protein", proposal["fields"]["claims"])
        self.assertNotIn("lunar_dust", proposal["fields"]["allergens_to_avoid"])

    def test_a_diet_outside_the_three_the_form_offers_is_ignored(self) -> None:
        reply = {"diet": "pescatarian"}
        proposal = document_brief.propose(
            document("A cookie. 40 g pack.\n"),
            use_model=True,
            settings=configured(),
            transport=transport_for(reply),
        )
        self.assertIsNone(proposal["fields"]["diet"])

    def test_a_pack_size_the_parser_missed_is_taken_and_labelled(self) -> None:
        text = "High protein ragi cookie\nProtein 15 g per 100 g. Supplied in a retail carton.\n"
        reply = {
            "pack_size": 40,
            "pack_unit": "g",
            "pack_evidence": "retail carton of 40 g",
            "confidence": 0.5,
        }
        proposal = document_brief.propose(
            document(text),
            use_model=True,
            settings=configured(),
            transport=transport_for(reply),
        )
        self.assertAlmostEqual(proposal["fields"]["unit_weight_g"], 40.0)
        pack = [item for item in proposal["found"] if item["field"] == "Pack size"]
        self.assertEqual(pack[0]["how"], "model")
        self.assertIn("retail carton of 40 g", pack[0]["evidence"])
        self.assertTrue(any("has not been checked by a rule" in note for note in proposal["notes"]))
        self.assertTrue(any("came from the model" in item for item in proposal["missing"]))

    def test_a_pack_size_the_parser_found_is_not_replaced(self) -> None:
        reply = {"pack_size": 999, "pack_unit": "g", "confidence": 0.9}
        proposal = document_brief.propose(
            document(SPEC),
            use_model=True,
            settings=configured(),
            transport=transport_for(reply),
        )
        self.assertAlmostEqual(proposal["fields"]["unit_weight_g"], 40.0)
        self.assertFalse(any(item["how"] == "model" and item["field"] == "Pack size" for item in proposal["found"]))

    def test_an_implausible_pack_size_is_ignored(self) -> None:
        for reply in (
            {"pack_size": 40, "pack_unit": "cups"},
            {"pack_size": -5, "pack_unit": "g"},
            {"pack_size": 99999, "pack_unit": "g"},
            {"pack_size": "about forty", "pack_unit": "g"},
        ):
            proposal = document_brief.propose(
                document("A cookie with no pack size stated.\n"),
                use_model=True,
                settings=configured(),
                transport=transport_for(reply),
            )
            self.assertIsNone(proposal["fields"]["unit_weight_g"], reply)

    def test_without_a_key_the_form_still_fills_and_says_why(self) -> None:
        proposal = document_brief.propose(
            document(SPEC), use_model=True, settings=configured(keys={}), transport=transport_for({})
        )
        self.assertFalse(proposal["model"]["reviewed"])
        self.assertIn("no model", proposal["model"]["note"])
        self.assertEqual(proposal["fields"]["category"], "cookie")

    def test_a_broken_provider_does_not_fail_the_upload(self) -> None:
        def transport(url, headers, body, timeout):
            raise OSError("no route to host")

        proposal = document_brief.propose(
            document(SPEC), use_model=True, settings=configured(), transport=transport
        )
        self.assertFalse(proposal["model"]["reviewed"])
        self.assertIn("could not be reached", proposal["model"]["note"])
        self.assertEqual(proposal["fields"]["category"], "cookie")

    def test_a_reply_that_is_not_json_is_treated_as_no_reading(self) -> None:
        proposal = document_brief.propose(
            document(SPEC),
            use_model=True,
            settings=configured(),
            transport=transport_for("I am afraid I cannot help with that."),
        )
        self.assertTrue(proposal["model"]["reviewed"])
        self.assertEqual(proposal["model"]["found"], {})

    def test_the_prompt_carries_the_document_the_reading_and_the_registries(self) -> None:
        seen = []
        document_brief.propose(
            document(SPEC),
            use_model=True,
            settings=configured(),
            transport=transport_for({"confidence": 0.4}, seen=seen),
        )
        self.assertEqual(len(seen), 1)
        messages = seen[0]["messages"]
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        # The instructions say what to answer with; the data message carries the document,
        # the parser's reading and the only ids the reply is allowed to name.
        instructions = messages[0]["content"]
        self.assertIn("pack_evidence", instructions)
        self.assertIn("Do not propose a target value", instructions)
        self.assertIn("single JSON object", instructions)
        prompt = messages[1]["content"]
        self.assertIn("High protein ragi cookie", prompt)
        self.assertIn("PARSER READING", prompt)
        self.assertIn("category: cookie", prompt)
        self.assertIn("targets found: cost_inr_kg", prompt)
        self.assertIn("CLAIM IDS AVAILABLE", prompt)
        self.assertIn("ALLERGEN IDS AVAILABLE", prompt)

    def test_a_reply_with_findings_is_parsed_rather_than_trusted(self) -> None:
        proposal = document_brief.propose(
            document(SPEC),
            use_model=True,
            settings=configured(),
            transport=transport_for(
                {"category": "cookie", "claims": "not-a-list", "missing": "Shelf life.", "confidence": "high"}
            ),
        )
        self.assertEqual(proposal["model"]["found"]["missing"], ["Shelf life."])
        self.assertNotIn("claims", proposal["model"]["found"])
        self.assertNotIn("confidence", proposal["model"]["found"])


if __name__ == "__main__":
    unittest.main()
