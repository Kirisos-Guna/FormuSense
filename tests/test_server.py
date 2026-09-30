"""Tests for the API surface the UI talks to.

The routes are thin wrappers, but two things about them are worth locking down:
the router has to prefer a literal path over a parameterised one of the same
shape, and every route has to fail with a *usable* answer (404 for an unknown
product, 400 for a missing specification) instead of a stack trace.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app import server
from app.bootstrap import CASES
from app.core import documents
from app.service import AgentService
from app.store import Store


def word_document(*lines: str) -> bytes:
    """A minimal .docx: one paragraph per line, which is all the reader needs."""
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in lines)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("word/document.xml", f'<?xml version="1.0"?><w:document>{body}</w:document>')
    return buffer.getvalue()


def as_data_url(name: str, payload: bytes) -> str:
    return f"data:application/octet-stream;base64," + base64.b64encode(payload).decode()


class RouterTests(unittest.TestCase):
    def match(self, method: str, path: str):
        return server.ROUTER.match(method, path)

    def test_a_known_route_matches_with_its_parameters(self) -> None:
        found = self.match("GET", "/api/products/12")
        self.assertIsNotNone(found)
        handler, params = found
        self.assertEqual(params["product_id"], "12")
        self.assertTrue(callable(handler))

    def test_nested_routes_match(self) -> None:
        found = self.match("POST", "/api/products/12/trials/34/analyse")
        self.assertIsNotNone(found)
        self.assertEqual(found[1], {"product_id": "12", "trial_id": "34"})

    def test_an_unknown_route_returns_nothing(self) -> None:
        self.assertIsNone(self.match("GET", "/api/nope"))
        self.assertIsNone(self.match("POST", "/api/benchmark/zap"))
        self.assertIsNone(self.match("GET", "/api/products/12/trials/34/analyse/extra"))

    def test_the_wrong_method_does_not_match(self) -> None:
        self.assertIsNone(self.match("DELETE", "/api/health"))

    def test_a_non_integer_identifier_is_a_bad_request(self) -> None:
        with self.assertRaises(ValueError):
            server._int("abc", "product_id")


class CatalogTests(unittest.TestCase):
    def test_the_catalogue_describes_every_category_and_ingredient(self) -> None:
        catalog = server.catalog()
        self.assertGreaterEqual(len(catalog["categories"]), 5)
        self.assertGreater(len(catalog["ingredients"]), 60)
        for category in catalog["categories"]:
            self.assertTrue(category["id"])
            self.assertTrue(category["slots"])
            self.assertTrue(category["parameters"])
            self.assertTrue(category["unit_operations"])
            for slot in category["slots"]:
                self.assertLess(slot["min_pct"], slot["max_pct"])
            # The form labels its pack-size field from this, so every category has to
            # say which unit its pack is stated in.
            self.assertIn(category["pack_unit"], ("g", "ml"))
        units = {c["id"]: c["pack_unit"] for c in catalog["categories"]}
        self.assertEqual(units["beverage"], "ml")
        self.assertEqual({u for c, u in units.items() if c != "beverage"}, {"g"})
        for ingredient in catalog["ingredients"]:
            self.assertTrue(ingredient["id"])
            self.assertTrue(ingredient["name"])
            self.assertGreaterEqual(ingredient["min_pct"], 0.0)
        self.assertTrue(catalog["claims"])
        self.assertTrue(catalog["allergens"])
        self.assertIn("vision", catalog)
        # The form discloses which model a run would use, so the catalog has to carry
        # the configured chain and not only the name of a provider.
        self.assertIn("ai", catalog)
        for key in ("configured", "provider", "model", "models"):
            self.assertIn(key, catalog["ai"])
        for chain in (catalog["ai"]["models"] or {}).values():
            self.assertTrue(chain, "a configured provider names no model")

    def test_the_case_studies_are_offered_with_their_plant_story(self) -> None:
        cases = server.cases()
        # Every seeded case is offered, plus the planted conflict demonstration.
        self.assertEqual(len(cases), len(CASES) + 1)
        feasible = [case for case in cases if case["feasible"]]
        self.assertEqual(len(feasible), len(CASES))
        for case in cases:
            self.assertTrue(case["narrative"])
            self.assertIn("spec_text", case["payload"])
            self.assertIn("plant", case["payload"])
        self.assertFalse([case for case in cases if not case["feasible"]][0]["feasible"])


class HandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-api-"))
        self.service = AgentService(Store(self.tmp / "test.db"))

    def tearDown(self) -> None:
        self.service.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body=None):
        matched = server.ROUTER.match(method, path)
        self.assertIsNotNone(matched, f"no route for {method} {path}")
        handler, params = matched
        return handler(self.service, body or {}, params)

    def test_health_reports_what_the_ui_shows_in_the_header(self) -> None:
        health = self.call("GET", "/api/health")
        self.assertTrue(health["ok"])
        self.assertGreater(health["ingredients"], 60)
        self.assertGreater(health["categories"], 3)

    def test_creating_a_case_study_returns_a_complete_design(self) -> None:
        created = self.call("POST", "/api/cases/cookie/create")
        self.assertIn("product_id", created)
        self.assertTrue(created["prediction"]["predictions"])
        self.assertIn("objective", created["evaluation"])
        self.assertTrue(created["case"]["narrative"])

    def test_an_unknown_case_is_a_not_found(self) -> None:
        with self.assertRaises(KeyError):
            self.call("POST", "/api/cases/offal_supreme/create")

    def test_a_product_view_carries_everything_the_tabs_need(self) -> None:
        created = self.call("POST", "/api/cases/namkeen/create")
        product_id = created["product_id"]
        view = self.call("GET", f"/api/products/{product_id}")
        for key in ("product", "brief", "formulation", "process", "trials", "plans", "efficiency", "ledger"):
            self.assertIn(key, view)
        self.assertEqual(view["formulation"]["version"], 1)

    def test_the_development_report_is_markdown_about_the_product(self) -> None:
        created = self.call("POST", "/api/cases/spread/create")
        payload = self.call("GET", f"/api/products/{created['product_id']}/report")
        markdown = payload["markdown"]
        self.assertIn("# Reduced-sugar mango fruit spread", markdown)
        self.assertIn("## 1. The brief as understood", markdown)
        self.assertIn("Trials to target", markdown)

    def test_the_process_route_returns_a_batch_sheet(self) -> None:
        created = self.call("POST", "/api/cases/cookie/create")
        payload = self.call("GET", f"/api/products/{created['product_id']}/process")
        self.assertTrue(payload["summary"]["unit_operations"])
        self.assertTrue(payload["batch_sheet"])

    def test_a_trial_can_be_run_and_analysed_through_the_api(self) -> None:
        created = self.call("POST", "/api/cases/namkeen/create")
        product_id = created["product_id"]
        trial = self.call("POST", f"/api/products/{product_id}/trials")
        analysed = self.call(
            "POST", f"/api/products/{product_id}/trials/{trial['trial_id']}/analyse"
        )
        self.assertTrue(analysed["analysis"]["residuals"])
        listed = self.call("GET", f"/api/products/{product_id}")
        self.assertEqual(len(listed["trials"]), 1)
        self.assertIsNotNone(listed["trials"][0]["analysis"])

    def test_an_empty_specification_is_rejected_with_a_reason(self) -> None:
        with self.assertRaises(ValueError):
            self.call("POST", "/api/products", {"product_name": "nothing"})

    def test_products_can_be_listed_and_deleted(self) -> None:
        created = self.call("POST", "/api/cases/cookie/create")
        listed = self.call("GET", "/api/products")
        self.assertEqual(len(listed["products"]), 1)
        self.assertEqual(listed["products"][0]["versions"], 1)
        deleted = self.call("DELETE", f"/api/products/{created['product_id']}")
        self.assertEqual(deleted["deleted"], created["product_id"])
        self.assertEqual(self.call("GET", "/api/products")["products"], [])


class AiSurfaceTests(unittest.TestCase):
    """The API's side of the model layer: what it admits to, and what it never says.

    A key in an environment variable is one careless ``return locals()`` away from
    being published, so the shape of the health payload is asserted rather than
    assumed.
    """

    KEY = "sk-or-test-abcdefghijklmnop"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-ai-api-"))
        self.service = AgentService(Store(self.tmp / "test.db"))

    def tearDown(self) -> None:
        self.service.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def with_key(self, **env):
        variables = {"OPENROUTER_API_KEY": self.KEY, "OPENROUTER_MODEL": "openai/gpt-4o-mini"}
        variables.update(env)
        return mock.patch.dict(os.environ, variables, clear=False)

    def test_the_health_payload_names_the_provider_and_never_the_key(self) -> None:
        with self.with_key():
            for strip in ("OPENAI_API_KEY", "GEMINI_API_KEY", "FORMUSENSE_AI"):
                with mock.patch.dict(os.environ, {strip: ""}, clear=False):
                    health = server.r_health(self.service, {}, {})
        self.assertTrue(health["ai"]["configured"])
        self.assertEqual(health["ai"]["provider"], "openrouter")
        self.assertEqual(health["ai"]["providers"], ["openrouter"])
        self.assertEqual(health["ai"]["model"], "openai/gpt-4o-mini")
        self.assertNotIn(self.KEY, json.dumps(health))
        self.assertNotIn("sk-or-test", json.dumps(health))

    def test_the_badge_is_told_which_provider_is_live(self) -> None:
        # The interface printed "AI: API" whatever was configured, because it read a
        # key that nothing set. A wrong badge is worse than none.
        with self.with_key():
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "GEMINI_API_KEY": ""}, clear=False):
                health = server.r_health(self.service, {}, {})
        vision = health["vision"]
        self.assertEqual(vision["provider"], "openrouter")
        self.assertEqual(vision["model"], "openai/gpt-4o-mini")
        self.assertTrue(vision["openrouter"])
        self.assertFalse(vision["openai"])
        self.assertFalse(vision["gemini"])
        self.assertIn("openrouter", vision["active_mode"])
        self.assertIn("budget", vision)
        self.assertLessEqual(vision["budget"]["used"], vision["budget"]["cap"])

    def test_without_a_key_the_badge_says_offline_and_the_layer_is_off(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "", "GEMINI_API_KEY": ""},
            clear=False,
        ):
            health = server.r_health(self.service, {}, {})
        self.assertFalse(health["ai"]["configured"])
        self.assertIsNone(health["ai"]["provider"])
        self.assertIsNone(health["vision"]["provider"])
        self.assertEqual(health["vision"]["active_mode"], "offline-image-analysis")
        self.assertTrue(health["vision"]["offline_analysis"])

    def test_the_kill_switch_turns_the_layer_off_even_with_a_key(self) -> None:
        # A machine that holds a key for other tools must be able to say "not here".
        with self.with_key(FORMUSENSE_AI="0"):
            health = server.r_health(self.service, {}, {})
        self.assertFalse(health["ai"]["configured"])
        self.assertEqual(health["ai"]["providers"], [])

    def test_a_question_is_a_route_that_exists(self) -> None:
        matched = server.ROUTER.match("POST", "/api/products/7/ask")
        self.assertIsNotNone(matched)
        self.assertEqual(matched[1], {"product_id": "7"})

    def test_asking_without_the_model_answers_with_a_reason_rather_than_failing(self) -> None:
        created = server.r_case_create(self.service, {}, {"key": "cookie"})
        matched = server.ROUTER.match("POST", "/api/products/%d/ask" % created["product_id"])
        handler, params = matched
        result = handler(self.service, {"question": "What is the cost ceiling?"}, params)
        self.assertEqual(result["answer"], "")
        self.assertIn("not requested", result["note"])
        self.assertEqual(result["question"], "What is the cost ceiling?")

    def test_a_question_about_an_unknown_product_is_a_not_found(self) -> None:
        handler, params = server.ROUTER.match("POST", "/api/products/424242/ask")
        with self.assertRaises(KeyError):
            handler(self.service, {"question": "Anything?"}, params)


class DocumentUploadTests(unittest.TestCase):
    """The upload route: it reads a document, and it writes nothing at all."""

    REPORT = (
        "High protein ragi cookie",
        "Specification: protein 15 g per 100 g, moisture 3.5 %, shelf life 180 days, "
        "cost not more than INR 240 per kg. 40 g pack.",
        "Gluten free and no soy. Vegetarian. High protein claim.",
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-doc-"))
        self.service = AgentService(Store(self.tmp / "test.db"))
        self.uploads = self.tmp / "uploads"
        self.patch = mock.patch.object(documents, "UPLOAD_DIR", self.uploads)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tearDown(self) -> None:
        self.service.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body=None):
        matched = server.ROUTER.match(method, path)
        self.assertIsNotNone(matched, f"no route for {method} {path}")
        handler, params = matched
        return handler(self.service, body or {}, params)

    def upload(self, name: str = "ragi-report.docx", **extra):
        return self.call(
            "POST",
            "/api/brief/from-document",
            {"filename": name, "data_url": as_data_url(name, word_document(*self.REPORT)), **extra},
        )

    def test_the_route_reads_a_document_into_form_fields(self) -> None:
        proposal = self.upload()
        fields = proposal["fields"]
        self.assertEqual(proposal["document"]["name"], "ragi-report.docx")
        self.assertEqual(proposal["document"]["format"], "docx")
        self.assertEqual(fields["product_name"], "High protein ragi cookie")
        self.assertEqual(fields["category"], "cookie")
        self.assertEqual(fields["diet"], "vegetarian")
        self.assertAlmostEqual(fields["unit_weight_g"], 40.0)
        self.assertIn("high_protein", fields["claims"])
        self.assertIn("gluten", fields["allergens_to_avoid"])
        self.assertTrue(proposal["found"])
        self.assertTrue(proposal["missing"])

    def test_reading_a_document_leaves_no_trace_on_the_record(self) -> None:
        self.upload()
        self.assertEqual(self.service.store.products(), [])
        self.assertEqual(self.service.store.entries(), [])
        self.assertFalse(self.uploads.exists())

    def test_the_upload_can_be_read_with_the_model_off(self) -> None:
        proposal = self.upload(use_ai=False)
        self.assertFalse(proposal["model"]["reviewed"])
        self.assertIn("not asked", proposal["model"]["note"])

    def test_a_body_without_a_file_is_a_bad_request(self) -> None:
        with self.assertRaises(ValueError):
            self.call("POST", "/api/brief/from-document", {"filename": "x.docx"})

    def test_a_url_that_is_not_a_data_url_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.call(
                "POST",
                "/api/brief/from-document",
                {"filename": "x.docx", "data_url": "https://example.com/x.docx"},
            )

    def test_a_format_nothing_can_read_is_refused_with_the_list(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.call(
                "POST",
                "/api/brief/from-document",
                {"filename": "report.pages", "data_url": as_data_url("report.pages", b"whatever")},
            )
        self.assertIn(".docx", str(caught.exception))

    def test_a_product_created_from_a_document_names_the_document(self) -> None:
        proposal = self.upload()
        created = self.call(
            "POST",
            "/api/products",
            {
                **proposal["fields"],
                "source_document": {
                    "name": "ragi-report.docx",
                    "format": "docx",
                    "characters": proposal["document"]["characters"],
                    "data_url": as_data_url("ragi-report.docx", word_document(*self.REPORT)),
                    "use_ai": False,
                },
            },
        )
        entries = [
            entry
            for entry in self.service.store.entries(created["product_id"])
            if entry["kind"] == "document"
        ]
        self.assertEqual(len(entries), 1)
        self.assertIn("ragi-report.docx", entries[0]["message"])
        # The file is kept, and only now - this is the moment it became evidence.
        self.assertEqual(len(list(self.uploads.glob("*.docx"))), 1)

    def test_a_product_designed_without_a_document_has_no_such_entry(self) -> None:
        created = self.call(
            "POST",
            "/api/products",
            {"product_name": "Typed brief", "category": "cookie", "spec_text": "Protein 15 g per 100 g. 40 g pack."},
        )
        kinds = {entry["kind"] for entry in self.service.store.entries(created["product_id"])}
        self.assertNotIn("document", kinds)
        self.assertFalse(self.uploads.exists())

    def test_the_catalogue_offers_the_formats_the_reader_implements(self) -> None:
        offered = server.catalog()["documents"]
        self.assertIn(".docx", offered["accept"])
        self.assertIn(".pdf", offered["accept"])
        self.assertGreaterEqual(offered["limit_mb"], 1)


if __name__ == "__main__":
    unittest.main()
