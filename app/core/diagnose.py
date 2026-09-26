"""Trial analysis and probable-cause diagnosis.

Two jobs, kept separate because they fail in different ways.

**Analysis** compares one trial against what the agent predicted, in units of the
*model uncertainty it published before the trial*. A residual only counts as a
finding if it is larger than the confidence interval the agent declared - which
is what makes "moisture came in high" a defensible statement rather than a
reaction to noise. The analysis also separates a *systematic* shift (most
residuals share a sign - the plant or the model is biased) from *random* scatter
(the product is fine and this is measurement).

**Diagnosis** matches the pattern of residuals against a table of signatures that
food scientists actually use. Each rule encodes a hypothesis with the evidence it
requires, so the output is not "something went wrong" but a ranked list of
probable causes, each with the numbers that support it, and the specific thing to
check or change. A model-bias candidate is always included, because with one
trial that is often the truth, and pretending otherwise would train the team to
distrust the system.

The rules are data, not code branches: adding a new failure mode means adding a
row to :data:`RULES`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import kb, kpi as kpi_registry
from .engine import PredictionResult
from .types import Brief, DiagnosisCause, Formulation, TrialResult

# A residual is worth reporting when it exceeds this many standard deviations of
# the *published* prediction interval.
INTERESTING_Z = 1.30


@dataclass
class Residual:
    kpi: str
    label: str
    unit: str
    predicted: float
    measured: float
    sigma: float
    z: float
    direction: str  # 'high' | 'low' | 'consistent'
    target: Optional[float] = None
    on_target: Optional[bool] = None
    comment: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kpi": self.kpi,
            "label": self.label,
            "unit": self.unit,
            "predicted": round(self.predicted, 4),
            "measured": round(self.measured, 4),
            "residual": round(self.measured - self.predicted, 4),
            "sigma": round(self.sigma, 4),
            "z": round(self.z, 3),
            "direction": self.direction,
            "target": self.target,
            "on_target": self.on_target,
            "comment": self.comment,
        }


@dataclass
class Analysis:
    trial_label: str
    version: int
    residuals: List[Residual] = field(default_factory=list)
    systematic: bool = False
    mean_z: float = 0.0
    process_deviations: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    predicted_objective: float = 0.0
    measured_objective: float = 0.0
    measured_on_target: int = 0
    measured_total: int = 0
    flagged: List[Residual] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trial_label": self.trial_label,
            "version": self.version,
            "residuals": [r.as_dict() for r in self.residuals],
            "flagged": [r.as_dict() for r in self.flagged],
            "systematic": self.systematic,
            "mean_z": round(self.mean_z, 3),
            "process_deviations": self.process_deviations,
            "summary": self.summary,
            "predicted_objective": round(self.predicted_objective, 4),
            "measured_objective": round(self.measured_objective, 4),
            "measured_on_target": self.measured_on_target,
            "measured_total": self.measured_total,
        }


def _sigma_from_interval(lo: float, hi: float, value: float) -> float:
    width = abs(hi - lo)
    if width > 1e-9:
        return width / (2.0 * 1.96)
    from . import uncertainty

    return max(abs(value) * uncertainty.BASE_SIGMA.get("generic", 0.10), 1e-6)


def analyse(
    trial: TrialResult,
    prediction: PredictionResult,
    brief: Optional[Brief] = None,
    setpoints: Optional[Dict[str, float]] = None,
) -> Analysis:
    """Compare one trial with the prediction that preceded it."""
    rows: List[Residual] = []
    measurement_values: Dict[str, float] = {}
    for kpi_id, measured in trial.measurements.items():
        forecast = prediction.prediction(kpi_id)
        if forecast is None:
            continue
        measurement_values[kpi_id] = float(measured)
        sigma = _sigma_from_interval(forecast.lo, forecast.hi, forecast.value)
        z = (float(measured) - forecast.value) / max(sigma, 1e-9)
        if abs(z) <= INTERESTING_Z:
            direction = "consistent"
        else:
            direction = "high" if z > 0 else "low"
        target = brief.target(kpi_id) if brief else None
        on_target = None
        if target is not None:
            on_target = kpi_registry.is_on_target(target, float(measured))
        rows.append(
            Residual(
                kpi=kpi_id,
                label=kpi_registry.kpi_label(kpi_id),
                unit=kpi_registry.kpi_unit(kpi_id),
                predicted=float(forecast.value),
                measured=float(measured),
                sigma=sigma,
                z=z,
                direction=direction,
                target=None if target is None else target.target,
                on_target=on_target,
            )
        )
    rows.sort(key=lambda r: -abs(r.z))

    flagged = [r for r in rows if r.direction != "consistent"]
    zs = [r.z for r in rows if abs(r.z) > 0.2]
    mean_z = sum(zs) / len(zs) if zs else 0.0
    # Systematicity is judged on magnitude, not on a head count: a product with
    # three large positive residuals and two small negative ones is still a
    # systematic shift, but a head count would call it mixed.
    sum_z = sum(zs)
    sum_abs = sum(abs(z) for z in zs)
    direction_score = sum_z / sum_abs if sum_abs > 1e-9 else 0.0
    same_sign = sum(1 for z in zs if (z > 0) == (direction_score > 0))
    systematic = len(flagged) >= 3 and abs(direction_score) >= 0.55 and abs(mean_z) > 0.5

    deviations: List[Dict[str, Any]] = []
    if setpoints:
        for key, setpoint in setpoints.items():
            actual = trial.process_actuals.get(key)
            if actual is None:
                continue
            span = max(abs(setpoint) * 0.05, 1.0)
            deviation = actual - setpoint
            if abs(deviation) > 0.35 * span:
                deviations.append(
                    {
                        "parameter": key,
                        "setpoint": round(float(setpoint), 3),
                        "actual": round(float(actual), 3),
                        "deviation": round(float(deviation), 3),
                        "relative_pct": round(100.0 * deviation / max(abs(setpoint), 1e-6), 1),
                    }
                )

    measured_objective = 0.0
    on_target_count = 0
    total = 0
    if brief:
        values = dict(measurement_values)
        for forecast in prediction.predictions:
            values.setdefault(forecast.id, forecast.value)
        measured_objective = kpi_registry.overall_desirability(brief.targets, values)
        for row in rows:
            if row.target is None:
                continue
            total += 1
            if row.on_target:
                on_target_count += 1

    if not flagged and not deviations:
        summary = (
            "Every measured KPI sits inside the published prediction interval: this trial "
            "confirms the formulation. No corrective action is indicated."
        )
    elif systematic:
        summary = (
            f"{len(flagged)} of {len(rows)} KPIs landed outside their prediction interval and "
            f"{same_sign}/{len(zs)} of them moved in the same direction (mean z {mean_z:+.2f}). "
            "That pattern is a systematic offset, not noise: something in the plant or in the "
            "model shifts the whole product, so the fix is a correction, not a new recipe."
        )
    else:
        summary = (
            f"{len(flagged)} of {len(rows)} KPIs landed outside their prediction interval with no "
            "consistent direction, which is more typical of dosing or measurement variation than "
            "of a formulation error."
        )

    return Analysis(
        trial_label=trial.label,
        version=trial.formulation_version,
        residuals=rows,
        systematic=systematic,
        mean_z=mean_z,
        process_deviations=deviations,
        summary=summary,
        predicted_objective=float(
            kpi_registry.overall_desirability(
                brief.targets, {p.id: p.value for p in prediction.predictions}
            )
            if brief
            else 0.0
        ),
        measured_objective=measured_objective,
        measured_on_target=on_target_count,
        measured_total=total,
        flagged=flagged,
    )


# --------------------------------------------------------------------------- #
# Diagnosis rules
# --------------------------------------------------------------------------- #
@dataclass
class Rule:
    """One failure signature: the hypothesis, what supports it, what to do."""

    id: str
    cause: str
    category: str
    kpis: Sequence[str]
    action: str
    test: Callable[[Dict[str, Residual], "RuleContext"], Optional[Tuple[float, List[str]]]]


@dataclass
class RuleContext:
    """Everything a rule may consult beyond the residuals themselves."""

    formulation: Formulation
    brief: Optional[Brief]
    analysis: Analysis
    process_actuals: Dict[str, float] = field(default_factory=dict)

    def measured(self, kpi: str) -> Optional[float]:
        for row in self.analysis.residuals:
            if row.kpi == kpi:
                return row.measured
        return None

    def target(self, kpi: str) -> Optional[float]:
        return self.brief.target(kpi).target if self.brief and self.brief.target(kpi) else None

    @property
    def category(self) -> str:
        return self.formulation.category


def _residual(residuals: Dict[str, Residual], kpi: str) -> Optional[Residual]:
    return residuals.get(kpi)


def _z(residuals: Dict[str, Residual], kpi: str) -> float:
    row = residuals.get(kpi)
    return row.z if row else 0.0


def _evidence(residuals: Dict[str, Residual], *kpis: str) -> List[str]:
    out: List[str] = []
    for kpi in kpis:
        row = residuals.get(kpi)
        if row is None or row.direction == "consistent":
            continue
        out.append(
            f"{row.label} came in {row.measured:.2f} {row.unit} against a predicted "
            f"{row.predicted:.2f} ({row.z:+.1f} sigma, {row.direction})"
        )
    return out


def _rule_drying_fault(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "moisture_pct") > INTERESTING_Z:
        severity = min(1.0, _z(residuals, "moisture_pct") / 4.0)
        if _z(residuals, "water_activity") > 0:
            severity = min(1.0, severity + 0.15)
        evidence = _evidence(residuals, "moisture_pct", "water_activity", "texture_index")
        evidence.append(
            "Product moisture is above prediction while the formulation that went in is unchanged, "
            "so water was not removed as the model expects."
        )
        return severity, evidence
    return None


def _rule_dryer_temperature(residuals: Dict[str, Residual], ctx: RuleContext):
    offset = ctx.process_actuals.get("dryer_temp_c") or ctx.process_actuals.get("bake_temp_c") or ctx.process_actuals.get("cook_temp_c")
    if offset is None or _z(residuals, "moisture_pct") <= INTERESTING_Z:
        return None
    severity = min(1.0, 0.5 + _z(residuals, "moisture_pct") / 6.0)
    return severity, [
        f"The recorded process temperature was {offset:.1f} degC against the planned setting",
        "Drying rate falls steeply with temperature, so this alone explains a high moisture result",
    ]


def _rule_acid_loss(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "ph") > INTERESTING_Z:
        severity = min(1.0, 0.45 + _z(residuals, "ph") / 5.0)
        evidence = _evidence(residuals, "ph")
        evidence.append(
            "pH above prediction means less acid is present than was weighed in: acid dosed early "
            "into an open kettle is partly lost, and stored acidulants lose strength."
        )
        return severity, evidence
    return None


def _rule_acid_overdose(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "ph") < -INTERESTING_Z:
        severity = min(1.0, 0.45 + abs(_z(residuals, "ph")) / 5.0)
        return severity, _evidence(residuals, "ph") + [
            "pH below prediction indicates more acid than intended: check the dose, the acid's "
            "declared strength (a 5% vinegar is not an 8% vinegar) and the weighing tolerance"
        ]
    return None


def _rule_scale_dosing(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "sodium_mg") > INTERESTING_Z:
        severity = min(1.0, 0.4 + _z(residuals, "sodium_mg") / 5.0)
        return severity, _evidence(residuals, "sodium_mg") + [
            "Sodium above prediction points at dosing rather than formulation: salt, leavening and "
            "preservative salts are all minor lines weighed on the same scale"
        ]
    return None


def _rule_over_dry(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "moisture_pct") < -INTERESTING_Z:
        severity = min(1.0, 0.4 + abs(_z(residuals, "moisture_pct")) / 4.0)
        evidence = _evidence(residuals, "moisture_pct", "texture_index")
        evidence.append("Product dried further than predicted: expect a harder, more brittle texture and check the post-bake hold time.")
        return severity, evidence
    return None


def _rule_texture_under(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "texture_index") < -INTERESTING_Z or _z(residuals, "hardness_n") < -INTERESTING_Z:
        severity = min(1.0, 0.45 + abs(min(_z(residuals, "texture_index"), _z(residuals, "hardness_n"))) / 5.0)
        return severity, _evidence(residuals, "texture_index", "hardness_n", "moisture_pct") + [
            "A softer structure than predicted follows from residual moisture, from under-expansion, "
            "or from a fat that is softer than the model assumes"
        ]
    return None


def _rule_texture_over(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "texture_index") > INTERESTING_Z or _z(residuals, "hardness_n") > INTERESTING_Z:
        severity = min(1.0, 0.45 + abs(max(_z(residuals, "texture_index"), _z(residuals, "hardness_n"))) / 5.0)
        return severity, _evidence(residuals, "texture_index", "hardness_n", "moisture_pct") + [
            "A harder structure than predicted usually traces to over-baking, to a fibre load "
            "absorbing more water than modelled, or to a crystallised syrup"
        ]
    return None


def _rule_extrusion(residuals: Dict[str, Residual], ctx: RuleContext):
    if ctx.category != "extruded_snack":
        return None
    feed = ctx.process_actuals.get("feed_moisture_pct")
    temp = ctx.process_actuals.get("barrel_temp_c")
    soft = _z(residuals, "texture_index") < -INTERESTING_Z
    if not soft or (feed is None and temp is None):
        return None
    severity = min(1.0, 0.5 + abs(_z(residuals, "texture_index")) / 5.0)
    evidence = _evidence(residuals, "texture_index")
    if feed is not None:
        evidence.append(f"Extruder feed moisture was recorded at {feed:.1f}%: above about 20% the extrudate expands less and reads soft")
    if temp is not None:
        evidence.append(f"Barrel temperature was recorded at {temp:.0f} degC: low barrel temperature reduces expansion and cook")
    return severity, evidence


def _rule_sugar_inversion(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "sugar_g") > INTERESTING_Z:
        severity = min(1.0, 0.4 + _z(residuals, "sugar_g") / 5.0)
        return severity, _evidence(residuals, "sugar_g") + [
            "Total sugars above prediction with the same syrup charge means inversion or syrup "
            "solids: check the syrup's own analysis and the kettle time at temperature"
        ]
    return None


def _rule_oxidation(residuals: Dict[str, Residual], ctx: RuleContext):
    rows = [r for k in ("oxidation_risk",) if (r := _residual(residuals, k))]
    rancid = any(r.z > INTERESTING_Z for r in rows) or _z(residuals, "shelf_life_days") < -INTERESTING_Z
    if not rancid:
        return None
    severity = min(1.0, 0.5 + abs(_z(residuals, "oxidation_risk") or _z(residuals, "shelf_life_days")) / 5.0)
    return severity, _evidence(residuals, "oxidation_risk", "shelf_life_days") + [
        "Oxidation or a short shelf life against prediction points at fat quality: check the oil's "
        "peroxide value and free fatty acids, and the hold time at temperature before packing"
    ]


def _rule_dilution(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "energy_kcal") < -INTERESTING_Z and _z(residuals, "moisture_pct") > 0:
        severity = min(1.0, 0.4 + abs(_z(residuals, "energy_kcal")) / 5.0)
        return severity, _evidence(residuals, "energy_kcal", "moisture_pct") + [
            "Energy short of prediction with extra moisture is the signature of an over-diluted batch: "
            "check the water addition and the batch yield against the sheet"
        ]
    return None


def _rule_yield_cost(residuals: Dict[str, Residual], ctx: RuleContext):
    if _z(residuals, "cost_inr_kg") > INTERESTING_Z:
        severity = min(1.0, 0.35 + _z(residuals, "cost_inr_kg") / 5.0)
        return severity, _evidence(residuals, "cost_inr_kg") + [
            "Cost above prediction at the same recipe means lower yield or over-fill: reconcile the "
            "batch's actual output weight before changing anything about the formula"
        ]
    return None


def _rule_process_deviation(residuals: Dict[str, Residual], ctx: RuleContext):
    if not ctx.analysis.process_deviations:
        return None
    worst = max(ctx.analysis.process_deviations, key=lambda d: abs(d["relative_pct"]))
    severity = min(1.0, 0.4 + abs(worst["relative_pct"]) / 25.0)
    return severity, [
        f"{worst['parameter']} was run at {worst['actual']} against a setpoint of {worst['setpoint']} "
        f"({worst['relative_pct']:+.1f}%)",
        "The trial did not run to plan, so its results describe the plant as much as the formulation",
    ]


def _rule_model_bias(residuals: Dict[str, Residual], ctx: RuleContext):
    if not ctx.analysis.systematic:
        return None
    severity = min(0.85, 0.35 + abs(ctx.analysis.mean_z) / 6.0)
    return severity, [
        f"The residuals are systematic (mean z {ctx.analysis.mean_z:+.2f} across "
        f"{len(ctx.analysis.flagged)} KPIs), which is the signature of a model that is offset for "
        "this plant rather than of a formulation error",
        "The surrogate will absorb this as a residual correction rather than a change to the recipe",
    ]


def _rule_confirm(residuals: Dict[str, Residual], ctx: RuleContext):
    if ctx.analysis.flagged:
        return None
    return 0.9, ["All measured KPIs sit inside the published prediction interval"]


RULES: List[Rule] = [
    Rule(
        id="dryer_temperature_offset",
        cause="Process temperature was below plan, so the product was not dried to specification",
        category="process",
        kpis=("moisture_pct", "water_activity"),
        action=(
            "Verify the oven/dryer profile with a calibrated thermocouple at the product surface, "
            "not at the controller; run a setpoint-confirmation trial before changing the recipe."
        ),
        test=_rule_dryer_temperature,
    ),
    Rule(
        id="incomplete_drying",
        cause="Drying or baking is not removing water at the rate the model assumes",
        category="process",
        kpis=("moisture_pct", "water_activity"),
        action=(
            "Increase bake/dryer time by 10-15% or raise temperature by 5 degC and re-check; if the "
            "gap persists, refit the drying constant from this trial's inlet/outlet data."
        ),
        test=_rule_drying_fault,
    ),
    Rule(
        id="acid_loss",
        cause="Acid dosed into the product is not all present at analysis (loss or strength)",
        category="formulation",
        kpis=("ph",),
        action=(
            "Add the acid later in the process and verify the delivered strength of the acidulant "
            "(titrate it); then re-dose against the titratable acidity rather than the weight."
        ),
        test=_rule_acid_loss,
    ),
    Rule(
        id="acid_overdose",
        cause="More acid present than the formula specifies",
        category="formulation",
        kpis=("ph",),
        action="Re-check the acid dose and the acidulant's declared strength; confirm the pH probe calibration.",
        test=_rule_acid_overdose,
    ),
    Rule(
        id="minor_dosing_error",
        cause="A minor ingredient (salt, leavening, preservative salt) was over-dosed",
        category="process",
        kpis=("sodium_mg",),
        action=(
            "Audit the weighing of minor lines: check scale calibration, the premix dilution, and "
            "whether the leavening and salt share a scoop on the line."
        ),
        test=_rule_scale_dosing,
    ),
    Rule(
        id="over_dried",
        cause="The product dried further than predicted",
        category="process",
        kpis=("moisture_pct", "texture_index"),
        action="Reduce bake/drying time or temperature, and check the hold time between the oven and packing.",
        test=_rule_over_dry,
    ),
    Rule(
        id="under_expansion",
        cause="Extrudate under-expanded, giving a soft structure",
        category="process",
        kpis=("texture_index",),
        action=(
            "Reduce feed moisture towards 16-18%, raise barrel temperature, and confirm screw speed "
            "and die pressure against the run sheet."
        ),
        test=_rule_extrusion,
    ),
    Rule(
        id="soft_structure",
        cause="Structure softer than predicted",
        category="formulation",
        kpis=("texture_index", "hardness_n"),
        action=(
            "Check the fat's solid-fat content and the sugar form (a coarse or partially "
            "crystalline sugar creams differently), then adjust the fat or the creaming time."
        ),
        test=_rule_texture_under,
    ),
    Rule(
        id="hard_structure",
        cause="Structure harder than predicted",
        category="formulation",
        kpis=("texture_index", "hardness_n"),
        action=(
            "Check the fibre load's water absorption and the syrup/sugar state; reduce mixing time "
            "after the flour is added to limit gluten development."
        ),
        test=_rule_texture_over,
    ),
    Rule(
        id="sugar_inversion",
        cause="Sugar level higher than formulated (inversion or syrup solids)",
        category="ingredient",
        kpis=("sugar_g",),
        action="Request the syrup's own sugar analysis, and shorten the time the batch spends hot before filling.",
        test=_rule_sugar_inversion,
    ),
    Rule(
        id="fat_oxidation",
        cause="Fat oxidation proceeding faster than modelled",
        category="ingredient",
        kpis=("oxidation_risk", "shelf_life_days"),
        action=(
            "Check the oil's peroxide value and free fatty acids, reduce the holding time of the "
            "fat blend, and confirm the antioxidant is added at the right point."
        ),
        test=_rule_oxidation,
    ),
    Rule(
        id="batch_dilution",
        cause="Batch over-diluted or over-filled",
        category="process",
        kpis=("energy_kcal", "moisture_pct"),
        action="Reconcile the added water against the batch sheet and the actual yield against the theoretical yield.",
        test=_rule_dilution,
    ),
    Rule(
        id="yield_or_fill_loss",
        cause="Yield or fill-weight loss is inflating unit cost",
        category="process",
        kpis=("cost_inr_kg",),
        action="Reconcile the batch output weight and the fill weights before touching the formulation.",
        test=_rule_yield_cost,
    ),
    Rule(
        id="process_setpoint_deviation",
        cause="The trial did not run at the planned settings",
        category="process",
        kpis=(),
        action="Repeat the trial at the intended setpoints; until then this trial cannot separate formulation from plant.",
        test=_rule_process_deviation,
    ),
    Rule(
        id="model_bias",
        cause="The prediction model is offset for this plant (systematic bias)",
        category="model",
        kpis=(),
        action=(
            "Do not change the recipe: keep the formulation and let the surrogate absorb the offset, "
            "then re-test. Confirm the bias reproduces on the next batch before adjusting."
        ),
        test=_rule_model_bias,
    ),
    Rule(
        id="confirmation",
        cause="Trial confirms the formulation",
        category="confirmation",
        kpis=(),
        action="Proceed to the next-gate work: shelf-life study set-up, packaging compatibility, and a second plant confirmation.",
        test=_rule_confirm,
    ),
]


def diagnose(
    analysis: Analysis,
    formulation: Formulation,
    brief: Optional[Brief] = None,
    max_causes: int = 5,
    process_actuals: Optional[Dict[str, float]] = None,
) -> List[DiagnosisCause]:
    """Rank the probable causes of a trial's deviations."""
    residuals = {row.kpi: row for row in analysis.residuals}
    ctx = RuleContext(
        formulation=formulation,
        brief=brief,
        analysis=analysis,
        process_actuals=dict(process_actuals or {}),
    )
    raw: List[Tuple[float, Rule, List[str]]] = []
    for rule in RULES:
        try:
            outcome = rule.test(residuals, ctx)
        except Exception:  # pragma: no cover - a rule must never break a diagnosis
            outcome = None
        if outcome is None:
            continue
        severity, evidence = outcome
        if severity <= 0.0:
            continue
        # A rule that describes a KPI the brief cares about is more relevant.
        priority = 1.0
        if brief is not None:
            weights = []
            for kpi_id in rule.kpis:
                target = brief.target(kpi_id)
                if target is not None:
                    weights.append(max(target.priority, 0.1) * (1.3 if target.hard else 0.8))
            if weights:
                priority = sum(weights) / len(weights)
        raw.append((severity * priority, rule, evidence))

    if not raw:
        return []
    ranked = sorted(raw, key=lambda row: -row[0])[:max_causes]
    total = sum(weight for weight, _, _ in ranked)
    causes: List[DiagnosisCause] = []
    for rank, (weight, rule, evidence) in enumerate(ranked, start=1):
        # Confidence is the rule's share of the surviving evidence mass. It is a
        # share, not a probability: two rules that both explain the data split it,
        # which is the honest reading of one trial.
        share = weight / total if total > 0 else 0.0
        confidence = 0.28 + 0.70 * share
        causes.append(
            DiagnosisCause(
                rank=rank,
                cause=rule.cause,
                category=rule.category,
                confidence=round(min(0.95, confidence), 3),
                evidence=evidence,
                kpis=list(rule.kpis),
                recommended_action=rule.action,
            )
        )
    return causes


def attach_process_actuals(analysis: Analysis, trial: TrialResult) -> Analysis:
    """Copy the trial's recorded process values into the analysis for the rules."""
    analysis.process_deviations = list(analysis.process_deviations)
    for key, value in trial.process_actuals.items():
        analysis.process_deviations.append({"parameter": key, "actual": round(float(value), 3)})
    return analysis
