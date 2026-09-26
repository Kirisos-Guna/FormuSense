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
2. **Multimodal model (optional).** If ``OPENAI_API_KEY`` or ``GEMINI_API_KEY`` is
   present and a reference image is supplied, the same question is put to a
   vision model, which returns the richer, semantic description (piece shape,
   surface detailing, apparent quality defects). The result is treated as a
   *hint layer on top of the same structured schema*, never as the source of
   numbers: a vision model must not be allowed to invent a target.

Whatever the mode, the output shape is identical, so the rest of the system -
and the report - never needs to branch on how the understanding was obtained.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def vision_available() -> Dict[str, Any]:
    """Report which understanding modes are available in this environment."""
    offline = True
    pillow = False
    try:  # pragma: no cover - import guard only
        import PIL  # noqa: F401

        pillow = True
    except Exception:
        pillow = False
    openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    mode = "offline-image-analysis"
    if pillow and openai_key:
        mode = "offline-image-analysis + openai-vision"
    elif pillow and gemini_key:
        mode = "offline-image-analysis + gemini-vision"
    return {
        "offline_analysis": offline,
        "pillow": pillow,
        "openai": openai_key,
        "gemini": gemini_key,
        "active_mode": mode,
        "note": (
            "Offline measurement of colour, lightness and texture proxies always runs. "
            "A multimodal model is used only when an API key is configured; it adds "
            "semantic description and never invents numeric targets."
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
VISION_PROMPT = (
    "You are a food product development scientist. You are looking at a reference "
    "image of a food product that must be reverse-engineered. Respond ONLY with a "
    "JSON object using these keys: product_form (short string), surface_finish "
    "(short string), dominant_colour (short string), visible_inclusions (list of "
    "strings), shape_and_size_notes (string), apparent_defects (list of strings), "
    "process_hypothesis (string, e.g. 'baked rotary-moulded biscuit'), "
    "confidence (0-1). Do not invent numeric composition values."
)


def _openai_vision(paths: List[str]) -> Optional[Dict[str, Any]]:
    import urllib.request

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    content: List[Dict[str, Any]] = [{"type": "text", "text": VISION_PROMPT}]
    for path in paths[:3]:
        if path.startswith("http"):
            content.append({"type": "image_url", "image_url": {"url": path}})
            continue
        try:
            with open(path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
        except OSError:
            continue
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
    if len(content) == 1:
        return None
    body = json.dumps(
        {
            "model": os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 700,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # pragma: no cover - network
            payload = json.loads(response.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        return {"source": "openai-vision", "raw": text, "parsed": _extract_json(text)}
    except Exception as exc:  # pragma: no cover - network
        return {"source": "openai-vision", "error": str(exc)}


def _gemini_vision(paths: List[str]) -> Optional[Dict[str, Any]]:  # pragma: no cover - network
    import urllib.request

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    parts: List[Dict[str, Any]] = [{"text": VISION_PROMPT}]
    for path in paths[:3]:
        if path.startswith("http"):
            continue
        try:
            with open(path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
        except OSError:
            continue
        parts.append({"inline_data": {"mime_type": "image/png", "data": encoded}})
    if len(parts) == 1:
        return None
    model = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.0-flash")
    body = json.dumps({"contents": [{"parts": parts}]}).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return {"source": "gemini-vision", "raw": text, "parsed": _extract_json(text)}
    except Exception as exc:
        return {"source": "gemini-vision", "error": str(exc)}


def _extract_json(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def describe_images(paths: List[str], category_hint: str = "") -> Dict[str, Any]:
    """Understand the reference images: measurement always, model when configured."""
    result = offline_describe(paths, category_hint)
    remote = any(p.startswith("http") for p in paths)
    semantic: Optional[Dict[str, Any]] = None
    if paths and (remote or result["analysed_count"]):
        semantic = _openai_vision(paths) or _gemini_vision(paths)
    if semantic and "error" not in semantic:
        result["semantic"] = semantic
        result["source"] = f"{result['source']} + {semantic.get('source')}"
    elif semantic:
        result["semantic_error"] = semantic.get("error")
    result["available"] = vision_available()
    return result


def apply_hints_to_payload(payload: Dict[str, Any], understanding: Dict[str, Any]) -> Dict[str, Any]:
    """Fold image understanding back into a brief payload as non-destructive hints."""
    augmented = dict(payload)
    hints: List[str] = list(understanding.get("hints") or [])
    semantic = (understanding.get("semantic") or {}).get("parsed") or {}
    if semantic:
        if semantic.get("process_hypothesis"):
            hints.append(f"Vision model process hypothesis: {semantic['process_hypothesis']}")
        for defect in semantic.get("apparent_defects") or []:
            hints.append(f"Vision model flagged: {defect}")
    augmented["vision_hints"] = hints
    augmented["vision_source"] = understanding.get("source")
    return augmented
