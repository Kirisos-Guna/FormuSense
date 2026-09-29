"""Reports: one product's development history, and the internship document.

Two very different documents come out of the same record:

* :func:`product_report` is the *project* report - what was asked for, what was
  designed, what the plant produced, what was diagnosed, what changed, and what it
  cost in trials. It is written from the database and nowhere else, so it can be
  regenerated at any time and it cannot flatter the work: the trial count in the
  report is the trial count in the record.
* :func:`internship_report` is the *academic* document required by the
  internship, assembled in the layout of the supplied template (see
  :mod:`app.docx_writer` for the file format and :mod:`app.report_sections` for
  the chapter text).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .core import kpi as kpi_registry

MAX_TRIAL_COLUMNS = 6


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return "-"
        return f"{value:.{digits}f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:.0f}"
    return str(value)


def _table(rows: List[List[str]], header: List[str]) -> List[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join([" --- "] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def product_report(view: Dict[str, Any], title: Optional[str] = None) -> str:
    """The development report of one product, as Markdown."""
    product = view.get("product") or {}
    brief = view.get("brief") or {}
    formulation = view.get("formulation") or {}
    trials = view.get("trials") or []
    efficiency = view.get("efficiency") or {}
    lines: List[str] = []

    lines.append(f"# {title or product.get('name', 'Product')}")
    lines.append("")
    lines.append(
        f"*Category: {brief.get('category_label', product.get('category', ''))} | "
        # A drink is declared by volume, so the report says millilitres for it.
        f"unit {('volume' if (brief.get('declared_unit') or 'g') == 'ml' else 'weight')}: "
        f"{_fmt(brief.get('declared_unit_size') or brief.get('unit_weight_g'), 1)} "
        f"{brief.get('declared_unit') or 'g'} | diet: {brief.get('diet', '')} | "
        f"versions: {efficiency.get('versions_used', 0)} | trials: {efficiency.get('trials_used', 0)}*"
    )
    lines.append("")

    # 1. What was asked for.
    lines.append("## 1. The brief as understood")
    lines.append("")
    if brief.get("description"):
        lines.append(brief["description"])
        lines.append("")
    if brief.get("claims"):
        lines.append(f"**Claims to substantiate:** {', '.join(brief['claims'])}")
        lines.append("")
    if brief.get("allergens_to_avoid"):
        lines.append(f"**Allergens to avoid:** {', '.join(brief['allergens_to_avoid'])}")
        lines.append("")
    lines.append(
        f"**Targets:** {brief.get('hard_target_count', 0)} hard of {brief.get('target_count', 0)}. "
        f"Parsed by: {brief.get('understood_by', 'offline-parser')}."
    )
    lines.append("")
    target_rows = []
    for target in brief.get("targets", []):
        target_rows.append(
            [
                str(target.get("label") or target.get("id")),
                f"{_fmt(target.get('target'))} {target.get('unit', '')}".strip(),
                ("+/- " + _fmt(target.get("tolerance"))) if target.get("tolerance") else "-",
                str(target.get("direction", "")),
                "hard" if target.get("hard") else "monitored",
            ]
        )
    if target_rows:
        lines.extend(_table(target_rows, ["KPI", "Target", "Tolerance", "Direction", "Class"]))
        lines.append("")

    # 2. What was designed.
    lines.append("## 2. The formulation on record")
    lines.append("")
    if formulation:
        lines.append(
            f"*{formulation.get('label', '')} - v{formulation.get('version', '')}, "
            f"total {_fmt(formulation.get('total_pct'), 2)}%*"
        )
        lines.append("")
        rows = []
        for item in formulation.get("items", []):
            rows.append(
                [
                    str(item.get("name") or item.get("ingredient_id")),
                    str(item.get("slot", "")),
                    _fmt(item.get("pct"), 3),
                ]
            )
        lines.extend(_table(rows, ["Ingredient", "Role", "% w/w"]))
        lines.append("")
        params = formulation.get("params") or {}
        if params:
            lines.append(
                "**Process settings:** "
                + ", ".join(f"{key} {_fmt(value, 2)}" for key, value in params.items())
                + "."
            )
            lines.append("")

    # 3. What the plant produced.
    lines.append("## 3. Trials, measurements and residuals")
    lines.append("")
    if not trials:
        lines.append("No physical trial has been run for this product yet.")
        lines.append("")
    else:
        rows = []
        for index, trial in enumerate(trials, start=1):
            summary = _trial_summary(trial)
            analysis = trial.get("analysis") or {}
            rows.append(
                [
                    str(trial.get("label") or f"T{index}"),
                    f"v{trial.get('formulation_version', '')}",
                    f"{summary['on_target']}/{summary['total']}",
                    _fmt(analysis.get("measured_objective"), 3),
                    str(summary["verdict"]),
                ]
            )
        lines.extend(_table(rows, ["Trial", "Version", "On target", "Objective", "Verdict"]))
        lines.append("")
        lines.extend(_measured_table(trials, brief))
        lines.append("")

    # 4. What was diagnosed.
    lines.append("## 4. Diagnosis")
    lines.append("")
    any_causes = False
    for index, trial in enumerate(trials, start=1):
        diagnosis = trial.get("diagnosis") or {}
        causes = diagnosis.get("causes") or []
        if not causes:
            continue
        any_causes = True
        lines.append(f"**{trial.get('label') or f'T{index}'}**")
        lines.append("")
        for cause in causes[:3]:
            lines.append(
                f"- *{cause.get('cause', '')}* "
                f"(confidence {_fmt(cause.get('confidence'), 2)}, category {cause.get('category', '')})"
            )
            for evidence in (cause.get("evidence") or [])[:2]:
                text = evidence if isinstance(evidence, str) else str(evidence)
                lines.append(f"  - {text}")
        lines.append("")
    if not any_causes:
        lines.append(
            "No systematic cause was identified: every measured value fell inside the interval "
            "published before the trial, so the deviations are consistent with the model as it stands."
        )
        lines.append("")

    # 5. What changed, and why.
    lines.append("## 5. Plans and changes")
    lines.append("")
    plans = view.get("plans") or []
    if not plans:
        lines.append("No reformulation plan has been made yet.")
        lines.append("")
    for plan in plans:
        payload = plan.get("payload") if isinstance(plan, dict) and "payload" in plan else plan
        payload = payload or {}
        lines.append(
            f"**Plan {plan.get('id', '')}: v{payload.get('from_version', '')} -> "
            f"v{payload.get('to_version', '')}** "
            f"({'accepted' if plan.get('accepted') else 'proposed'})"
        )
        lines.append("")
        for reason in payload.get("rationale") or []:
            lines.append(f"- {reason}")
        deltas = payload.get("deltas") or []
        if deltas:
            lines.append("")
            rows = [
                [
                    str(delta.get("name", "")),
                    _fmt(delta.get("from_pct"), 3),
                    _fmt(delta.get("to_pct"), 3),
                    _fmt(delta.get("delta_pct"), 3),
                ]
                for delta in deltas[:10]
            ]
            lines.extend(_table(rows, ["Ingredient", "From %", "To %", "Change"]))
        param_deltas = payload.get("param_deltas") or []
        if param_deltas:
            lines.append("")
            rows = [
                [
                    str(delta.get("label", delta.get("parameter", ""))),
                    _fmt(delta.get("from"), 2),
                    _fmt(delta.get("to"), 2),
                    str(delta.get("unit", "")),
                    str(delta.get("reason") or "optimiser move"),
                ]
                for delta in param_deltas
            ]
            lines.extend(_table(rows, ["Setting", "From", "To", "Unit", "Reason"]))
        lines.append("")

    # 6. What it cost.
    lines.append("## 6. Trial efficiency and prediction accuracy")
    lines.append("")
    lines.append(f"- Physical trials used: **{efficiency.get('trials_used', 0)}**")
    lines.append(f"- Formulation versions used: **{efficiency.get('versions_used', 0)}**")
    reached = efficiency.get("trials_to_target")
    lines.append(
        "- Trials to target: **"
        + (str(reached) if reached else "not reached within the trials run")
        + "**"
    )
    lines.append(f"- First version passed as designed: **{_fmt(efficiency.get('first_version_passed'))}**")
    lines.append(f"- Material consumed in trials: **{_fmt(efficiency.get('material_used_kg'), 2)} kg**")
    lines.append("")
    accuracy = efficiency.get("prediction_accuracy") or {}
    rows = [
        [
            kpi_registry.kpi_label(str(row.get("kpi", ""))),
            _fmt(row.get("mape_pct"), 1) + " %",
            str(row.get("samples", "")),
        ]
        for row in (accuracy.get("per_kpi") or [])
    ]
    if rows:
        lines.extend(_table(rows, ["KPI", "Mean absolute error vs published interval", "Samples"]))
        lines.append(
            f"Across all KPIs and trials the mean absolute prediction error was "
            f"**{_fmt(accuracy.get('overall_mape_pct'), 1)} %**, and "
            f"**{_fmt(accuracy.get('interval_coverage_pct'), 0)} %** of measurements landed inside "
            "the interval published before the trial (95% intervals, so ~95% is the target)."
        )
        lines.append("")
    return "\n".join(lines)


def _trial_summary(trial: Dict[str, Any]) -> Dict[str, Any]:
    analysis = trial.get("analysis") or {}
    # The analysis record carries the counts on itself; a nested summary is used
    # by the service while a loop is running.
    summary = analysis.get("measured_summary") or analysis
    total = int(summary.get("measured_total") or 0)
    on_target = int(summary.get("measured_on_target") or 0)
    verdict = "reformulate"
    if total and on_target == total:
        verdict = "pass"
    elif total and on_target >= total - 1:
        verdict = "marginal"
    return {"total": total, "on_target": on_target, "verdict": verdict}


def _measured_table(trials: List[Dict[str, Any]], brief: Dict[str, Any]) -> List[str]:
    """Measured values of every KPI across the trials run, newest column last."""
    targets = {target["id"]: target for target in (brief.get("targets") or [])}
    measured_kpis: List[str] = []
    for trial in trials:
        for kpi_id in (trial.get("measurements") or {}):
            if kpi_id not in measured_kpis:
                measured_kpis.append(kpi_id)
    if not measured_kpis:
        return []
    header = ["KPI", "Target"]
    for index, trial in enumerate(trials[:MAX_TRIAL_COLUMNS], start=1):
        header.append(str(trial.get("label") or f"T{index}"))
    header.append("Status")
    rows: List[List[str]] = []
    for kpi_id in measured_kpis:
        target = targets.get(kpi_id)
        row = [
            kpi_registry.kpi_label(kpi_id),
            (
                _fmt(target.get("target")) + " " + str(target.get("unit", ""))
                if target and target.get("target") is not None
                else "-"
            ),
        ]
        last_status = "-"
        for trial in trials[:MAX_TRIAL_COLUMNS]:
            value = (trial.get("measurements") or {}).get(kpi_id)
            row.append(_fmt(value, 3))
            analysis = (trial.get("analysis") or {}).get("residuals") or []
            for residual in analysis:
                if residual.get("kpi") == kpi_id:
                    on_target = residual.get("on_target")
                    if on_target is None:
                        last_status = "-"
                    else:
                        last_status = "inside tolerance" if on_target else "outside tolerance"
        row.append(last_status)
        rows.append(row)
    return _table(rows, header)


def benchmark_summary(report: Dict[str, Any]) -> str:
    """A short Markdown summary of a benchmark run, for the report."""
    summary = report.get("summary") or {}
    lines = [
        "## Trial-efficiency benchmark",
        "",
        f"- Products compared: **{summary.get('cases', 0)}**",
        f"- Agent: **{summary.get('agent_successes', 0)}** reached target, mean "
        f"**{summary.get('agent_mean_trials_to_target')}** trials",
        f"- One-factor-at-a-time: **{summary.get('ofat_successes', 0)}** reached target, mean "
        f"**{summary.get('ofat_mean_trials_to_target')}** trials (censored at the budget)",
        f"- Mean saving: **{summary.get('mean_trials_saved')} trials per product**",
        "",
    ]
    rows = []
    for row in report.get("comparison") or []:
        rows.append(
            [
                str(row.get("case", ""))[:44],
                str(row.get("agent_trials") or "not reached"),
                str(row.get("ofat_trials") or "not reached"),
                _fmt(row.get("agent_truth_objective"), 3),
                _fmt(row.get("ofat_truth_objective"), 3),
            ]
        )
    if rows:
        lines.extend(
            _table(rows, ["Product", "Agent trials", "OFAT trials", "Agent objective", "OFAT objective"])
        )
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The internship document
# --------------------------------------------------------------------------- #
def internship_report(
    benchmark: Optional[Dict[str, Any]] = None,
    products: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The chapters of the internship report, as data.

    Kept as a structure rather than as finished text so the same content can be
    written to DOCX, HTML or Markdown without being retyped, and so the figures
    and tables are generated from the record instead of being pasted in.
    """
    from . import report_sections

    context = {
        "benchmark": benchmark or {},
        "products": products or [],
    }
    return report_sections.build(context)
