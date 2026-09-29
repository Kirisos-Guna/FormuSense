"""Shared domain types for the formulation engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Item:
    """A single ingredient line of a formulation."""

    ingredient_id: str
    pct: float
    slot: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"ingredient_id": self.ingredient_id, "pct": round(self.pct, 4), "slot": self.slot}


@dataclass
class Formulation:
    """A complete, versioned formulation with its process parameters."""

    category: str
    version: int
    items: List[Item]
    params: Dict[str, float] = field(default_factory=dict)
    label: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def total_pct(self) -> float:
        return sum(i.pct for i in self.items)

    def pct_of(self, ingredient_id: str) -> float:
        for item in self.items:
            if item.ingredient_id == ingredient_id:
                return item.pct
        return 0.0

    def as_dict(self, min_line_pct: float = 0.001) -> Dict[str, Any]:
        """Presentation form: what a formula sheet would actually print.

        Lines the optimiser has driven to zero are dropped rather than listed at
        0.000%. They are still part of the formulation for the mathematics, but a
        batch sheet that says "butter 0.000%" is noise in a report.
        """
        return {
            "category": self.category,
            "version": self.version,
            "label": self.label,
            "total_pct": round(self.total_pct, 4),
            "items": [i.as_dict() for i in self.items if i.pct >= min_line_pct],
            "params": {k: round(v, 4) for k, v in self.params.items()},
            "notes": list(self.notes),
        }

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "Formulation":
        return Formulation(
            category=payload["category"],
            version=int(payload.get("version", 1)),
            label=payload.get("label", ""),
            items=[Item(i["ingredient_id"], float(i["pct"]), i.get("slot", "")) for i in payload["items"]],
            params={k: float(v) for k, v in (payload.get("params") or {}).items()},
            notes=list(payload.get("notes") or []),
        )


@dataclass
class Prediction:
    """A predicted product characteristic with an uncertainty interval."""

    id: str
    label: str
    value: float
    unit: str
    lo: float
    hi: float
    confidence: float
    method: str
    direction: str = "target"
    target: Optional[float] = None
    status: str = "unknown"

    @property
    def width(self) -> float:
        return abs(self.hi - self.lo)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "value": round(self.value, 4),
            "unit": self.unit,
            "lo": round(self.lo, 4),
            "hi": round(self.hi, 4),
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "direction": self.direction,
            "target": None if self.target is None else round(self.target, 4),
            "status": self.status,
        }


@dataclass
class KpiTarget:
    """A measurable goal the product must hit."""

    id: str
    label: str
    unit: str
    target: float
    tolerance: float
    priority: float = 1.0
    direction: str = "target"
    hard: bool = True

    @property
    def lo(self) -> float:
        return self.target - self.tolerance

    @property
    def hi(self) -> float:
        return self.target + self.tolerance

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "unit": self.unit,
            "target": round(self.target, 4),
            "tolerance": round(self.tolerance, 4),
            "priority": round(self.priority, 3),
            "direction": self.direction,
            "hard": self.hard,
        }


@dataclass
class Brief:
    """The structured understanding of the target product."""

    product_name: str
    category: str
    unit_weight_g: float
    #: What the pack says and how the interface prints it: a drink is declared in
    #: millilitres and everything else in grams. ``unit_weight_g`` stays the mass the
    #: models work in, so the two agree at a density near 1 g/ml.
    declared_unit: str = "g"
    declared_unit_size: Optional[float] = None
    description: str = ""
    claims: List[str] = field(default_factory=list)
    diet: str = "vegan"
    cost_ceiling_inr_kg: Optional[float] = None
    allergens_to_avoid: List[str] = field(default_factory=list)
    targets: List[KpiTarget] = field(default_factory=list)
    spec_text: str = ""
    images: List[str] = field(default_factory=list)
    understood_by: str = "offline-parser"
    open_questions: List[str] = field(default_factory=list)

    def target(self, kpi_id: str) -> Optional[KpiTarget]:
        for t in self.targets:
            if t.id == kpi_id:
                return t
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "product_name": self.product_name,
            "category": self.category,
            "unit_weight_g": self.unit_weight_g,
            "declared_unit": self.declared_unit,
            "declared_unit_size": self.declared_unit_size,
            "description": self.description,
            "claims": list(self.claims),
            "diet": self.diet,
            "cost_ceiling_inr_kg": self.cost_ceiling_inr_kg,
            "allergens_to_avoid": list(self.allergens_to_avoid),
            "targets": [t.as_dict() for t in self.targets],
            "spec_text": self.spec_text,
            "images": list(self.images),
            "understood_by": self.understood_by,
            "open_questions": list(self.open_questions),
        }


@dataclass
class TrialResult:
    """Measured outcome of one physical trial."""

    label: str
    formulation_version: int
    measurements: Dict[str, float] = field(default_factory=dict)
    sensory: Dict[str, float] = field(default_factory=dict)
    process_actuals: Dict[str, float] = field(default_factory=dict)
    batch_size_kg: float = 1.0
    operator: str = "pilot plant"
    trial_date: str = ""
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosisCause:
    """One probable root cause, ranked."""

    rank: int
    cause: str
    category: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    kpis: List[str] = field(default_factory=list)
    recommended_action: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "cause": self.cause,
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "evidence": list(self.evidence),
            "kpis": list(self.kpis),
            "recommended_action": self.recommended_action,
        }


@dataclass
class DoeRun:
    """A single run of a suggested design of experiments."""

    run: int
    factors: Dict[str, float]
    purpose: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"run": self.run, "factors": {k: round(v, 3) for k, v in self.factors.items()}, "purpose": self.purpose}


@dataclass
class DoePlan:
    design: str
    rationale: str
    factors: List[str] = field(default_factory=list)
    runs: List[DoeRun] = field(default_factory=list)
    response_kpis: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "design": self.design,
            "rationale": self.rationale,
            "factors": list(self.factors),
            "runs": [r.as_dict() for r in self.runs],
            "response_kpis": list(self.response_kpis),
        }


@dataclass
class ReformulationPlan:
    """The proposed next formulation plus how to test it."""

    product_id: int
    from_version: int
    to_version: int
    formulation: Formulation
    deltas: List[Dict[str, Any]] = field(default_factory=list)
    param_deltas: List[Dict[str, Any]] = field(default_factory=list)
    expected: List[Prediction] = field(default_factory=list)
    pass_probability: float = 0.0
    objective_value: float = 0.0
    rationale: List[str] = field(default_factory=list)
    doe: Optional[DoePlan] = None
    drivers: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "formulation": self.formulation.as_dict(),
            "deltas": self.deltas,
            "param_deltas": self.param_deltas,
            "expected": [p.as_dict() for p in self.expected],
            "pass_probability": round(self.pass_probability, 3),
            "objective_value": round(self.objective_value, 4),
            "rationale": list(self.rationale),
            "doe": self.doe.as_dict() if self.doe else None,
            "drivers": self.drivers,
        }
