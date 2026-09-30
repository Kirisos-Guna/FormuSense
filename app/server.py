"""HTTP API and user interface server, standard library only.

`run.py` starts this module. It serves two things from one port:

* ``/api/...``  the JSON API, one route per capability of the agent. Every route
  is a thin wrapper around :class:`app.service.AgentService`, so the UI cannot do
  anything the service cannot do, and the record in the database is the same
  whichever way the work was driven.
* everything else, the single-page UI from ``app/web``.

Three engineering notes:

* **One store per request.** The server is threaded - a benchmark run takes
  fifteen seconds and must not block the UI - and a SQLite connection belongs to
  the thread that opened it, so each request opens its own ``Store``. This is
  cheap (the file is local, the schema is created with ``IF NOT EXISTS``) and it
  removes an entire class of cross-thread bugs.
* **Calibration from the record, never from memory.** Every route re-reads the
  product, its versions, trials and plans from the database, so the UI sees
  exactly the state the previous request left behind, including from another
  process.
* **No route can invent a number.** Predictions, objectives and probabilities are
  all produced by the service; the server only moves them.
"""
from __future__ import annotations

import json
import mimetypes
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from . import benchmark as benchmark_module
from .bootstrap import CASES, INFEASIBLE_CASE, seed_all
from .config import ai_settings, settings
from .core import documents, kb, kpi as kpi_registry, population as population_module, vision
from .logging_setup import configure_logging, logger, new_request_id, summarise_path
from .db import driver_available, migration_status
from .service import AgentService
from .store import Store

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
INDEX = "index.html"

# Serialises the two expensive multi-product operations so two browser tabs
# cannot run benchmarks over the same tables at the same time.
_HEAVY = threading.Lock()


# --------------------------------------------------------------------------- #
# Catalogue: what the UI is allowed to offer the user
# --------------------------------------------------------------------------- #
def catalog() -> Dict[str, Any]:
    """Categories, slots, ingredients, claims, allergens, KPIs and uploads for the UI."""
    categories = []
    for category_id, category in kb.categories().items():
        categories.append(
            {
                "id": category_id,
                "label": category.label,
                # The unit the pack size is stated in, so the form can label its field
                # "unit volume (ml)" for a drink and "unit weight (g)" for the rest.
                "pack_unit": category.pack_unit,
                "typical_unit_weight_g": category.typical_unit_weight_g,
                "typical_moisture_pct": list(category.typical_moisture_pct),
                "typical_aw": list(category.typical_aw),
                "unit_operations": list(category.unit_operations),
                "slots": [
                    {
                        "id": slot.id,
                        "label": slot.label,
                        "groups": list(slot.groups),
                        "target_pct": slot.target,
                        "min_pct": slot.min,
                        "max_pct": slot.max,
                        "max_lines": slot.count,
                        "required": slot.required,
                    }
                    for slot in category.slots
                ],
                "parameters": [
                    {
                        "id": parameter.id,
                        "label": parameter.label,
                        "unit": parameter.unit,
                        "min": parameter.min,
                        "max": parameter.max,
                        "default": parameter.default,
                    }
                    for parameter in category.parameters
                ],
                "defaults": category.default_params(),
            }
        )
    ingredients = []
    for ingredient_id, ing in kb.ingredients().items():
        ingredients.append(
            {
                "id": ingredient_id,
                "name": ing.name,
                "group": ing.group,
                "diet": ing.diet,
                "cost_inr_kg": ing.cost_inr_kg,
                "min_pct": ing.min_pct,
                "max_pct": ing.max_pct,
                "allergens": list(ing.allergens),
                "protein_g": ing.protein,
                "fat_g": ing.fat,
                "sugar_g": ing.sugar,
                "fibre_g": ing.fibre,
                "sodium_mg": ing.sodium_mg,
                "moisture_pct": ing.moisture,
                "water_activity": ing.aw,
            }
        )
    return {
        "categories": categories,
        "ingredients": ingredients,
        "claims": kb.claim_rules(),
        "allergens": kb.allergen_labels(),
        "kpis": [
            {
                "id": kpi_id,
                "label": kpi_registry.kpi_label(kpi_id),
                "unit": kpi_registry.kpi_unit(kpi_id),
                "direction": kpi_registry.kpi_direction(kpi_id),
            }
            for kpi_id in sorted(kpi_registry.KPI_DEFS)
        ],
        "category_kpis": {cat: kpi_registry.kpis_for_category(cat) for cat in kb.categories()},
        "limits": kb.limits(),
        # No store here: the catalogue describes what the interface may offer, and the
        # budget belongs to a request (see /api/health), not to a static description.
        "vision": vision.vision_available(),
        "ai": ai_settings().as_dict(),
        # Which documents the upload accepts, and how large one may be: the list comes
        # from the reader that implements it rather than from the interface, so the
        # form cannot offer a format nothing can open.
        "documents": documents.catalog_entry(),
        "summary": kb.summarise_kb(),
    }


def case_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    """The API payload for one seeded case study."""
    return {
        "product_name": case["name"],
        "category": case["category"],
        "spec_text": case["spec_text"],
        "diet": case.get("diet", "vegetarian"),
        "claims": list(case.get("claims") or []),
        "unit_weight_g": case.get("unit_weight_g"),
        "plant": case.get("plant") or {},
    }


def cases() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for case in list(CASES) + [INFEASIBLE_CASE]:
        rows.append(
            {
                "key": case["key"],
                "name": case["name"],
                "category": case["category"],
                "narrative": case.get("plant_narrative", ""),
                "plant": case.get("plant") or {},
                "feasible": case is not INFEASIBLE_CASE,
                "payload": case_payload(case),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
class Router:
    """Tiny path router: exact paths, and one-segment patterns like /products/{id}."""

    def __init__(self) -> None:
        self.routes: List[Tuple[str, str, Callable[..., Any]]] = []

    def add(self, method: str, pattern: str, handler: Callable[..., Any]) -> None:
        self.routes.append((method.upper(), pattern, handler))

    def match(self, method: str, path: str) -> Optional[Tuple[Callable[..., Any], Dict[str, str]]]:
        parts = [p for p in path.strip("/").split("/") if p != ""]
        best: Optional[Tuple[Callable[..., Any], Dict[str, str]]] = None
        for route_method, pattern, handler in self.routes:
            if route_method != method.upper():
                continue
            pattern_parts = [p for p in pattern.strip("/").split("/") if p != ""]
            if len(pattern_parts) != len(parts):
                continue
            params: Dict[str, str] = {}
            for expected, actual in zip(pattern_parts, parts):
                if expected.startswith("{") and expected.endswith("}"):
                    params[expected[1:-1]] = unquote(actual)
                elif expected != actual:
                    break
            else:
                # Prefer a literal route over a parameterised one of equal length.
                if all(not p.startswith("{") for p in pattern_parts) or best is None:
                    best = (handler, params)
                    if all(not p.startswith("{") for p in pattern_parts):
                        break
        return best


ROUTER = Router()


def _route(method: str, pattern: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(handler: Callable[..., Any]) -> Callable[..., Any]:
        ROUTER.add(method, pattern, handler)
        return handler

    return register


_GET = lambda pattern: _route("GET", pattern)  # noqa: E731 - reads as a decorator
_POST = lambda pattern: _route("POST", pattern)  # noqa: E731
_DELETE = lambda pattern: _route("DELETE", pattern)  # noqa: E731


def _int(value: str, name: str = "id") -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer, got {value!r}") from None


# ---------------------------------------------------------------- read routes #
@_GET("/api/health")
def r_health(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    products = service.store.products()
    trials = sum(int(p.get("trials") or 0) for p in products)
    return {
        "ok": True,
        "agent": "PS-1 AI Food Product Development Agent",
        "products": len(products),
        "trials": trials,
        "categories": len(kb.categories()),
        "ingredients": len(kb.ingredients()),
        "vision": vision.vision_available(cache=service.store),
        "ai": ai_settings().as_dict(),
        "benchmark": bool(service.store.latest_benchmark()),
        "database": getattr(service.store, "dialect", "sqlite"),
        "acceptance_model": bool(service.acceptance_model()),
        "population_groups": len(population_module.profiles()),
    }


@_GET("/api/config")
def r_config(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """The non-secret runtime configuration, so the UI can label the environment."""
    return {"settings": settings().as_dict()}


@_GET("/api/db/status")
def r_db_status(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """Which database is in use, whether its driver is present, and its migrations."""
    current = settings()
    available, driver_note = driver_available(current.db_url)
    status = migration_status(service.store.conn, getattr(service.store, "dialect", "sqlite"))
    return {
        "database": current.as_dict(),
        "driver": {"available": available, "detail": driver_note},
        "migrations": status,
    }


@_GET("/api/ready")
def r_ready(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """Readiness: the record must be readable, not merely the process alive."""
    try:
        products = service.store.products()
    except Exception as exc:  # noqa: BLE001 - readiness must answer, not raise
        return {"ready": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"ready": True, "products": len(products)}


@_GET("/api/model")
def r_model(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """The trained acceptance model's provenance and out-of-sample metrics."""
    bundle = service.acceptance_model()
    if bundle is None:
        return {
            "available": False,
            "note": "No trained model. Build and train it with: python run.py --build-dataset && python run.py --train",
        }
    return {
        "available": True,
        "name": bundle.name,
        "version": bundle.version,
        "created_at": bundle.created_at,
        "dataset": bundle.dataset,
        "metrics": bundle.metrics,
        "drivers": bundle.driver_notes(),
    }


@_GET("/api/populations")
def r_populations_catalog(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """The population reference set, with its source and disclaimer."""
    return {
        "summary": population_module.summary(),
        "source": population_module.source_note(),
        "groups": [p.as_dict() for p in population_module.profiles()],
    }


@_GET("/api/products/{product_id}/populations")
def r_product_populations(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """What one serving contributes to each population group's protein need."""
    return service.population_guide(_int(params["product_id"], "product_id"))


@_GET("/api/catalog")
def r_catalog(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    return catalog()


@_GET("/api/cases")
def r_cases(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    return {"cases": cases()}


@_GET("/api/products")
def r_products(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    rows = []
    for product in service.store.products():
        formulation = service.store.formulation(int(product["id"]))
        record = service.store.prediction_for_version(int(product["id"]), formulation.version) if formulation else None
        rows.append(
            {
                "id": product["id"],
                "name": product["name"],
                "category": product["category"],
                "created_at": product.get("created_at"),
                "versions": int(product.get("versions") or 0),
                "trials": int(product.get("trials") or 0),
                "latest_version": formulation.version if formulation else None,
                "predicted_objective": (record or {}).get("objective"),
                "plant": (product.get("plant") or {}).get("name", ""),
            }
        )
    return {"products": rows}


@_GET("/api/products/{product_id}")
def r_product(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    return service.product_view(_int(params["product_id"], "product_id"))


@_GET("/api/products/{product_id}/report")
def r_product_report(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """The development story of one product: versions, trials, causes, plans."""
    from . import report as report_module

    product_id = _int(params["product_id"], "product_id")
    view = service.product_view(product_id)
    return {
        "product_id": product_id,
        "markdown": report_module.product_report(view),
        "efficiency": view["efficiency"],
    }


@_GET("/api/products/{product_id}/process")
def r_product_process(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    from .core import process as process_module

    product_id = _int(params["product_id"], "product_id")
    product = service.store.product(product_id)
    if product is None:
        raise KeyError(f"Unknown product {product_id}")
    formulation = service.store.formulation(product_id)
    if formulation is None:
        raise KeyError("No formulation for this product")
    from .service import _brief_from_payload

    brief = _brief_from_payload(product["brief"])
    return {
        "summary": process_module.summary(formulation, brief),
        "batch_sheet": process_module.plan(formulation, brief, batch_size_kg=50.0),
    }


@_GET("/api/benchmark")
def r_benchmark_latest(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    record = service.store.latest_benchmark()
    if not record:
        return {"benchmark": None, "text": ""}
    return {"benchmark": record, "text": benchmark_module.format_benchmark(record)}


# --------------------------------------------------------------- write routes #
@_POST("/api/brief/from-document")
def r_brief_from_document(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """Read an uploaded R&D document into a proposal for the New product form.

    A POST because the file travels in the body as a data URL, and a separate route
    from /api/products because reading a document is not creating a product: this one
    stores nothing, so an upload can be tried, read back and abandoned.
    """
    return service.read_document(body)


@_POST("/api/products")
def r_create_product(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    if not str(body.get("spec_text") or body.get("description") or "").strip():
        raise ValueError("A specification or description is required to design a product.")
    return service.create_product(body)


@_POST("/api/products/{product_id}/predict")
def r_predict(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    version = body.get("version")
    return service.predict(
        _int(params["product_id"], "product_id"),
        version=None if version in (None, "") else _int(str(version), "version"),
        trials_for_calibration=bool(body.get("calibrated", True)),
    )


@_POST("/api/products/{product_id}/trials")
def r_trial(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    version = body.get("version")
    seed = body.get("seed")
    return service.run_trial(
        _int(params["product_id"], "product_id"),
        version=None if version in (None, "") else _int(str(version), "version"),
        seed=None if seed in (None, "") else _int(str(seed), "seed"),
        operator=str(body.get("operator") or "pilot plant"),
        note=str(body.get("note") or ""),
    )


@_POST("/api/products/{product_id}/trials/{trial_id}/analyse")
def r_analyse(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    return service.analyse_trial(
        _int(params["product_id"], "product_id"), _int(params["trial_id"], "trial_id")
    )


@_POST("/api/products/{product_id}/plans")
def r_plan(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    budget = body.get("budget")
    return service.plan_reformulation(
        _int(params["product_id"], "product_id"),
        budget=int(budget) if budget else 1600,
    )


@_POST("/api/products/{product_id}/ask")
def r_ask(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    """Answer a question from the product's own record.

    Read-only: the model reads the record and its answer is written to the ledger, and
    nothing else in the product changes. That is why this is a POST - a question has
    side effects on the record's history - and why it is safe to expose without the
    write routes' token: it cannot alter a formulation.
    """
    return service.ask_record(
        _int(params["product_id"], "product_id"),
        str(body.get("question") or ""),
        use_model=bool(body.get("use_ai")),
    )


@_POST("/api/products/{product_id}/plans/{plan_id}/accept")
def r_accept(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    return service.accept_plan(
        _int(params["product_id"], "product_id"), _int(params["plan_id"], "plan_id")
    )


@_POST("/api/products/{product_id}/loop")
def r_loop(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    max_trials = body.get("max_trials")
    return service.closed_loop(
        _int(params["product_id"], "product_id"),
        max_trials=int(max_trials) if max_trials else 6,
        accept_plans=bool(body.get("accept_plans", True)),
        pass_objective=float(body.get("pass_objective") or 0.85),
    )


@_POST("/api/cases/{key}/create")
def r_case_create(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    key = params["key"]
    wanted = {case["key"]: case for case in list(CASES) + [INFEASIBLE_CASE]}
    case = wanted.get(key)
    if case is None:
        raise KeyError(f"Unknown case study {key!r}")
    payload = case_payload(case)
    payload.update({k: v for k, v in body.items() if k in ("plant", "seed", "product_name")})
    created = service.create_product(payload)
    created["case"] = {"key": case["key"], "name": case["name"], "narrative": case.get("plant_narrative", "")}
    return created


@_POST("/api/benchmark")
def r_benchmark(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    max_trials = body.get("max_trials")
    with _HEAVY:
        report = benchmark_module.run_benchmark(
            service.store,
            max_trials=int(max_trials) if max_trials else 6,
            include_conflict=bool(body.get("include_conflict", True)),
        )
    return {"benchmark": report, "text": benchmark_module.format_benchmark(report)}


@_POST("/api/seed")
def r_seed(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    with _HEAVY:
        return seed_all(force=True)


@_DELETE("/api/products/{product_id}")
def r_delete(service: AgentService, body: Dict[str, Any], params: Dict[str, str]) -> Any:
    product_id = _int(params["product_id"], "product_id")
    if service.store.product(product_id) is None:
        raise KeyError(f"Unknown product {product_id}")
    service.store.clear(product_id)
    return {"deleted": product_id}


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "FormuSense/1.0"
    protocol_version = "HTTP/1.1"

    def _request_id(self) -> str:
        return str(getattr(self, "_rid", ""))

    # ------------------------------------------------------------------ verbs #
    def do_GET(self) -> None:  # noqa: N802 - required name
        self._rid = new_request_id()
        path, query = self._split()
        if path.startswith("/api/"):
            self._api("GET", path, query, {})
        else:
            self._static(path, query)

    def do_POST(self) -> None:  # noqa: N802
        self._rid = new_request_id()
        path, query = self._split()
        body = self._body()
        if path.startswith("/api/"):
            self._api("POST", path, query, body)
        else:
            self._send_json({"error": "Only /api routes accept POST."}, 405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._rid = new_request_id()
        path, query = self._split()
        if path.startswith("/api/"):
            self._api("DELETE", path, query, {})
        else:
            self._send_json({"error": f"Not found: {path}"}, 404)

    # ---------------------------------------------------------------- routing #
    def _authorised(self) -> bool:
        """Optional bearer-token auth for write routes.

        Off by default so the local demo is frictionless; set
        ``FORMUSENSE_AUTH_TOKEN`` to require it. Reads stay open either way.
        """
        token = settings().auth_token
        if not token:
            return True
        header = str(self.headers.get("Authorization") or "").strip()
        return header in (f"Bearer {token}", token)

    def _api(self, method: str, path: str, query: Dict[str, List[str]], body: Dict[str, Any]) -> None:
        if method in ("POST", "DELETE") and not self._authorised():
            self._send_json(
                {"error": "Unauthorised: this instance requires a bearer token.", "kind": "unauthorised"},
                401,
            )
            return
        matched = ROUTER.match(method, path)
        if matched is None:
            self._send_json({"error": f"No such endpoint: {method} {path}"}, 404)
            return
        handler, params = matched
        # Query-string values fill in anything the body left out.
        for key, values in query.items():
            if values and key not in body:
                body[key] = values[0]
        try:
            service = AgentService(Store())
            result = handler(service, body, params)
        except KeyError as exc:
            self._send_json({"error": str(exc), "kind": "not_found"}, 404)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc), "kind": "bad_request"}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - the UI needs the reason, not a blank 500
            traceback.print_exc()
            self._send_json(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "kind": "server_error",
                    "traceback": traceback.format_exc().splitlines()[-6:],
                },
                500,
            )
            return
        self._send_json(result, 200)

    # ------------------------------------------------------------ static files #
    def _static(self, path: str, query: Dict[str, List[str]]) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / relative).resolve()
        try:
            inside = target.is_relative_to(WEB_DIR.resolve())
        except AttributeError:  # pragma: no cover - Python < 3.9
            inside = str(target).startswith(str(WEB_DIR.resolve()))
        if not inside or not target.is_file():
            # Single-page app: unknown non-API paths fall back to the shell.
            target = WEB_DIR / INDEX
            if not target.is_file():
                self._send_json(
                    {"error": f"UI not installed (expected {target})", "kind": "missing_ui"}, 404
                )
                return
        data = target.read_bytes()
        guessed = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix in (".html", ".css", ".js", ".svg", ".json", ".webmanifest"):
            guessed += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(data)))
        # The UI is served from the same origin and never cached between edits.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # --------------------------------------------------------------- helpers #
    def _split(self) -> Tuple[str, Dict[str, List[str]]]:
        parsed = urlparse(self.path)
        return parsed.path, _parse_query(parsed.query)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Request body is not valid JSON: {exc}") from None
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, default=str, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", self._request_id())
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        # One line per request, but only for the routes worth watching: a page load
        # fires a dozen asset requests and they bury everything else. The request
        # id ties the line to the response header, so a browser network entry can
        # be matched to a server log line.
        if not settings().log_requests:
            return
        if "/api/" in f"{self.path}":
            logger().info("[%s] %s %s", self._request_id(), summarise_path(self.path), str(args[-1] if args else ""))


def _parse_query(query: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        out.setdefault(unquote(key), []).append(unquote(value.replace("+", " ")))
    return out


class Server(ThreadingHTTPServer):
    """Threaded so a long benchmark never blocks the UI - see the module docstring."""

    daemon_threads = True
    allow_reuse_address = True


def serve(host: str = "127.0.0.1", port: int = 8770, banner: bool = True) -> None:
    """Run the API and UI until interrupted."""
    configure_logging(settings().log_level)
    httpd = Server((host, port), Handler)
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    if banner:
        print("=" * 68)
        print("  AI-Powered Food Product Development Agent  (PS-1 | Tiny Dot Foods)")
        print("=" * 68)
        print(f"  UI  : http://{shown}:{port}/")
        print(f"  API : http://{shown}:{port}/api/health")
        print("=" * 68)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        httpd.server_close()
