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
python run.py --slides        # write the presentation (PPTX) from the record
python run.py --build-dataset # build the acceptance-model dataset (offline, no API key)
python run.py --train         # train and evaluate the acceptance model, then report it
python run.py --train-report  # print the metrics of the latest trained model
python run.py --db-migrate    # apply pending database migrations (both dialects)
python -m unittest discover -s tests -t .   # the test suite
```

## Using the interface

The interface is a single page served by the same process, and it needs no
build step. Open `http://127.0.0.1:8770` after `python run.py`, or press
**How to use** in the navigation for the same guide the app shows:

1. **Case studies** creates a complete product from a fixed brief in about a
   second - the fastest way to see the whole loop. **New product** takes your
   own specification text instead, and the example-brief chips fill the form
   from a seeded case if you would rather not write one.
2. The product opens on **Overview**: the targets the agent parsed, the
   formulation, and anything the text could not answer as an open question.
3. **Run physical trial** makes a batch on the simulated plant and lands you on
   **Trials**, where the residuals in sigma units and the probable causes are.
4. **Plan next version** corrects the line and rewrites the recipe; accepting
   the plan stores its predictions before the next batch. **Run closed loop**
   does steps 3 and 4 until the product passes.
5. **Benchmark** is the stored comparison against one-factor-at-a-time, and
   **Model** is the held-out metrics of the locally trained acceptance model.

A plain-language next-step banner at the top of every product page says which
of those actions the record is waiting for. The full guide is
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).

The interface is responsive down to a 320 px phone: the navigation and the
product tab strip become single scrollable rows, wide tables scroll inside
their own card instead of widening the page, the product actions stack full
width, and form controls are 16 px so mobile browsers do not zoom on focus.
The rules that fixed each measured overflow are locked in by `tests/test_ui.py`,
which runs without a browser.

## Live demo

The application is a Python server, so it needs a host that runs processes - a
static host can only publish files. The deployed instance is this repository,
built by the `Dockerfile` and described by `render.yaml`:

### <https://formusense.onrender.com>

* It is a free Render instance, so it sleeps after fifteen minutes without a
  visitor and the first request after that takes about a minute to wake. A
  scheduled workflow, `.github/workflows/keepalive.yml`, pings it every ten
  minutes so that a judge does not meet that wait.
* It runs exactly this code, on the bundled SQLite record, seeded with the
  demonstration cases before the port opens (`FORMUSENSE_SEED_ON_START`), with the
  acceptance model trained from the dataset during the image build. Nothing is
  fetched from a third party at runtime and no API key exists anywhere in it.
* Reads and writes are open, so the app can actually be used: create a product,
  run a trial, plan the next version, read the ledger. The record is recreated
  whenever Render recycles the instance, which is why the boot seed exists.
* A free instance has a fraction of one CPU, so the closed loop, the benchmark and
  report generation are slower up there than on your own machine.

To stand up the same shape yourself, press this button and pick the repository:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Kirisos-Guna/FormuSense)

Or run it locally, which needs nothing installed beyond Python:

```
python run.py --open          # the interface at http://127.0.0.1:8770
```

The slide deck is generated from the project record and lives in the repository as
[`report/Technostatic_Wings_FormuSense_Slides.html`](report/Technostatic_Wings_FormuSense_Slides.html)
(the `.pptx` beside it is the same deck for PowerPoint).

## The database

One SQLite file is the default, and nothing needs installing to use it. The same
record layer runs on PostgreSQL: set `FORMUSENSE_DB_URL` and it migrates on
startup, with the driver imported lazily so a machine without it still runs the
offline path.

```
FORMUSENSE_DB_URL=postgresql://formu:formu@localhost:5432/formusense python run.py
docker compose up --build     # app + PostgreSQL, schema migrated on startup
```

Schema changes are forward-only, numbered SQL files per dialect under
`app/db/migrations/`, tracked in a `schema_migrations` table. `GET /api/db/status`
reports the dialect, whether its driver is present and which migrations have run.

## The learning pipeline

`python run.py --build-dataset` writes a versioned dataset (rows, plus a manifest
with the seed and a content hash), and `--train` fits a **ridge regression** for
the measured objective and a **calibrated logistic regression** for pass/fail,
both implemented in plain Python. There is no API call and no key anywhere in the
pipeline.

The split is by **plant instance**, not by random row, so a model cannot memorise
one plant's offset and be tested on it, and every reported number is measured on
the held-out test fold against two baselines (predict-the-mean and
predict-the-base-rate). The UI's **Model** tab and `GET /api/model` show those
out-of-sample metrics. The dataset is generated by the simulated plant and the
manifest says so; `app/ml/dataset.py` documents the column schema for importing
real plant or sensory data instead.

## Consumption guidance by population

Every product gets a **Populations** tab: what one serving delivers to children
(by age band), adult men and women, pregnant and lactating women, and adults 60+,
against ICMR-NIN 2020 protein reference values - protein per serving, the share of
the daily requirement, how many servings reach it, and the cautions that apply to
that group. The reference set is editable data (`app/data/dri_profiles.json`) and
every row carries the standard it was compared against. This is
development guidance, not medical advice, and the payload says so.

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
| Record | `app/store.py`, `app/db/` | A relational record of products, immutable formulation versions, predictions as published, trials, analyses, diagnoses, plans, ledger and benchmarks, behind a dialect adapter: **SQLite** by default, **PostgreSQL** by setting `FORMUSENSE_DB_URL`, with versioned migrations. Every efficiency number is derived from these rows. |
| Guide consumption | `app/core/population.py`, `app/data/dri_profiles.json` | What one serving delivers to each population group (children by age, adult men and women, pregnant and lactating women, 60+) against ICMR-NIN 2020 protein references: protein per serving, share of the daily requirement, servings to reach it, and the cautions for that group. |
| Learn from data | `app/ml/` | A versioned dataset and a train/validation/test protocol: a ridge regression for the measured objective and a calibrated logistic regression for pass/fail, both in plain Python, scored out of sample against two baselines. No API key is used anywhere. |

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
| High-protein whey beverage (ready-to-drink) | 2 | not reached | 0.985 vs 0.786 |

Mean saving: 4.25 trials per case, and 4 of 4 products reach the gate against 0 of
4 for the comparison arm. The benchmark also demonstrates the case where
the honest answer is a refusal: a brief whose fibre target no lever can reach is
reported as a conflict, with the target, the achieved value and the reason.

The benchmark now runs over **four** seeded products - the three above plus the
ready-to-drink whey beverage, whose heat-exchanger fault is corrected through the
same loop - and every number in the table above is read back from the record by
`python run.py --benchmark`, never typed in.

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

## The presentation

`python run.py --slides` writes `report/Technostatic_Wings_FormuSense_Slides.pptx`,
a seven-slide deck built from the same record as the report: the benchmark table on
the evidence slide is the stored benchmark, the metrics on the learning slide come
from the trained bundle, and the database and reference-set facts are read from
the live settings and the data files. Nothing is quoted from memory, so the deck
cannot drift from the application — rebuild it after new trials or a retrain.

The package is written by `app/pptx_writer.py`, which produces the OOXML parts
from the standard library in the same way `app/docx_writer.py` produces the Word
file: no `python-pptx`, no install. `--out` chooses where it is written and
`--no-tests` skips the suite run that stamps the test evidence onto the slides.
Speaker notes are not embedded; the deck is content-complete without them.

## Layout

```
run.py                  launcher: server, seed, benchmark, report, slides
app/core/               knowledge base, models, generation, optimisation, diagnosis, planning, population guidance
app/ml/                 dataset, features, models, evaluation and the trained-model registry (offline)
app/config.py           environment-driven settings
app/logging_setup.py    process logging and request ids
app/data/               ingredients.json, processes.json, limits.json, dri_profiles.json, formusense.db, figures
app/db/                 dialect adapter (backend.py) and versioned migrations/{sqlite,postgres}
Dockerfile              app image; trains the model, then serves; .dockerignore keeps it lean
render.yaml             the Render blueprint: one free Docker web service, health-checked
.github/workflows/      CI (both dialects), plus the keep-alive ping for the live demo
app/plant.py            the simulated plant
app/store.py            the record layer: products, versions, trials, evidence, plans
app/service.py          the agent: one method per stage of the loop
app/bootstrap.py        the seeded demonstration cases and their plant faults
app/benchmark.py        agent loop vs one-factor-at-a-time
app/server.py           JSON API + single-page interface
app/web/                the interface (vanilla JavaScript, no build step)
docs/USER_GUIDE.md      how to use the app, also shown in the interface at #/guide
app/figures.py          report figures and code listings
app/report_sections.py  the chapters of the report, as data
app/report_writers.py   DOCX / HTML / Markdown writers
app/docx_writer.py      a .docx writer built on the standard library
app/slides.py           the presentation's content, built from the record
app/pptx_writer.py      a .pptx writer built on the standard library
tests/                  the test suite
report/                 generated report and presentation output
```
