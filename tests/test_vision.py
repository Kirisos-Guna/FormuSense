"""Tests for the optional vision layer: what it may accept and what it may not.

Two properties matter more than the rest, and neither is about the model being good.
The first is that the offline measurement path is unchanged and always available, so a
machine with no key produces exactly the brief it produced before the model layer
existed. The second is that a model reply cannot put a number into the brief: the
acceptance filter that enforces it is tested here directly, with a deliberately
hostile reply.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import ai_settings
from app.core import vision

OPENROUTER = {"OPENROUTER_API_KEY": "sk-or-test-abcdefghij"}


def transport_reply(payload, model="openai/gpt-4o-mini"):
    text = json.dumps(payload) if not isinstance(payload, str) else payload

    def transport(url, headers, body, timeout):
        return json.dumps(
            {"model": model, "choices": [{"message": {"content": text}}]}
        ).encode("utf-8")

    return transport


def fake_image(directory: Path) -> str:
    """A photograph stand-in that Pillow has no chance of opening.

    Deliberate: the model path must not depend on the offline measurement having
    succeeded first, and this is the case that proves it.
    """
    path = directory / "reference.png"
    path.write_bytes(b"FAKE-IMAGE" + b"f" * 64)
    return str(path)


class AvailableTests(unittest.TestCase):
    """What the interface is told about the layer, which is what it prints."""

    def test_with_no_key_the_layer_reports_itself_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            report = vision.vision_available()
        self.assertFalse(report["configured"])
        self.assertIsNone(report["provider"])
        self.assertIsNone(report["model"])
        self.assertEqual(report["active_mode"], "offline-image-analysis")
        self.assertFalse(report["openrouter"] or report["openai"] or report["gemini"])

    def test_a_configured_key_names_the_provider_and_the_model(self) -> None:
        # This is the pair the header pill reads, and the reason it used to be wrong:
        # it asked for a "provider" that nothing set.
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            report = vision.vision_available()
        self.assertTrue(report["configured"])
        self.assertEqual(report["provider"], "openrouter")
        # The model it would be asked first: the default is a free one, and the paid
        # source of the same model answers when the free pool is busy.
        self.assertEqual(report["model"], ai_settings().primary_model("openrouter"))
        self.assertIn("openrouter", report["active_mode"])
        self.assertTrue(report["openrouter"])

    def test_the_two_original_keys_still_report_under_their_own_names(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "g"}, clear=True):
            report = vision.vision_available()
        self.assertEqual(report["provider"], "gemini")
        self.assertTrue(report["gemini"])
        self.assertFalse(report["openai"])

    def test_the_offline_flag_still_describes_this_machine(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            report = vision.vision_available()
        self.assertTrue(report["offline_analysis"])
        self.assertEqual(report["pillow"], vision.pillow_available())


class AcceptanceTests(unittest.TestCase):
    """The filter that stands between a model and the brief."""

    def test_unknown_keys_are_discarded(self) -> None:
        accepted = vision._accept_semantic({"product_form": "biscuit", "notes": "ignore me", "extra": 1})
        self.assertEqual(accepted, {"product_form": "biscuit"})

    def test_a_key_that_names_a_composition_value_is_discarded(self) -> None:
        # The failure this exists for: a helpful model answering "describe the product"
        # with a protein content, which would then be indistinguishable from a
        # prediction once it was on the brief.
        hostile = {
            "product_form": "round biscuit",
            "protein_g_per_100g": 12.5,
            "unit_weight_g": 40,
            "cost_inr_kg": 240,
            "moisture_pct": 3.0,
            "energy_kcal": 470,
            "sugar_content": "9 g",
        }
        accepted = vision._accept_semantic(hostile)
        self.assertEqual(sorted(accepted), ["product_form"])

    def test_confidence_is_clamped_into_range(self) -> None:
        self.assertEqual(vision._accept_semantic({"confidence": 1.7})["confidence"], 1.0)
        self.assertEqual(vision._accept_semantic({"confidence": -3})["confidence"], 0.0)
        self.assertNotIn("confidence", vision._accept_semantic({"confidence": "very high"}))

    def test_lists_are_capped_and_items_are_cleaned(self) -> None:
        accepted = vision._accept_semantic(
            {"visible_inclusions": ["  chocolate   fleck  "] + ["item%d" % i for i in range(20)]}
        )
        self.assertEqual(accepted["visible_inclusions"][0], "chocolate fleck")
        self.assertLessEqual(len(accepted["visible_inclusions"]), 6)

    def test_a_list_of_objects_is_flattened_to_text(self) -> None:
        accepted = vision._accept_semantic({"apparent_defects": [{"text": "broken corner"}, {"name": "crack"}]})
        self.assertEqual(accepted["apparent_defects"], ["broken corner", "crack"])

    def test_a_string_where_a_list_was_expected_is_still_usable(self) -> None:
        self.assertEqual(vision._accept_semantic({"visible_inclusions": "one fleck"})["visible_inclusions"], ["one fleck"])

    def test_long_text_is_flattened_to_one_line_and_truncated(self) -> None:
        accepted = vision._accept_semantic({"shape_and_size_notes": "line one" + chr(10) + "line two" + "x" * 500})
        self.assertNotIn(chr(10), accepted["shape_and_size_notes"])
        self.assertLessEqual(len(accepted["shape_and_size_notes"]), 240)

    def test_something_that_is_not_an_object_is_an_empty_result(self) -> None:
        self.assertEqual(vision._accept_semantic([1, 2, 3]), {})
        self.assertEqual(vision._accept_semantic("text"), {})


class DescribeTests(unittest.TestCase):
    """The whole path, with a fake provider and a real record."""

    def setUp(self) -> None:
        from app.store import Store

        self.directory = Path(tempfile.mkdtemp(prefix="formusense-vision-"))
        self.store = Store(path=self.directory / "cache.db")
        self.image = fake_image(self.directory)
        self.reply = {
            "product_form": "round rotary-moulded biscuit",
            "surface_finish": "matte with a light dusting",
            "dominant_colour": "golden brown",
            "visible_inclusions": ["oat flake"],
            "apparent_defects": [],
            "process_hypothesis": "baked rotary-moulded biscuit",
            "confidence": 0.8,
            "protein_g_per_100g": 12.5,
        }

    def tearDown(self) -> None:
        self.store.close()

    def test_with_no_key_the_offline_result_is_the_answer_and_says_why(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            described = vision.describe_images([self.image], "cookie")
        self.assertEqual(described["source"], "offline-image-analysis")
        self.assertIsNone(described["semantic"])
        self.assertIsNone(described["model"])
        self.assertIn("no AI provider key", described["model_note"])

    def test_a_run_that_did_not_ask_for_the_model_does_not_call_one(self) -> None:
        called = []

        def transport(*args):
            called.append(args)
            return b"{}"

        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            described = vision.describe_images(
                [self.image], "cookie", use_model=False, transport=transport, cache=self.store
            )
        self.assertEqual(called, [])
        self.assertEqual(described["model_note"], "the model was not requested for this run")

    def test_a_configured_key_produces_a_description_and_its_provenance(self) -> None:
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            described = vision.describe_images(
                [self.image],
                "cookie",
                settings=ai_settings(),
                transport=transport_reply(self.reply),
                cache=self.store,
                product_id=1,
            )
        self.assertIn("openrouter-vision", described["source"])
        self.assertEqual(described["model"]["provider"], "openrouter")
        self.assertEqual(described["model"]["model"], "openai/gpt-4o-mini")
        self.assertFalse(described["model"]["cached"])
        self.assertEqual(described["semantic"]["parsed"]["product_form"], "round rotary-moulded biscuit")
        # The number the model volunteered is not in the description it may give.
        self.assertNotIn("protein_g_per_100g", described["semantic"]["parsed"])
        self.assertEqual(described["model_note"], "")

    def test_the_description_is_cached_against_the_image_bytes(self) -> None:
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            first = vision.describe_images(
                [self.image], "cookie", cache=self.store, transport=transport_reply(self.reply)
            )
            second = vision.describe_images(
                [self.image], "cookie", cache=self.store, transport=transport_reply({"product_form": "else"})
            )
        self.assertFalse(first["model"]["cached"])
        self.assertTrue(second["model"]["cached"])
        self.assertEqual(second["semantic"]["parsed"]["product_form"], "round rotary-moulded biscuit")

    def test_a_provider_that_fails_leaves_the_offline_description_in_place(self) -> None:
        def broken(url, headers, body, timeout):
            raise RuntimeError("connection reset by peer")

        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            described = vision.describe_images([self.image], "cookie", cache=self.store, transport=broken)
        self.assertIsNone(described["semantic"])
        self.assertEqual(described["source"], "offline-image-analysis")
        self.assertIn("could not be reached", described["model_note"])
        self.assertIn("connection reset", described["semantic_error"])
        self.assertIn("images", described)

    def test_the_hourly_ceiling_stops_the_call_and_explains_itself(self) -> None:
        called = []

        def transport(*args):
            called.append(args)
            return b"{}"

        self.store.save_ai_call(None, "vision", "openrouter", "m", "row", "text", cached=False)
        environment = dict(OPENROUTER, FORMUSENSE_AI_MAX_CALLS_PER_HOUR="1")
        with mock.patch.dict(os.environ, environment, clear=True):
            described = vision.describe_images([self.image], "cookie", cache=self.store, transport=transport)
        self.assertEqual(called, [])
        self.assertIn("budget is spent", described["model_note"])
        self.assertIsNone(described["semantic"])

    def test_a_remote_image_is_offered_to_the_model_without_pillow(self) -> None:
        # A URL cannot be measured locally, and that must not stop the model from
        # receiving it: the provider fetches and decodes it itself.
        seen = {}

        def transport(url, headers, body, timeout):
            seen["body"] = json.loads(body.decode("utf-8"))
            return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode("utf-8")

        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            described = vision.describe_images(["https://example.test/photo.jpg"], "cookie", transport=transport)
        content = seen["body"]["messages"][-1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "https://example.test/photo.jpg")
        self.assertEqual(described["model"]["provider"], "openrouter")

    def test_no_images_is_a_reason_not_an_error(self) -> None:
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            described = vision.describe_images([], "cookie")
        self.assertEqual(described["model_note"], "no reference images were supplied")


class DescriptionLineTests(unittest.TestCase):
    """One description, rendered the same way for the browser and the report."""

    def test_the_lines_name_the_model_that_produced_them(self) -> None:
        lines = vision.description_lines_from_record(
            json.dumps({"product_form": "round biscuit", "dominant_colour": "golden brown"}), "openai/gpt-4o-mini"
        )
        self.assertTrue(lines[0].startswith("Model description (openai/gpt-4o-mini):"))
        self.assertIn("Colour: golden brown", lines)

    def test_a_stored_reply_goes_through_the_same_filter_as_a_fresh_one(self) -> None:
        # The table keeps whatever the provider sent. Only this function decides what
        # counts as a description, so a stored numeric key cannot leak either.
        lines = vision.description_lines_from_record(json.dumps({"protein_g": 12.5}), "some-model")
        self.assertEqual(lines, [])

    def test_a_reply_that_is_not_json_produces_no_lines(self) -> None:
        self.assertEqual(vision.description_lines_from_record("not json", "m"), [])
        self.assertEqual(vision.description_lines_from_record("", ""), [])

    def test_the_hints_carry_the_provenance_into_the_payload(self) -> None:
        understanding = {
            "hints": ["Measured lightness 190."],
            "source": "offline-image-analysis + openrouter-vision",
            "semantic": {"parsed": {"process_hypothesis": "baked biscuit", "apparent_defects": ["dimple"]}},
            "model": {"model": "openai/gpt-4o-mini"},
            "model_note": "",
        }
        augmented = vision.apply_hints_to_payload({"product_name": "x"}, understanding)
        self.assertEqual(augmented["vision_model"], "openai/gpt-4o-mini")
        self.assertEqual(augmented["vision_note"], "")
        self.assertIn("Vision model process hypothesis: baked biscuit", augmented["vision_hints"])
        self.assertIn("Vision model flagged: dimple", augmented["vision_hints"])


if __name__ == "__main__":
    unittest.main()
