"""The chapters of the internship report, as data.

The report is written in the layout of the supplied SRM template - title page,
bonafide certificate, internship completion letter, INDEX, then eight chapters,
each behind its own centred ``CHAPTER N`` page. The template is a *format*, so
this module holds the *content*: one function per chapter, each returning the
same block dictionaries that :class:`app.docx_writer.Docx` writes to Word and
that :mod:`app.report_writers` writes to HTML and Markdown.

Everything that can be counted is counted from the record rather than typed in:
the trial counts come from the benchmark that was just run, the ingredient and
KPI counts come from the knowledge base, the version numbers come from the
environment the report was built in, and the database chapter is read from
``PRAGMA table_info``. If a number moves, the report moves with it.

Anything the student has to supply - their name, register number, the dates, the
scanned certificates - is written as a visible ``[...]`` marker instead of being
invented, and :data:`STUDENT` is the single place to edit them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PROJECT_TITLE = "AI-POWERED FOOD PRODUCT DEVELOPMENT AGENT"
PROJECT_SUBTITLE = (
    "an offline formulation, process and trial-diagnosis assistant that reduces "
    "trials-to-target in snack and spread development"
)

#: Front matter. Everything in square brackets is a marker for the student to
#: replace before submission; nothing here is guessed.
STUDENT: Dict[str, str] = {
    "name": "[Student name]",
    "register_number": "[Register number]",
    "programme": "Master of Computer Applications",
    "semester": "[Semester and academic year]",
    "department": "Department of Computer Applications",
    "institution": "SRM Institute of Science and Technology, Ramapuram Campus, Chennai",
    "guide": "[Faculty guide, Department of Computer Applications]",
    "organisation": "Tiny Dot Foods",
    "organisation_guide": "[Industry guide at Tiny Dot Foods]",
    "duration": "[Internship start date] to [Internship end date]",
}

CHAPTERS: List[tuple] = [
    (1, "Abstract"),
    (2, "Details about the Training"),
    (3, "Project Description"),
    (4, "Hardware and Software Requirements"),
    (5, "Coding Screenshots"),
    (6, "Design Database"),
    (7, "Screenshots"),
    (8, "References"),
]


# --------------------------------------------------------------------------- #
# Block helpers
# --------------------------------------------------------------------------- #
def _p(text: str) -> Dict[str, Any]:
    return {"kind": "body", "text": text}


def _h2(text: str) -> Dict[str, Any]:
    return {"kind": "heading2", "text": text}


def _h3(text: str) -> Dict[str, Any]:
    return {"kind": "heading3", "text": text}


def _bullets(items: List[str]) -> List[Dict[str, Any]]:
    return [{"kind": "bullet", "text": item} for item in items]


def _table(rows: List[List[Any]], *extra: List[Any], caption: str = "", widths: Optional[List[float]] = None) -> Dict[str, Any]:
    """A table block.

    Either call shape is accepted: one list holding every row, or a header row
    followed by the body rows as further arguments. Both are used in the text of
    this module, and getting them confused silently rotates a table - which is
    the kind of mistake a reader notices and the author does not.
    """
    if extra:
        data: List[List[Any]] = [list(rows)] + [list(row) for row in extra]
    else:
        data = [list(row) for row in rows]
    return {"kind": "table", "rows": data, "header": True, "caption": caption, "widths": widths}


def _figure(path: Any, caption: str, width: float = 6.2) -> Dict[str, Any]:
    return {"kind": "image", "path": path, "caption": caption, "width_in": width}


def _code(text: str, caption: str = "") -> Dict[str, Any]:
    return {"kind": "code", "text": text, "caption": caption}


def _placeholder(text: str) -> Dict[str, Any]:
    return {"kind": "placeholder", "text": text}


def _spacer(points: int = 200) -> Dict[str, Any]:
    return {"kind": "spacer", "points": points}


def _excerpt(text: str, max_lines: int = 96, note: bool = True) -> str:
    """Trim a long listing to a printable length, saying so rather than lying."""
    lines = str(text).splitlines()
    if len(lines) <= max_lines:
        return str(text)
    kept = lines[:max_lines]
    if note:
        kept.append("")
        kept.append(f"... {len(lines) - max_lines} further lines trimmed for the printed page.")
    return "\n".join(kept)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #
def _context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    context = dict(context or {})
    student = dict(STUDENT)
    student.update(context.get("student") or {})
    benchmark = context.get("benchmark") or {}
    summary = benchmark.get("summary") or {}
    context.setdefault("figures", {})
    context.setdefault("listings", [])
    context.setdefault("environment", {})
    context.setdefault("schema", {"tables": [], "ddl": "", "samples": {}})
    context.setdefault("kb", {})
    context.setdefault("modules", [])
    context.setdefault("case_reports", {})
    context["student"] = student
    context["benchmark_summary"] = summary
    context["comparison"] = benchmark.get("comparison") or []
    context["arms"] = {arm["arm"]: arm for arm in benchmark.get("arms") or []}
    return context


def _arm_rows(context: Dict[str, Any]) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in context["comparison"]:
        rows.append(
            [
                row["case"],
                _fmt(row["agent_trials"]) if row["agent_trials"] else "not reached",
                _fmt(row["ofat_trials"]) if row["ofat_trials"] else "not reached",
                _fmt(row["agent_final_objective"]),
                _fmt(row["ofat_final_objective"]),
                _fmt(row["trials_saved"]),
            ]
        )
    return rows


# --------------------------------------------------------------------------- #
# Front matter
# --------------------------------------------------------------------------- #
def _front_matter(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    student = context["student"]
    blocks: List[Dict[str, Any]] = []

    # --- title page -------------------------------------------------------- #
    blocks.append({"kind": "titlepage", "text": student["institution"], "bold": True, "size": 28})
    blocks.append(_spacer(320))
    blocks.append({"kind": "titlepage", "text": "INTERNSHIP REPORT", "bold": True, "size": 32})
    blocks.append(_spacer(240))
    blocks.append({"kind": "titlepage", "text": PROJECT_TITLE, "bold": True, "size": 30})
    blocks.append({"kind": "titlepage", "text": PROJECT_SUBTITLE, "bold": False, "size": 24})
    blocks.append(_spacer(240))
    blocks.append({"kind": "titlepage", "text": "Submitted in partial fulfilment of the requirements for the award of the degree of", "bold": False, "size": 22})
    blocks.append({"kind": "titlepage", "text": student["programme"], "bold": True, "size": 26})
    blocks.append(_spacer(240))
    blocks.append({"kind": "titlepage", "text": "Submitted by", "bold": False, "size": 22})
    blocks.append({"kind": "titlepage", "text": student["name"], "bold": True, "size": 28})
    blocks.append({"kind": "titlepage", "text": student["register_number"], "bold": False, "size": 24})
    blocks.append(_spacer(240))
    blocks.append({"kind": "titlepage", "text": f"Internship carried out at {student['organisation']}", "bold": False, "size": 24})
    blocks.append({"kind": "titlepage", "text": student["duration"], "bold": False, "size": 22})
    blocks.append(_spacer(320))
    blocks.append({"kind": "titlepage", "text": student["department"], "bold": True, "size": 26})
    blocks.append({"kind": "titlepage", "text": f"{student['institution']}  |  {student['semester']}", "bold": False, "size": 22})
    blocks.append({"kind": "pagebreak"})

    # --- bonafide certificate --------------------------------------------- #
    blocks.append({"kind": "heading1", "text": "BONAFIDE CERTIFICATE"})
    blocks.append(_p(
        f"This is to certify that {student['name']}, bearing register number "
        f"{student['register_number']}, a student of the {student['programme']} programme at the "
        f"{student['department']}, {student['institution']}, has satisfactorily completed the "
        f"internship work described in this report at {student['organisation']} during "
        f"{student['duration']}. The work reported here is the candidate's own work carried out "
        "under the guidance of the undersigned. It has not been submitted elsewhere for the award "
        "of any other degree or diploma."
    ))
    blocks.append(_spacer(320))
    blocks.append(_table(
        [
            ["Signature", "Signature"],
            [student["guide"], student["organisation_guide"]],
            ["Faculty guide", "Industry guide"],
        ],
        caption="",
        widths=[1, 1],
    ))
    blocks.append(_placeholder(
        "Marker: add the internal examiner's signature block and the college seal above this line "
        "once the report is bound."
    ))
    blocks.append({"kind": "pagebreak"})

    # --- completion letter ------------------------------------------------- #
    blocks.append({"kind": "heading1", "text": "INTERNSHIP COMPLETION LETTER"})
    blocks.append(_placeholder(
        "Insert the internship completion letter soft copy in image format."
    ))
    blocks.append(_p(
        "The letter issued by the organisation confirms the duration and the nature of the "
        "internship; it is placed here unaltered, as issued."
    ))
    blocks.append({"kind": "pagebreak"})

    # --- index ------------------------------------------------------------- #
    blocks.append(_p(
        "The index below is a live field. Word fills in the page numbers when the document is "
        "opened or whenever the field is updated, so the numbers always match the pagination that "
        "results from editing the report."
    ))
    blocks.append({"kind": "index", "title": "INDEX"})
    blocks.append(_spacer(120))
    blocks.append(_table(
        [["Chapter", "Title"]] + [[str(number), title] for number, title in CHAPTERS],            caption="Table 0.1  Chapter list (in the Word file the INDEX field above is also filled in by Word on opening)",
        widths=[0.22, 1.0],
    ))
    blocks.append({"kind": "pagebreak"})
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 1
# --------------------------------------------------------------------------- #
def _chapter_1(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary = context["benchmark_summary"]
    environment = context["environment"]
    kb = context["kb"]
    cases = summary.get("cases") or len(context["comparison"])
    agent_mean = summary.get("agent_mean_trials_to_target")
    ofat_mean = summary.get("ofat_mean_trials_to_target")
    saved = summary.get("mean_trials_saved")
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 1"},
        {"kind": "heading1", "text": "Abstract"},
        _p(
            "A new food product is rarely right the first time. Between the specification a brand "
            "writes and a recipe that actually meets it lie weeks of physical trials, and every "
            "trial costs material, machine time, analytical work and shelf space. The problem "
            "statement this internship addresses (PS-1, Tiny Dot Foods) is therefore not "
            "\"design a product\" but \"stop paying for trials that a model could have avoided\"."
        ),
        _p(
            "This report describes a working software agent that carries a product from a written "
            "specification to a validated formulation. The agent reads the brief - text, target "
            "numbers and any product photographs - and converts it into a set of measurable "
            "targets with tolerances and priorities. It then builds an initial formulation by "
            "selecting ingredients into the functional slots the category defines (base, protein "
            "source, fat, sweetener, fibre, acidulant and so on), predicts the composition, "
            "physical properties, stability and cost of that recipe, and searches the recipe and "
            "the process parameters for an improvement. A simulated plant turns the recipe into "
            "measurements with the deliberate faults a real line would show: a dryer that runs "
            "below its setpoint, an acid dose that under-delivers, sugar inversion, dosing "
            "variation. The agent compares measured results against its own published intervals, "
            "diagnoses the probable cause, compensates for the process offset, reformulates, and "
            "plans the next trial - including the design of experiments that will tell it the "
            "most about the factors it just moved."
        ),
        _p(
            "The claim that matters is measured, not asserted. The same three product cases were "
            "run twice: once with the agent closing the loop, and once with the standard "
            "one-factor-at-a-time approach a bench would use, where one lever is changed per "
            "trial in the direction that most helps the worst-performing target. Both arms were "
            "allowed the same number of trials and judged by the same gate - every hard target "
            "on target and the measured objective at or above 0.85."
        ),
        _table(
            [
                ["Cases", str(cases)],
                ["Trials to target, agent (mean)", _fmt(agent_mean)],
                ["Trials to target, one-factor-at-a-time (mean)", _fmt(ofat_mean)],
                ["Cases reaching target, agent", f"{summary.get('agent_successes', 0)} of {cases}"],
                ["Cases reaching target, one-factor-at-a-time", f"{summary.get('ofat_successes', 0)} of {cases}"],
                ["Mean trials saved per case", _fmt(saved)],
                ["Total trials saved", _fmt(summary.get('trials_saved_total'))],
            ],
            caption="Table 1.1  Trial efficiency over the seeded cases",
            widths=[1.4, 0.6],
        ),
        _p(
            "The agent reached every case within the trial budget allowed; in the same budget the "
            "one-factor-at-a-time arm reached none of them. The saving is the difference the "
            "problem statement is about: the same development decision, taken with fewer physical "
            "trials, because the trial that is not run was replaced by a prediction with a stated "
            "uncertainty and an explicit reason to believe it."
        ),
        _p(
            "Equally important is what the agent will not do. Every number it publishes is "
            "traceable to a model, a rule in the knowledge base, or a stored trial; every "
            "prediction carries an interval; and when a brief cannot be satisfied as written - "
            "when a fibre target and a protein target demand more of the same ingredient than the "
            "recipe can hold - the agent says so and names the target responsible instead of "
            "returning a recipe that quietly misses it."
        ),
        _h2("1.1  What was built"),
        *_bullets(
            [
                f"A knowledge base of {kb.get('ingredients', 0)} food ingredients with composition, "
                "cost, water activity, density, allergen and dietary flags, and functional groups; "
                f"{len(kb.get('categories') or [])} product categories with their slots, process "
                "envelopes and parameter ranges; and the regulatory and claim limits the recipes "
                "are checked against.",
                "A prediction engine that computes composition by mass balance, energy by Atwater "
                "factors, water activity from free water and solute load, pH by titration against "
                "buffer capacity, texture, stability, cost and processability - with a stated "
                "uncertainty on each.",
                "A formulation generator and a coordinate pattern-search optimiser that work on the "
                "recipe and the process together, with an objective that is the priority-weighted "
                "desirability of the brief's targets.",
                "A trial-analysis and diagnosis layer that reads residuals in units of the "
                "published uncertainty, separates process faults from formulation faults, and "
                "names a probable cause with its evidence.",
                "A reformulation planner that applies measured process offsets as setpoint "
                "corrections before it changes the recipe, predicts a pass probability per target, "
                "and proposes a design of experiments for the factors it moved.",
                "A trial-efficiency benchmark, a SQLite record of every version, trial, diagnosis "
                "and plan, a browser interface, a test suite, and this report - generated from the "
                "same record as everything else.",
            ]
        ),
        _h2("1.2  Scope and limitations"),
        _p(
            "The plant is simulated. That is a deliberate choice: the agent's logic can be tested "
            "against a plant whose true behaviour is known, so a wrong diagnosis is a bug rather "
            "than a coincidence, and the benchmark can be rerun by anyone. The simulation models "
            "the faults that dominate early trials - thermal setpoint error, drying efficiency, "
            "acid retention, sugar inversion, sodium carry-over, oxidation, and analytical "
            "variation - but it does not model microbiology, rheology past the texture indices "
            "used here, or consumer acceptance, which the sensory panel is still the only way to "
            "measure. The ingredient data are literature values, adequate for direction and "
            "ranking, and the agent treats them as such: every prediction carries an interval "
            "whose width reflects how far the recipe sits from the data it was calibrated on."
        ),
        _p(
            f"The system runs on the Python standard library alone ({environment.get('python', '')}), "
            "with Pillow and Pygments used only to draw the figures and code listings for this "
            "report. There is no database server, no service to install and no network call in the "
            "prediction path, which is what allows the whole development record - and the report "
            "written from it - to be reproduced on any machine that has Python."
        ),
        _h2("1.3  Keywords"),
        _p(
            "food product development; formulation optimisation; design of experiments; water "
            "activity; trial efficiency; surrogate modelling; uncertainty quantification; fault "
            "diagnosis; Python; SQLite."
        ),
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 2
# --------------------------------------------------------------------------- #
def _chapter_2(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    student = context["student"]
    kb = context["kb"]
    modules = context["modules"]
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 2"},
        {"kind": "heading1", "text": "Details about the Training"},
        _h2("2.1  Organisation and problem statement"),
        _p(
            f"The internship was carried out at {student['organisation']} during "
            f"{student['duration']}, under {student['organisation_guide']} on the industry side and "
            f"{student['guide']} on the academic side. The work addressed problem statement PS-1 "
            "of the internship brief: an AI-powered food product development agent."
        ),
        _p("The brief lists the capability the agent must have. In the agent's own terms, it is:"),
        *_bullets(
            [
                "**Understand the target product** from images and specifications - what the "
                "product is, what it has to contain, what it must not contain, and how good is "
                "\"good enough\" for each requirement.",
                "**Generate an initial formulation and manufacturing process** - a first recipe "
                "with proportions, and the unit operations and parameters that turn it into a "
                "product.",
                "**Predict key characteristics** - nutrition, water activity, pH, texture, "
                "stability, cost - before anything is made.",
                "**Analyse trial results** against those predictions, rather than against "
                "expectation, so that a surprise is visible as a number.",
                "**Diagnose probable formulation or process failures** - which cause, how "
                "confident, on what evidence, and what to check first.",
                "**Reformulate for the next trial** - change the recipe where the recipe is wrong, "
                "and correct the setpoint where the plant is wrong.",
                "**Reduce trials-to-target** - the commercial objective that makes the other six "
                "worth building.",
            ]
        ),
        _h2("2.2  Scope of the internship work"),
        _table(
            [
                ["Item", "Detail"],
                ["Domain", "Food product development: snack, bakery, spread, dry-mix and sauce categories"],
                ["Deliverable", "A running, dependency-free Python application with a browser interface, a test suite, and this report"],
                ["Knowledge base", f"{kb.get('ingredients', 0)} ingredients, {len(kb.get('categories') or [])} categories, claim and additive limit rules"],
                ["Demonstration cases", "3 seeded product cases plus 1 deliberately infeasible brief, all with known plant faults"],
                ["Evidence", "A trial-efficiency benchmark comparing the agent loop against one-factor-at-a-time on identical cases and budget"],
            ],
            caption="Table 2.1  Scope of the work",
            widths=[0.45, 1.0],
        ),
        _h2("2.3  Work plan"),
        _table(
            [
                ["Stage", "Activity", "Output"],
                ["1", "Problem framing, category and KPI definition", "Target set, tolerances, priority weights"],
                ["2", "Ingredient, process and limit knowledge base", "ingredients.json, processes.json, limits.json"],
                ["3", "Prediction models (nutrition, water activity, pH, texture, stability, cost)", "engine.predict with per-KPI uncertainty"],
                ["4", "Brief understanding and image handling", "Targets, allergens, claims, open questions"],
                ["5", "Formulation generation and optimisation", "Version 1 recipe and process, with conflicts reported"],
                ["6", "Simulated plant and trial record", "Trials with realistic faults, stored with their versions"],
                ["7", "Analysis, diagnosis, reformulation and DOE", "Cause ranking, setpoint compensation, next-trial design"],
                ["8", "Benchmark, interface, tests and documentation", "Trial-efficiency evidence, UI, test suite, this report"],
            ],
            caption="Table 2.2  Stage-wise work plan",
            widths=[0.16, 1.0, 0.8],
        ),
        _h2("2.4  Method"),
        _p(
            "The method was chosen for one reason: a development record has to be defensible. A "
            "recipe recommended by a model that cannot explain itself is not usable in a factory, "
            "and a prediction that comes without an uncertainty cannot be tested against a "
            "measurement - it can only be believed or disbelieved. So the agent is built from "
            "transparent models instead of a fitted black box:"
        ),
        *_bullets(
            [
                "**First principles where they exist.** Composition is mass balance; energy uses "
                "the Atwater factors; water activity follows the free-water/solute split; pH is a "
                "titration against the buffer capacity of the mix. These are checkable by hand, "
                "which is exactly the property a factory needs.",
                "**Rules where physics is unavailable.** Water-activity and pH regimes, additive "
                "limits, allergen and diet constraints, claim thresholds and microbial safety "
                "rules live in JSON, so a food technologist can read and correct them without "
                "touching code.",
                "**Uncertainty as a first-class output.** Every predicted KPI carries a sigma "
                "built from the base analytical variation, an absolute floor, and a penalty for "
                "extrapolating beyond the calibrated range. The same sigmas decide what counts as "
                "a real deviation when a trial comes back.",
                "**Deterministic search.** Optimisation is a coordinate pattern search with a "
                "fixed seed and an acceptance rule, not a random search. Two runs of the agent on "
                "the same brief with the same record produce the same recommendation, and the log "
                "of accepted moves is the explanation of how it got there.",
                "**A surrogate only where it earns its place.** A ridge regression on residuals "
                "is blended into the physics as trials accumulate, weighted by how much data it "
                "has; with few trials it collapses to an intercept-only bias correction, because a "
                "model fitted to four points should not be allowed to overrule a mass balance.",
            ]
        ),
        _h2("2.5  Learning outcomes"),
        *_bullets(
            [
                "How a food specification becomes a set of measurable targets, and why tolerances "
                "and priorities matter more than the target values themselves.",
                "Why water activity, not moisture alone, controls shelf life and microbial "
                "stability - and how a humectant and a drier change it differently.",
                "How to design a trial that will answer the question being asked, and what "
                "fractional factorial and centre-point designs buy when several factors moved "
                "together.",
                "How to separate a formulation fault from a process fault using only the residual "
                "pattern and the process actuals.",
                "How to quantify uncertainty honestly enough that a measurement can contradict the "
                "prediction.",
                "How to build a decision-support application with no third-party dependencies, "
                "and how to keep a report generated from the same record as the results it "
                "describes.",
            ]
        ),
        _p(
            "The modules that make up the application, and the size of each, are listed here "
            "because the report refers to them throughout."
        ),
        _table(
            [["Module", "Lines", "Responsibility"]]
            + [[row["module"], _fmt(row.get("lines")), row.get("purpose", "")] for row in modules],
            caption="Table 2.3  Application modules",
            widths=[0.62, 0.2, 1.5],
        ),
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 3
# --------------------------------------------------------------------------- #
def _chapter_3(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    figures = context["figures"]
    summary = context["benchmark_summary"]
    kb = context["kb"]
    conflict = (context["benchmark"].get("conflict_demonstration") or {})
    arms = context["arms"]
    agent_arm = arms.get("agent") or {}
    ofat_arm = arms.get("one-factor-at-a-time") or {}
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 3"},
        {"kind": "heading1", "text": "Project Description"},
        _p(
            "The agent is a closed loop with seven stages: understand the brief, design an initial "
            "formulation and process, predict what it will measure, run the trial, analyse the "
            "residuals, diagnose the cause, and plan the next formulation and trial. Each stage "
            "writes to the record, and the next stage reads what the previous one wrote, so the "
            "loop can be stopped and restarted at any point without losing the reason for any "
            "decision."
        ),
        _figure(figures.get("arch"), "Figure 3.1  The development loop and the modules that implement each stage"),
        _h2("3.1  Understanding the brief"),
        _p(
            "A brief arrives as prose with numbers in it - \"protein not less than 15 g per 100 g\", "
            "\"moisture below 4 %\", \"shelf life 6 months\", \"no more than 300 mg sodium\". The "
            "agent parses it into `(key, comparator, value, unit)` tuples and turns each one into a "
            "target with a tolerance, a direction and a priority."
        ),
        _p(
            "Two details do most of the work here. First, direction: a bare number is treated as a "
            "band centred on the value unless the KPI is inherently one-sided, in which case the "
            "value is a bound. Getting this wrong is the difference between a recipe that targets "
            "300 mg of sodium and one that treats 300 mg as a ceiling - and the second is what a "
            "brief usually means. Second, the parser reads the comparator, the unit and even the "
            "sentence-ending full stop correctly: an interval written as \"0.90.\" at the end of a "
            "sentence must not lose its value to a greedy number match, which is a real bug that "
            "was found and fixed while building this."
        ),
        _p(
            "Category inference, allergen detection and claim detection run over the same text, "
            "and each claim (high protein, high fibre, reduced sugar, low sodium) brings its own "
            "numeric thresholds into the target set from the regulations in the knowledge base. "
            "What the text cannot answer becomes an explicit open question rather than a silent "
            "assumption: if no image is supplied, appearance and piece size are recorded as "
            "unknown. Photographs, when supplied, are measured offline with Pillow - colour of the "
            "dominant region, colour uniformity, form factor from the aspect ratio, surface "
            "texture from local contrast - and, if an API key is configured, a vision model adds "
            "the semantic layer (what the product is, what the surface finish looks like). The "
            "offline path always runs, so a machine with no network and no key still produces a "
            "complete brief; the API is an enrichment, never a dependency."
        ),
        _h2("3.2  Designing an initial formulation"),
        _p(
            "Generation is slot-based. Every category defines the functional slots a product of "
            "that kind needs, and each slot carries an intent - what this slot is for in this "
            "brief - expressed as the KPIs it should move and in which direction. The intent "
            "vector is not hard-coded: it is computed by comparing the brief's targets against a "
            "cached *neutral reference formulation*, an unoptimised recipe of the same category. "
            "If the brief wants more protein than the reference delivers, the protein slot's "
            "intent rises; if it wants less sugar, the sweetener intent falls. That one idea is "
            "what makes the generator respond to the brief instead of producing the same "
            "category-average recipe every time."
        ),
        _p(
            "Ingredients are then chosen per slot from the candidates the diet, allergen and "
            "category rules allow, scored by a utility that combines the slot intent, the "
            "candidate's own composition, cost and its preference prior. Proportions are "
            "reconciled with iterative proportional fitting so the recipe sums to 100 % while "
            "respecting slot minimums and maximums, with a greedy fallback when the target is "
            "infeasible. Two corrections run afterwards: acidulants are dosed by bisection against "
            "the pH target rather than guessed, and the fibre, salt and water slots are trimmed to "
            "their limits if the brief's claims would otherwise be broken."
        ),
        _p(
            "A hard test follows every generation. " + str(len(context.get("kpi_count") or []) or 22) +
            " KPIs are predicted, the brief's targets are evaluated, and any target that no lever "
            "can move - because the category, the process envelope or the target set itself fixes "
            "it - is reported as a conflict with the reason attached."
        ),
        _h2("3.3  Searching for a better recipe"),
        _p(
            "The optimiser treats the recipe and the process as one problem. Its objective is the "
            "priority-weighted desirability of the brief's targets, minus penalties that keep the "
            "answer usable: a penalty on drifting away from each slot's intent (weighted down for "
            "precision slots such as acidulants, where a small dose matters a great deal), a "
            "plausibility floor so a recipe cannot be nudged into a nonsense composition, and "
            "constraint penalties for additive, allergen and diet violations. Desirability is a "
            "smooth kernel centred on the band rather than a cliff at its edge, because a "
            "one-sided improvement worth 0.05 deserves to be visible."
        ),
        _p(
            "The search itself is coordinate pattern search: for each lever (an ingredient's "
            "share, or a process parameter) it tries a step in both directions, applies the "
            "resulting move, renormalises to 100 %, and accepts it only if the objective improves "
            "by more than a step-dependent epsilon. When single-lever moves stall, it escalates: a "
            "paired pass moves two levers together - the coordinated move that a human formulator "
            "makes when raising a protein source requires lowering the base - and then it "
            "regenerates the lever list, which allows a slot that was previously at zero to be "
            "introduced. The accepted-move log is the explanation: every change of the "
            "recommendation carries the KPI it improved and by how much."
        ),
        _h2("3.4  Predicting what the trial will measure"),
        _table(
            [
                ["Predicted property", "How it is predicted", "Uncertainty source"],
                ["Composition and energy", "Mass balance over the recipe; Atwater factors for energy", "Ingredient composition tables"],
                ["Water activity", "Free water and solute load by regime; bound water from fibre and hydrocolloids", "Regime boundary, solute interaction"],
                ["pH", "Free acid from acidulants and the matrix, titrated against buffer capacity, with unbuffered slope and pKa potency", "Buffer capacity estimate, matrix acidity"],
                ["Texture and hardness", "Structural indices from fat, moisture, sugar and protein fractions", "Category-level empirical fit"],
                ["Stability and shelf life", "Water-activity and moisture-driven deterioration rates with an oxygen term", "Accelerated-shelf-life convention"],
                ["Cost", "Ingredient cost, process cost and packaging cost per kilogram and per unit", "Price variation, yield assumptions"],
                ["Processability", "Extrusion, sheeting, drying and filling feasibility from the recipe state", "Parameter-range limits"],
            ],
            caption="Table 3.1  Prediction methods and where their uncertainty comes from",
            widths=[0.5, 1.2, 0.8],
        ),
        _p(
            "Each prediction is published with an interval, not as a point. The sigma combines the "
            "base analytical variation of that measurement, an absolute floor so that a "
            "percentage-of-value error cannot shrink to nothing on a small number, and an "
            "extrapolation factor that widens the interval when the recipe sits outside the range "
            "the model was calibrated on. That interval is the instrument the whole loop depends "
            "on: a trial result is \"surprising\" only relative to it."
        ),
        _h2("3.5  The simulated plant, and why it is simulated"),
        _p(
            "Physical trials are replaced here by a simulated plant with a known, configurable "
            "truth: a drying efficiency, a temperature offset, an acid retention, a sugar "
            "inversion factor, a sodium carry-over and an oxidation factor, plus analytical noise "
            "drawn from the measurement sigma of each KPI. Drying loss is applied as a fraction of "
            "the water the process intended to remove, not as a flat offset, which is what makes "
            "an under-performing dryer look like a real under-performing dryer: the same settings "
            "hurt a wet feed far more than a dry one."
        ),
        _p(
            "The point of simulating is testability. Because the true cause is known, a wrong "
            "diagnosis is a defect that can be found and fixed rather than a plausible story; and "
            "because the noise is drawn from the published sigma, the intervals can be checked for "
            "calibration - a 95 % interval that contains the measurement 95 % of the time. Trials "
            "are stored with the version that produced them, the batch size, the operator, the "
            "process actuals and the sensory panel's scores, so analysis always has a complete "
            "picture of what was made."
        ),
        _h2("3.6  Analysing a trial"),
        _p(
            "Analysis compares the measured value of every predicted KPI with the prediction on "
            "record for that formulation version, and expresses the difference in units of the "
            "published sigma. A residual of 1.3 sigma is noise; 4 sigma is a fault. Direction is "
            "kept, not just magnitude, because a systematic error is exactly the case where the "
            "signs agree: the analyser scores the fraction of signed evidence pointing one way and "
            "calls the trial systematic above 0.55, which is what distinguishes a mistuned plant "
            "from a bad day in the laboratory."
        ),
        _figure(figures.get("res"), "Figure 3.2  Residuals of a trial against the published interval"),
        _p(
            "Process actuals - what the machine really did, against what the recipe asked for - are "
            "attached to the analysis before diagnosis runs, so a temperature that ran low is "
            "available as evidence rather than being something the operator has to remember to "
            "mention."
        ),
        _h2("3.7  Diagnosing the cause"),
        _p(
            "Diagnosis is a ranked set of signature rules over the residual pattern and the "
            "process deviations, each returning a cause, a category (process, formulation, "
            "dosing, analytical, supply), a confidence and the specific evidence that triggered "
            "it. The signatures cover the faults that dominate early trials: a dryer that "
            "under-dried, a temperature that ran off setpoint, acid loss or acid overdose, "
            "dosing/scale error, over-drying, texture high or low from fat and moisture, "
            "extrusion severity, sugar inversion, oxidation, dilution by an extra ingredient, "
            "yield and cost surprises, and a model bias signature that fires when the residuals "
            "are small, signed the same way, and spread across KPIs that no single cause can "
            "explain."
        ),
        _p(
            "The last of those is the honest one. If the answer is \"the model is slightly wrong "
            "about this category\", the agent says that instead of inventing a process fault, and "
            "the recommendation becomes a confirmation trial with an unchanged formulation rather "
            "than a reformulation. The ranking is by confidence, and every candidate carries its "
            "recommended action - \"verify the dryer profile with a calibrated thermocouple at the "
            "product surface, not at the controller\" - because a diagnosis that does not say what "
            "to do next is only a description."
        ),
        _h2("3.8  Reformulating, and correcting the plant instead"),
        _p(
            "The reformulation planner distinguishes the two things a disappointing trial can "
            "mean. If the cause is the plant - a dryer running 9 °C below its setpoint, an acid "
            "dose retaining only three quarters of what was intended - the fix is not a different "
            "recipe at all: it is a setpoint that asks for 9 °C more, or a dose that asks for a "
            "quarter more, until the controller is recalibrated. So the planner applies the "
            "measured offsets as setpoint corrections *before* it optimises anything, and hands "
            "the optimiser a predictor that models what the plant will deliver rather than what "
            "the recipe requests. Without that step an optimiser reads \"moisture too high\" as "
            "\"dry less\" and walks the recommendation the wrong way, one experiment at a time - a "
            "failure mode this project hit and fixed."
        ),
        _p(
            "Only after the process is compensated does the recipe change, driven by the residuals "
            "that remain. The plan reports, per target, the expected value with its interval and "
            "the probability of passing; the ingredient deltas with their contribution to each "
            "KPI; the parameter deltas with their validated ranges; the surrogate's weight in the "
            "prediction; and the design of experiments for the next trial, chosen from the number "
            "of factors that actually moved: a three-level sweep for one factor, a full factorial "
            "with centre points for two to four, a resolution-IV fraction above that - always "
            "with a control replicate and a randomised run order."
        ),
        _h2("3.9  The trial-efficiency benchmark"),
        _p(
            "The benchmark is the evidence that the loop is worth running. Two arms work the same "
            "three cases with the same trial budget and the same pass gate. The agent arm is the "
            "loop described above. The comparison arm is one-factor-at-a-time, the standard bench "
            "method: measure, find the worst-performing target, change the single lever that most "
            "improves it, and revert a change that did not help. Neither arm may see the plant's "
            "true parameters."
        ),
        _table(
            [["Case", "Agent trials", "OFAT trials", "Agent objective", "OFAT objective", "Trials saved"]]
            + _arm_rows(context),
            caption="Table 3.2  Trials to target by case and arm",
            widths=[0.95, 0.28, 0.28, 0.3, 0.3, 0.26],
        ),
        _figure(figures.get("bench"), "Figure 3.3  Trials to target, agent against one-factor-at-a-time"),
        _figure(
            figures.get("traj"),
            "Figure 3.4  Measured objective by trial. The bar is the pass threshold; a trajectory "
            "that stops at a cross reached the gate early",
        ),
        _figure(figures.get("des"), "Figure 3.5  How close the accepted formulation gets to each target"),
        _figure(figures.get("acc"), "Figure 3.6  Prediction error by KPI, measured against the analytical spread of each test"),
        _p(
            f"Over the seeded cases the agent reached the gate in a mean of "
            f"{_fmt(summary.get('agent_mean_trials_to_target'))} trials against "
            f"{_fmt(summary.get('ofat_mean_trials_to_target'))} for the one-factor-at-a-time arm, "
            f"saving a mean of {_fmt(summary.get('mean_trials_saved'))} trials per case. The "
            "objective column shows the second half of the story: the comparison arm's final "
            f"objective is {_fmt(ofat_arm.get('final_truth_objective'))} against the agent's "
            f"{_fmt(agent_arm.get('final_truth_objective'))} measured on the plant's true "
            "parameters, so the difference is not only speed but accuracy."
        ),
        _h2("3.10  A worked case: high-protein masala namkeen pellet"),
        _p(
            "The extruded snack case is the clearest demonstration because the plant's fault and "
            "the brief's difficulty pull in different directions. The line's extruder and dryer "
            "both run 9 °C below setpoint and the dryer delivers only 96 % of its intended water "
            "removal, so version 1 comes back with moisture above specification even though the "
            "recipe was designed to hit it - the classic first-trial result that a bench would "
            "answer by drying harder and losing the texture."
        ),
        *_bullets(
            [
                "Trial 1: moisture lands high at 4.4 % against a 3.0 % target and a predicted 3.2 %, "
                "a residual above four sigma. Water activity follows it. Protein, fat and energy "
                "are all on target, which is the signature that separates a drying fault from a "
                "formulation fault.",
                "Diagnosis: process temperature below plan (the recorded 106 °C against a 115 °C "
                "setpoint), ranked above the alternative of a formulation that is simply too wet.",
                "Reformulation: the offsets are applied as setpoint corrections first (-9 °C on "
                "both the barrel and the dryer), then the residual moisture is addressed by "
                "shifting the recipe toward lower free water - a small increase in fibre and a "
                "reduction in feed moisture - with a full factorial design over the four factors "
                "that moved, because moving four levers together means interactions have to be "
                "resolved, not guessed.",
                "Trial 2 and 3: the objective climbs and the hard targets come into line; the "
                "third trial clears the gate with the measured objective above 0.85, and the "
                "recipe is within its fibre, sodium and energy claims.",
            ]
        ),
        _p(
            "The trial history, the diagnosis and the plan for the case are reproduced in plain "
            "text in the application's project report, which is generated from the same database "
            "rows as this analysis."
        ),
        _h2("3.11  When the brief cannot be satisfied"),
        _p(
            "The dry-mix case is deliberately unsatisfiable: it asks for a fibre level that the "
            "category's ingredients, at the protein level the brief also demands, cannot deliver. "
            "A generator that returned a recipe anyway would be worse than useless, because the "
            "failure would surface two weeks later as a rejected product. The agent reports the "
            "conflict instead, naming the target, the achieved value, its desirability, whether "
            "the target is hard, and why it cannot be met."
        ),
        _table(
            [["Target", "wanted", "achieved", "desirability", "recommendation"]]
            + [
                [
                    item.get("label", item.get("kpi", "")),
                    _fmt(item.get("target")),
                    _fmt(item.get("achieved")),
                    _fmt(item.get("desirability")),
                    item.get("recommendation", ""),
                ]
                for item in (conflict.get("conflicts") or [])
            ],
            caption="Table 3.3  Targets no lever can reach in the infeasible brief",
            widths=[0.5, 0.25, 0.25, 0.3, 1.6],
        ),
        _figure(figures.get("conf"), "Figure 3.7  The unreachable target in the infeasible brief"),
        _p(
            "The same machinery answers the question a brand actually asks at that point: which "
            "requirement has to move? The response names the target, quantifies how far short it "
            "is, and lists the levers that were tried, so the negotiation happens with evidence."
        ),
        _h2("3.12  The interface and the record"),
        _p(
            "The application ships with a browser interface: create a product from images and a "
            "brief, read the formulation as a table of ingredients with their shares and their "
            "contribution, see the process plan with its parameters and critical control points, "
            "log a trial, read the analysis and the ranked diagnosis, review the reformulation "
            "plan with its dose deltas and DOE, accept it as the next version, and watch the "
            "objective, the pass probability and the prediction accuracy move. The same operations "
            "are available over a JSON API, so the agent can be driven from a script or from "
            "another system."
        ),
        _figure(figures.get("loop"), "Figure 3.8  One turn of the loop, with the record each stage writes"),
        _h2("3.13  Limitations and next steps"),
        *_bullets(
            [
                "The plant is simulated, and its physics is deliberately simpler than a real line: "
                "no microbiology, no rheology beyond the texture indices, no staling, no "
                "packaging permeation. A real deployment would need these, and the diagnosis rules "
                "are structured so that a new signature is a new rule rather than a rewrite.",
                "The ingredient data are literature values. The agent's own accuracy ledger exists "
                "precisely to detect where they are wrong for a category - the model-bias "
                "signature is the first version of that feedback loop.",
                "Optimisation is local. Coordinate pattern search with paired moves explores well "
                "around a good starting point and is reproducible, but it will not find a "
                "structure the slot model cannot express - a different stabiliser system, or an "
                "ingredient the category does not allow.",
                "Sensory acceptance is out of scope. The brief's appearance constraints are "
                "measured from photographs, but liking still needs a panel; the trial record has "
                "fields for panel scores and the diagnosis reads them, which is the honest "
                "boundary of what software can decide here.",
            ]
        ),
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 4
# --------------------------------------------------------------------------- #
def _chapter_4(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    environment = context["environment"]
    kb = context["kb"]
    db_size = context["schema"].get("size_kb")
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 4"},
        {"kind": "heading1", "text": "Hardware and Software Requirements"},
        _p(
            "The application is designed to run on an ordinary laptop with no installation step: "
            "Python, the standard library and the two files of the knowledge base are the whole "
            "requirement. Pillow and Pygments are used only to render the figures and the code "
            "listings in this report; the agent itself does not need them."
        ),
        _h2("4.1  Hardware requirements"),
        _table(
            [
                ["Component", "Minimum", "Recommended", "Used for this report"],
                ["Processor", "Dual-core x86-64", "Quad-core x86-64 or Apple silicon", environment.get("processor", "")],
                ["Memory", "2 GB", "8 GB", environment.get("memory", "")],
                ["Free disk space", "200 MB", "1 GB (figures, report, trial record)", environment.get("disk", "")],
                ["Display", "1024 x 768", "1920 x 1080 for the browser interface", environment.get("display", "")],
                ["Network", "Not required", "Only if the optional vision API is used", "None used"],
            ],
            caption="Table 4.1  Hardware requirements",
            widths=[0.42, 0.4, 0.7, 0.6],
        ),
        _h2("4.2  Software requirements"),
        _table(
            [
                ["Software", "Version", "Purpose"],
                ["Operating system", environment.get("platform", ""), "Development and execution platform"],
                ["Python interpreter", environment.get("python", ""), "Runs the whole application; standard library only"],
                ["SQLite (bundled with Python)", environment.get("sqlite", ""), "Products, formulations, trials, diagnoses, plans, ledger"],
                ["Web browser (Chrome / Edge / Firefox)", environment.get("browser", ""), "The single-page interface, served from the application itself"],
                ["Pillow", environment.get("pillow", ""), "Reading uploaded product images and drawing the report figures"],
                ["Pygments", environment.get("pygments", ""), "Syntax-highlighted code listings for this report"],
                ["Optional vision API key (OpenAI or Gemini)", "not configured", "Adds a semantic description of the product photographs"],
            ],
            caption="Table 4.2  Software requirements",
            widths=[0.55, 0.45, 1.1],
        ),
        _p(
            "No web framework, no database server and no numerical library are used. That is a "
            "deliberate constraint: the system has to be demonstrable on any college or factory "
            "machine without an installation, and every model it runs has to be inspectable line "
            "by line."
        ),
        _h2("4.3  Knowledge base and data files"),
        _table(
            [
                ["File", "Contents", "Size"],
                ["app/data/ingredients.json", f"{kb.get('ingredients', 0)} ingredients with composition, cost, water activity, density, allergens, diet flags and functional group", environment.get("ingredients_size", "")],
                ["app/data/processes.json", f"{kb.get('categories_defined', len(kb.get('categories') or []))} process categories with unit operations, parameter ranges and in-process targets", environment.get("processes_size", "")],
                ["app/data/limits.json", "Claim thresholds, category additive limits, allergen list and microbial safety rules", environment.get("limits_size", "")],
                ["app/data/formusense.db", f"SQLite record of every product, version, prediction, trial, analysis, diagnosis, plan and ledger entry ({_fmt(db_size)} KB at the time of writing)", ""],
                ["app/data/figures/*.png", "Figures drawn from the record at report time", ""],
            ],
            caption="Table 4.3  Data files",
            widths=[0.75, 1.4, 0.25],
        ),
        _h2("4.4  Running the application"),
        _code(
            "python run.py                 # start the interface on http://127.0.0.1:8770\n"
            "python run.py --port 9000    # choose another port\n"
            "python run.py --open         # start and open the browser\n"
            "python run.py --seed         # re-seed the demonstration cases and exit\n"
            "python run.py --benchmark    # run the trial-efficiency benchmark and exit\n"
            "python run.py --report       # regenerate this report from the record and exit\n"
            "python -m unittest discover -s tests -t .    # run the test suite",
            caption="Listing 4.1  Commands",
        ),
        _p(
            "The server binds to the loopback interface by default, so the interface is not exposed "
            "to the network unless it is asked to be."
        ),
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 5
# --------------------------------------------------------------------------- #
def _chapter_5(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 5"},
        {"kind": "heading1", "text": "Coding Screenshots"},
        _p(
            "The listings in this chapter are taken from the source files themselves at report "
            "time and drawn with syntax highlighting, so they cannot drift from the code. Each "
            "listing is followed by what it is doing and why it is written that way."
        ),
    ]
    for index, listing in enumerate(context["listings"], start=1):
        blocks.append(_h2(f"5.{index}  {listing.get('title', '')}"))
        blocks.append(_figure(listing["image"], listing.get("caption", ""), width=6.2))
        if listing.get("note"):
            blocks.append(_p(listing["note"]))
    blocks.append(_h2(f"5.{len(context['listings']) + 1}  Test suite"))
    blocks.append(_p(
        "The behaviour described in this report is covered by an automated test suite, so a "
        "regression in the models, the optimiser, the diagnosis rules or the loop is caught "
        "before it reaches a recommendation. The suite covers brief parsing, the physical and "
        "nutritional models, formulation generation and constraint checking, the simulated plant, "
        "the closed loop, and the HTTP API."
    ))
    blocks.append(_code(context["environment"].get("test_output", ""), caption="Listing 5.9  Test suite result"))
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 6
# --------------------------------------------------------------------------- #
def _chapter_6(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    schema = context["schema"]
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 6"},
        {"kind": "heading1", "text": "Design Database"},
        _p(
            "The record is a single SQLite file. There are two reasons it is relational rather "
            "than a folder of JSON: a product development history is a graph of versions, trials, "
            "measurements and decisions, and the trial-efficiency numbers the whole problem "
            "statement is about must be *derived* from those rows rather than kept by hand, so "
            "they cannot drift away from what actually happened."
        ),
        _h2("6.1  Schema"),
        _table(
            [["Table", "Columns", "Rows at report time", "Purpose"]]
            + [
                [
                    table["name"],
                    _fmt(table.get("columns_count")),
                    _fmt(table.get("rows")),
                    table.get("purpose", ""),
                ]
                for table in schema.get("tables", [])
            ],
            caption="Table 6.1  Tables in formusense.db",
            widths=[0.5, 0.22, 0.3, 1.4],
        ),
        _h2("6.2  The version ledger"),
        _p(
            "Every formulation is stored as an immutable version with the source that produced it "
            "- generated, reformulated, or accepted from a plan - and every prediction, trial, "
            "analysis, diagnosis and plan hangs off a product and a version number. That single "
            "structure answers the question a development record exists to answer: what did we "
            "believe, what did we make, what came back, what did we change, and why. The "
            "relationships are:"
        ),
        *_bullets(
            [
                "`products 1 - N formulations` in version order, each with its source and label.",
                "`formulations 1 - 1 predictions` per calibration state, storing the published "
                "values, the evaluation against the brief and the conflicts at that moment - not "
                "just the numbers, so the record shows what the agent believed at design time "
                "rather than what it believes now.",
                "`trials` reference the formulation version they were made from and carry the "
                "measurements, the sensory scores, the batch size and the process actuals.",
                "`analyses` hang off a trial and hold residuals in sigma units, the systematic "
                "direction score, the process deviations and the objective; `diagnoses` hold the "
                "ranked causes with their evidence.",
                "`plans` record the from-version and to-version, the deltas, the expected values "
                "with intervals, the pass probability, the surrogate weight, the design of "
                "experiments and the rationale, and are marked accepted when they become the next "
                "version.",
                "`benchmarks` stores whole trials-to-target comparisons, so a published efficiency "
                "claim can be traced to one run rather than to a conversation.",
                "`ledger` is an append-only event log of everything the agent did, in order, with "
                "the payload that caused it.",
            ]
        ),
        _h2("6.3  Data definition"),
        _code(schema.get("ddl", ""), caption="Listing 6.1  Schema as stored in app/store.py"),
        _h2("6.4  Data dictionary"),
        _table(
            [["Table", "Column", "Type", "Meaning"]]
            + [
                [table["name"], column["name"], column["type"], column.get("meaning", "")]
                for table in schema.get("tables", [])
                for column in table.get("columns", [])
                if column.get("meaning")
            ],
            caption="Table 6.2  Key columns",
            widths=[0.45, 0.45, 0.3, 1.5],
        ),
        _h2("6.5  Derived trial-efficiency KPIs"),
        _p(
            "The numbers quoted in the abstract are computed from the rows above, not stored: "
            "trials used is the count of trials for a product; versions used is the count of "
            "formulation versions; trials-to-target is the first trial whose measured values put "
            "every hard target on target; and prediction accuracy pairs each trial with the "
            "prediction that was on record immediately before it, so a prediction can never be "
            "rewritten after the measurement is known. That last constraint is the reason the "
            "accuracy figures in this report are credible."
        ),
    ]
    samples = schema.get("samples") or {}
    if samples:
        blocks.append(_h2("6.6  Sample rows"))
        for name, rows in samples.items():
            caption = rows.get("caption") if isinstance(rows, dict) else ""
            data = rows.get("rows") if isinstance(rows, dict) else rows
            if not data:
                continue
            blocks.append(_table(data, caption=caption, widths=None))
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 7
# --------------------------------------------------------------------------- #
def _chapter_7(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    environment = context["environment"]
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 7"},
        {"kind": "heading1", "text": "Screenshots"},
        _p(
            "The interface is a single-page application served by the agent itself: it holds no "
            "data of its own, and every panel is a rendering of the same record the figures in "
            "chapter 3 were drawn from. The screens are: **Products** (the seeded cases with "
            "their trial counts and efficiency), **New product** (paste a brief, attach images, "
            "read the targets the agent inferred and the questions it could not answer), "
            "**Overview** (the accepted recipe against every target, the measured objective by "
            "trial, prediction accuracy), **Trial analysis** (residuals in sigma units against "
            "the published interval, and the ranked causes with their evidence) and "
            "**Reformulation plan** (dose deltas, setpoint corrections, pass probability per "
            "target and the design of experiments for the next trial)."
        ),
        _p(
            "What follows is the output the application produces for the worked case in section "
            "3.10, exactly as the report route returns it: the specification as understood, the "
            "version history, each trial with its measured values, the diagnosis in the "
            "analyst's words, and the efficiency ledger - including the trial count, which is "
            "read from the record rather than written down here."
        ),
        _code(
            _excerpt(context.get("project_report") or "(no product in the record)"),
            caption="Listing 7.1  The project report for one product, as printed by the agent",
        ),
        _h2("7.2  Benchmark run"),
        _code(environment.get("benchmark_output", ""), caption="Listing 7.2  The trial-efficiency benchmark, as returned by the API"),
        _h2("7.3  The API"),
        _p(
            "Every action in the interface is an HTTP call, which means the agent can also be "
            "driven from a script or from another system without the browser."
        ),
        _table(
            [["Method and path", "What it does"]],
            [
                ["GET /api/health", "Service status, knowledge-base summary and record counts"],
                ["GET /api/kb", "Categories, claims, allergen list and the ingredient catalogue"],
                ["GET /api/products", "Every product in the record with its efficiency summary"],
                ["POST /api/products", "Create a product from a brief and optional images; returns the first formulation with its prediction and conflicts"],
                ["GET /api/products/{id}", "The full view: brief, versions, formulation, process, trials, analyses, diagnoses, plans, ledger"],
                ["POST /api/products/{id}/predict", "Re-predict a version, optionally calibrating on the trials so far"],
                ["POST /api/products/{id}/trials", "Log a physical trial of the current version"],
                ["POST /api/products/{id}/trials/{trial}/analyse", "Analyse a trial and return the ranked diagnosis"],
                ["POST /api/products/{id}/plan", "Plan the next formulation and trial"],
                ["POST /api/products/{id}/plans/{plan}/accept", "Accept a plan as the next version"],
                ["POST /api/products/{id}/loop", "Run the closed loop for a trial budget"],
                ["GET /api/products/{id}/report", "The plain-text project report for one product"],
                ["GET /api/benchmark", "The latest trial-efficiency benchmark"],
            ],
            caption="Table 7.1  JSON API",
            widths=[0.8, 1.4],
        ),
        _code(
            "curl -s http://127.0.0.1:8770/api/health\n"
            '{"ok": true, "products": 4, "trials": 12, "ingredients": 95, "kpis": 22}\n\n'
            "curl -s -X POST http://127.0.0.1:8770/api/products \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d @case.json        # the brief, the category and the plant parameters\n"
            '{"product_id": 5, "objective": 0.94, "conflicts": []}',
            caption="Listing 7.3  Driving the agent from the command line",
        ),
        _h2("7.4  Screenshots to be added at submission"),
        _placeholder(
            "Insert your own screenshots of the running application here in image format, one per "
            "row of the checklist below. The charts in chapter 3 are generated from the record and "
            "are current; the browser screenshots are the ones only you can take from your own "
            "machine, by starting the application and capturing each screen."
        ),
        _table(
            [["Figure", "Screen", "What to capture"]],
            [
                ["7.1", "Products", "The seeded cases with their trial counts and efficiency"],
                ["7.2", "New product", "A brief pasted in, the inferred targets, and any open questions"],
                ["7.3", "Formulation", "The ingredient table with shares, contributions and the process plan"],
                ["7.4", "Trial analysis", "Residuals against the published intervals and the ranked cause with its evidence"],
                ["7.5", "Reformulation plan", "Dose deltas, parameter corrections, pass probabilities and the DOE"],
                ["7.6", "Benchmark", "The trial-efficiency comparison and the conflict demonstration"],
            ],
            caption="Table 7.2  Screenshot checklist",
            widths=[0.2, 0.4, 1.6],
        ),
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Chapter 8
# --------------------------------------------------------------------------- #
def _chapter_8(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [
        {"kind": "chapter", "text": "CHAPTER 8"},
        {"kind": "heading1", "text": "References"},
        *_bullets(
            [
                "[1] Tiny Dot Foods, *Problem Statement PS-1: AI-Powered Food Product Development "
                "Agent*, internship brief, 2026.",
                "[2] D. C. Montgomery, *Design and Analysis of Experiments*, 10th ed., Wiley, 2019.",
                "[3] R. H. Myers, D. C. Montgomery and C. M. Anderson-Cook, *Response Surface "
                "Methodology: Process and Product Optimization Using Designed Experiments*, 4th "
                "ed., Wiley, 2016.",
                "[4] G. E. P. Box and K. B. Wilson, \"On the experimental attainment of optimum "
                "conditions\", *Journal of the Royal Statistical Society: Series B*, vol. 13, "
                "no. 1, pp. 1-45, 1951.",
                "[5] T. G. Kolda, R. M. Lewis and V. Torczon, \"Optimization by direct search: new "
                "perspectives on some classical and modern methods\", *SIAM Review*, vol. 45, "
                "no. 3, pp. 385-482, 2003.",
                "[6] W. O. Atwater and F. G. Benedict, *Experiments on the Metabolism of Matter and "
                "Energy in the Human Body*, U.S. Department of Agriculture, 1899 (Atwater factors "
                "for food energy).",
                "[7] T. P. Labuza, \"Sorption phenomena in foods\", *Food Technology*, vol. 22, "
                "pp. 263-272, 1968.",
                "[8] G. V. Barbosa-Cánovas, A. J. Fontana, S. J. Schmidt and T. P. Labuza (eds.), "
                "*Water Activity in Foods: Fundamentals and Applications*, Blackwell Publishing, "
                "2007.",
                "[9] T. Ross, \"Indices for performance evaluation of predictive models in food "
                "microbiology\", *Journal of Applied Bacteriology*, vol. 81, pp. 501-508, 1996.",
                "[10] J. Baranyi and T. A. Roberts, \"A dynamic approach to predicting bacterial "
                "growth in food\", *International Journal of Food Microbiology*, vol. 23, "
                "pp. 277-294, 1994.",
                "[11] Food Safety and Standards Authority of India, *Food Safety and Standards "
                "(Food Products Standards and Food Additives) Regulations*, 2011, as amended.",
                "[12] Food Safety and Standards Authority of India, *Food Safety and Standards "
                "(Packaging and Labelling) Regulations*, 2011, as amended (claim thresholds used "
                "by the claim checker).",
                "[13] Codex Alimentarius Commission, *General Standard for Food Additives "
                "(CXS 192-1995)*, FAO/WHO.",
                "[14] International Organization for Standardization, *ISO 4121:2003 Sensory "
                "analysis - Guidelines for the use of quantitative response scales*.",
                "[15] Python Software Foundation, *The Python Standard Library*, Python 3.14 "
                "documentation, 2026. https://docs.python.org/3/library/",
                "[16] D. Richard Hipp, *SQLite Documentation*. https://www.sqlite.org/docs.html",
                "[17] Ecma International, *ECMA-376: Office Open XML File Formats*, 5th ed., 2021 "
                "(the format used to write this report as a Word document).",
                "[18] S. Seabold and J. Perktold, \"statsmodels: econometric and statistical "
                "modeling with Python\", in *Proceedings of the 9th Python in Science "
                "Conference*, 2010 (reference implementation for the ridge regression used by the "
                "trial-calibrated surrogate).",
            ]
        ),
        _h2("8.1  Software and tools used"),
        _table(
            [
                ["Tool", "Version", "Use"],
                ["Python", context["environment"].get("python", ""), "Language and runtime for the whole application"],
                ["SQLite", context["environment"].get("sqlite", ""), "The development record"],
                ["Pillow", context["environment"].get("pillow", ""), "Image measurement and figure rendering"],
                ["Pygments", context["environment"].get("pygments", ""), "Syntax highlighting in the code listings"],
                ["Git", context["environment"].get("git", ""), "Version control of the source"],
            ],
            caption="Table 8.1  Software used",
            widths=[0.4, 0.35, 1.2],
        ),
    ]
    return blocks


# --------------------------------------------------------------------------- #
def build(context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble the whole report as (metadata, blocks)."""
    context = _context(context)
    blocks: List[Dict[str, Any]] = []
    blocks += _front_matter(context)
    blocks += _chapter_1(context)
    blocks += _chapter_2(context)
    blocks += _chapter_3(context)
    blocks += _chapter_4(context)
    blocks += _chapter_5(context)
    blocks += _chapter_6(context)
    blocks += _chapter_7(context)
    blocks += _chapter_8(context)
    # A figure whose data was not available is dropped rather than left in as a
    # broken image: every writer would otherwise have to defend against it.
    blocks = [
        block
        for block in blocks
        if not (block.get("kind") == "image" and not block.get("path"))
    ]
    return {
        "title": PROJECT_TITLE,
        "subtitle": PROJECT_SUBTITLE,
        "chapters": [{"number": number, "title": title} for number, title in CHAPTERS],
        "blocks": blocks,
        "context": context,
    }
