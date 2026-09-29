"""Tests for the optional model layer: one client, one prompt set, no network.

The suite is offline by construction. ``chat`` takes its transport as an argument, so
every test here hands it a fake and asserts the request that would have gone out -
the URL, the headers, the model, the images - without a socket. That is what lets the
AI paths be covered inside a pipeline that must not hold a credential, and it is why
no test in this file can spend a real key even by accident.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from app.ai import AiError, AiUnavailable, client
from app import config as config_module
from app.config import AI_DEFAULT_MODEL, ai_settings, model_chain
from app.store import Store

OPENROUTER = {"OPENROUTER_API_KEY": "sk-or-test-abcdefghij"}

#: A free model and a paid one, as an environment line would name them. The default
#: is the free model alone; a chain is what a deployment asks for when it would
#: rather pay a fraction of a rupee than fail while the free pool is busy.
FREE, PAID = "vendor/free-model:free", "vendor/paid-model"
CHAIN = FREE + "," + PAID


def reply(text: str, model: str = "openai/gpt-4o-mini", usage=None) -> bytes:
    return json.dumps(
        {
            "model": model,
            "choices": [{"message": {"content": text}}],
            "usage": usage if usage is not None else {"total_tokens": 11},
        }
    ).encode("utf-8")


def fake_image(directory: Path, name: str = "reference.png", seed: bytes = b"f") -> Path:
    """A file that stands in for a photograph.

    It is deliberately not a decodable image. ``image_part`` only encodes bytes, and
    keeping it unreadable proves the model path does not quietly depend on Pillow
    being able to open the file first - which it must not, because the provider decodes
    the image itself.
    """
    path = directory / name
    path.write_bytes(b"FAKE-IMAGE-" + seed * 32)
    return path


def no_sleep(seconds: float) -> None:
    """The retry pause, removed. A test that waits 1.5s to prove a backoff is a slow
    suite for no added confidence: the request count is what matters."""
    return None


def http_error(code: int, detail: str):
    error = urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", code, "x", {}, None)
    error.read = lambda: detail.encode("utf-8")  # type: ignore[method-assign]
    return error


class FakeTransport:
    """Records every request and answers from a queue.

    An entry may be a bytes reply or an exception to raise, which is how the failure
    and fallback paths are exercised without a provider that has to be broken on
    purpose. A failure can also be pinned to one model id with ``fail``, which is what
    a retry needs: with retries on, a queue alone cannot express "this model is busy
    and that one is not", because the busy model would simply take the next entry.
    """

    def __init__(self, *replies, fail=None) -> None:
        self.replies = list(replies)
        self.fail = dict(fail or {})
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        self.requests.append({"url": url, "headers": headers, "body": payload, "timeout": timeout})
        pinned = self.fail.get(payload.get("model"))
        if pinned is not None:
            raise pinned
        if not self.replies:
            return reply("{}")
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def models(self):
        return [request["body"]["model"] for request in self.requests]


class ConfigTests(unittest.TestCase):
    """What the environment decides, and what the API is allowed to show of it."""

    def test_no_key_means_the_layer_is_simply_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            config = ai_settings()
            self.assertFalse(config.configured)
            self.assertEqual(config.providers(), [])

    def test_openrouter_is_preferred_and_the_others_remain_as_fallbacks(self) -> None:
        environment = {"OPENROUTER_API_KEY": "a", "OPENAI_API_KEY": "b", "GEMINI_API_KEY": "c"}
        with mock.patch.dict(os.environ, environment, clear=True):
            config = ai_settings()
        self.assertEqual(config.provider, "openrouter")
        self.assertEqual([name for name, *_ in config.providers()], ["openrouter", "openai", "gemini"])

    def test_the_off_switch_is_honoured_even_when_a_key_is_present(self) -> None:
        # A machine can have a key in its environment for other tools.
        with mock.patch.dict(os.environ, dict(OPENROUTER, FORMUSENSE_AI="0"), clear=True):
            self.assertFalse(ai_settings().configured)

    def test_one_override_renames_the_model_for_whichever_provider_answers(self) -> None:
        with mock.patch.dict(os.environ, dict(OPENROUTER, FORMUSENSE_AI_MODEL="vendor/model-x"), clear=True):
            config = ai_settings()
        self.assertEqual(config.model_chain(config.provider), ["vendor/model-x"])

    def test_a_model_setting_may_list_a_chain_in_order(self) -> None:
        # Free first, paid second: the order in the setting is the order they are tried.
        environment = dict(
            OPENROUTER,
            OPENROUTER_MODEL="vendor/free-model:free, vendor/paid-model ",
        )
        with mock.patch.dict(os.environ, environment, clear=True):
            config = ai_settings()
        self.assertEqual(config.model_chain("openrouter"), ["vendor/free-model:free", "vendor/paid-model"])
        self.assertEqual(config.primary_model("openrouter"), "vendor/free-model:free")
        shown = config.as_dict()
        self.assertEqual(shown["model"], "vendor/free-model:free")
        self.assertEqual(shown["models"]["openrouter"], ["vendor/free-model:free", "vendor/paid-model"])

    def test_the_public_view_never_carries_the_key(self) -> None:
        with mock.patch.dict(os.environ, dict(OPENROUTER, OPENROUTER_MODEL="openai/gpt-4o-mini"), clear=True):
            shown = json.dumps(ai_settings().as_dict())
        self.assertNotIn("sk-or-test", shown)
        self.assertIn("openrouter", shown)
        self.assertIn("openai/gpt-4o-mini", shown)

    def test_a_typo_in_a_numeric_setting_falls_back_instead_of_raising(self) -> None:
        # The layer is optional, so a malformed value must not stop the application.
        with mock.patch.dict(os.environ, dict(OPENROUTER, FORMUSENSE_AI_TIMEOUT="soon"), clear=True):
            self.assertEqual(ai_settings().timeout, 45.0)

    def test_a_ceiling_of_zero_means_no_ceiling(self) -> None:
        with mock.patch.dict(os.environ, dict(OPENROUTER, FORMUSENSE_AI_MAX_CALLS_PER_HOUR="0"), clear=True):
            config = ai_settings()
        self.assertEqual(config.max_calls_per_hour, 0)


class RequestTests(unittest.TestCase):
    """The request itself: where it goes, what it carries and what it may not."""

    def test_the_request_names_the_endpoint_the_model_and_the_key(self) -> None:
        asked = model_chain(AI_DEFAULT_MODEL["openrouter"])[0]
        transport = FakeTransport(reply('{"answer": "ok"}', asked))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            result = client.chat([{"role": "user", "content": "hello"}], transport=transport)
        sent = transport.requests[0]
        self.assertEqual(sent["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer sk-or-test-abcdefghij")
        self.assertEqual(sent["headers"]["Content-Type"], "application/json")
        # The default OpenRouter chain asks the free model first, so that is the one
        # the request names - and the model is reported as the provider named it, not
        # as it was asked for.
        self.assertEqual(sent["body"]["model"], asked)
        self.assertEqual(result.text, '{"answer": "ok"}')
        self.assertEqual((result.provider, result.model), ("openrouter", asked))
        self.assertFalse(result.cached)

    def test_the_attribution_headers_are_openrouters_own_and_optional(self) -> None:
        transport = FakeTransport(reply("{}"))
        environment = dict(OPENROUTER, OPENROUTER_HTTP_REFERER="https://example.test", OPENROUTER_APP_TITLE="FormuSense")
        with mock.patch.dict(os.environ, environment, clear=True):
            client.chat([{"role": "user", "content": "x"}], transport=transport)
        headers = transport.requests[0]["headers"]
        self.assertEqual(headers["HTTP-Referer"], "https://example.test")
        self.assertEqual(headers["X-Title"], "FormuSense")

    def test_an_image_is_attached_to_the_last_user_message(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="formusense-ai-"))
        image = fake_image(directory)
        transport = FakeTransport(reply("{}"))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            client.chat([{"role": "user", "content": "describe this"}], images=[str(image)], transport=transport)
        content = transport.requests[0]["body"]["messages"][-1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "describe this"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_no_more_images_are_sent_than_the_limit_allows(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="formusense-ai-"))
        paths = []
        for index in range(5):
            path = fake_image(directory, f"image{index}.png", bytes([index + 1]))
            paths.append(str(path))
        transport = FakeTransport(reply("{}"))
        with mock.patch.dict(os.environ, dict(OPENROUTER, FORMUSENSE_AI_MAX_IMAGES="2"), clear=True):
            client.chat([{"role": "user", "content": "x"}], images=paths, transport=transport)
        content = transport.requests[0]["body"]["messages"][-1]["content"]
        self.assertEqual(len([part for part in content if part["type"] == "image_url"]), 2)

    def test_an_absent_image_file_is_skipped_rather_than_failing_the_call(self) -> None:
        transport = FakeTransport(reply("{}"))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            client.chat([{"role": "user", "content": "x"}], images=["/no/such/file.png"], transport=transport)
        self.assertEqual(transport.requests[0]["body"]["messages"][-1]["content"], "x")

    def test_no_response_format_is_sent_because_not_every_model_accepts_one(self) -> None:
        transport = FakeTransport(reply("{}"))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            client.chat([{"role": "user", "content": "x"}], transport=transport)
        self.assertNotIn("response_format", transport.requests[0]["body"])


class FailureTests(unittest.TestCase):
    """Nothing a provider does may escape as anything other than a sentence."""

    def test_no_configured_provider_raises_the_unavailable_error(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AiUnavailable):
                client.chat([{"role": "user", "content": "x"}], transport=FakeTransport())

    def test_a_provider_that_fails_is_tried_and_the_next_one_answers(self) -> None:
        transport = FakeTransport(RuntimeError("connection reset"), reply("from openai", "gpt-4o-mini"))
        environment = dict(
            OPENROUTER,
            OPENROUTER_MODEL="vendor/model-one",
            OPENAI_API_KEY="second-key-abcdefghij",
            OPENAI_BASE_URL="https://api.openai.test/v1",
            OPENAI_VISION_MODEL="gpt-4o-mini",
            FORMUSENSE_AI_RETRIES="0",
        )
        with mock.patch.dict(os.environ, environment, clear=True):
            result = client.chat([{"role": "user", "content": "x"}], transport=transport)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.text, "from openai")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(transport.requests[1]["url"], "https://api.openai.test/v1/chat/completions")

    def test_every_provider_failing_names_all_of_them(self) -> None:
        transport = FakeTransport(RuntimeError("boom one"), RuntimeError("boom two"))
        environment = dict(
            OPENROUTER,
            OPENROUTER_MODEL="vendor/model-one",
            OPENAI_API_KEY="second-key-abcdefghij",
            FORMUSENSE_AI_RETRIES="0",
        )
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError) as caught:
                client.chat([{"role": "user", "content": "x"}], transport=transport)
        message = str(caught.exception)
        self.assertIn("openrouter", message)
        self.assertIn("openai", message)

    def test_a_key_echoed_back_in_an_error_never_reaches_the_message(self) -> None:
        # Providers do echo the request back, and a message that quotes the key would
        # put it in a log line, a ledger row and an API response.
        transport = FakeTransport(
            RuntimeError("401 for key sk-or-test-abcdefghij (Authorization: Bearer sk-or-test-abcdefghij)")
        )
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one", FORMUSENSE_AI_RETRIES="0")
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError) as caught:
                client.chat([{"role": "user", "content": "x"}], transport=transport)
        self.assertNotIn("sk-or-test-abcdefghij", str(caught.exception))
        self.assertIn("***", str(caught.exception))

    def test_an_http_error_reports_the_providers_own_explanation(self) -> None:
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            {},
            None,
        )
        error.read = lambda: b'{"error": {"message": "rate limit exceeded"}}'  # type: ignore[method-assign]
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one", FORMUSENSE_AI_RETRIES="0")
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError) as caught:
                client.chat([{"role": "user", "content": "x"}], transport=FakeTransport(error))
        self.assertIn("429", str(caught.exception))
        self.assertIn("rate limit exceeded", str(caught.exception))
        # urllib's response base is a tempfile wrapper, so an unclosed HTTPError
        # prints a ResourceWarning over the suite's output when it is collected.
        error.close()

    def test_a_reply_without_text_is_a_failure_not_an_empty_answer(self) -> None:
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one", FORMUSENSE_AI_RETRIES="0")
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError):
                client.chat(
                    [{"role": "user", "content": "x"}],
                    transport=FakeTransport(reply("")),
                )


class CacheAndBudgetTests(unittest.TestCase):
    """The two guardrails: a repeat is free, and the hour has a ceiling."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="formusense-cache-"))
        self.store = Store(path=self.directory / "cache.db")

    def tearDown(self) -> None:
        self.store.close()

    def test_the_second_identical_request_is_answered_from_the_record(self) -> None:
        first = FakeTransport(reply('{"answer": "the first answer"}'))
        second = FakeTransport(reply('{"answer": "a different answer"}'))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            one = client.chat([{"role": "user", "content": "same"}], transport=first, cache=self.store)
            two = client.chat([{"role": "user", "content": "same"}], transport=second, cache=self.store)
        self.assertFalse(one.cached)
        self.assertTrue(two.cached)
        self.assertEqual(two.text, one.text)
        self.assertEqual(second.requests, [])
        self.assertEqual(two.call_id, one.call_id)

    def test_a_different_question_is_a_different_request(self) -> None:
        transport_a = FakeTransport(reply('{"answer": "one"}'))
        transport_b = FakeTransport(reply('{"answer": "two"}'))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            client.chat([{"role": "user", "content": "one"}], transport=transport_a, cache=self.store)
            second = client.chat([{"role": "user", "content": "two"}], transport=transport_b, cache=self.store)
        self.assertFalse(second.cached)
        self.assertEqual(transport_b.requests and len(transport_b.requests), 1)

    def test_the_reply_is_recorded_with_its_provenance(self) -> None:
        transport = FakeTransport(reply('{"answer": "recorded"}'))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            result = client.chat(
                [{"role": "user", "content": "q"}], transport=transport, cache=self.store, kind="ask"
            )
        rows = self.store.ai_calls()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "ask")
        self.assertEqual(rows[0]["provider"], "openrouter")
        self.assertEqual(rows[0]["model"], "openai/gpt-4o-mini")
        self.assertFalse(rows[0]["cached"])
        self.assertEqual(result.call_id, rows[0]["id"])

    def test_a_cached_reply_is_not_charged_against_the_hour(self) -> None:
        # Charging for it would defeat the cache for exactly the traffic it exists to
        # absorb: a visitor reloading the same photograph.
        self.store.save_ai_call(None, "vision", "openrouter", "m", "h1", "text", cached=False)
        self.store.save_ai_call(None, "vision", "openrouter", "m", "h2", "text", cached=True)
        used = self.store.ai_calls_since("2000-01-01T00:00:00")
        self.assertEqual(used, 1)

    def test_the_ceiling_is_counted_from_the_record_not_from_memory(self) -> None:
        # Counted from the rows rather than from a counter in the process, so two
        # threads - which is how the server runs - cannot both believe they are first.
        with mock.patch.dict(os.environ, dict(OPENROUTER, FORMUSENSE_AI_MAX_CALLS_PER_HOUR="2"), clear=True):
            config = ai_settings()
            for index in range(2):
                self.store.save_ai_call(None, "ask", "openrouter", "m", f"row{index}", "text", cached=False)
            budget = client.usage_this_hour(self.store, config)
        self.assertEqual((budget.used, budget.cap), (2, 2))
        self.assertTrue(budget.exceeded)
        self.assertEqual(budget.remaining, 0)
        # The window is a real hour of wall clock, so a window that starts in the
        # future holds nothing.
        self.assertEqual(self.store.ai_calls_since("2999-01-01T00:00:00"), 0)

    def test_a_cache_that_is_not_there_is_simply_a_miss(self) -> None:
        transport = FakeTransport(reply('{"answer": "no cache"}'))
        with mock.patch.dict(os.environ, OPENROUTER, clear=True):
            result = client.chat([{"role": "user", "content": "x"}], transport=transport, cache=object())
        self.assertFalse(result.cached)
        self.assertEqual(result.text, '{"answer": "no cache"}')


class ParsingTests(unittest.TestCase):
    """A reply is untrusted input, whatever the prompt asked for."""

    def test_json_is_recovered_from_a_fenced_or_padded_reply(self) -> None:
        # Providers wrap the object in a fence or a sentence often enough that the
        # parser has to find it rather than assume it is the whole reply.
        fence = chr(96) * 3
        fenced = fence + "json" + chr(10) + '{"a": 1}' + chr(10) + fence
        self.assertEqual(client.parse_json_object(fenced), {"a": 1})
        self.assertEqual(client.parse_json_object('Here it is: {"a": 1} - hope that helps'), {"a": 1})

    def test_a_reply_that_is_not_json_is_an_empty_object_rather_than_an_error(self) -> None:
        for text in ("", "I could not read the image.", "[1, 2, 3]", "{not json}"):
            self.assertEqual(client.parse_json_object(text), {})

    def test_content_returned_as_parts_is_joined(self) -> None:
        message = {"content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]}
        self.assertEqual(client.content_text(message), "one" + chr(10) + "two")

    def test_a_remote_image_is_passed_through_and_a_local_one_is_encoded(self) -> None:
        self.assertEqual(
            client.image_part("https://example.test/a.jpg")["image_url"]["url"],
            "https://example.test/a.jpg",
        )
        self.assertIsNone(client.image_part("/no/such/file.png"))
        self.assertIsNone(client.image_part(""))


class ModelChainTests(unittest.TestCase):
    """One provider, several models, in the order the environment lists them.

    This is what makes a free model usable: the free pool is shared, so it answers
    429 whenever somebody else is using it, and the run has to be able to carry on
    into a model that costs a fraction of a rupee rather than stop.
    """

    BUSY = '{"error": {"message": "rate limit exceeded"}}'

    def test_the_free_model_is_asked_first_and_its_paid_sibling_answers_when_it_is_busy(self) -> None:
        busy = http_error(429, self.BUSY)
        self.addCleanup(busy.close)
        transport = FakeTransport(reply('{"answer": "from the paid model"}', PAID), fail={FREE: busy})
        environment = dict(OPENROUTER, OPENROUTER_MODEL=CHAIN, FORMUSENSE_AI_RETRIES="1")
        with mock.patch.dict(os.environ, environment, clear=True):
            result = client.chat([{"role": "user", "content": "x"}], transport=transport, sleeper=no_sleep)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model, PAID)
        # Two attempts at the free model - it was busy, not broken - and then the paid
        # one, which is the whole point of the chain.
        self.assertEqual(transport.models(), [FREE, FREE, PAID])

    def test_the_default_chain_cannot_spend_anything(self) -> None:
        # OpenRouter is the provider a key alone turns on, and a shared public demo
        # must not be able to spend through it, so every model it defaults to is a free
        # one. Several of them, because the free pools are separate: one pool being
        # busy is exactly what the next entry is for.
        #
        # The OpenAI and Gemini defaults are not covered here and do not need to be:
        # neither is reachable without the deployer supplying that vendor's own key,
        # which is a decision rather than a default.
        chain = model_chain(AI_DEFAULT_MODEL["openrouter"])
        self.assertGreaterEqual(len(chain), 2)
        for model in chain:
            self.assertTrue(model.endswith(":free"), model + " is not a free model")

    def test_a_busy_provider_is_asked_again_before_it_is_written_off(self) -> None:
        pauses: list = []
        busy = http_error(429, self.BUSY)
        self.addCleanup(busy.close)
        transport = FakeTransport(busy, reply('{"answer": "second time lucky"}', "vendor/model-one"))
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one")
        with mock.patch.dict(os.environ, environment, clear=True):
            result = client.chat([{"role": "user", "content": "x"}], transport=transport, sleeper=pauses.append)
        self.assertEqual(result.text, '{"answer": "second time lucky"}')
        self.assertEqual(len(transport.requests), 2)
        # The wait is real but bounded, and it is visible rather than magic.
        self.assertEqual(pauses, [client.RETRY_PAUSE_SECONDS])

    def test_an_unexpected_failure_is_not_retried(self) -> None:
        transport = FakeTransport(RuntimeError("a bug in the transport"))
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one")
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError):
                client.chat([{"role": "user", "content": "x"}], transport=transport, sleeper=no_sleep)
        self.assertEqual(len(transport.requests), 1)

    def test_a_refused_key_is_not_asked_again(self) -> None:
        # 401 is a fact about the key, not about the moment: retrying it would spend
        # the caller's time budget to be told the same thing.
        refused = http_error(401, '{"error": {"message": "invalid api key"}}')
        self.addCleanup(refused.close)
        transport = FakeTransport(fail={FREE: refused, PAID: refused})
        environment = dict(OPENROUTER, OPENROUTER_MODEL=CHAIN)
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError) as caught:
                client.chat([{"role": "user", "content": "x"}], transport=transport, sleeper=no_sleep)
        self.assertEqual(transport.models(), [FREE, PAID])
        message = str(caught.exception)
        self.assertIn("401", message)
        self.assertIn(FREE, message)
        self.assertIn(PAID, message)

    def test_the_retry_count_is_configurable(self) -> None:
        busy = http_error(429, self.BUSY)
        self.addCleanup(busy.close)
        for retries, expected in (("0", 1), ("2", 3), ("99", 6)):
            transport = FakeTransport(fail={FREE: busy, PAID: busy})
            environment = dict(OPENROUTER, OPENROUTER_MODEL=CHAIN, FORMUSENSE_AI_RETRIES=retries)
            with mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(AiError):
                    client.chat([{"role": "user", "content": "x"}], transport=transport, sleeper=no_sleep)
            # Six is the ceiling: five retries plus the first attempt, whatever the
            # environment says, so one setting cannot hold a request open for minutes.
            self.assertEqual(len([m for m in transport.models() if m == FREE]), expected, retries)

    def test_a_fallback_to_the_paid_model_is_cached_so_it_is_not_bought_twice(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="formusense-ai-chain-"))
        store = Store(directory / "cache.db")
        self.addCleanup(store.close)
        busy = http_error(429, self.BUSY)
        self.addCleanup(busy.close)
        transport = FakeTransport(reply('{"answer": "from the paid model"}', PAID), fail={FREE: busy})
        environment = dict(OPENROUTER, OPENROUTER_MODEL=CHAIN, FORMUSENSE_AI_RETRIES="0")
        with mock.patch.dict(os.environ, environment, clear=True):
            first = client.chat([{"role": "user", "content": "x"}], transport=transport, cache=store)
            assert not first.cached
            second = client.chat([{"role": "user", "content": "x"}], transport=transport, cache=store)
        self.assertTrue(second.cached)
        self.assertEqual(second.model, PAID)
        # The free model is still asked each time - it might be free this time - but the
        # paid answer is not bought again.
        self.assertEqual(int(store.ai_calls_since("2000-01-01T00:00:00")), 1)


class ReasoningTests(unittest.TestCase):
    """How much the model is allowed to think before it answers.

    These prompts extract rather than reason, and the thinking is billed as output
    tokens - the expensive half. Left at the model's default, a real call spent 409
    of its 418 output tokens working out that it was being asked for the number 42.
    """

    def body(self, environment, **kwargs):
        transport = FakeTransport(reply('{"answer": "ok"}', model_chain(AI_DEFAULT_MODEL["openrouter"])[0]))
        with mock.patch.dict(os.environ, environment, clear=True):
            client.chat([{"role": "user", "content": "x"}], transport=transport, **kwargs)
        return transport.requests[0]["body"]

    def test_thinking_is_off_by_default(self) -> None:
        self.assertEqual(self.body(OPENROUTER)["reasoning"], {"enabled": False})

    def test_the_budget_can_be_turned_back_up(self) -> None:
        for value, expected in (("low", {"effort": "low"}), ("high", {"effort": "high"})):
            body = self.body(dict(OPENROUTER, FORMUSENSE_AI_REASONING=value))
            self.assertEqual(body["reasoning"], expected, value)

    def test_leaving_it_to_the_model_sends_nothing_at_all(self) -> None:
        body = self.body(dict(OPENROUTER, FORMUSENSE_AI_REASONING="default"))
        self.assertNotIn("reasoning", body)

    def test_a_provider_that_does_not_know_the_field_is_never_sent_it(self) -> None:
        # It is an OpenRouter extension of the OpenAI shape rather than part of it, and
        # a provider may refuse the whole request rather than ignore one unknown key.
        environment = {"OPENAI_API_KEY": "sk-openai-test-abcdefghij", "OPENAI_BASE_URL": "https://api.openai.test/v1"}
        body = self.body(environment)
        self.assertNotIn("reasoning", body)
        self.assertEqual(body["model"], "gpt-4o-mini")

    def test_a_model_that_thought_itself_out_of_tokens_says_so(self) -> None:
        truncated = json.dumps(
            {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        ).encode("utf-8")
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one", FORMUSENSE_AI_RETRIES="0")
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AiError) as caught:
                client.chat([{"role": "user", "content": "x"}], transport=FakeTransport(truncated))
        # Not "the provider is broken" and not "your key is wrong": the one setting
        # that would fix it.
        self.assertIn("FORMUSENSE_AI_MAX_TOKENS", str(caught.exception))


class RepairTests(unittest.TestCase):
    """What happens when a model answers the question but not in the format.

    Free models do this intermittently: one reply is a tidy JSON object and the next
    is an essay. To the parser those look identical to "I found nothing", which is a
    legitimate answer - so the format is asked for once more rather than being
    reported, and the first reply is kept if the second is prose too.
    """

    PROSE = "Here is my analysis. The product is a round biscuit with 12 g protein."

    def chat(self, *replies, expect_json=True, **kwargs):
        transport = FakeTransport(*replies)
        environment = dict(OPENROUTER, OPENROUTER_MODEL="vendor/model-one")
        with mock.patch.dict(os.environ, environment, clear=True):
            result = client.chat(
                [{"role": "user", "content": "review this"}],
                transport=transport,
                expect_json=expect_json,
                sleeper=no_sleep,
                **kwargs,
            )
        return result, transport

    def test_a_prose_reply_is_asked_for_again_in_words(self) -> None:
        result, transport = self.chat(reply(self.PROSE), reply('{"open_questions": ["Is it vegan?"]}'))
        self.assertEqual(client.parse_json_object(result.text), {"open_questions": ["Is it vegan?"]})
        self.assertTrue(result.repaired)
        self.assertEqual(len(transport.requests), 2)
        follow_up = transport.requests[1]["body"]["messages"]
        self.assertEqual(follow_up[-1], {"role": "user", "content": client.REPAIR_INSTRUCTION})
        self.assertEqual(follow_up[-2], {"role": "assistant", "content": self.PROSE})
        # The original instruction is still there: the repair is a turn in the same
        # conversation, not a fresh and shorter question.
        self.assertEqual(follow_up[0]["content"], "review this")

    def test_the_repair_is_attempted_once_and_not_twice(self) -> None:
        # A model that ignored the instruction twice will not obey a third time, and
        # the caller is waiting.
        result, transport = self.chat(reply(self.PROSE), reply("I still cannot do that."))
        self.assertEqual(result.text, self.PROSE)
        self.assertFalse(result.repaired)
        self.assertEqual(len(transport.requests), 2)

    def test_a_reply_that_already_carries_an_object_is_left_alone(self) -> None:
        for text in ('{"answer": "ok"}', "{}", "Here it is: {\"a\": 1}"):
            result, transport = self.chat(reply(text))
            self.assertEqual(len(transport.requests), 1, text)
            self.assertFalse(result.repaired)

    def test_a_caller_that_wants_prose_is_never_repaired(self) -> None:
        result, transport = self.chat(reply(self.PROSE), expect_json=False)
        self.assertEqual(result.text, self.PROSE)
        self.assertEqual(len(transport.requests), 1)

    def test_a_repair_that_fails_leaves_the_first_reply_intact(self) -> None:
        # The second turn can be refused, rate-limited or cut off. Neither case is a
        # reason to lose what the model did say.
        result, transport = self.chat(reply(self.PROSE), RuntimeError("connection reset"))
        self.assertEqual(result.text, self.PROSE)
        self.assertFalse(result.repaired)

    def test_both_turns_are_counted_in_what_the_call_cost(self) -> None:
        # A repair turn is a second billed call. Reporting only one of them would
        # understate the run, and the ledger is where that number goes.
        first = json.dumps({"choices": [{"message": {"content": self.PROSE}}], "usage": {"total_tokens": 40, "cost": 0.001}}).encode()
        second = json.dumps({"choices": [{"message": {"content": '{"a": 1}'}}], "usage": {"total_tokens": 25, "cost": 0.002}}).encode()
        result, _ = self.chat(first, second)
        self.assertTrue(result.repaired)
        self.assertEqual(result.usage["total_tokens"], 65)
        self.assertAlmostEqual(result.usage["cost"], 0.003)


class EnvFileTests(unittest.TestCase):
    """Where a local key actually comes from.

    ``.env.example`` says to copy it to ``.env`` and ``.gitignore`` keeps that file out
    of the repository, so something has to read it - otherwise the documented way to
    configure the layer silently does nothing.
    """

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="formusense-env-"))
        self.path = self.directory / ".env"

    def write(self, body: str) -> None:
        self.path.write_text(body, encoding="ascii", newline=chr(10))

    def test_a_key_in_the_env_file_configures_the_layer(self) -> None:
        self.write("OPENROUTER_API_KEY=sk-or-test-from-file" + chr(10))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn("OPENROUTER_API_KEY", config_module.load_env_file(self.path))
            settings = ai_settings()
        self.assertTrue(settings.configured)
        self.assertEqual(settings.provider, "openrouter")

    def test_the_environment_wins_over_the_file(self) -> None:
        # A deployment sets real variables; a file must never quietly override them.
        self.write("OPENROUTER_API_KEY=sk-or-test-from-file" + chr(10))
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-from-shell"}, clear=True):
            self.assertEqual(config_module.load_env_file(self.path), [])
            self.assertEqual(os.environ["OPENROUTER_API_KEY"], "sk-or-test-from-shell")

    def test_a_missing_file_is_not_an_error(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config_module.load_env_file(self.directory / "nope"), [])

    def test_comments_quotes_and_blank_lines_are_all_understood(self) -> None:
        self.write(
            chr(10).join([
                "# a comment",
                "",
                "OPENROUTER_MODEL=qwen/qwen3.8-27b:free   # free first",
                'OPENROUTER_APP_TITLE="FormuSense, with a # in it"',
                "not a setting",
                "=no name",
            ])
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = config_module.load_env_file(self.path)
            self.assertEqual(os.environ["OPENROUTER_MODEL"], "qwen/qwen3.8-27b:free")
            self.assertEqual(os.environ["OPENROUTER_APP_TITLE"], "FormuSense, with a # in it")
        self.assertEqual(sorted(loaded), ["OPENROUTER_APP_TITLE", "OPENROUTER_MODEL"])


if __name__ == "__main__":
    unittest.main()

