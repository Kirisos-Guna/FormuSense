"""One OpenAI-compatible chat client, because the model layer is one dependency.

The system asked for an optional AI layer and originally got two of them: an OpenAI
function and a Gemini function, each with its own request shape, its own key variable
and its own copy of the error handling. Adding a third vendor that way would mean a
third copy, and a fourth.

OpenRouter removes the reason to have any of them. It is a single endpoint that
speaks the OpenAI chat format and routes to whichever vendor serves the model named
in the request, so one key reaches every model and the vendor behind it becomes a
string in a configuration table. Google publishes an OpenAI-compatibility endpoint
for the same reason, so the two original integrations collapse into the same code
path with a different base URL - which is why ``app.config`` holds a table of
endpoints rather than ``app.ai`` holding a function each.

Four properties are load-bearing, and each is enforced here rather than trusted:

* **Offline-first.** No key is not a degraded state, it is the default. ``chat``
  raises :class:`AiUnavailable` and every caller in the application falls back to
  its own offline implementation, which is what makes the test suite, the CI
  pipeline and the report reproducible.
* **Contained failure.** A provider that is down, rate-limited, slow or being
  handed a revoked key raises :class:`AiError` after every candidate has been tried.
  No caller lets that reach the user as a failure of the *product*: the design run
  completes with the offline result and records why the model was not used.
* **The key never leaves.** Errors are redacted before they become a message, and
  ``AiSettings.as_dict`` does not contain a key at all. A provider that echoes the
  Authorization header back in an error body is the case this is written for.
* **The network is a parameter.** ``transport`` defaults to the standard library and
  is replaced in tests, so the suite can assert the exact request without a socket
  and can never spend anybody's credit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..config import AiSettings
from ..config import ai_settings as _ai_settings

#: ``(url, headers, body, timeout) -> response bytes``. The seam that keeps the
#: network out of the tests.
Transport = Callable[[str, Dict[str, str], bytes, float], bytes]

#: Media types for the image extensions the uploader accepts.
IMAGE_TYPES: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_SECRET_IN_TEXT = re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)(?:bearer\s+)?[A-Za-z0-9._\-]{8,}")


class AiUnavailable(RuntimeError):
    """No provider key is configured, so the offline implementation is the answer."""


class AiError(RuntimeError):
    """A configured provider was asked and failed.

    Raised only after every candidate has been tried. The message names the
    providers and their errors, with credentials removed, and is short enough to
    put in front of a user.
    """


@dataclass
class ChatResult:
    """One model reply, with enough provenance to put in the record.

    ``call_id`` is the ``ai_calls`` row this reply was recorded as. It exists so a
    caller that does not yet have a product id - which is the case for the image
    description, whose hints belong in the brief that the product row stores - can
    adopt the row once the product exists.
    """

    text: str
    provider: str
    model: str
    cached: bool = False
    ms: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    call_id: Optional[int] = None
    repaired: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "cached": self.cached,
            "ms": self.ms,
            "usage": dict(self.usage),
            "call_id": self.call_id,
            "repaired": self.repaired,
        }


@dataclass
class TokenBudget:
    """How much of the hourly allowance has been spent.

    A cached reply does not count: it cost nothing, so charging it against the
    budget would make the cache useless for exactly the traffic it exists to
    absorb - a visitor reloading the same photograph.
    """

    used: int
    cap: int

    @property
    def exceeded(self) -> bool:
        return self.cap > 0 and self.used >= self.cap

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.used) if self.cap else 0

    def as_dict(self) -> Dict[str, Any]:
        return {"used": self.used, "cap": self.cap, "remaining": self.remaining, "exceeded": self.exceeded}


# --------------------------------------------------------------------------- #
# Transport and redaction
# --------------------------------------------------------------------------- #
def _default_transport(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> bytes:
    """POST and read the body. The only place in the system that opens a socket."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # pragma: no cover - network
        return response.read()


def redact(text: str, settings: AiSettings) -> str:
    """Remove anything key-shaped from a string before it becomes a message.

    Two passes, because one is not enough. The first replaces each configured key
    literally, which covers a provider that echoes the request back. The second
    catches a key that arrives by some other route - a proxy's error page, a
    redirect target - by matching the shape of an Authorization header in text.
    """
    out = str(text or "")
    for key in settings.keys.values():
        if key and len(key) > 3:
            out = out.replace(key, "***")
    out = _SECRET_IN_TEXT.sub(r"\1***", out)
    return out.strip()[:600]


def _http_detail(exc: Any) -> str:
    """The provider's own explanation, which is where the useful part usually is."""
    try:
        return exc.read().decode("utf-8", "replace").strip() or str(exc)
    except Exception:
        return str(exc)


def parse_json_object(text: str) -> Dict[str, Any]:
    """The JSON object inside a reply, or an empty object.

    Models wrap JSON in a sentence or a code fence often enough that the keys cannot
    be read directly, and return something that is not JSON at all often enough that
    this must not raise. An unparseable reply becomes an empty one, which every
    caller already handles: it is also what an absent key produces.
    """
    match = _JSON_OBJECT.search(text or "")
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def content_text(message: Dict[str, Any]) -> str:
    """The text of a message whose content may be a string or a list of parts."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n".join(chunks).strip()
    return ""


# --------------------------------------------------------------------------- #
# Request assembly
# --------------------------------------------------------------------------- #
def image_part(path: str) -> Optional[Dict[str, Any]]:
    """One image as an OpenAI-style ``image_url`` part, or ``None`` if unreadable.

    A remote URL is passed through - both providers fetch it themselves - and a local
    file is encoded into a data URL. Same key either way, so no caller has to branch
    on where the photograph came from.
    """
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return {"type": "image_url", "image_url": {"url": path}}
    if not os.path.exists(path):
        return None
    _, extension = os.path.splitext(path.lower())
    media = IMAGE_TYPES.get(extension, "image/png")
    try:
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        return None
    return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{encoded}"}}


def messages_with_images(
    messages: Sequence[Dict[str, Any]], parts: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Attach image parts to the last user message, which is where they belong.

    The vision request is one user turn that carries both the instruction and the
    photographs; splitting them into separate turns is a shape some providers accept
    and others quietly ignore, so it is not worth the risk to be tidier.
    """
    out = [dict(message) for message in messages]
    if not parts:
        return out
    target: Optional[Dict[str, Any]] = None
    for message in reversed(out):
        if str(message.get("role")) == "user":
            target = message
            break
    if target is None:
        out.append({"role": "user", "content": list(parts)})
    elif isinstance(target.get("content"), str):
        target["content"] = [{"type": "text", "text": target["content"]}, *parts]
    return out


def _headers(provider: str, key: str, settings: AiSettings) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    # OpenRouter's attribution headers. They decide which application the spend is
    # booked against, which matters as soon as one key is shared between a
    # development machine and a public demo.
    if provider == "openrouter":
        if settings.referer:
            headers["HTTP-Referer"] = settings.referer
        if settings.app_title:
            headers["X-Title"] = settings.app_title
    return headers


#: Statuses that mean "not now" rather than "not you": the provider is busy, the
#: free pool is shared, or the upstream model is being restarted. Anything else -
#: 401, 402, 404 - is a fact about the request or the account, and asking again
#: would only spend the time budget to receive the same answer.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 529})

#: How long to wait before asking a busy provider again. One retry of a rate-limited
#: free pool does not need a long wait, and a request a user is watching does not
#: want a long one either.
RETRY_PAUSE_SECONDS = 1.5


#: What is sent when a model answers in prose instead of the object it was asked for.
#: Free models do this intermittently - one reply is a tidy JSON object and the next is
#: an essay - and to the parser a prose reply and a reply that found nothing look
#: exactly alike. Since "found nothing" is a legitimate answer, the only way to tell
#: them apart is to ask again.
REPAIR_INSTRUCTION = (
    "That reply was not the JSON object asked for. Reply with only the JSON object "
    "described above, starting with { and ending with }: no prose, no markdown, no code "
    "fence, no commentary."
)


def _exchange(
    send: Transport, url: str, headers: Dict[str, str], body: bytes, wait: float
) -> Tuple[str, Dict[str, Any]]:
    """One HTTP round trip, as the reply text and the whole payload it arrived in."""
    raw = send(url, headers, body, wait)
    payload = json.loads(raw.decode("utf-8"))
    choices = payload.get("choices") or [{}]
    text = content_text(choices[0].get("message") or {})
    if not text:
        raise ValueError(_empty_reply_note(choices[0].get("finish_reason")))
    return text, payload


def _merged_usage(first: Any, second: Any) -> Dict[str, Any]:
    """What two replies cost, added together.

    A repair turn is a second billed call, so reporting only the second one's usage
    would understate what the run spent. The nested per-provider detail is left out of
    the sum: the totals are what the ledger, the budget and the report read.
    """
    out: Dict[str, Any] = dict(first) if isinstance(first, dict) else {}
    if not isinstance(second, dict):
        return out
    for key, value in second.items():
        if isinstance(value, (int, float)) and isinstance(out.get(key), (int, float)):
            out[key] = out[key] + value
        elif key not in out:
            out[key] = value
    return out


def _repair_reply(
    send: Transport,
    url: str,
    headers: Dict[str, str],
    model: str,
    conversation: List[Dict[str, Any]],
    limit: int,
    field: Optional[Dict[str, Any]],
    wait: float,
    text: str,
    payload: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], bool]:
    """Ask once more, in words, for the object. Returns the reply to use.

    One turn only: a model that ignored the instruction twice will not obey a third
    time, and if the second reply is prose as well then the first is kept - it is no
    worse, and it is the one the user can see the model actually said.
    """
    follow_up = list(conversation) + [
        {"role": "assistant", "content": text},
        {"role": "user", "content": REPAIR_INSTRUCTION},
    ]
    try:
        second, second_payload = _exchange(
            send, url, headers, _body(model, follow_up, limit, field), wait
        )
    except Exception:  # noqa: BLE001 - the first reply is still the answer of record
        return text, payload, False
    if "{" not in second:
        return text, payload, False
    usage = _merged_usage(payload.get("usage"), second_payload.get("usage"))
    return second, dict(payload, usage=usage), True


def _empty_reply_note(finish_reason: Any) -> str:
    """Why a reply with no text is a failure, in terms a reader can act on.

    A reasoning model that spends its whole token budget thinking returns nothing at
    all and says ``length``. That is not a broken provider and it is not a bad key, so
    the message has to name the one setting that would fix it.
    """
    if str(finish_reason or "") == "length":
        return "the model used its whole token budget before answering (raise FORMUSENSE_AI_MAX_TOKENS)"
    return "the reply carried no text"


def _failure(provider: str, model: str, exc: BaseException, config: AiSettings) -> str:
    """One line saying which model failed and why, with any key taken out of it.

    The model id is in the message because a provider can now be asked more than one
    model, and "openrouter failed" is not a diagnosis when a free variant was tried
    first: the reader needs to know which one was busy.
    """
    where = provider + "/" + model
    if isinstance(exc, urllib.error.HTTPError):
        return f"{where} (HTTP {exc.code}): {redact(_http_detail(exc), config)}"
    return f"{where}: {redact(str(exc), config)}"



def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return int(getattr(exc, "code", 0) or 0) in RETRYABLE_STATUS
    # A dropped connection, a timeout or a malformed reply is worth one more try:
    # none of them says anything about whether the next attempt would work.
    return isinstance(
        exc,
        (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError),
    )


def _body(
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    reasoning: Optional[Dict[str, Any]] = None,
) -> bytes:
    # No ``response_format``. Providers disagree about which of their models accept
    # it, and trading a working call for a tidier one - when the prompt already asks
    # for a bare JSON object - is the wrong way round.
    payload: Dict[str, Any] = {"model": model, "messages": messages, "max_tokens": int(max_tokens)}
    # Only sent when a caller asks for it, and only to the provider that understands
    # it: a reasoning budget spent on describing a photograph buys nothing and costs
    # both the wall clock and - because the thinking is billed as output - the money.
    if reasoning:
        payload["reasoning"] = reasoning
    return json.dumps(payload).encode("utf-8")


def prompt_hash(model: str, messages: List[Dict[str, Any]], max_tokens: int) -> str:
    """A stable key for one request.

    The model id and the conversation are both part of it, and the conversation
    already carries the image bytes as data URLs - so re-uploading the same
    photograph is answered from the cache, while switching models asks again. That
    is what a reader would expect of each.
    """
    digest = hashlib.sha256()
    digest.update(f"{model}|{int(max_tokens)}|".encode("utf-8"))
    digest.update(json.dumps(messages, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# The cache, and the hourly budget
# --------------------------------------------------------------------------- #
#: The cache is any object with ``ai_call_by_hash`` and ``save_ai_call``; the store
#: implements it. It is passed in rather than imported, so this package does not
#: depend on the persistence layer and can be tested without a database.
def _cache_lookup(cache: Any, fingerprint: str) -> Optional[Dict[str, Any]]:
    getter = getattr(cache, "ai_call_by_hash", None)
    if getter is None:
        return None
    try:
        return getter(fingerprint)
    except Exception:  # a broken cache is a cache miss, never a failed request
        return None


def _cache_store(
    cache: Any, product_id: Optional[int], kind: str, result: ChatResult, fingerprint: str
) -> Optional[int]:
    saver = getattr(cache, "save_ai_call", None)
    if saver is None:
        return None
    try:
        return int(saver(
            product_id=product_id,
            kind=kind,
            provider=result.provider,
            model=result.model,
            prompt_hash=fingerprint,
            text=result.text,
            cached=False,
            ms=result.ms,
            usage=result.usage,
        ))
    except Exception:  # a cache that cannot write must not fail the call
        return None


def usage_this_hour(cache: Any = None, settings: Optional[AiSettings] = None) -> TokenBudget:
    """How many *paid* calls this hour has already made.

    The hour is read from the clock rather than incremented in memory, so two
    processes - or two threads, which is how the server runs - cannot each believe
    they are the first caller.
    """
    config = settings or _ai_settings()
    cap = int(config.max_calls_per_hour or 0)
    counter = getattr(cache, "ai_calls_since", None)
    if counter is None:
        return TokenBudget(used=0, cap=cap)
    try:
        used = int(counter(time.strftime("%Y-%m-%dT%H:00:00")) or 0)
    except Exception:
        return TokenBudget(used=0, cap=cap)
    return TokenBudget(used=used, cap=cap)


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #
def chat(
    messages: Sequence[Dict[str, Any]],
    *,
    images: Optional[Sequence[str]] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    transport: Optional[Transport] = None,
    settings: Optional[AiSettings] = None,
    cache: Any = None,
    product_id: Optional[int] = None,
    kind: str = "chat",
    sleeper: Optional[Callable[[float], None]] = None,
    expect_json: bool = False,
) -> ChatResult:
    """Ask the first configured provider that answers, and report who answered.

    Candidates are tried in the order ``app.config`` lists them, which is OpenRouter
    first: one key, every vendor. Every candidate failing raises :class:`AiError`
    with all of their reasons, and no candidate at all raises
    :class:`AiUnavailable` - the two are different on purpose, because "nothing is
    configured" needs no explanation and "everything is configured and broken" does.

    Callers are expected to have checked :func:`usage_this_hour` first. The budget is
    not enforced here: this function cannot tell a paid call from a free one, and a
    guardrail that silently stops the feature is a worse demo than one that announces
    it has been reached.
    """
    config = settings or _ai_settings()
    candidates = config.providers()
    if not candidates:
        raise AiUnavailable("no AI provider key is configured")

    parts: List[Dict[str, Any]] = []
    for path in list(images or [])[: max(0, int(config.max_images))]:
        part = image_part(str(path))
        if part is not None:
            parts.append(part)
    conversation = messages_with_images(messages, parts)
    limit = int(max_tokens or config.max_tokens)
    wait = float(timeout or config.timeout)
    send = transport or _default_transport

    errors: List[str] = []
    pause = sleeper or time.sleep
    url = ""
    headers: Dict[str, str] = {}
    for provider, base_url, models, key in candidates:
        url = base_url.rstrip("/") + "/chat/completions"
        headers = _headers(provider, key, config)
        field = config.reasoning_field() if provider == "openrouter" else None
        # A provider may name more than one model. The free variant is asked first
        # where the environment lists it first, and the paid sibling answers when the
        # shared free pool is busy - which is what keeps a live demonstration from
        # depending on somebody else's traffic.
        for model in models:
            fingerprint = prompt_hash(model, conversation, limit)
            hit = _cache_lookup(cache, fingerprint)
            if hit is not None:
                return ChatResult(
                    text=str(hit.get("text") or ""),
                    provider=str(hit.get("provider") or provider),
                    model=str(hit.get("model") or model),
                    cached=True,
                    ms=0,
                    usage=dict(hit.get("usage") or {}),
                    call_id=int(hit["id"]) if hit.get("id") else None,
                )
            for attempt in range(int(config.retries) + 1):
                started = time.time()
                try:
                    text, payload = _exchange(
                        send, url, headers, _body(model, conversation, limit, field), wait
                    )
                except Exception as exc:  # noqa: BLE001 - every failure is the next model's turn
                    if attempt < int(config.retries) and _retryable(exc):
                        # The wait grows with the attempt, so two retries are not two
                        # identical requests in the same congested instant.
                        pause(RETRY_PAUSE_SECONDS * (attempt + 1))
                        continue
                    errors.append(_failure(provider, model, exc, config))
                    break
                repaired = False
                if expect_json and "{" not in text:
                    text, payload, repaired = _repair_reply(
                        send, url, headers, model, conversation, limit, field, wait, text, payload
                    )
                usage = payload.get("usage")
                result = ChatResult(
                    text=text,
                    provider=provider,
                    model=str(payload.get("model") or model),
                    cached=False,
                    ms=int((time.time() - started) * 1000),
                    usage=dict(usage) if isinstance(usage, dict) else {},
                )
                result.repaired = repaired
                result.call_id = _cache_store(cache, product_id, kind, result, fingerprint)
                return result
    raise AiError("; ".join(errors))
