# AI-Powered Food Product Development Agent (PS-1)

An offline agent that takes a food product from a written brief to a validated
formulation: it understands the specification, designs a recipe and a process,
predicts what a trial will measure, analyses what actually came back, diagnoses
the probable cause of a miss, corrects the plant setpoint before it rewrites the
recipe, and plans the next trial — so that fewer physical trials are needed to
reach the target.

Nothing here is installed: Python and the standard library are the whole
requirement. Pillow and Pygments are used only to draw the figures and code
listings for the report.

```
python run.py                 # start the interface on http://127.0.0.1:8770
python run.py --open          # start and open the browser
python run.py --seed          # (re)create the demonstration cases
python run.py --benchmark     # trial-efficiency benchmark: agent vs one-factor-at-a-time
python run.py --report        # write the internship report (DOCX, HTML, Markdown)
python -m unittest discover -s tests -t .   # the test suite
```

## What it does

| Stage | Module | What happens |
| --- | --- | --- |
| Understand the brief | `app/core/brief.py`, `app/core/vision.py` | Spec numbers, comparators and units are parsed into targets with tolerances; category, diet, allergens and claims are inferred; photographs are measured offline with Pillow, and a vision model is used only if a key is configured. Anything the text cannot answer becomes an open question. |
| Design | `app/core/formulate.py` | Slots per category, filled by intent measured against a neutral reference formulation, reconciled to 100 % by iterative proportional fitting, with acidulants dosed by bisection against the pH target. |
| Predict | `app/core/{nutrition,physical,cost,engine,uncertainty}.py` | Mass balance, Atwater energy, water activity, pH by titration, texture, stability, cost and processability — each with a sigma and a published interval. |
| Optimise | `app/core/optimize.py` | Coordinate pattern search on recipe and process together, with paired moves, a plausibility floor and an accepted-move log that explains the recommendation. |
| Make and measure | `app/plant.py` | A simulated plant with known faults (thermal offsets, drying efficiency, acid retention, sugar inversion, sodium carry-over, oxidation) and analytical noise drawn from the published sigma. |
| Analyse and diagnose | `app/core/diagnose.py` | Residuals in sigma units, a signed systematic score, process deviations, and ranked signature rules with evidence and a recommended action. |
| Reformulate | `app/core/reformulate.py`, `app/core/doe.py` | Measured process offsets become setpoint corrections first; then the recipe changes, with a pass probability per target and a design of experiments for the factors that moved. |
| Record | `app/store.py` | SQLite: products, immutable formulation versions, predictions as published, trials, analyses, diagnoses, plans, ledger, benchmarks. Every efficiency number is derived from these rows. |

## Evidence

`python run.py --benchmark` runs the same three cases twice — once with the agent
closing the loop, once with the one-factor-at-a-time method a bench would use —
against the same trial budget and the same pass gate (every hard target on
target, measured objective ≥ 0.85).

| Case | Agent trials | OFAT trials | Final objective |
| --- | --- | --- | --- |
| High-protein masala namkeen pellet | 3 | not reached | 0.989 vs 0.932 |
| High-protein high-fibre ragi cookie | 3 | not reached | 0.956 vs 0.878 |
| Reduced-sugar mango fruit spread | 3 | not reached | 0.927 vs 0.800 |

Mean saving: 4 trials per case. The benchmark also demonstrates the case where
the honest answer is a refusal: a brief whose fibre target no lever can reach is
reported as a conflict, with the target, the achieved value and the reason.

## The report

`python run.py --report` writes the internship report, in the format of the
supplied template, from the record itself:

- `report/Internship_Report.docx` — editable Word document (Times New Roman 12,
  1.5 spacing, justified, thin-bordered tables, a live INDEX field)
- `report/Internship_Report.html` — the same content with the figures embedded
- `report/Internship_Report.md` — the same content as Markdown

Every number in it is read from the benchmark and the database at build time, the
figures are drawn from the same record, and the code listings are taken from the
source files. Front-matter items that only the student can supply — name,
register number, dates, the scanned certificate and letter, the browser
screenshots — are written as visible `[...]` markers or explicit instructions
rather than being invented. Edit `STUDENT` in `app/report_sections.py` to fill in
the details you know.

## Layout

```
run.py                  launcher: server, seed, benchmark, report
app/core/               knowledge base, models, generation, optimisation, diagnosis, planning
app/data/               ingredients.json, processes.json, limits.json, formusense.db, figures
app/plant.py            the simulated plant
app/store.py            the SQLite record
app/service.py          the agent: one method per stage of the loop
app/bootstrap.py        the seeded demonstration cases and their plant faults
app/benchmark.py        agent loop vs one-factor-at-a-time
app/server.py           JSON API + single-page interface
app/web/                the interface (vanilla JavaScript, no build step)
app/figures.py          report figures and code listings
app/report_sections.py  the chapters of the report, as data
app/report_writers.py   DOCX / HTML / Markdown writers
app/docx_writer.py      a .docx writer built on the standard library
tests/                  the test suite
report/                 generated report output
```
