"""Image understanding for the brief.

The agent is asked to understand a target product *from images and product
specifications*. Specifications are handled in :mod:`app.core.brief`; this module
handles the images.

It works in two modes, in this order of preference:

1. **Offline image analysis (always available).** Pillow is used to measure
   objective, reproducible properties of the reference photograph: mean lightness,
   dominant colour and its nearest named bucket, luminance heterogeneity (a proxy
   for crumb openness or inclusion load), edge density (a proxy for surface
   roughness or glaze), and uniformity of colour across the frame. These
   measurements are mapped onto product attributes - form, surface finish, colour
   family, evidence of inclusions - and onto hints such as "expect a darker
   crumb, target browning is high". This runs with no network, no key and no
   cost, and it is the mode the demo, the tests and the report all use.
2. **Multimodal model (optional).** When a key is configured and a reference image
   is supplied, the same question is put to a vision model, which returns the
   richer, semantic description (piece shape, surface detailing, apparent quality
   defects). OpenRouter is the provider this is designed around, because one key
   there reaches every vendor's models; ``OPENAI_API_KEY`` and ``GEMINI_API_KEY``
   are still honoured, through the same single call in :mod:`app.ai.client`.

   The result is treated as a *hint layer on top of the same structured schema*,
   never as the source of numbers, and that is enforced rather than promised:
   :func:`_accept_semantic` keeps only the eight description keys and drops
   anything else, so a reply that volunteers a protein content - or a weight, or a
   cost - cannot reach the brief even if the model insists.

   The call is also bounded: at most ``max_images`` photographs, a bounded reply
   length, a timeout, and an hourly ceiling shared by every product in the process
   (:func:`app.ai.client.usage_this_hour`). Over the ceiling the offline answer is
   returned with the reason attached, so the feature degrades rather than fails.

Whatever the mode, the output shape is identical, so the rest of the system -
and the report - never needs to branch on how the understanding was obtained.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..ai import AiError, AiUnavailable
from ..ai import client as ai_client
from ..ai import prompts as ai_prompts
from ..config import AiSettings, ai_settings

# Colour buckets used to name the dominant hue in plain language.
COLOUR_BUCKETS: Tuple[Tuple[str, Tuple[float, float, float]], ...] = (
    ("cream / pale", (238.0, 232.0, 214.0)),
    ("golden brown", (196.0, 148.0, 86.0)),
    ("brown", (132.0, 92.0, 58.0)),
    ("dark brown", (86.0, 58.0, 40.0)),
    ("red / berry", (168.0, 60.0, 56.0)),
    ("orange / mango", (226.0, 150.0, 60.0)),
    ("green", (110.0, 148.0, 88.0)),
    ("off-white powder", (246.0, 244.0, 236.0)),
    ("grey", (150.0, 150.0, 148.0)),
)

FORM_BY_ASPECT = (
    (1.9, "round piece / disc"),
    (1.25, "rectangular slab or bar"),
    (1.0, "square piece"),
    (0.0, "tall piece or deep fill"),
)

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"


def pillow_available() -> bool:
    """Whether the offline measurement path can actually decode an image here."""
    try:  # pragma: no cover - import guard only
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def vision_available(
    settings: Optional[AiSettings] = None, cache: Any = None
) -> Dict[str, Any]:
    """Report which understanding modes this environment can actually offer.

    ``configured`` is the field the status pill reads, and it is the one that has to
    be right: the badge is the single line a reviewer reads to decide whether the model
    layer is really on, so a deployment with a working key has to be able to say so.
    ``provider`` and ``model`` are reported for the record and the API - the interface
    names the model and not the platform behind it.
    """
    config = settings or ai_settings()
    budget = ai_client.usage_this_hour(cache, config)
    keys = {name for name, *_ in config.providers()}
    mode = "offline-image-analysis"
    if config.provider:
        mode = "offline-image-analysis + " + config.provider + "-vision"
    return {
        "offline_analysis": True,
        "pillow": pillow_available(),
        "configured": config.configured,
        "provider": config.provider or None,
        # The model it would be asked first: the chain is in ai_settings for a
        # reader who wants to know what happens when that one is busy.
        "model": config.primary_model(config.provider) if config.provider else None,
        "base_url": config.base_urls.get(config.provider) if config.provider else None,
        # The two original key names stay in the payload: the interface reads them,
        # and they were part of this response before OpenRouter existed.
        "openrouter": "openrouter" in keys,
        "openai": "openai" in keys,
        "gemini": "gemini" in keys,
        "active_mode": mode,
        "budget": budget.as_dict(),
        "note": (
            "Offline measurement of colour, lightness and texture proxies always runs. "
            "A multimodal model is used only when an API key is configured and the run "
            "asks for it; it adds a semantic description and never invents numeric targets."
        ),
    }



# --------------------------------------------------------------------------- #
# Image loading
# --------------------------------------------------------------------------- #
def _decode_data_url(data_url: str) -> Optional[bytes]:
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", data_url or "", re.DOTALL)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(2))
    except Exception:
        return None


def save_upload(name: str, data_url: str) -> Optional[str]:
    """Persist an uploaded reference image and return its path."""
    payload = _decode_data_url(data_url)
    if not payload:
        return None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name or "reference.png")
    if not safe.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        safe += ".png"
    path = UPLOAD_DIR / safe
    path.write_bytes(payload)
    return str(path)


def _pillow_features(path: str) -> Optional[Dict[str, Any]]:
    """Measure objective image properties. Returns ``None`` if the file is unreadable."""
    try:
        from PIL import Image, ImageFilter, ImageStat
    except Exception:  # pragma: no cover - Pillow is optional at runtime
        return None
    try:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
            width, height = image.size
            small = image.resize((160, 160))
            stat = ImageStat.Stat(small)
            mean = tuple(float(v) for v in stat.mean)
            rgb_std = tuple(float(v) for v in stat.stddev)

            grey = small.convert("L")
            grey_stat = ImageStat.Stat(grey)
            lightness = float(grey_stat.mean[0])
            luminance_std = float(grey_stat.stddev[0])

            edges = grey.filter(ImageFilter.FIND_EDGES)
            edge_mean = float(ImageStat.Stat(edges).mean[0])

            # Coarse colour spread across a 4x4 grid: how uniform is the product?
            tile_means: List[float] = []
            for row in range(4):
                for col in range(4):
                    box = (col * 40, row * 40, (col + 1) * 40, (row + 1) * 40)
                    tile_means.append(float(ImageStat.Stat(grey.crop(box)).mean[0]))
            spread = max(tile_means) - min(tile_means)
            mean_tile = sum(tile_means) / len(tile_means)
            uniformity = 1.0 - min(spread / max(mean_tile, 1e-6), 1.0)

            aspect = width / max(height, 1)
            return {
                "width": width,
                "height": height,
                "aspect": round(aspect, 3),
                "lightness": round(lightness, 2),
                "rgb_mean": [round(v, 1) for v in mean],
                "rgb_std": [round(v, 1) for v in rgb_std],
                "luminance_std": round(luminance_std, 2),
                "edge_density": round(edge_mean, 2),
                "colour_uniformity": round(uniformity, 3),
            }
    except Exception:
        return None


def _dominant_colour_name(rgb: Tuple[float, float, float]) -> Tuple[str, float]:
    best = None
    best_distance = 1e9
    for name, reference in COLOUR_BUCKETS:
        distance = sum((a - b) ** 2 for a, b in zip(rgb, reference)) ** 0.5
        if distance < best_distance:
            best, best_distance = name, distance
    assert best is not None
    # Map a 0-441 RGB distance to a 0-1 confidence.
    confidence = max(0.0, 1.0 - best_distance / 260.0)
    return best, round(confidence, 3)


def _form_from_aspect(aspect: float) -> str:
    for threshold, label in FORM_BY_ASPECT:
        if aspect >= threshold:
            return label
    return "undetermined"


def interpret_features(features: Dict[str, Any], category_hint: str = "") -> Dict[str, Any]:
    """Turn raw measurements into product attributes and formulation hints.

    The mapping is deliberately conservative and every inference is labelled as an
    inference, so a developer can see exactly which statements came from a
    measurement and which came from an interpretation of it.
    """
    rgb = tuple(features.get("rgb_mean") or (200.0, 180.0, 160.0))
    colour_name, colour_confidence = _dominant_colour_name(rgb)  # type: ignore[arg-type]
    lightness = float(features.get("lightness", 190.0))
    lum_std = float(features.get("luminance_std", 20.0))
    edge = float(features.get("edge_density", 6.0))
    uniformity = float(features.get("colour_uniformity", 0.8))
    aspect = float(features.get("aspect", 1.0))

    # Surface finish from edge density.
    if edge >= 22.0:
        finish = "rough, open or granular surface"
    elif edge >= 12.0:
        finish = "matte with visible texture"
    elif edge >= 6.0:
        finish = "smooth, low sheen"
    else:
        finish = "glossy or highly polished surface"

    # Inclusions from luminance heterogeneity.
    if lum_std >= 42.0:
        inclusions = "strongly heterogeneous surface - inclusions or toppings likely"
        inclusion_pct = 10.0
    elif lum_std >= 28.0:
        inclusions = "moderately uneven surface - some particulates likely"
        inclusion_pct = 6.0
    else:
        inclusions = "homogeneous surface - no obvious particulates"
        inclusion_pct = 0.0

    browning = "light" if lightness >= 205 else ("medium" if lightness >= 165 else "dark")
    hints: List[str] = []
    if browning == "dark":
        hints.append(
            "The reference is dark: browning is part of the target. Expect bake/roast "
            "temperature or cocoa content to sit at the higher end of the range, and "
            "watch acrylamide-forming time-temperature combinations."
        )
    elif browning == "light":
        hints.append(
            "The reference is pale: browning must be limited, so prefer lower bake "
            "temperature with longer time, or a covered process."
        )
    if inclusion_pct > 0:
        hints.append(
            f"Allow roughly {inclusion_pct:.0f}% of the formula for visible inclusions "
            "if the appearance is part of the specification."
        )
    if uniformity < 0.55:
        hints.append(
            "Colour is uneven across the frame: the product may be marbled, dusted or "
            "carried in a tray - piece-to-piece colour variation should be a sensory "
            "check at the first trial."
        )
    if "powder" in " ".join(h or "" for h in [category_hint]) or colour_name == "off-white powder":
        hints.append("Appearance is consistent with a dry powder or premix: particle size and free-flow matter more than shape.")

    return {
        "form": _form_from_aspect(aspect),
        "aspect_ratio": round(aspect, 3),
        "dominant_colour": colour_name,
        "dominant_colour_rgb": [int(round(v)) for v in rgb],
        "dominant_colour_confidence": colour_confidence,
        "lightness_0_255": round(lightness, 1),
        "browning_band": browning,
        "surface_finish": finish,
        "inclusion_evidence": inclusions,
        "suggested_inclusion_pct": inclusion_pct,
        "colour_uniformity": round(uniformity, 3),
        "interpretation_hints": hints,
    }


def offline_describe(paths: List[str], category_hint: str = "") -> Dict[str, Any]:
    """Describe the reference images using measurement only (no network)."""
    observations: List[Dict[str, Any]] = []
    for path in paths:
        local = path
        if path.startswith("http://") or path.startswith("https://"):
            observations.append(
                {
                    "source": path,
                    "status": "skipped",
                    "reason": "remote image can only be analysed when a vision API key is configured",
                }
            )
            continue
        if not os.path.exists(local):
            observations.append({"source": path, "status": "missing", "reason": "file not found"})
            continue
        features = _pillow_features(local)
        if features is None:
            observations.append({"source": path, "status": "unreadable", "reason": "not a decodable raster image"})
            continue
        observations.append(
            {
                "source": local,
                "status": "analysed",
                "features": features,
                "attributes": interpret_features(features, category_hint),
            }
        )
    analysed = [o for o in observations if o.get("status") == "analysed"]
    summary_hints: List[str] = []
    for observation in analysed:
        for hint in observation["attributes"].get("interpretation_hints", []):
            if hint not in summary_hints:
                summary_hints.append(hint)
    return {
        "source": "offline-image-analysis",
        "images": observations,
        "analysed_count": len(analysed),
        "hints": summary_hints,
    }


# --------------------------------------------------------------------------- #
# Optional multimodal path
# --------------------------------------------------------------------------- #
#: The prompt lives with the other prompts, so everything the agent says to a model
#: can be read in one place. The name is repeated here because this module is where a
#: reader looks for it.
VISION_PROMPT = ai_prompts.VISION_PROMPT

#: Any key whose *name* looks like a composition value is dropped from a reply,
#: however plausibly it is written. The models in ``app.core`` own those numbers. A
#: language model that supplied one would be supplying something indistinguishable
#: from a measurement, and the record could no longer say which it was.
_NUMERIC_KEY = re.compile(
    r"(?i)(protein|fat|sugar|carb|fibre|fiber|sodium|salt|moisture|calorie|energy|"
    r"kcal|kj|cost|price|density|weight|volume|mass|percent|pct|water_activity)"
)

_MAX_TEXT = 240
_MAX_ITEMS = 6
_MAX_ITEM = 120


def _clean_text(value: Any, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_list(value: Any) -> List[str]:
    """A list of short strings, from whatever the model actually returned."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value[: _MAX_ITEMS * 2]:
        if isinstance(item, dict):
            item = item.get("text") or item.get("value") or item.get("name") or ""
        text = _clean_text(item, _MAX_ITEM)
        if text:
            out.append(text)
        if len(out) >= _MAX_ITEMS:
            break
    return out


def _accept_semantic(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of a model's reply this system is willing to store.

    Eight keys, by name; anything else is discarded. A key that names a composition
    value is discarded specifically, because "describe this product" is an invitation
    a helpful model will sometimes answer with a protein content, and a number from a
    language model must never become a number on the brief. Scalars are flattened to
    a single line and truncated, and lists are capped, so a reply cannot grow the
    record without bound.
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ai_prompts.VISION_KEYS:
        if key not in raw or _NUMERIC_KEY.search(key):
            continue
        value = raw[key]
        if key in ("visible_inclusions", "apparent_defects"):
            out[key] = _clean_list(value)
        elif key == "confidence":
            try:
                out[key] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
        else:
            text = _clean_text(value)
            if text:
                out[key] = text
    return out


def model_describe(
    paths: List[str],
    *,
    settings: Optional[AiSettings] = None,
    cache: Any = None,
    product_id: Optional[int] = None,
    transport: Optional[ai_client.Transport] = None,
) -> Dict[str, Any]:
    """Ask a vision model about the reference images, through the one client.

    Returned in the same ``{"source", "raw", "parsed"}`` shape the two per-vendor
    functions used to return, with the provenance added on top - so this module's
    callers did not have to learn a new shape when two vendor functions collapsed
    into one provider table.
    """
    config = settings or ai_settings()
    result = ai_client.chat(
        [{"role": "user", "content": ai_prompts.VISION_PROMPT}],
        images=paths,
        settings=config,
        cache=cache,
        product_id=product_id,
        transport=transport,
        kind="vision",
        # A description that cannot be parsed is a description nobody can read, and the
        # free models this defaults to answer in prose now and then.
        expect_json=True,
    )
    return {
        "source": result.provider + "-vision",
        "raw": result.text,
        "parsed": _accept_semantic(ai_client.parse_json_object(result.text)),
        "provider": result.provider,
        "model": result.model,
        "cached": result.cached,
        "ms": result.ms,
        "call_id": result.call_id,
    }


def describe_images(
    paths: List[str],
    category_hint: str = "",
    *,
    use_model: bool = True,
    settings: Optional[AiSettings] = None,
    cache: Any = None,
    product_id: Optional[int] = None,
    transport: Optional[ai_client.Transport] = None,
) -> Dict[str, Any]:
    """Understand the reference images: measurement always, model when asked for.

    The model is asked only when four things hold at once: the caller asked for it, a
    key is configured, there is an image something can read, and this hour's budget
    has not been spent. Every other outcome is a *reason* rather than an error, and
    it is reported in ``model_note`` - so the interface can say why a description is
    thin instead of leaving the user to wonder whether the feature is broken.
    """
    config = settings or ai_settings()
    result = offline_describe(paths, category_hint)
    result["source"] = "offline-image-analysis"
    result["model_note"] = ""
    result["model"] = None
    result["semantic"] = None

    if not use_model:
        result["model_note"] = "the model was not requested for this run"
    elif not config.configured:
        result["model_note"] = "no AI provider key is configured, so this is the offline description"
    elif not paths:
        result["model_note"] = "no reference images were supplied"
    else:
        # The condition is about the *file*, not about Pillow. A machine without
        # Pillow gets an empty offline measurement, but the vision model decodes the
        # image itself and can still describe it - so gating the model on a Pillow
        # result would silently switch the whole model layer off on exactly the
        # installations that have nothing else to fall back on.
        readable = any(str(p).startswith("http") or os.path.exists(str(p)) for p in paths)
        if not readable:
            result["model_note"] = "no reference image could be read"
        else:
            budget = ai_client.usage_this_hour(cache, config)
            if budget.exceeded:
                result["model_note"] = (
                    "the hourly model budget is spent ("
                    + str(budget.used)
                    + "/"
                    + str(budget.cap)
                    + "), so this is the offline description"
                )
            else:
                try:
                    semantic = model_describe(
                        paths,
                        settings=config,
                        cache=cache,
                        product_id=product_id,
                        transport=transport,
                    )
                except AiUnavailable as exc:
                    result["model_note"] = "no AI provider key is configured (" + str(exc) + ")"
                except AiError as exc:
                    # A provider that fails is a sentence on the record, not a failure
                    # of the product. The offline description is still a description.
                    result["semantic_error"] = str(exc)
                    result["model_note"] = "the model could not be reached: " + str(exc)
                else:
                    result["semantic"] = semantic
                    result["model"] = {
                        "provider": semantic["provider"],
                        "model": semantic["model"],
                        "cached": semantic["cached"],
                        "ms": semantic["ms"],
                        "call_id": semantic["call_id"],
                    }
                    result["source"] = result["source"] + " + " + semantic["source"]
    result["available"] = vision_available(config, cache)
    return result


def description_lines(understanding: Optional[Dict[str, Any]]) -> List[str]:
    """The semantic description as short lines, for the interface and the report.

    Kept here rather than in the interface so the browser and the written report say
    the same thing about the same image, in the same words, with the same
    attribution. A description that reads differently in two places is worse than one
    place having no description at all.
    """
    data = understanding or {}
    semantic = (data.get("semantic") or {}).get("parsed") or {}
    provider = (data.get("model") or {}).get("model")
    prefix = ("Model description (" + str(provider) + "): ") if provider else "Model description: "
    lines: List[str] = []
    if semantic.get("product_form"):
        lines.append(prefix + semantic["product_form"])
    for key, label in (
        ("surface_finish", "surface"),
        ("dominant_colour", "colour"),
        ("shape_and_size_notes", "shape and size"),
    ):
        if semantic.get(key):
            lines.append(label.capitalize() + ": " + semantic[key])
    for note in semantic.get("visible_inclusions") or []:
        lines.append("Visible inclusion: " + note)
    for note in semantic.get("apparent_defects") or []:
        lines.append("Apparent defect: " + note)
    return lines


def description_lines_from_record(text: str, model: str = "") -> List[str]:
    """The description lines for a reply that is already stored.

    The interface and the report both re-render a description from the record rather
    than from the response that produced it, so the two cannot drift and a description
    survives a reload. The stored text goes through the same acceptance filter as a
    fresh reply: the table keeps whatever the provider sent, and only this function
    decides what counts as a description.
    """
    semantic = _accept_semantic(ai_client.parse_json_object(text or ""))
    understanding: Dict[str, Any] = {"semantic": {"parsed": semantic}}
    if model:
        understanding["model"] = {"model": model}
    return description_lines(understanding)


def apply_hints_to_payload(payload: Dict[str, Any], understanding: Dict[str, Any]) -> Dict[str, Any]:
    """Fold image understanding back into a brief payload as non-destructive hints."""
    augmented = dict(payload)
    hints: List[str] = list(understanding.get("hints") or [])
    semantic = (understanding.get("semantic") or {}).get("parsed") or {}
    if semantic:
        if semantic.get("process_hypothesis"):
            hints.append("Vision model process hypothesis: " + str(semantic["process_hypothesis"]))
        for defect in semantic.get("apparent_defects") or []:
            hints.append("Vision model flagged: " + str(defect))
    augmented["vision_hints"] = hints
    augmented["vision_source"] = understanding.get("source")
    # The provenance travels with the description. A report that says a model
    # described the photograph, without saying which model and whether the answer came
    # from the cache, is a claim nobody can check.
    augmented["vision_model"] = (understanding.get("model") or {}).get("model")
    augmented["vision_note"] = understanding.get("model_note") or ""
    return augmented
