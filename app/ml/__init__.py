"""Local, dependency-free learning: a dataset, a train/test protocol, a model.

Nothing in this package calls a network API or needs a key. The algorithms are
implemented in plain Python over the standard library so the whole system still
runs from a clean checkout, and the trained coefficients are stored as JSON.
"""
from __future__ import annotations

from .features import FEATURE_VERSION, extract, feature_names
from .registry import ModelBundle, latest_bundle, load_bundle, save_bundle

__all__ = [
    "FEATURE_VERSION",
    "extract",
    "feature_names",
    "ModelBundle",
    "latest_bundle",
    "load_bundle",
    "save_bundle",
]
