"""Logging: one place to configure the process, and a request id per request.

Kept deliberately small. The server already logs one line per API call; this
module gives those lines a consistent format, a level that can be set from the
environment, and a request id so the lines from one user action can be grouped.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

LOGGER_NAME = "formusense"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    return logger


def logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def summarise_path(path: str, max_length: int = 120) -> str:
    """A request path safe to log: query strings are dropped, long paths clipped."""
    base = str(path or "").split("?", 1)[0]
    return base[:max_length]


def log_level_from(value: Optional[str]) -> str:
    return str(value or "INFO").upper()
