"""Runtime configuration, read from the environment with safe defaults.

The offline promise is the default: with no environment set, the application uses
the bundled SQLite file and needs nothing installed. Pointing
``FORMUSENSE_DB_URL`` at a PostgreSQL server switches the same code onto a real
database server; the driver is imported lazily, so a machine without it still
runs the SQLite path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = ROOT / "data" / "formusense.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


#: The file a local key or setting is kept in. It is git-ignored, and it is never
#: required: the environment is the real source of configuration, and this file only
#: fills in what is not already there.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Optional[Path] = None) -> List[str]:
    """Read ``KEY=value`` lines from ``.env`` into the environment.

    ``.env.example`` says to copy it to ``.env`` and ``.gitignore`` keeps that file out
    of the repository - so something has to read it, or the documented way to set a key
    would silently do nothing. A name already present in the environment wins, because
    that is what a deployment sets and a file must never quietly override it.

    Returns the names it set, so the launcher can say what it picked up.
    """
    target = Path(path) if path is not None else ENV_FILE
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        # A trailing comment is stripped only when the value is not quoted, so a value
        # that contains a hash keeps it.
        value = value.strip()
        if value[:1] in ("'", '"'):
            value = value[1:].split(value[0], 1)[0]
        else:
            value = value.split("#", 1)[0].strip()
        if not name or name in os.environ:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _port() -> int:
    """The port to serve on.

    ``FORMUSENSE_PORT`` is the project's own setting; ``PORT`` is what a hosting
    platform injects (Render, and most of its neighbours). The project's variable
    wins when both are set, so a container can be pointed somewhere deliberately,
    and the platform's is followed when it is the only one - which is the case on a
    deployment, where nobody types a port at all.

    The two are not read with the same tolerance. A typo in our own variable raises,
    because somebody meant to say something; a ``PORT`` that is not a usable port is
    ignored, because shells and tooling export things like ``PORT=0`` by habit and
    following that would move the interface to a port the kernel picks, where nobody
    would find it.
    """
    own = _env("FORMUSENSE_PORT")
    if own:
        return int(own)
    try:
        platform = int(_env("PORT"))
    except ValueError:
        return 8770
    return platform if 0 < platform < 65536 else 8770


@dataclass(frozen=True)
class Settings:
    db_url: str
    host: str
    port: int
    auth_token: str
    log_level: str
    log_requests: bool

    @property
    def is_postgres(self) -> bool:
        return self.db_url.startswith(("postgres://", "postgresql://"))

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite:")

    def redacted_db_url(self) -> str:
        """The database URL with any password removed, safe to log or display."""
        if "@" not in self.db_url:
            return self.db_url
        scheme, _, rest = self.db_url.partition("://")
        credentials, _, host = rest.rpartition("@")
        user = credentials.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"

    def as_dict(self) -> Dict[str, object]:
        return {
            "db_url": self.redacted_db_url(),
            "database": "postgresql" if self.is_postgres else "sqlite",
            "host": self.host,
            "port": self.port,
            "auth_required": bool(self.auth_token),
            "log_level": self.log_level,
        }


def settings(overrides: Optional[Dict[str, object]] = None) -> Settings:
    values = {
        "db_url": _env("FORMUSENSE_DB_URL", DEFAULT_DB_URL),
        "host": _env("FORMUSENSE_HOST", "127.0.0.1"),
        "port": _port(),
        "auth_token": _env("FORMUSENSE_AUTH_TOKEN"),
        "log_level": _env("FORMUSENSE_LOG_LEVEL", "INFO").upper(),
        "log_requests": _env("FORMUSENSE_LOG_REQUESTS", "1") not in ("0", "false", "False"),
    }
    if overrides:
        values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)  # type: ignore[arg-type]


def sqlite_path_from_url(url: str) -> Optional[Path]:
    if not url.startswith("sqlite:"):
        return None
    _, _, tail = url.partition("sqlite:///")
    if not tail or tail == ":memory:":
        return None
    return Path(tail)


# --------------------------------------------------------------------------- #
# The optional AI layer
# --------------------------------------------------------------------------- #
#: Providers the model layer may call, in the order it prefers them. OpenRouter
#: comes first because a single key reaches every vendor behind one
#: OpenAI-compatible endpoint, so a deployment needs exactly one secret and no
#: vendor account of its own. The other two stay supported: they were the original
#: integrations, and somebody who already holds one of those keys should not have
#: to buy a second.
AI_PROVIDER_ORDER: Tuple[str, ...] = ("openrouter", "openai", "gemini")

#: Where each provider's key is read from.
AI_KEY_ENV: Dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

#: Default endpoint per provider. Gemini is reached through Google's
#: OpenAI-compatibility endpoint rather than its native ``generateContent`` shape,
#: and that is what lets one request body serve all three providers: no per-vendor
#: branch, and a new vendor is a row in this table rather than a new function.
AI_DEFAULT_BASE_URL: Dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

#: Default model per provider: free, vision-capable and good at JSON, so the
#: demonstration costs nothing to run and needs no credit balance.
#:
#: A value may be a comma-separated list, tried in order, and the default is one:
#: OpenRouter's free models are served from a shared pool that answers 429 whenever
#: somebody else is using it, and one model's pool is not another's. The three below
#: were each asked for a real description of a test image and each answered with all
#: eight expected keys; the first is the one to prefer, the others are what answers
#: when its pool is busy. Nothing paid is named, so a run cannot spend: if every free
#: model refuses, the run says so and the offline layers answer instead.
AI_DEFAULT_MODEL: Dict[str, str] = {
    "openrouter": (
        "qwen/qwen3.8-27b:free,"
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,"
        "dots-studio/dots-3-note-preview:free"
    ),
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}

#: How much a model is allowed to think before answering. The three prompts in this
#: application - describe a photograph, review a specification, answer from the
#: record - are extraction rather than reasoning: on the model this project defaults
#: to, turning the thinking off halves the wall-clock time and cuts the output tokens
#: by an order of magnitude, and the output tokens are the expensive half.
#: ``off``, ``low``, ``medium``, ``high``, or anything else to leave it to the model.
AI_REASONING_CHOICES: Tuple[str, ...] = ("off", "low", "medium", "high")


def reasoning_parameter(value: str) -> Optional[Dict[str, object]]:
    """The request field for a reasoning setting, or ``None`` to say nothing.

    Only OpenRouter is sent this field: it is an extension of the OpenAI-compatible
    shape rather than part of it, and a provider that does not know it may refuse the
    whole request rather than ignore one key.
    """
    choice = str(value or "").strip().lower()
    if choice == "off":
        return {"enabled": False}
    if choice in AI_REASONING_CHOICES:
        return {"effort": choice}
    return None


#: The per-provider model override, as a comma-separated list of ids. The two
#: original names are deliberately kept, because they are what the README and the
#: existing deployment already document.
AI_MODEL_ENV: Dict[str, str] = {
    "openrouter": "OPENROUTER_MODEL",
    "openai": "OPENAI_VISION_MODEL",
    "gemini": "GEMINI_VISION_MODEL",
}


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def model_chain(value: str) -> List[str]:
    """The model ids in a setting, in the order they should be tried.

    One setting names one model, or several separated by commas. The list is what
    the client walks, so a fallback is a line in the environment rather than a
    branch in the request code.
    """
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _number(name: str, default: float) -> float:
    """A numeric setting, or the default when it is absent or unreadable.

    A typo here must not stop the application: the AI layer is optional, so a
    malformed value falls back rather than raising, unlike ``FORMUSENSE_PORT``,
    where somebody clearly meant to say something and silence would be worse.
    """
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class AiSettings:
    """What the model layer needs, read once from the environment.

    Only the key, the endpoint and the model id differ between providers, so those
    are held per provider and resolved on demand; every other setting is shared.
    An instance with ``provider == ""`` is a valid, fully-featured configuration:
    it is what the application runs on when nobody has supplied a key, and every
    capability falls back to its offline implementation.
    """

    provider: str
    keys: Dict[str, str]
    base_urls: Dict[str, str]
    models: Dict[str, str]
    referer: str
    app_title: str
    timeout: float
    max_tokens: int
    max_images: int
    max_calls_per_hour: int
    retries: int
    reasoning: str

    @property
    def configured(self) -> bool:
        """True when at least one provider key is present and the layer is on."""
        return bool(self.provider)

    def reasoning_field(self) -> Optional[Dict[str, object]]:
        """What to put in the request body about thinking, if anything."""
        return reasoning_parameter(self.reasoning)

    def model_chain(self, name: str) -> List[str]:
        """The model ids to try, in order, for one provider."""
        return model_chain(self.models.get(name) or "")

    def primary_model(self, name: str) -> str:
        """The model a provider is described by: the first one it would be asked."""
        chain = self.model_chain(name)
        return chain[0] if chain else ""

    def providers(self) -> List[Tuple[str, str, List[str], str]]:
        """``(provider, base_url, models, key)`` in the order they should be tried.

        Only providers that actually hold a key appear, so the fallback chain is a
        consequence of what is configured rather than of a condition anywhere in
        the calling code. The models are a list, tried in order, so one provider can
        carry its own fallback.
        """
        out: List[Tuple[str, str, List[str], str]] = []
        for name in AI_PROVIDER_ORDER:
            key = self.keys.get(name) or ""
            if not key:
                continue
            out.append((name, self.base_urls[name], self.model_chain(name), key))
        return out

    def as_dict(self) -> Dict[str, object]:
        """The non-secret view, safe to log and safe to return from the API.

        The keys themselves never appear here - not masked, not truncated. Only
        whether one is present, what it would be used with, and what the guardrails
        are. An interface that can tell you the key is set but never what it is
        cannot leak it by accident.
        """
        provider = self.provider
        return {
            "configured": self.configured,
            "provider": provider or None,
            "providers": [name for name, *_ in self.providers()],
            "model": self.primary_model(provider) if provider else None,
            # The whole chain, not just the model it starts with: a run that falls
            # back to a paid model must be visible as having done so.
            "models": {name: chain for name, _, chain, _ in self.providers()},
            "base_url": self.base_urls.get(provider) if provider else None,
            "timeout_seconds": self.timeout,
            "max_tokens": self.max_tokens,
            "max_images": self.max_images,
            "max_calls_per_hour": self.max_calls_per_hour,
            "retries": self.retries,
            "reasoning": self.reasoning or "provider default",
            "attribution": self.app_title or None,
        }


def ai_settings(overrides: Optional[Dict[str, object]] = None) -> AiSettings:
    """Read the AI layer's configuration. No key means the layer is simply off.

    ``FORMUSENSE_AI=0`` is an explicit off switch, for a machine that has a key in
    its environment for other tools and does not want this application spending
    against it. ``FORMUSENSE_AI_MODEL`` overrides the model for every provider at
    once, which is the fastest way to try a different one without deciding which
    vendor it belongs to.
    """
    enabled = _flag("FORMUSENSE_AI", True)
    keys: Dict[str, str] = {}
    if enabled:
        for name in AI_PROVIDER_ORDER:
            value = _env(AI_KEY_ENV[name])
            if value:
                keys[name] = value

    shared_model = _env("FORMUSENSE_AI_MODEL")
    models: Dict[str, str] = {}
    base_urls: Dict[str, str] = {}
    for name in AI_PROVIDER_ORDER:
        models[name] = shared_model or _env(AI_MODEL_ENV[name]) or AI_DEFAULT_MODEL[name]
        base_urls[name] = _env(f"{name.upper()}_BASE_URL") or AI_DEFAULT_BASE_URL[name]

    values: Dict[str, object] = {
        "provider": next((name for name in AI_PROVIDER_ORDER if name in keys), ""),
        "keys": keys,
        "base_urls": base_urls,
        "models": models,
        "referer": _env("OPENROUTER_HTTP_REFERER"),
        "app_title": _env("OPENROUTER_APP_TITLE", "FormuSense"),
        "timeout": max(1.0, _number("FORMUSENSE_AI_TIMEOUT", 45.0)),
        "max_tokens": max(64, int(_number("FORMUSENSE_AI_MAX_TOKENS", 700))),
        "max_images": max(1, int(_number("FORMUSENSE_AI_MAX_IMAGES", 3))),
        "max_calls_per_hour": max(0, int(_number("FORMUSENSE_AI_MAX_CALLS_PER_HOUR", 60))),
        # A shared free pool answers 429 while somebody else is using it. A refused
        # request costs nothing, so a couple of retries are free in money and only
        # cost a few seconds - the difference between a demo that works and one that
        # needs apologising for.
        "retries": min(5, max(0, int(_number("FORMUSENSE_AI_RETRIES", 2)))),
        "reasoning": (_env("FORMUSENSE_AI_REASONING", "off") or "off").strip().lower(),
    }
    if overrides:
        values.update({k: v for k, v in overrides.items() if v is not None})
        # The preferred provider follows whatever keys survived the override, so a
        # test that injects one key does not also have to remember to inject which
        # provider it belongs to.
        if "keys" in overrides:
            surviving = values["keys"] if isinstance(values["keys"], dict) else {}
            values["provider"] = next((name for name in AI_PROVIDER_ORDER if name in surviving), "")
    return AiSettings(**values)  # type: ignore[arg-type]
