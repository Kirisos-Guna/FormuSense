# FormuSense — how to use the app

A user guide for the **AI-Powered Food Product Development Agent (PS-1)**.

This is the same guide the app shows at **How to use** (`#/guide`), kept as a
document so it can be read, printed or handed over without the server running.
It is written for a first-time user: a food technologist, a product developer or
an evaluator who wants to see the agent work.

---

## 1. Starting the app

Nothing needs installing beyond Python itself.

```bash
python run.py          # start the interface on http://127.0.0.1:8770
python run.py --open   # start it and open the browser
python run.py --port 9000   # start it somewhere else, if the port is taken
```

Open `http://127.0.0.1:8770`. The three pills in the header confirm what the
process can see as soon as the page answers:

| Pill | What it tells you |
| --- | --- |
| *N products, M trials* | What is on record in the database right now. |
| *N ingredients, N categories* | The size of the loaded knowledge base. |
| *vision: …* | Whether offline image analysis (Pillow) or a vision API key is available. Everything else works without either. |

The database is one SQLite file by default — nothing to install, nothing to
configure. PostgreSQL is opt-in through `FORMUSENSE_DB_URL`.

---

## 2. Five-minute quickstart

**Step 1 — Load a case study.** Open **Case studies** and press *Design against
this case* on any card. The four seeded cases each carry a fixed brief and a
plant with known commissioning deviations. A complete first formulation appears
in about a second, which is the fastest way to see the whole loop.

**Step 2 — Look at what was understood.** The product opens on **Overview**:
every KPI the agent parsed, its target, whether it is a *hard* or a *monitored*
target, the predicted value and the status. Anything the specification text
could not answer is listed there as an open question — those are for you, not
for the machine.

**Step 3 — Run a physical trial.** Press **Run physical trial** in the product
header. A batch is made on the simulated plant, measured with analytical noise,
and compared with the interval that was published *before* the batch. You land
on the **Trials** tab.

**Step 4 — Read the diagnosis.** The residual table shows measured minus
predicted in sigma units. The probable-cause cards say what went wrong and why,
each with evidence and a confidence.

**Step 5 — Let it plan.** Press **Plan next version**. The measured process
offset is corrected first, then the recipe is re-optimised under the brief's
constraints. Accept the plan to make it the next version — its predictions are
stored *before* the next trial, so the next batch is a real test of a forecast.

**Step 6 — Or close the loop in one press.** **Run closed loop** repeats trial,
diagnosis, plan and accept up to a trial budget you set, and stops the moment
the product passes. Then open **Benchmark** to see the same loop measured
against one-factor-at-a-time.

Prefer your own product? **New product** takes your specification text. If you
would rather not write one, press an example brief chip to fill the form from a
seeded case and then edit it.

---

## 3. The loop, step by step

| # | Stage | What happens |
| --- | --- | --- |
| 1 | **Understand** | The specification is parsed into numeric targets with tolerances, plus category, diet, allergens and claims. Anything unanswered becomes an open question. |
| 2 | **Design** | The category's slots are filled by intent and reconciled to 100 %, with acidulants dosed against the pH target. |
| 3 | **Predict** | Every characteristic is predicted with a 95 % interval and the method that produced it. |
| 4 | **Trial** | A batch is made on the simulated plant and measured; the measurements are compared with the interval published before the batch. |
| 5 | **Diagnose** | Residuals become sigma units and are matched against signature rules — thermal offset, drying shortfall, acid loss, sodium carry-over, oxidation. |
| 6 | **Plan** | The measured process offset becomes a setpoint correction; then the recipe is re-optimised, with a pass probability per target and a design of experiments. |
| 7 | **Accept** | The plan is promoted to the next version, with its predictions stored before the trial. |
| 8 | **Repeat** | Loop until every hard target is inside tolerance and the measured objective reaches the pass bar of 0.85. |

---

## 4. Every screen

| Screen | What it is for | What to press | What you will see |
| --- | --- | --- | --- |
| **New product** | Start your own product. | Describe the product as a brand team would, then *Design product*. | A parsed brief and a first formulation, opening on Overview. |
| **Products** | Everything on record. | Click a product name. | One row per product: category, versions, trials, predicted objective, created. |
| **Case studies** | Four worked examples with a story. | *Design against this case*. | A product created end-to-end from a fixed brief and a faulty plant. |
| **Benchmark** | The evidence that the loop works. | Set a budget, press *Run benchmark*. | Agent vs one-factor-at-a-time on the same cases, budget and pass gate, plus trials saved. |
| **Model** | The locally trained acceptance model. | Read it; train it from the command line. | RMSE, ROC-AUC and Brier on the held-out test fold, each next to its baseline. |
| **Knowledge base** | The ingredients and the rules. | Filter the ingredient table. | Categories and their unit operations, substantiable claims, tracked allergens, and the ingredient table with composition, cost, limits and diet flags. |
| **How to use** | This guide. | — | Quickstart, screen reference, glossary. |

### Inside a product

The header buttons — **Re-predict**, **Run physical trial**, **Plan next
version**, **Run closed loop** — are available from every tab. A plain-language
*next step* banner at the top of the page tells you which one is wanted next,
based on the record.

| Tab | What it shows |
| --- | --- |
| **Overview** | The brief as understood, next to the formulation on record, plus open questions and any target conflicts. |
| **Prediction** | Predicted values with 95 % intervals and the method for each, desirability per target, the objective, and how many hard targets are met. |
| **Populations** | What one serving delivers to each population group — protein per serving, share of the daily requirement, servings to reach it, and cautions. |
| **Trials** | The trajectory of measured objective against the pass bar, then per trial the residual table, the interval-coverage plot and the ranked probable causes. |
| **Plan** | The proposed next version: pass probability, recipe and process deltas (with the measured offset named), expected performance, and a design of experiments. |
| **Process** | Unit operations with equipment and durations, operating parameters against validated ranges, in-process targets, critical control points, packaging and yield. |
| **Report** | The development report for the product, generated from the stored rows. |
| **Ledger** | The append-only audit trail of everything recorded, with timestamps. |

---

## 5. Reading the numbers

- **Objective** — one number for the whole formulation: a priority-weighted
  geometric mean of desirability, from 0 to 1. The pass bar is **0.85**. It
  rewards balance, so one badly missed KPI drags the score down.
- **95 % interval** — the range the model expects *before* the trial. Judge a
  measurement against it, not against the target alone: a value inside the
  interval means the model was right even when the target was missed.
- **Residual / z** — measured minus predicted, divided by the published sigma.
  Beyond about 2 sigma is worth a look; beyond 3 sigma it is a systematic fault
  and the diagnosis will name a cause.
- **Hard vs monitored** — hard targets gate the pass; monitored targets are
  scored and shown but do not by themselves fail the batch.
- **Protein %RDA** — one serving's protein as a share of a population group's
  daily requirement against ICMR-NIN 2020 reference values. This is product
  development guidance, not medical advice, and every row prints the reference
  it used.
- **Trials to target** — physical batches needed to reach the gate. It is
  censored at the trial budget for an arm that never gets there, so "not
  reached" is a result, not a blank.

Every number on screen is computed server-side from the stored record. The
interface formats what it is given and never fills a gap with a guess. For the
headline comparison, run `python run.py --benchmark` and read the **Benchmark**
screen — the figures there are the stored run, not a copy in the prose.

---

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **Brief** | The written specification, and what the agent understood from it. |
| **Slot** | A role in the recipe for a category (the protein slot, the sweetener slot). Ingredients compete for slots under their inclusion limits. |
| **KPI** | A measurable characteristic: protein, moisture, water activity, pH, sodium, cost, texture. |
| **Hard target** | A KPI that must be inside its tolerance for the product to pass. |
| **Objective** | A priority-weighted geometric mean of desirability across every KPI. Higher is better; 1.0 is every target on its ideal. |
| **Desirability** | How satisfied one KPI is, from 0 (outside the acceptable region) to 1 (ideal). |
| **95 % interval** | The range published before a trial, from the model's uncertainty. |
| **Residual / z** | Measured minus predicted, in units of the published sigma. |
| **Pass gate** | Every hard target on target **and** a measured objective of at least 0.85. |
| **Trials to target** | How many physical batches were needed to reach the gate. |
| **OFAT** | One-factor-at-a-time, the comparison arm. |
| **%RDA** | Share of a population group's daily protein requirement that one serving delivers. |
| **CCP** | Critical control point: a line step with a critical limit, monitoring and an action if it is exceeded. |
| **Ledger** | The append-only audit trail for a product. |

---

## 7. Other things you can run

```bash
python run.py --seed           # recreate the demonstration cases
python run.py --benchmark      # agent vs one-factor-at-a-time, from the record
python run.py --build-dataset  # build the acceptance-model dataset (offline)
python run.py --train          # train and evaluate the acceptance model
python run.py --train-report   # print the metrics of the latest trained model
python run.py --report         # write the internship report (DOCX, HTML, Markdown)
python run.py --slides         # write the presentation (PPTX)
python run.py --db-migrate     # apply pending database migrations
```

No API key and no network call is needed for any of these. Pillow and Pygments
are optional and only draw the report's figures and code listings.

---

## 8. Troubleshooting and honest limits

| Symptom | What to do |
| --- | --- |
| Nothing loads | Check the server is running and look at the terminal it was started from. If the port is taken, use `python run.py --port 9000`. |
| Header says *vision: image analysis off* | Image analysis needs the optional Pillow install. The rest of the pipeline does not. |
| The Model tab is empty | No model has been trained in this database. Run `python run.py --build-dataset` then `python run.py --train`. |
| A brief cannot be satisfied | That is a result, not a bug: the agent reports the target, the best achievable value and the reason instead of quietly missing it. |
| A PostgreSQL error | The default is SQLite. PostgreSQL is opt-in through `FORMUSENSE_DB_URL`; migrations for both dialects live in `db/migrations/`. |

**What this app does not do.** It does not replace a pilot plant. Predictions,
objectives and probabilities are model output with published intervals; the
physical trial is still the test, and the design the agent proposes still has to
be run. The population guidance is development guidance, not medical advice.

For the design of the system — the models, the optimiser, the database and the
learning pipeline — see [`README.md`](../README.md).
