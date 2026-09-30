"""Tests for the specification reviewer, which may only ask questions.

The reviewer exists because a rule-based parser misses things in prose. The point of
these tests is not that the suggestions are good - a fake transport cannot tell us
that - but that whatever the model says, the brief's numbers come out unchanged. That
is the property the whole design rests on, so it is asserted directly, with a reply
that tries to change them.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import ai_settings
from app.core import brief as brief_module
from app.core import kb, spec_review

OPENROUTER = {"OPENROUTER_API_KEY": "sk-or-test-abcdefghij"}

SPEC = (
    "High protein high fibre ragi biscuit for a health-conscious range. 40 g pack. "
    "Protein 12 g per 100 g and dietary fibre 7 g per 100 g. Moisture 3%. "
    "Contains tree nuts and soya. Ingredient cost target INR 240 per kg."
)


def payload(**overrides):
    base = {
        "product_name": "High-protein ragi cookie",
        "category": "cookie",
        "spec_text": SPEC,
        "diet": "vegetarian",
        "claims": ["high_protein"],
        "allergens_to_avoid": ["tree_nuts"],
        "unit_weight_g": 40,
    }
    base.update(overrides)
    return base


def configured(keys=None):
    """Settings with a key, injected rather than read from the environment.

    Passing the settings in keeps every test in this file deterministic: none of them
    depends on what happens to be exported on the machine running the suite.
    """
    return ai_settings({"keys": {"openrouter": "sk-or-test-abcdefghij"} if keys is None else keys})


def review_of(reply, brief, settings=None):
    """Run a review whose reply is fixed, through a transport that never dials out."""

    def transport(url, headers, body, timeout):
        text = json.dumps(reply) if not isinstance(reply, str) else reply
        return json.dumps({"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": text}}]}).encode("utf-8")

    return spec_review.review(brief, settings=settings or configured(), transport=transport)


class GuaranteeTests(unittest.TestCase):
    """The claim the design makes: the model cannot change the brief."""

    HOSTILE = {
        "claims_missed": [{"id": "reduced_sugar", "evidence": "no added sugar"}],
        "allergens_missed": [{"id": "soy", "evidence": "soya"}],
        "measure_mismatch": {"found": "ml", "expected": "g", "evidence": "40 g pack"},
        "open_questions": ["What is the target water activity?"],
        "confidence": 0.6,
        "targets": [{"id": "protein_g", "target": 99}],
        "unit_weight_g": 500,
        "cost_ceiling_inr_kg": 12,
        "declared_unit": "ml",
        "protein_g": 30,
    }

    def test_a_hostile_review_changes_nothing_but_the_open_questions(self) -> None:
        brief = brief_module.build_brief(payload())
        before = copy.deepcopy(brief.as_dict())
        outcome = review_of(self.HOSTILE, brief)
        written = spec_review.apply_review(brief, outcome)
        after = brief.as_dict()

        self.assertTrue(written)
        for key in (
            "targets",
            "unit_weight_g",
            "declared_unit",
            "declared_unit_size",
            "cost_ceiling_inr_kg",
            "claims",
            "diet",
            "category",
            "allergens_to_avoid",
            "spec_text",
            "product_name",
        ):
            self.assertEqual(before[key], after[key], f"the review changed {key}")
        self.assertEqual(
            [key for key in after if after[key] != before[key]], ["open_questions"]
        )

    def test_a_suggested_claim_is_a_question_and_not_a_claim(self) -> None:
        brief = brief_module.build_brief(payload())
        outcome = review_of(self.HOSTILE, brief)
        spec_review.apply_review(brief, outcome)
        self.assertNotIn("reduced_sugar", brief.claims)
        self.assertTrue(any("reduced_sugar" in question for question in brief.open_questions))

    def test_a_suggested_allergen_is_a_question_and_not_an_avoidance(self) -> None:
        brief = brief_module.build_brief(payload())
        outcome = review_of(self.HOSTILE, brief)
        spec_review.apply_review(brief, outcome)
        self.assertEqual(brief.allergens_to_avoid, ["tree_nuts"])
        self.assertTrue(any("soy" in question for question in brief.open_questions))

    def test_applying_the_same_review_twice_does_not_duplicate_the_questions(self) -> None:
        brief = brief_module.build_brief(payload())
        outcome = review_of(self.HOSTILE, brief)
        first = spec_review.apply_review(brief, outcome)
        second = spec_review.apply_review(brief, outcome)
        self.assertTrue(first)
        self.assertEqual(second, [])

    def test_a_claim_id_the_system_does_not_know_is_dropped(self) -> None:
        # Keeping it would mean a claim on the brief that no rule can substantiate.
        reply = {"claims_missed": [{"id": "keto_friendly", "evidence": "no carbs"}]}
        brief = brief_module.build_brief(payload())
        found = review_of(reply, brief)["found"]
        self.assertNotIn("claims_missed", found)

    def test_a_claim_already_on_the_brief_is_not_suggested_again(self) -> None:
        reply = {"claims_missed": [{"id": "high_protein", "evidence": "Protein 12 g"}]}
        brief = brief_module.build_brief(payload())
        self.assertNotIn("claims_missed", review_of(reply, brief)["found"])

    def test_the_claim_and_allergen_names_are_normalised(self) -> None:
        # Ids arrive as whatever the model felt like writing, so they are folded to the
        # registry's spelling before they are matched against it.
        reply = {
            "claims_missed": [{"id": "Low Sodium", "evidence": "reduced salt"}],
            "allergens_missed": [{"id": "Tree Nuts", "evidence": "hazelnut paste"}],
        }
        brief = brief_module.build_brief(payload(claims=[], allergens_to_avoid=[]))
        found = review_of(reply, brief)["found"]
        self.assertEqual(found["claims_missed"][0]["id"], "low_sodium")
        self.assertEqual(found["allergens_missed"][0]["id"], "tree_nuts")

    def test_a_measure_mismatch_is_only_reported_when_it_really_disagrees(self) -> None:
        brief = brief_module.build_brief(payload())
        # Agreeing with the category is not a mismatch, whatever the model thinks.
        agree = {"measure_mismatch": {"found": "g", "expected": "g", "evidence": "40 g pack"}}
        self.assertNotIn("measure_mismatch", review_of(agree, brief)["found"])
        # Nor is a unit this system does not store.
        odd = {"measure_mismatch": {"found": "oz", "expected": "lb", "evidence": "x"}}
        self.assertNotIn("measure_mismatch", review_of(odd, brief)["found"])
        real = {"measure_mismatch": {"found": "ml", "expected": "g", "evidence": "40 g pack"}}
        self.assertEqual(review_of(real, brief)["found"]["measure_mismatch"]["found"], "ml")

    def test_a_drink_is_expected_in_millilitres_so_the_same_flag_flips(self) -> None:
        beverage = brief_module.build_brief(
            {
                "product_name": "Whey drink",
                "category": "beverage",
                "spec_text": "High protein whey beverage. 200 ml bottle. Protein 10 g per 100 ml.",
                "diet": "vegetarian",
                "claims": ["high_protein"],
                "unit_weight_g": 200,
            }
        )
        reply = {"measure_mismatch": {"found": "g", "expected": "ml", "evidence": "200 ml bottle"}}
        found = review_of(reply, beverage)["found"]
        self.assertEqual(found["measure_mismatch"]["expected"], "ml")


class BehaviourTests(unittest.TestCase):
    """Every way of not reviewing, and the one way of reviewing."""

    def setUp(self) -> None:
        from app.store import Store

        self.directory = Path(tempfile.mkdtemp(prefix="formusense-review-"))
        self.store = Store(path=self.directory / "cache.db")

    def tearDown(self) -> None:
        self.store.close()

    def test_with_no_key_the_review_is_skipped_with_a_reason(self) -> None:
        brief = brief_module.build_brief(payload())
        outcome = spec_review.review(brief, settings=configured(keys={}), cache=self.store)
        self.assertFalse(outcome["reviewed"])
        self.assertIn("no AI provider key", outcome["note"])
        self.assertEqual(spec_review.apply_review(brief, outcome), [])

    def test_there_is_nothing_to_review_without_a_specification(self) -> None:
        brief = brief_module.build_brief(payload(spec_text=""))
        outcome = spec_review.review(brief, settings=configured(), cache=self.store)
        self.assertFalse(outcome["reviewed"])
        self.assertIn("no specification text", outcome["note"])

    def test_the_hourly_ceiling_skips_the_review_rather_than_failing_the_run(self) -> None:
        brief = brief_module.build_brief(payload())
        self.store.save_ai_call(None, "spec-review", "openrouter", "m", "row", "text", cached=False)
        capped = ai_settings({"keys": {"openrouter": "k"}, "max_calls_per_hour": 1})
        outcome = spec_review.review(brief, settings=capped, cache=self.store)
        self.assertFalse(outcome["reviewed"])
        self.assertIn("budget is spent", outcome["note"])

    def test_a_provider_that_cannot_be_reached_is_a_note_not_an_exception(self) -> None:
        brief = brief_module.build_brief(payload())

        def broken(url, headers, body, timeout):
            raise RuntimeError("timed out")

        outcome = spec_review.review(brief, settings=configured(), cache=self.store, transport=broken)
        self.assertFalse(outcome["reviewed"])
        self.assertIn("could not be reached", outcome["note"])
        self.assertIn("timed out", outcome["error"])

    def test_a_reply_that_is_not_json_is_a_review_that_found_nothing(self) -> None:
        brief = brief_module.build_brief(payload())
        outcome = review_of("I could not read that specification.", brief)
        self.assertTrue(outcome["reviewed"])
        self.assertEqual(outcome["found"], {})
        self.assertEqual(spec_review.apply_review(brief, outcome), [])

    def test_a_review_records_who_performed_it(self) -> None:
        brief = brief_module.build_brief(payload())
        outcome = review_of('{"open_questions": ["Is the fibre target soluble or total?"]}', brief)
        self.assertTrue(outcome["reviewed"])
        self.assertEqual(outcome["provider"], "openrouter")
        self.assertEqual(outcome["model"], "openai/gpt-4o-mini")
        self.assertFalse(outcome["cached"])

    def test_the_review_is_told_what_the_parser_already_found(self) -> None:
        # Without this the model restates the parser, and a review that repeats what is
        # already there is noise a technologist has to read through.
        seen = {}

        def transport(url, headers, body, timeout):
            seen["body"] = json.loads(body.decode("utf-8"))
            return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")

        brief = brief_module.build_brief(payload())
        spec_review.review(brief, settings=configured(), cache=self.store, transport=transport)
        prompt = seen["body"]["messages"][-1]["content"]
        self.assertIn("SPECIFICATION TEXT", prompt)
        self.assertIn("PARSER READING", prompt)
        self.assertIn("target protein_g", prompt)
        self.assertIn("high_protein", prompt)
        self.assertIn("CLAIM IDS AVAILABLE", prompt)
        self.assertIn("ALLERGEN IDS AVAILABLE", prompt)

    def test_the_review_is_told_what_to_return(self) -> None:
        # The data message names the sections; only the instruction says which keys to
        # answer with and that a value is not among them. It travels as a system message,
        # and without it the model is left to invent a shape and the parser finds nothing.
        seen = {}

        def transport(url, headers, body, timeout):
            seen["body"] = json.loads(body.decode("utf-8"))
            return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")

        brief = brief_module.build_brief(payload())
        spec_review.review(brief, settings=configured(), cache=self.store, transport=transport)
        messages = seen["body"]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        instructions = messages[0]["content"]
        self.assertIn("claims_missed", instructions)
        self.assertIn("open_questions", instructions)
        self.assertIn("Do not restate what the parser already found", instructions)
        self.assertIn("single JSON object", instructions)

    def test_the_question_limit_is_enforced_however_many_arrive(self) -> None:
        reply = {"open_questions": ["Question %d?" % index for index in range(40)]}
        brief = brief_module.build_brief(payload())
        found = review_of(reply, brief)["found"]
        self.assertLessEqual(len(found["open_questions"]), 6)


if __name__ == "__main__":
    unittest.main()
