"""Tests for the ask-the-record chat, whose only job is to read the record back.

The feature is a chat window, which is exactly the sort of thing that grows a second
write path if nobody says otherwise. So the tests come in two halves: that an answer
is assembled and cited, and that asking a question changes nothing about the product.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import ai_settings
from app.service import AgentService
from app.store import Store

SPEC = (
    "High protein ragi biscuit for a health-conscious range. 40 g pack. "
    "Protein 12 g per 100 g and dietary fibre 7 g per 100 g. Moisture 3%. "
    "Contains tree nuts. Ingredient cost target INR 240 per kg."
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


def answer_with(reply, *, seen=None):
    """A transport that returns a fixed answer and records what it was asked."""

    def transport(url, headers, body, timeout):
        if seen is not None:
            seen["url"] = url
            seen["headers"] = headers
            seen["body"] = json.loads(body.decode("utf-8"))
        text = json.dumps(reply) if not isinstance(reply, str) else reply
        return json.dumps(
            {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": text}}]}
        ).encode("utf-8")

    return transport


class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="formusense-ask-"))
        self.service = AgentService(Store(self.directory / "test.db"))
        self.product_id = self.service.create_product(payload())["product_id"]

    def tearDown(self) -> None:
        self.service.store.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def settings(self, **overrides):
        return ai_settings({"keys": {"openrouter": "sk-or-test-abcdefghij"}, **overrides})

    def ask(self, question, *, use_model=True, transport=None, config=None):
        chosen = config or self.settings()
        with mock.patch("app.config.ai_settings", return_value=chosen), mock.patch(
            "app.service.ai_settings", return_value=chosen
        ), mock.patch("app.ai.client._ai_settings", return_value=chosen):
            return self.service.ask_record(
                self.product_id, question, use_model=use_model, transport=transport
            )

    def reply(self, reply, question="What moisture did we predict?"):
        return self.ask(question, transport=answer_with(reply))

    def test_an_answer_is_returned_with_its_citations(self) -> None:
        result = self.reply(
            {
                "answer": "The prediction on file is 3.1 percent moisture, target 3.0.",
                "citations": ["targets and the prediction on file"],
                "found": True,
            }
        )
        self.assertIn("3.1 percent moisture", result["answer"])
        self.assertEqual(result["citations"], ["targets and the prediction on file"])
        self.assertTrue(result["found"])
        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(result["model"], "openai/gpt-4o-mini")
        self.assertEqual(result["note"], "")

    def test_the_record_is_what_the_model_is_given(self) -> None:
        seen = {}
        self.ask(
            "Why is moisture off?",
            transport=answer_with({"answer": "Because."}, seen=seen),
        )
        prompt = seen["body"]["messages"][-1]["content"]
        self.assertIn("Why is moisture off?", prompt)
        self.assertIn("SECTION product", prompt)
        self.assertIn("High-protein ragi cookie", prompt)
        # And the figures it can quote come from the record rather than the model.
        self.assertIn("target protein_g", prompt)
        self.assertEqual(seen["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-or-test-abcdefghij")

    def test_an_answer_that_says_it_could_not_find_the_fact_is_passed_through(self) -> None:
        self.assertFalse(self.reply({"answer": "Not recorded.", "found": False})["found"])

    def test_an_answer_in_prose_is_still_an_answer(self) -> None:
        # A model that ignores the JSON instruction should not produce a blank screen.
        result = self.reply("Moisture was predicted at 3.1 percent.")
        self.assertIn("3.1 percent", result["answer"])
        self.assertEqual(result["citations"], [])

    # -- what a question may not do ------------------------------------------ #

    def test_asking_a_question_changes_nothing_on_the_product(self) -> None:
        before = self.service.product_view(self.product_id)
        self.reply(
            {"answer": "Set protein to 99 g.", "targets": [{"id": "protein_g", "target": 99}]}
        )
        after = self.service.product_view(self.product_id)
        for key in ("brief", "formulation", "process", "trials", "plans"):
            self.assertEqual(before[key], after[key], "asking a question changed " + key)

    def test_the_question_reaches_the_ledger_and_the_model_history(self) -> None:
        self.reply({"answer": "Recorded answer."}, question="What is the cost ceiling?")
        ledger = self.service.product_view(self.product_id)["ledger"]
        self.assertTrue(any(entry["kind"] == "ask" for entry in ledger))
        calls = self.service.store.ai_calls(self.product_id)
        self.assertEqual([call["kind"] for call in calls], ["ask"])

    def test_the_raw_answer_is_not_stored_in_the_history_list(self) -> None:
        # The history answers "which model, how slow"; the text lives in the ledger.
        # Keeping it in neither would lose the answer, keeping it in both is waste.
        self.reply({"answer": "A very long answer that nobody needs twice."})
        self.assertNotIn("text", self.service.store.ai_calls(self.product_id)[0])

    # -- how it declines ----------------------------------------------------- #

    def test_a_question_without_a_key_is_declined_with_a_reason(self) -> None:
        result = self.ask("Anything?", config=self.settings(keys={}))
        self.assertEqual(result["answer"], "")
        self.assertIn("no AI provider key", result["note"])

    def test_a_run_that_does_not_ask_for_the_model_never_calls_it(self) -> None:
        def explode(url, headers, body, timeout):
            raise AssertionError("the model was called when it was not requested")

        result = self.ask("Anything?", use_model=False, transport=explode)
        self.assertEqual(result["answer"], "")
        self.assertIn("not requested", result["note"])

    def test_an_empty_question_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.ask_record(self.product_id, "   ")

    def test_an_unknown_product_is_not_found(self) -> None:
        with self.assertRaises(KeyError):
            self.service.ask_record(9999, "Anything?")

    def test_a_provider_that_cannot_be_reached_leaves_a_note_not_an_error(self) -> None:
        def broken(url, headers, body, timeout):
            raise RuntimeError("connection reset")

        result = self.ask("Anything?", transport=broken)
        self.assertEqual(result["answer"], "")
        self.assertIn("could not be reached", result["note"])

    def test_a_question_is_not_asked_once_the_hourly_budget_is_spent(self) -> None:
        self.service.store.save_ai_call(
            None, "ask", "openrouter", "m", "row", "text", cached=False
        )
        result = self.ask("Anything?", config=self.settings(max_calls_per_hour=1))
        self.assertEqual(result["answer"], "")
        self.assertIn("budget is spent", result["note"])

    def test_the_same_question_twice_is_answered_from_the_cache(self) -> None:
        first = self.reply({"answer": "Cached answer."}, question="What is the moisture?")
        self.assertFalse(first["cached"])
        second = self.ask("What is the moisture?", transport=answer_with({"answer": "Nope."}))
        self.assertTrue(second["cached"])
        self.assertEqual(second["answer"], first["answer"])


if __name__ == "__main__":
    unittest.main()
