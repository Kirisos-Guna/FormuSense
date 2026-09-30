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
| *AI: …* | Which model, if any, is configured for the optional layer - for example *AI: openrouter*. Hovering it shows the model id, which free model answers if that one is busy, and how much of the hourly budget is left. With no key it reads *AI: off*, and nothing else changes. Offline image analysis needs the optional Pillow install. |

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
seeded case and then edit it - or upload the document the R&D team already has.

**Step 0 - or upload the report.** At the top of the **New product** form there
is an upload: drop a `.docx`, `.xlsx`, `.pptx`, `.pdf`, `.txt`, `.csv` or `.md` on
it, or press **Choose the document**. The agent reads the specification out of it
and fills the form - product name, category, diet, pack size, claims, allergens -
then lists what the document does *not* state. Every value is shown with whether it
came from a rule or from the model, and the text that was read is there to check
against. Nothing is designed until you press **Design product**, and reading the
document stores nothing: the file is kept only when the product it described is
created, where the ledger records which document the brief came from.

The pack-size field is labelled in the unit the product is sold in: **Unit
volume (ml)** for a beverage, **Unit weight (g)** for everything else. It
follows the category you pick, and the number you type is read in that unit -
a drink does not have to be converted into grams by hand. You can leave it
blank: the size is then taken from the specification text (`200 ml bottle`,
`1 litre bottle`, `40 g pack`) or from the category's usual pack.

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
| **Pack size** | The size of one unit as the pack declares it: millilitres for a product sold by volume (a beverage), grams otherwise. The brief, the form, the process sheet and the report all print the declared unit. The models work in mass, and millilitres and grams are the same number at a density near 1 g/ml. |
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
| **Ask** | Questions answered from this product's own record, once a model key is configured. The answer is drawn from the stored brief, formulation, prediction, trials and plan, and it lists the parts of the record it used. It cannot change anything: the question and its answer are added to the ledger, and nothing else is touched. |

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
python run.py --icmr-export    # write the ICMR-NIN 2020 reference set (XLSX and CSV)
python run.py --db-migrate     # apply pending database migrations
```

No API key and no network call is needed for any of these. Pillow and Pygments
are optional and only draw the report's figures and code listings. The optional
model layer described in section 8 is never used by these commands.

---

## 8. The optional model layer

Everything in this guide works with no API key, no network call and no third-party
service, and that is the default. With a key configured, a model is added to four
places - and only four - and in each of them it is an enrichment:

* **Reference photographs.** The offline measurement of colour, lightness and
  texture always runs. A model adds the semantic description on top: piece shape,
  surface finish, visible inclusions, apparent defects and a process hypothesis.
  Its reply is filtered to those description keys, so it cannot put a protein
  content or a weight into the brief even if it tries.
* **The specification text.** A model reads the text alongside what the rule-based
  parser already extracted and reports only what appears to be missing: a claim or
  an allergen from the registries the system holds, a declared pack size in a unit
  the category does not use, and further questions. Those land in the brief's open
  questions, and the claims are shown as suggestions for **you** to tick. No target,
  pack size, cost ceiling or already-parsed claim is ever changed by it.
* **An uploaded R&D document.** The document is read into text by the standard
  library first, and the same rule-based parser reads it, so the upload works with no
  key at all. A model adds only what the parser missed: a product name, a category, a
  diet, claim and allergen ids that already exist in the registries, and a declared
  pack size the rules did not recognise. There is no field for a target or a property,
  the one number it may transcribe is labelled in the form as read by a model and not
  parsed by a rule, and the specification text that reaches the parser is always the
  document's own words.
* **The product record.** The **Ask** tab answers a question from a bounded extract
  of the product's stored brief, formulation, prediction, trials and plan, naming
  the parts of the record it used. It is read-only.

To turn it on, put one key in `.env` beside `run.py` (the file is git-ignored) and
restart the server:

```
OPENROUTER_API_KEY=sk-or-...
```

OpenRouter is the provider this is built around, because one key reaches every
vendor's models. The default model list is free, so nothing is spent and no credit
balance is needed: the free models come from a shared pool that answers `429` when
somebody else is using it, so `OPENROUTER_MODEL` takes a comma-separated list and the
next free model answers when the first one is busy. `OPENAI_API_KEY` and
`GEMINI_API_KEY` are still honoured and go through the same client. A hosted instance
is given its key as an environment variable in the host's dashboard, never in the
repository.

Then tick **Use the model for this run** on the **New product** form. It is off by
default, so nothing spends a call unless you ask it to. `.env.example` lists every
setting; the ones worth knowing:

| Setting | Default | What it does |
| --- | --- | --- |
| `OPENROUTER_MODEL` | three free models | Which models to ask, in order. Free ones by default, so nothing is spent. |
| `FORMUSENSE_AI_RETRIES` | `2` | How many times a model may answer `429` before the next one in the list is tried. A refused request is free. |
| `FORMUSENSE_AI_REASONING` | `off` | Whether a model may think before answering. These prompts extract rather than reason, and turning it off is several times faster. |
| `FORMUSENSE_AI` | `1` | Set to `0` to switch the layer off even when a key is present. |
| `FORMUSENSE_AI_MODEL` | unset | Overrides the model for whichever provider the key belongs to. |
| `FORMUSENSE_AI_MAX_CALLS_PER_HOUR` | `60` | The ceiling. `0` means no ceiling. Over it, runs continue with the offline result. |
| `FORMUSENSE_AI_MAX_IMAGES` | `3` | How many photographs one call may carry. |
| `FORMUSENSE_AI_TIMEOUT` | `45` | Seconds before a provider is given up on. |

Two properties are worth relying on. Every reply is stored against a hash of the
request, images included, so asking the same thing twice is answered from the
database rather than the provider - it costs nothing and returns the same words.
And a failure never fails a run: a revoked key, a rate limit or an unreachable
provider is recorded as a sentence on the ledger and the product is designed,
predicted and reported exactly as it would have been offline.

---

## 9. Troubleshooting and honest limits

| Symptom | What to do |
| --- | --- |
| Nothing loads | Check the server is running and look at the terminal it was started from. If the port is taken, use `python run.py --port 9000`. |
| Header says *AI: off* | No model key is configured, which is the default and affects only the optional layer. Offline image analysis additionally needs the optional Pillow install. The rest of the pipeline needs neither. |
| *the model could not be reached* | Every model in `OPENROUTER_MODEL` refused or timed out. Free models are shared, so this is usually somebody else's traffic: press the button again, raise `FORMUSENSE_AI_MAX_CALLS_PER_HOUR`, or add another model to the list. The product was still designed, predicted and reported - only the description, the review or the answer is missing. |
| The **Ask** tab says the record cannot be asked | No model key is configured. Set `OPENROUTER_API_KEY` and restart the server, then reload the page. |
| The model did not run when a run asked for it | The reason is on the ledger and in the run's own output: no key, the hourly ceiling reached, nothing readable to send, or a provider that could not be reached. In every case the offline result is what was used. |
| The Model tab is empty | No model has been trained in this database. Run `python run.py --build-dataset` then `python run.py --train`. |
| A brief cannot be satisfied | That is a result, not a bug: the agent reports the target, the best achievable value and the reason instead of quietly missing it. |
| A PostgreSQL error | The default is SQLite. PostgreSQL is opt-in through `FORMUSENSE_DB_URL`; migrations for both dialects live in `app/db/migrations/`. Run `python run.py --db-check`: it writes and reads back one of every record the app keeps and names the step that fails. |

**What this app does not do.** It does not replace a pilot plant. Predictions,
objectives and probabilities are model output with published intervals; the
physical trial is still the test, and the design the agent proposes still has to
be run. The population guidance is development guidance, not medical advice.

For the design of the system — the models, the optimiser, the database and the
learning pipeline — see [`README.md`](../README.md).
