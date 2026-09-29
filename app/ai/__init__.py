"""The optional model layer: one provider-agnostic client and its prompts.

Nothing in this package runs unless a key is configured, and nothing in it is
imported by the parts of the system that produce numbers.
"""
from __future__ import annotations

from .client import AiError, AiUnavailable, ChatResult, TokenBudget, chat, usage_this_hour

__all__ = [
    "AiError",
    "AiUnavailable",
    "ChatResult",
    "TokenBudget",
    "chat",
    "usage_this_hour",
]
