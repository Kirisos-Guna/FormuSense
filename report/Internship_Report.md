**SRM Institute of Science and Technology, Ramapuram Campus, Chennai**

**INTERNSHIP REPORT**

**AI-POWERED FOOD PRODUCT DEVELOPMENT AGENT**
an offline formulation, process and trial-diagnosis assistant that reduces trials-to-target in snack and spread development

Submitted in partial fulfilment of the requirements for the award of the degree of
**Master of Computer Applications**

Submitted by
**[Student name]**
[Register number]

Internship carried out at Tiny Dot Foods
[Internship start date] to [Internship end date]

**Department of Computer Applications**
SRM Institute of Science and Technology, Ramapuram Campus, Chennai  |  [Semester and academic year]

---


# BONAFIDE CERTIFICATE
This is to certify that [Student name], bearing register number [Register number], a student of the Master of Computer Applications programme at the Department of Computer Applications, SRM Institute of Science and Technology, Ramapuram Campus, Chennai, has satisfactorily completed the internship work described in this report at Tiny Dot Foods during [Internship start date] to [Internship end date]. The work reported here is the candidate's own work carried out under the guidance of the undersigned. It has not been submitted elsewhere for the award of any other degree or diploma.


| Signature | Signature |
| --- | --- |
| [Faculty guide, Department of Computer Applications] | [Industry guide at Tiny Dot Foods] |
| Faculty guide | Industry guide |

> **To be supplied:** Marker: add the internal examiner's signature block and the college seal above this line once the report is bound.

---


# INTERNSHIP COMPLETION LETTER
> **To be supplied:** Insert the internship completion letter soft copy in image format.
The letter issued by the organisation confirms the duration and the nature of the internship; it is placed here unaltered, as issued.


---

The index below is a live field. Word fills in the page numbers when the document is opened or whenever the field is updated, so the numbers always match the pagination that results from editing the report.


## INDEX
- Chapter 1. Abstract
- Chapter 2. Details about the Training
- Chapter 3. Project Description
- Chapter 4. Hardware and Software Requirements
- Chapter 5. Coding Screenshots
- Chapter 6. Design Database
- Chapter 7. Screenshots
- Chapter 8. References

**Table 0.1  Chapter list (in the Word file the INDEX field above is also filled in by Word on opening)**
| Chapter | Title |
| --- | --- |
| 1 | Abstract |
| 2 | Details about the Training |
| 3 | Project Description |
| 4 | Hardware and Software Requirements |
| 5 | Coding Screenshots |
| 6 | Design Database |
| 7 | Screenshots |
| 8 | References |


---


# CHAPTER 1

# Abstract
A new food product is rarely right the first time. Between the specification a brand writes and a recipe that actually meets it lie weeks of physical trials, and every trial costs material, machine time, analytical work and shelf space. The problem statement this internship addresses (PS-1, Tiny Dot Foods) is therefore not "design a product" but "stop paying for trials that a model could have avoided".

This report describes a working software agent that carries a product from a written specification to a validated formulation. The agent reads the brief - text, target numbers and any product photographs - and converts it into a set of measurable targets with tolerances and priorities. It then builds an initial formulation by selecting ingredients into the functional slots the category defines (base, protein source, fat, sweetener, fibre, acidulant and so on), predicts the composition, physical properties, stability and cost of that recipe, and searches the recipe and the process parameters for an improvement. A simulated plant turns the recipe into measurements with the deliberate faults a real line would show: a dryer that runs below its setpoint, an acid dose that under-delivers, sugar inversion, dosing variation. The agent compares measured results against its own published intervals, diagnoses the probable cause, compensates for the process offset, reformulates, and plans the next trial - including the design of experiments that will tell it the most about the factors it just moved.

The claim that matters is measured, not asserted. The same three product cases were run twice: once with the agent closing the loop, and once with the standard one-factor-at-a-time approach a bench would use, where one lever is changed per trial in the direction that most helps the worst-performing target. Both arms were allowed the same number of trials and judged by the same gate - every hard target on target and the measured objective at or above 0.85.

**Table 1.1  Trial efficiency over the seeded cases**
| Cases | 3 |
| --- | --- |
| Trials to target, agent (mean) | 3 |
| Trials to target, one-factor-at-a-time (mean) | 7 |
| Cases reaching target, agent | 3 of 3 |
| Cases reaching target, one-factor-at-a-time | 0 of 3 |
| Mean trials saved per case | 4 |
| Total trials saved | 12 |

The agent reached every case within the trial budget allowed; in the same budget the one-factor-at-a-time arm reached none of them. The saving is the difference the problem statement is about: the same development decision, taken with fewer physical trials, because the trial that is not run was replaced by a prediction with a stated uncertainty and an explicit reason to believe it.

Equally important is what the agent will not do. Every number it publishes is traceable to a model, a rule in the knowledge base, or a stored trial; every prediction carries an interval; and when a brief cannot be satisfied as written - when a fibre target and a protein target demand more of the same ingredient than the recipe can hold - the agent says so and names the target responsible instead of returning a recipe that quietly misses it.


## 1.1  What was built
- A knowledge base of 95 food ingredients with composition, cost, water activity, density, allergen and dietary flags, and functional groups; 6 product categories with their slots, process envelopes and parameter ranges; and the regulatory and claim limits the recipes are checked against.
- A prediction engine that computes composition by mass balance, energy by Atwater factors, water activity from free water and solute load, pH by titration against buffer capacity, texture, stability, cost and processability - with a stated uncertainty on each.
- A formulation generator and a coordinate pattern-search optimiser that work on the recipe and the process together, with an objective that is the priority-weighted desirability of the brief's targets.
- A trial-analysis and diagnosis layer that reads residuals in units of the published uncertainty, separates process faults from formulation faults, and names a probable cause with its evidence.
- A reformulation planner that applies measured process offsets as setpoint corrections before it changes the recipe, predicts a pass probability per target, and proposes a design of experiments for the factors it moved.
- A trial-efficiency benchmark, a SQLite record of every version, trial, diagnosis and plan, a browser interface, a test suite, and this report - generated from the same record as everything else.

## 1.2  Scope and limitations
The plant is simulated. That is a deliberate choice: the agent's logic can be tested against a plant whose true behaviour is known, so a wrong diagnosis is a bug rather than a coincidence, and the benchmark can be rerun by anyone. The simulation models the faults that dominate early trials - thermal setpoint error, drying efficiency, acid retention, sugar inversion, sodium carry-over, oxidation, and analytical variation - but it does not model microbiology, rheology past the texture indices used here, or consumer acceptance, which the sensory panel is still the only way to measure. The ingredient data are literature values, adequate for direction and ranking, and the agent treats them as such: every prediction carries an interval whose width reflects how far the recipe sits from the data it was calibrated on.

The system runs on the Python standard library alone (3.14.6 (64-bit)), with Pillow and Pygments used only to draw the figures and code listings for this report. There is no database server, no service to install and no network call in the prediction path, which is what allows the whole development record - and the report written from it - to be reproduced on any machine that has Python.


## 1.3  Keywords
food product development; formulation optimisation; design of experiments; water activity; trial efficiency; surrogate modelling; uncertainty quantification; fault diagnosis; Python; SQLite.


# CHAPTER 2

# Details about the Training

## 2.1  Organisation and problem statement
The internship was carried out at Tiny Dot Foods during [Internship start date] to [Internship end date], under [Industry guide at Tiny Dot Foods] on the industry side and [Faculty guide, Department of Computer Applications] on the academic side. The work addressed problem statement PS-1 of the internship brief: an AI-powered food product development agent.

The brief lists the capability the agent must have. In the agent's own terms, it is:

- **Understand the target product** from images and specifications - what the product is, what it has to contain, what it must not contain, and how good is "good enough" for each requirement.
- **Generate an initial formulation and manufacturing process** - a first recipe with proportions, and the unit operations and parameters that turn it into a product.
- **Predict key characteristics** - nutrition, water activity, pH, texture, stability, cost - before anything is made.
- **Analyse trial results** against those predictions, rather than against expectation, so that a surprise is visible as a number.
- **Diagnose probable formulation or process failures** - which cause, how confident, on what evidence, and what to check first.
- **Reformulate for the next trial** - change the recipe where the recipe is wrong, and correct the setpoint where the plant is wrong.
- **Reduce trials-to-target** - the commercial objective that makes the other six worth building.

## 2.2  Scope of the internship work
**Table 2.1  Scope of the work**
| Item | Detail |
| --- | --- |
| Domain | Food product development: snack, bakery, spread, dry-mix and sauce categories |
| Deliverable | A running, dependency-free Python application with a browser interface, a test suite, and this report |
| Knowledge base | 95 ingredients, 6 categories, claim and additive limit rules |
| Demonstration cases | 3 seeded product cases plus 1 deliberately infeasible brief, all with known plant faults |
| Evidence | A trial-efficiency benchmark comparing the agent loop against one-factor-at-a-time on identical cases and budget |


## 2.3  Work plan
**Table 2.2  Stage-wise work plan**
| Stage | Activity | Output |
| --- | --- | --- |
| 1 | Problem framing, category and KPI definition | Target set, tolerances, priority weights |
| 2 | Ingredient, process and limit knowledge base | ingredients.json, processes.json, limits.json |
| 3 | Prediction models (nutrition, water activity, pH, texture, stability, cost) | engine.predict with per-KPI uncertainty |
| 4 | Brief understanding and image handling | Targets, allergens, claims, open questions |
| 5 | Formulation generation and optimisation | Version 1 recipe and process, with conflicts reported |
| 6 | Simulated plant and trial record | Trials with realistic faults, stored with their versions |
| 7 | Analysis, diagnosis, reformulation and DOE | Cause ranking, setpoint compensation, next-trial design |
| 8 | Benchmark, interface, tests and documentation | Trial-efficiency evidence, UI, test suite, this report |


## 2.4  Method
The method was chosen for one reason: a development record has to be defensible. A recipe recommended by a model that cannot explain itself is not usable in a factory, and a prediction that comes without an uncertainty cannot be tested against a measurement - it can only be believed or disbelieved. So the agent is built from transparent models instead of a fitted black box:

- **First principles where they exist.** Composition is mass balance; energy uses the Atwater factors; water activity follows the free-water/solute split; pH is a titration against the buffer capacity of the mix. These are checkable by hand, which is exactly the property a factory needs.
- **Rules where physics is unavailable.** Water-activity and pH regimes, additive limits, allergen and diet constraints, claim thresholds and microbial safety rules live in JSON, so a food technologist can read and correct them without touching code.
- **Uncertainty as a first-class output.** Every predicted KPI carries a sigma built from the base analytical variation, an absolute floor, and a penalty for extrapolating beyond the calibrated range. The same sigmas decide what counts as a real deviation when a trial comes back.
- **Deterministic search.** Optimisation is a coordinate pattern search with a fixed seed and an acceptance rule, not a random search. Two runs of the agent on the same brief with the same record produce the same recommendation, and the log of accepted moves is the explanation of how it got there.
- **A surrogate only where it earns its place.** A ridge regression on residuals is blended into the physics as trials accumulate, weighted by how much data it has; with few trials it collapses to an intercept-only bias correction, because a model fitted to four points should not be allowed to overrule a mass balance.

## 2.5  Learning outcomes
- How a food specification becomes a set of measurable targets, and why tolerances and priorities matter more than the target values themselves.
- Why water activity, not moisture alone, controls shelf life and microbial stability - and how a humectant and a drier change it differently.
- How to design a trial that will answer the question being asked, and what fractional factorial and centre-point designs buy when several factors moved together.
- How to separate a formulation fault from a process fault using only the residual pattern and the process actuals.
- How to quantify uncertainty honestly enough that a measurement can contradict the prediction.
- How to build a decision-support application with no third-party dependencies, and how to keep a report generated from the same record as the results it describes.
The modules that make up the application, and the size of each, are listed here because the report refers to them throughout.

**Table 2.3  Application modules**
| Module | Lines | Responsibility |
| --- | --- | --- |
| app/core/kb.py | 326 | Knowledge-base loader: ingredients, categories, process envelopes, limits, allergens |
| app/core/types.py | 285 | The vocabulary: Brief, KpiTarget, Formulation, Item, TrialResult |
| app/core/kpi.py | 269 | KPI registry, target construction, desirability kernel, status |
| app/core/nutrition.py | 305 | Composition by mass balance, energy, claim checking, label table |
| app/core/physical.py | 450 | Water activity, pH by titration, texture, stability and shelf life, processability |
| app/core/uncertainty.py | 182 | Per-KPI sigma, interval and confidence label |
| app/core/cost.py | 93 | Ingredient, process and packaging cost per kilogram and per unit |
| app/core/engine.py | 224 | Prediction orchestration and evaluation of a prediction against the brief |
| app/core/brief.py | 649 | Brief understanding: spec parsing, category, diet, allergens, claims, targets |
| app/core/vision.py | 432 | Image handling: offline measurement with Pillow, optional vision model |
| app/core/formulate.py | 928 | Slot-based formulation generation, reference-relative intent, constraint checking |
| app/core/optimize.py | 800 | Objective, levers, coordinate pattern search with paired moves |
| app/core/surrogate.py | 408 | Residual ridge regression with LOO validation and a bias-only mode |
| app/core/doe.py | 211 | Design of experiments for the factors that moved |
| app/core/diagnose.py | 699 | Residual analysis and ranked signature rules for probable causes |
| app/core/reformulate.py | 423 | Setpoint compensation, reformulation plan, pass probability |
| app/core/process.py | 221 | Unit operations, parameter table, in-process targets, CCPs, batch sheet |
| app/plant.py | 229 | The simulated plant: known faults, measurement noise drawn from the published sigma |
| app/store.py | 464 | SQLite record: products, versions, predictions, trials, diagnoses, plans, ledger, benchmarks |
| app/service.py | 710 | The agent: create, predict, run trial, analyse, diagnose, plan, accept, close the loop |
| app/bootstrap.py | 188 | The seeded demonstration cases and their plant faults |
| app/benchmark.py | 391 | Trial-efficiency benchmark: agent loop against one-factor-at-a-time |
| app/docx_writer.py | 522 | A .docx writer built on the standard library alone |
| app/figures.py | 520 | Report figures and syntax-highlighted code listings, drawn with Pillow and Pygments |
| app/report_sections.py | 1252 | The chapters of this report, as data |
| app/report_writers.py | 867 | Writes the report as DOCX, HTML and Markdown |
| app/server.py | 584 | HTTP server: JSON API and the single-page interface |


# CHAPTER 3

# Project Description
The agent is a closed loop with seven stages: understand the brief, design an initial formulation and process, predict what it will measure, run the trial, analyse the residuals, diagnose the cause, and plan the next formulation and trial. Each stage writes to the record, and the next stage reads what the previous one wrote, so the loop can be stopped and restarted at any point without losing the reason for any decision.

![Figure 3.1  The development loop and the modules that implement each stage](arch.png)
*Figure 3.1  The development loop and the modules that implement each stage*

## 3.1  Understanding the brief
A brief arrives as prose with numbers in it - "protein not less than 15 g per 100 g", "moisture below 4 %", "shelf life 6 months", "no more than 300 mg sodium". The agent parses it into `(key, comparator, value, unit)` tuples and turns each one into a target with a tolerance, a direction and a priority.

Two details do most of the work here. First, direction: a bare number is treated as a band centred on the value unless the KPI is inherently one-sided, in which case the value is a bound. Getting this wrong is the difference between a recipe that targets 300 mg of sodium and one that treats 300 mg as a ceiling - and the second is what a brief usually means. Second, the parser reads the comparator, the unit and even the sentence-ending full stop correctly: an interval written as "0.90." at the end of a sentence must not lose its value to a greedy number match, which is a real bug that was found and fixed while building this.

Category inference, allergen detection and claim detection run over the same text, and each claim (high protein, high fibre, reduced sugar, low sodium) brings its own numeric thresholds into the target set from the regulations in the knowledge base. What the text cannot answer becomes an explicit open question rather than a silent assumption: if no image is supplied, appearance and piece size are recorded as unknown. Photographs, when supplied, are measured offline with Pillow - colour of the dominant region, colour uniformity, form factor from the aspect ratio, surface texture from local contrast - and, if an API key is configured, a vision model adds the semantic layer (what the product is, what the surface finish looks like). The offline path always runs, so a machine with no network and no key still produces a complete brief; the API is an enrichment, never a dependency.


## 3.2  Designing an initial formulation
Generation is slot-based. Every category defines the functional slots a product of that kind needs, and each slot carries an intent - what this slot is for in this brief - expressed as the KPIs it should move and in which direction. The intent vector is not hard-coded: it is computed by comparing the brief's targets against a cached *neutral reference formulation*, an unoptimised recipe of the same category. If the brief wants more protein than the reference delivers, the protein slot's intent rises; if it wants less sugar, the sweetener intent falls. That one idea is what makes the generator respond to the brief instead of producing the same category-average recipe every time.

Ingredients are then chosen per slot from the candidates the diet, allergen and category rules allow, scored by a utility that combines the slot intent, the candidate's own composition, cost and its preference prior. Proportions are reconciled with iterative proportional fitting so the recipe sums to 100 % while respecting slot minimums and maximums, with a greedy fallback when the target is infeasible. Two corrections run afterwards: acidulants are dosed by bisection against the pH target rather than guessed, and the fibre, salt and water slots are trimmed to their limits if the brief's claims would otherwise be broken.

A hard test follows every generation. 22 KPIs are predicted, the brief's targets are evaluated, and any target that no lever can move - because the category, the process envelope or the target set itself fixes it - is reported as a conflict with the reason attached.


## 3.3  Searching for a better recipe
The optimiser treats the recipe and the process as one problem. Its objective is the priority-weighted desirability of the brief's targets, minus penalties that keep the answer usable: a penalty on drifting away from each slot's intent (weighted down for precision slots such as acidulants, where a small dose matters a great deal), a plausibility floor so a recipe cannot be nudged into a nonsense composition, and constraint penalties for additive, allergen and diet violations. Desirability is a smooth kernel centred on the band rather than a cliff at its edge, because a one-sided improvement worth 0.05 deserves to be visible.

The search itself is coordinate pattern search: for each lever (an ingredient's share, or a process parameter) it tries a step in both directions, applies the resulting move, renormalises to 100 %, and accepts it only if the objective improves by more than a step-dependent epsilon. When single-lever moves stall, it escalates: a paired pass moves two levers together - the coordinated move that a human formulator makes when raising a protein source requires lowering the base - and then it regenerates the lever list, which allows a slot that was previously at zero to be introduced. The accepted-move log is the explanation: every change of the recommendation carries the KPI it improved and by how much.


## 3.4  Predicting what the trial will measure
**Table 3.1  Prediction methods and where their uncertainty comes from**
| Predicted property | How it is predicted | Uncertainty source |
| --- | --- | --- |
| Composition and energy | Mass balance over the recipe; Atwater factors for energy | Ingredient composition tables |
| Water activity | Free water and solute load by regime; bound water from fibre and hydrocolloids | Regime boundary, solute interaction |
| pH | Free acid from acidulants and the matrix, titrated against buffer capacity, with unbuffered slope and pKa potency | Buffer capacity estimate, matrix acidity |
| Texture and hardness | Structural indices from fat, moisture, sugar and protein fractions | Category-level empirical fit |
| Stability and shelf life | Water-activity and moisture-driven deterioration rates with an oxygen term | Accelerated-shelf-life convention |
| Cost | Ingredient cost, process cost and packaging cost per kilogram and per unit | Price variation, yield assumptions |
| Processability | Extrusion, sheeting, drying and filling feasibility from the recipe state | Parameter-range limits |

Each prediction is published with an interval, not as a point. The sigma combines the base analytical variation of that measurement, an absolute floor so that a percentage-of-value error cannot shrink to nothing on a small number, and an extrapolation factor that widens the interval when the recipe sits outside the range the model was calibrated on. That interval is the instrument the whole loop depends on: a trial result is "surprising" only relative to it.


## 3.5  The simulated plant, and why it is simulated
Physical trials are replaced here by a simulated plant with a known, configurable truth: a drying efficiency, a temperature offset, an acid retention, a sugar inversion factor, a sodium carry-over and an oxidation factor, plus analytical noise drawn from the measurement sigma of each KPI. Drying loss is applied as a fraction of the water the process intended to remove, not as a flat offset, which is what makes an under-performing dryer look like a real under-performing dryer: the same settings hurt a wet feed far more than a dry one.

The point of simulating is testability. Because the true cause is known, a wrong diagnosis is a defect that can be found and fixed rather than a plausible story; and because the noise is drawn from the published sigma, the intervals can be checked for calibration - a 95 % interval that contains the measurement 95 % of the time. Trials are stored with the version that produced them, the batch size, the operator, the process actuals and the sensory panel's scores, so analysis always has a complete picture of what was made.


## 3.6  Analysing a trial
Analysis compares the measured value of every predicted KPI with the prediction on record for that formulation version, and expresses the difference in units of the published sigma. A residual of 1.3 sigma is noise; 4 sigma is a fault. Direction is kept, not just magnitude, because a systematic error is exactly the case where the signs agree: the analyser scores the fraction of signed evidence pointing one way and calls the trial systematic above 0.55, which is what distinguishes a mistuned plant from a bad day in the laboratory.

![Figure 3.2  Residuals of a trial against the published interval](res.png)
*Figure 3.2  Residuals of a trial against the published interval*
Process actuals - what the machine really did, against what the recipe asked for - are attached to the analysis before diagnosis runs, so a temperature that ran low is available as evidence rather than being something the operator has to remember to mention.


## 3.7  Diagnosing the cause
Diagnosis is a ranked set of signature rules over the residual pattern and the process deviations, each returning a cause, a category (process, formulation, dosing, analytical, supply), a confidence and the specific evidence that triggered it. The signatures cover the faults that dominate early trials: a dryer that under-dried, a temperature that ran off setpoint, acid loss or acid overdose, dosing/scale error, over-drying, texture high or low from fat and moisture, extrusion severity, sugar inversion, oxidation, dilution by an extra ingredient, yield and cost surprises, and a model bias signature that fires when the residuals are small, signed the same way, and spread across KPIs that no single cause can explain.

The last of those is the honest one. If the answer is "the model is slightly wrong about this category", the agent says that instead of inventing a process fault, and the recommendation becomes a confirmation trial with an unchanged formulation rather than a reformulation. The ranking is by confidence, and every candidate carries its recommended action - "verify the dryer profile with a calibrated thermocouple at the product surface, not at the controller" - because a diagnosis that does not say what to do next is only a description.


## 3.8  Reformulating, and correcting the plant instead
The reformulation planner distinguishes the two things a disappointing trial can mean. If the cause is the plant - a dryer running 9 °C below its setpoint, an acid dose retaining only three quarters of what was intended - the fix is not a different recipe at all: it is a setpoint that asks for 9 °C more, or a dose that asks for a quarter more, until the controller is recalibrated. So the planner applies the measured offsets as setpoint corrections *before* it optimises anything, and hands the optimiser a predictor that models what the plant will deliver rather than what the recipe requests. Without that step an optimiser reads "moisture too high" as "dry less" and walks the recommendation the wrong way, one experiment at a time - a failure mode this project hit and fixed.

Only after the process is compensated does the recipe change, driven by the residuals that remain. The plan reports, per target, the expected value with its interval and the probability of passing; the ingredient deltas with their contribution to each KPI; the parameter deltas with their validated ranges; the surrogate's weight in the prediction; and the design of experiments for the next trial, chosen from the number of factors that actually moved: a three-level sweep for one factor, a full factorial with centre points for two to four, a resolution-IV fraction above that - always with a control replicate and a randomised run order.


## 3.9  The trial-efficiency benchmark
The benchmark is the evidence that the loop is worth running. Two arms work the same three cases with the same trial budget and the same pass gate. The agent arm is the loop described above. The comparison arm is one-factor-at-a-time, the standard bench method: measure, find the worst-performing target, change the single lever that most improves it, and revert a change that did not help. Neither arm may see the plant's true parameters.

**Table 3.2  Trials to target by case and arm**
| Case | Agent trials | OFAT trials | Agent objective | OFAT objective | Trials saved |
| --- | --- | --- | --- | --- | --- |
| High-protein masala namkeen pellet | 3 | not reached | 0.99 | 0.93 | 4 |
| High-protein high-fibre ragi cookie | 3 | not reached | 0.96 | 0.88 | 4 |
| Reduced-sugar mango fruit spread | 3 | not reached | 0.93 | 0.8 | 4 |

![Figure 3.3  Trials to target, agent against one-factor-at-a-time](bench.png)
*Figure 3.3  Trials to target, agent against one-factor-at-a-time*
![Figure 3.4  Measured objective by trial. The bar is the pass threshold; a trajectory that stops at a cross reached the gate early](traj.png)
*Figure 3.4  Measured objective by trial. The bar is the pass threshold; a trajectory that stops at a cross reached the gate early*
![Figure 3.5  How close the accepted formulation gets to each target](des.png)
*Figure 3.5  How close the accepted formulation gets to each target*
![Figure 3.6  Prediction error by KPI, measured against the analytical spread of each test](acc.png)
*Figure 3.6  Prediction error by KPI, measured against the analytical spread of each test*
Over the seeded cases the agent reached the gate in a mean of 3 trials against 7 for the one-factor-at-a-time arm, saving a mean of 4 trials per case. The objective column shows the second half of the story: the comparison arm's final objective is 0.91 against the agent's 0.92 measured on the plant's true parameters, so the difference is not only speed but accuracy.


## 3.10  A worked case: high-protein masala namkeen pellet
The extruded snack case is the clearest demonstration because the plant's fault and the brief's difficulty pull in different directions. The line's extruder and dryer both run 9 °C below setpoint and the dryer delivers only 96 % of its intended water removal, so version 1 comes back with moisture above specification even though the recipe was designed to hit it - the classic first-trial result that a bench would answer by drying harder and losing the texture.

- Trial 1: moisture lands high at 4.4 % against a 3.0 % target and a predicted 3.2 %, a residual above four sigma. Water activity follows it. Protein, fat and energy are all on target, which is the signature that separates a drying fault from a formulation fault.
- Diagnosis: process temperature below plan (the recorded 106 °C against a 115 °C setpoint), ranked above the alternative of a formulation that is simply too wet.
- Reformulation: the offsets are applied as setpoint corrections first (-9 °C on both the barrel and the dryer), then the residual moisture is addressed by shifting the recipe toward lower free water - a small increase in fibre and a reduction in feed moisture - with a full factorial design over the four factors that moved, because moving four levers together means interactions have to be resolved, not guessed.
- Trial 2 and 3: the objective climbs and the hard targets come into line; the third trial clears the gate with the measured objective above 0.85, and the recipe is within its fibre, sodium and energy claims.
The trial history, the diagnosis and the plan for the case are reproduced in plain text in the application's project report, which is generated from the same database rows as this analysis.


## 3.11  When the brief cannot be satisfied
The dry-mix case is deliberately unsatisfiable: it asks for a fibre level that the category's ingredients, at the protein level the brief also demands, cannot deliver. A generator that returned a recipe anyway would be worse than useless, because the failure would surface two weeks later as a rejected product. The agent reports the conflict instead, naming the target, the achieved value, its desirability, whether the target is hard, and why it cannot be met.

**Table 3.3  Targets no lever can reach in the infeasible brief**
| Target | wanted | achieved | desirability | recommendation |
| --- | --- | --- | --- | --- |
| Dietary fibre | 4 | 10.2 | 0.09 | No lever moves this KPI: it is set by the category, the process envelope or the target set itself. |

![Figure 3.7  The unreachable target in the infeasible brief](conf.png)
*Figure 3.7  The unreachable target in the infeasible brief*
The same machinery answers the question a brand actually asks at that point: which requirement has to move? The response names the target, quantifies how far short it is, and lists the levers that were tried, so the negotiation happens with evidence.


## 3.12  The interface and the record
The application ships with a browser interface: create a product from images and a brief, read the formulation as a table of ingredients with their shares and their contribution, see the process plan with its parameters and critical control points, log a trial, read the analysis and the ranked diagnosis, review the reformulation plan with its dose deltas and DOE, accept it as the next version, and watch the objective, the pass probability and the prediction accuracy move. The same operations are available over a JSON API, so the agent can be driven from a script or from another system.

![Figure 3.8  One turn of the loop, with the record each stage writes](loop.png)
*Figure 3.8  One turn of the loop, with the record each stage writes*

## 3.13  Limitations and next steps
- The plant is simulated, and its physics is deliberately simpler than a real line: no microbiology, no rheology beyond the texture indices, no staling, no packaging permeation. A real deployment would need these, and the diagnosis rules are structured so that a new signature is a new rule rather than a rewrite.
- The ingredient data are literature values. The agent's own accuracy ledger exists precisely to detect where they are wrong for a category - the model-bias signature is the first version of that feedback loop.
- Optimisation is local. Coordinate pattern search with paired moves explores well around a good starting point and is reproducible, but it will not find a structure the slot model cannot express - a different stabiliser system, or an ingredient the category does not allow.
- Sensory acceptance is out of scope. The brief's appearance constraints are measured from photographs, but liking still needs a panel; the trial record has fields for panel scores and the diagnosis reads them, which is the honest boundary of what software can decide here.

# CHAPTER 4

# Hardware and Software Requirements
The application is designed to run on an ordinary laptop with no installation step: Python, the standard library and the two files of the knowledge base are the whole requirement. Pillow and Pygments are used only to render the figures and the code listings in this report; the agent itself does not need them.


## 4.1  Hardware requirements
**Table 4.1  Hardware requirements**
| Component | Minimum | Recommended | Used for this report |
| --- | --- | --- | --- |
| Processor | Dual-core x86-64 | Quad-core x86-64 or Apple silicon | Intel64 Family 6 Model 189 Stepping 1, GenuineIntel |
| Memory | 2 GB | 8 GB | 31.5 GB |
| Free disk space | 200 MB | 1 GB (figures, report, trial record) | 284.5 GB free |
| Display | 1024 x 768 | 1920 x 1080 for the browser interface | 1920 x 1080 |
| Network | Not required | Only if the optional vision API is used | None used |


## 4.2  Software requirements
**Table 4.2  Software requirements**
| Software | Version | Purpose |
| --- | --- | --- |
| Operating system | Windows 11 (build 10.0.26200) | Development and execution platform |
| Python interpreter | 3.14.6 (64-bit) | Runs the whole application; standard library only |
| SQLite (bundled with Python) | 3.50.4 | Products, formulations, trials, diagnoses, plans, ledger |
| Web browser (Chrome / Edge / Firefox) | Microsoft Edge / Google Chrome (Chromium), any recent version | The single-page interface, served from the application itself |
| Pillow | 12.3.0 | Reading uploaded product images and drawing the report figures |
| Pygments | 2.21.0 | Syntax-highlighted code listings for this report |
| Optional vision API key (OpenAI or Gemini) | not configured | Adds a semantic description of the product photographs |

No web framework, no database server and no numerical library are used. That is a deliberate constraint: the system has to be demonstrable on any college or factory machine without an installation, and every model it runs has to be inspectable line by line.


## 4.3  Knowledge base and data files
**Table 4.3  Data files**
| File | Contents | Size |
| --- | --- | --- |
| app/data/ingredients.json | 95 ingredients with composition, cost, water activity, density, allergens, diet flags and functional group | 32 KB |
| app/data/processes.json | 6 process categories with unit operations, parameter ranges and in-process targets | 18 KB |
| app/data/limits.json | Claim thresholds, category additive limits, allergen list and microbial safety rules | 4 KB |
| app/data/formusense.db | SQLite record of every product, version, prediction, trial, analysis, diagnosis, plan and ledger entry (728 KB at the time of writing) |  |
| app/data/figures/*.png | Figures drawn from the record at report time |  |


## 4.4  Running the application
**Listing 4.1  Commands**
```
python run.py                 # start the interface on http://127.0.0.1:8770
python run.py --port 9000    # choose another port
python run.py --open         # start and open the browser
python run.py --seed         # re-seed the demonstration cases and exit
python run.py --benchmark    # run the trial-efficiency benchmark and exit
python run.py --report       # regenerate this report from the record and exit
python -m unittest discover -s tests -t .    # run the test suite
```
The server binds to the loopback interface by default, so the interface is not exposed to the network unless it is asked to be.


# CHAPTER 5

# Coding Screenshots
The listings in this chapter are taken from the source files themselves at report time and drawn with syntax highlighting, so they cannot drift from the code. Each listing is followed by what it is doing and why it is written that way.


## 5.1  Understanding the written specification
![Listing 5.1  app/core/brief.py, function parse_spec_numbers() (lines 199-242 shown, docstring omitted (the function spans 192-253))](code_1_parse_spec_numbers.png)
*Listing 5.1  app/core/brief.py, function parse_spec_numbers() (lines 199-242 shown, docstring omitted (the function spans 192-253))*
This is where a brief stops being prose. Every number is captured with its comparator and its unit, and the two failure modes that matter are handled explicitly: a sentence-ending full stop must not swallow the value ("0.90." is 0.90), and a keyword is only ignored when it is followed by a different unit - so "energy 425 kcal per 100 g" does not lose its energy to the "per 100 g" that describes it.


## 5.2  Composition and energy by mass balance
![Listing 5.2  app/core/nutrition.py, function analyse() (lines 65-108 of 65-253 shown (189 lines in all))](code_2_analyse.png)
*Listing 5.2  app/core/nutrition.py, function analyse() (lines 65-108 of 65-253 shown (189 lines in all))*
The first-principles core. Composition is the sum of the ingredient contributions, moisture is carried separately because the physical models need it, and energy uses the Atwater factors. Nothing here is fitted: if a recipe's energy is wrong, the ingredient table is wrong, and the test suite asserts the closure.


## 5.3  Water activity: the control that decides shelf life
![Listing 5.3  app/core/physical.py, function water_activity() (lines 148-191 of 148-205 shown (58 lines in all))](code_3_water_activity.png)
*Listing 5.3  app/core/physical.py, function water_activity() (lines 148-191 of 148-205 shown (58 lines in all))*
Moisture alone does not predict spoilage; the water that is free to take part in chemistry does. This function separates free from bound water, applies a solute load in whichever regime the product sits in, and returns the value that the stability model, the microbial rules and the claim checks all read. Humectants and drying therefore affect it differently, which is what makes the reformulation advice specific.


## 5.4  Generating the first formulation
![Listing 5.4  app/core/formulate.py, function generate_optimised() (lines 874-894 of the file)](code_4_generate_optimised.png)
*Listing 5.4  app/core/formulate.py, function generate_optimised() (lines 874-894 of the file)*
A seed recipe is generated from the category's slots, then handed to the optimiser and returned with its constraint report. Keeping generation and optimisation as two calls is what allows the interface to show what the brief alone produced, and then what the search made of it - two numbers that are often far apart, and that difference is itself a diagnosis of the brief.


## 5.5  The objective the search is trying to improve
![Listing 5.5  app/core/optimize.py, function score_result() (lines 173-216 of 173-283 shown (111 lines in all))](code_5_score_result.png)
*Listing 5.5  app/core/optimize.py, function score_result() (lines 173-216 of 173-283 shown (111 lines in all))*
One function, and the whole design philosophy of the agent in it: priority-weighted desirability, minus a penalty for drifting away from each slot's intent (weighted down for precision slots such as acidulants and colours), minus a plausibility term that stops a recipe being nudged into a nonsense composition, minus constraint penalties. Nothing is optimised that is not quantified here.


## 5.6  Publishing an answer with an interval, not a number
![Listing 5.6  app/core/uncertainty.py, function interval() (lines 150-166 of the file)](code_6_interval.png)
*Listing 5.6  app/core/uncertainty.py, function interval() (lines 150-166 of the file)*
Three contributions - the base analytical variation of that measurement, an absolute floor so a percentage error cannot vanish on a small value, and an extrapolation factor that widens the interval when the recipe sits outside the calibrated range. The same sigma is what makes a trial result surprising, and what makes a prediction falsifiable.


## 5.7  Ranking the probable causes of a disappointing trial
![Listing 5.7  app/core/diagnose.py, function diagnose() (lines 631-674 of 631-691 shown (61 lines in all))](code_7_diagnose.png)
*Listing 5.7  app/core/diagnose.py, function diagnose() (lines 631-674 of 631-691 shown (61 lines in all))*
Signature rules are evaluated over the residual pattern and the process actuals, each returning a cause, a category, a confidence and the evidence that triggered it, and the result is ranked with the recommended action attached. The model-bias signature is the honest fallback: when the residuals are small, consistently signed and spread across unrelated KPIs, the answer is "the model is slightly wrong here", not an invented process fault.


## 5.8  Correcting the plant before rewriting the recipe
![Listing 5.8  app/core/reformulate.py, function plan_reformulation() (lines 241-284 of 241-423 shown (183 lines in all))](code_8_plan_reformulation.png)
*Listing 5.8  app/core/reformulate.py, function plan_reformulation() (lines 241-284 of 241-423 shown (183 lines in all))*
The heart of the loop. Measured process offsets are applied as setpoint corrections first, so the optimiser is not asked to fix a machine error with a formulation change; only then are the remaining residuals addressed. The plan carries the dose deltas, the pass probability per target, the surrogate's weight and the design of experiments for the next trial.


## 5.9  Running the loop to a target
![Listing 5.9  app/service.py, function closed_loop() (lines 477-520 of 477-547 shown (71 lines in all))](code_9_closed_loop.png)
*Listing 5.9  app/service.py, function closed_loop() (lines 477-520 of 477-547 shown (71 lines in all))*
Create, predict, run a trial, analyse, diagnose, plan, accept, repeat - with the gate that stops it: every measured hard target on target and a measured objective of at least 0.85. The function returns the whole history, which is why the trial counts in this report can be regenerated by anyone who runs it.


## 5.10  The simulated plant
![Listing 5.10  app/plant.py, function run_trial() (lines 158-201 of 158-220 shown (63 lines in all))](code_10_run_trial.png)
*Listing 5.10  app/plant.py, function run_trial() (lines 158-201 of 158-220 shown (63 lines in all))*
The plant applies its true parameters to the recipe - drying efficiency, thermal offsets, acid retention, sugar inversion, sodium carry-over, oxidation - and then adds analytical noise drawn from the published sigma of each KPI. Simulating is what makes a wrong diagnosis a defect that can be fixed rather than a plausible story, and it is what lets the intervals be tested for calibration.


## 5.11  Test suite
The behaviour described in this report is covered by an automated test suite, so a regression in the models, the optimiser, the diagnosis rules or the loop is caught before it reaches a recommendation. The suite covers brief parsing, the physical and nutritional models, formulation generation and constraint checking, the simulated plant, the closed loop, and the HTTP API.

**Listing 5.9  Test suite result**
```
test_a_sentence_final_full_stop_is_not_part_of_the_number (tests.test_brief.SpecParsingTests.test_a_sentence_final_full_stop_is_not_part_of_the_number) ... ok
test_claim_words_do_not_leak_into_each_other (tests.test_brief.SpecParsingTests.test_claim_words_do_not_leak_into_each_other) ... ok
test_pack_weight_is_not_read_as_a_nutrient (tests.test_brief.SpecParsingTests.test_pack_weight_is_not_read_as_a_nutrient) ... ok
test_reads_numbers_written_the_way_indian_specs_write_them (tests.test_brief.SpecParsingTests.test_reads_numbers_written_the_way_indian_specs_write_them) ... ok
test_a_bare_number_is_a_band_and_a_comparator_is_a_limit (tests.test_brief.TargetTests.test_a_bare_number_is_a_band_and_a_comparator_is_a_limit) ... ok
test_a_category_the_spec_does_not_declare_keeps_its_defaults (tests.test_brief.TargetTests.test_a_category_the_spec_does_not_declare_keeps_its_defaults) ... ok
...
test_section_headings_are_numbered_under_the_chapter (tests.test_report.ChapterTests.test_section_headings_are_numbered_under_the_chapter) ... ok
test_table_rows_keep_their_order (tests.test_report.ChapterTests.test_table_rows_keep_their_order) ... ok
test_generate_writes_markdown_from_the_record (tests.test_report.GenerateTests.test_generate_writes_markdown_from_the_record) ... ok
test_docx_is_a_valid_word_package (tests.test_report.WriterTests.test_docx_is_a_valid_word_package) ... ok
test_docx_survives_a_missing_figure (tests.test_report.WriterTests.test_docx_survives_a_missing_figure) ... ok
test_html_is_self_contained (tests.test_report.WriterTests.test_html_is_self_contained) ... ok
test_markdown_carries_the_headings_and_the_tables (tests.test_report.WriterTests.test_markdown_carries_the_headings_and_the_tables) ... ok
test_the_case_studies_are_offered_with_their_plant_story (tests.test_server.CatalogTests.test_the_case_studies_are_offered_with_their_plant_story) ... ok
test_the_catalogue_describes_every_category_and_ingredient (tests.test_server.CatalogTests.test_the_catalogue_describes_every_category_and_ingredient) ... ok
test_a_product_view_carries_everything_the_tabs_need (tests.test_server.HandlerTests.test_a_product_view_carries_everything_the_tabs_need) ... ok
test_a_trial_can_be_run_and_analysed_through_the_api (tests.test_server.HandlerTests.test_a_trial_can_be_run_and_analysed_through_the_api) ... ok
test_an_empty_specification_is_rejected_with_a_reason (tests.test_server.HandlerTests.test_an_empty_specification_is_rejected_with_a_reason) ... ok
test_an_unknown_case_is_a_not_found (tests.test_server.HandlerTests.test_an_unknown_case_is_a_not_found) ... ok
test_creating_a_case_study_returns_a_complete_design (tests.test_server.HandlerTests.test_creating_a_case_study_returns_a_complete_design) ... ok
test_health_reports_what_the_ui_shows_in_the_header (tests.test_server.HandlerTests.test_health_reports_what_the_ui_shows_in_the_header) ... ok
test_products_can_be_listed_and_deleted (tests.test_server.HandlerTests.test_products_can_be_listed_and_deleted) ... ok
test_the_development_report_is_markdown_about_the_product (tests.test_server.HandlerTests.test_the_development_report_is_markdown_about_the_product) ... ok
test_the_process_route_returns_a_batch_sheet (tests.test_server.HandlerTests.test_the_process_route_returns_a_batch_sheet) ... ok
test_a_known_route_matches_with_its_parameters (tests.test_server.RouterTests.test_a_known_route_matches_with_its_parameters) ... ok
test_a_non_integer_identifier_is_a_bad_request (tests.test_server.RouterTests.test_a_non_integer_identifier_is_a_bad_request) ... ok
test_an_unknown_route_returns_nothing (tests.test_server.RouterTests.test_an_unknown_route_returns_nothing) ... ok
test_nested_routes_match (tests.test_server.RouterTests.test_nested_routes_match) ... ok
test_the_wrong_method_does_not_match (tests.test_server.RouterTests.test_the_wrong_method_does_not_match) ... ok
----------------------------------------------------------------------
Ran 89 tests in 108.771s
OK
```

# CHAPTER 6

# Design Database
The record is a single SQLite file. There are two reasons it is relational rather than a folder of JSON: a product development history is a graph of versions, trials, measurements and decisions, and the trial-efficiency numbers the whole problem statement is about must be *derived* from those rows rather than kept by hand, so they cannot drift away from what actually happened.


## 6.1  Schema
**Table 6.1  Tables in formusense.db**
| Table | Columns | Rows at report time | Purpose |
| --- | --- | --- | --- |
| analyses | 5 | 9 | Trial analysis: residuals, deviations, objectives |
| benchmarks | 3 | 1 | Trial-efficiency benchmark runs |
| diagnoses | 5 | 9 | Ranked probable causes for a trial result |
| formulations | 7 | 48 | Every formulation version, immutable |
| ledger | 6 | 81 | Append-only activity log |
| plans | 7 | 6 | Reformulation plans and their designs of experiments |
| predictions | 5 | 16 | What was published for a version before any trial ran |
| products | 6 | 10 | One row per product under development |
| trials | 12 | 27 | Physical trials, with measurements, sensory scores and process actuals |


## 6.2  The version ledger
Every formulation is stored as an immutable version with the source that produced it - generated, reformulated, or accepted from a plan - and every prediction, trial, analysis, diagnosis and plan hangs off a product and a version number. That single structure answers the question a development record exists to answer: what did we believe, what did we make, what came back, what did we change, and why. The relationships are:

- `products 1 - N formulations` in version order, each with its source and label.
- `formulations 1 - 1 predictions` per calibration state, storing the published values, the evaluation against the brief and the conflicts at that moment - not just the numbers, so the record shows what the agent believed at design time rather than what it believes now.
- `trials` reference the formulation version they were made from and carry the measurements, the sensory scores, the batch size and the process actuals.
- `analyses` hang off a trial and hold residuals in sigma units, the systematic direction score, the process deviations and the objective; `diagnoses` hold the ranked causes with their evidence.
- `plans` record the from-version and to-version, the deltas, the expected values with intervals, the pass probability, the surrogate weight, the design of experiments and the rationale, and are marked accepted when they become the next version.
- `benchmarks` stores whole trials-to-target comparisons, so a published efficiency claim can be traced to one run rather than to a conversation.
- `ledger` is an append-only event log of everything the agent did, in order, with the payload that caused it.

## 6.3  Data definition
**Listing 6.1  Schema as stored in app/store.py**
```
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    plant_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS formulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, version)
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    formulation_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    objective REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    formulation_version INTEGER NOT NULL,
    label TEXT,
    measurements_json TEXT NOT NULL,
    sensory_json TEXT,
    process_json TEXT,
    batch_size_kg REAL,
    operator TEXT,
    trial_date TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    trial_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    trial_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    accepted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## 6.4  Data dictionary
**Table 6.2  Key columns**
| Table | Column | Type | Meaning |
| --- | --- | --- | --- |
| analyses | payload_json | TEXT | Residuals in sigma units, systematic direction score, process deviations, objectives |
| benchmarks | payload_json | TEXT | A complete trials-to-target comparison, so an efficiency claim is traceable to one run |
| diagnoses | payload_json | TEXT | Ranked causes with their category, confidence, evidence and recommended action |
| formulations | version | INTEGER | Version number, incremented every time the recipe changes |
| formulations | source | TEXT | What produced this version: generated, reformulated, or accepted from a plan |
| ledger | kind | TEXT | Event type, in order, with the payload that caused it |
| plans | payload_json | TEXT | Dose and parameter deltas, expected values with intervals, pass probability, DOE, rationale |
| predictions | payload_json | TEXT | The published values, the evaluation against every target and the conflicts at that moment |
| products | brief_json | TEXT | The brief as understood: targets, tolerances, priorities, claims, open questions |
| products | plant_json | TEXT | The plant parameters of the line this product is made on |
| trials | formulation_version | INTEGER | The version the batch was made from - the link between belief and measurement |


## 6.5  Derived trial-efficiency KPIs
The numbers quoted in the abstract are computed from the rows above, not stored: trials used is the count of trials for a product; versions used is the count of formulation versions; trials-to-target is the first trial whose measured values put every hard target on target; and prediction accuracy pairs each trial with the prediction that was on record immediately before it, so a prediction can never be rewritten after the measurement is known. That last constraint is the reason the accuracy figures in this report are credible.


## 6.6  Sample rows
**Table 6.3  Sample rows from products**
| id | name | category | created_at |
| --- | --- | --- | --- |
| 1 | High-protein masala namkeen pellet | extruded_snack | 2026-09-26T11:32:35 |
| 2 | High-protein high-fibre ragi cookie | cookie | 2026-09-26T11:32:37 |
| 3 | Reduced-sugar mango fruit spread | spread | 2026-09-26T11:32:39 |
| 4 | High-protein masala namkeen pellet [agent] | extruded_snack | 2026-09-26T11:32:40 |
| 5 | High-protein masala namkeen pellet [OFAT] | extruded_snack | 2026-09-26T11:32:44 |
| 6 | High-protein high-fibre ragi cookie [agent] | cookie | 2026-09-26T11:32:47 |


# CHAPTER 7

# Screenshots
The interface is a single-page application served by the agent itself: it holds no data of its own, and every panel is a rendering of the same record the figures in chapter 3 were drawn from. The screens are: **Products** (the seeded cases with their trial counts and efficiency), **New product** (paste a brief, attach images, read the targets the agent inferred and the questions it could not answer), **Overview** (the accepted recipe against every target, the measured objective by trial, prediction accuracy), **Trial analysis** (residuals in sigma units against the published interval, and the ranked causes with their evidence) and **Reformulation plan** (dose deltas, setpoint corrections, pass probability per target and the design of experiments for the next trial).

What follows is the output the application produces for the worked case in section 3.10, exactly as the report route returns it: the specification as understood, the version history, each trial with its measured values, the diagnosis in the analyst's words, and the efficiency ledger - including the trial count, which is read from the record rather than written down here.

**Listing 7.1  The project report for one product, as printed by the agent**
```
# High-protein masala namkeen pellet [agent]

*Category: Extruded snack | unit weight: 30 g | diet: vegetarian | versions: 3 | trials: 3*

## 1. The brief as understood

High protein masala extruded namkeen pellet for the evening-snack segment.

**Claims to substantiate:** High protein

**Targets:** 7 hard of 11. Parsed by: offline-parser.

| KPI | Target | Tolerance | Direction | Class |
| --- | --- | --- | --- | --- |
| Energy | 425 kcal/100 g | +/- 35 | target | monitored |
| Protein | 15 g/100 g | +/- 1.5 | target | hard |
| Total fat | 8 g/100 g | +/- 1.5 | lower | hard |
| Moisture | 3 % | +/- 1 | target | hard |
| Water activity | 0.35 aw | +/- 0.06 | lower | hard |
| Texture index | 62 index 0-100 | +/- 8 | target | hard |
| Ingredient cost | 185 INR/kg | +/- 20 | lower | hard |
| Dietary fibre | 6 g/100 g | +/- 1.2 | higher | monitored |
| Sodium | 480 mg/100 g | +/- 60 | target | monitored |
| Processability | 74 index 0-100 | +/- 8 | higher | monitored |
| Predicted shelf life | 180 days | +/- 30 | higher | hard |

## 2. The formulation on record

*v3 reformulated after trial analysis - v3, total 100%*

| Ingredient | Role | % w/w |
| --- | --- | --- |
| corn_flour | grain | 41.268 |
| bajra_flour | grain | 20.363 |
| jowar_flour | grain | 0.439 |
| besan | protein | 0.071 |
| defatted_soy_flour | protein | 12.595 |
| moong_flour | protein | 0.03 |
| inulin | fibre | 0.135 |
| rice_bran_oil | fat | 3.439 |
| salt | salt | 0.961 |
| potassium_chloride | salt | 0.056 |
| onion_powder | flavour | 2.797 |
| cocoa_powder | flavour | 0.299 |
| lecithin | emulsifier | 0.236 |
| water | water | 13.844 |
| psyllium_husk | fibre | 0.648 |
| wheat_bran | fibre | 2.821 |

**Process settings:** barrel_temp_c 110, screw_speed_rpm 445.8, feed_moisture_pct 15, dryer_temp_c 140, dryer_time_min 17.32.

## 3. Trials, measurements and residuals

| Trial | Version | On target | Objective | Verdict |
| --- | --- | --- | --- | --- |
| T1 (v1) | v1 | 10/11 | 0.942 | marginal |
| T2 (v2) | v2 | 10/11 | 0.955 | marginal |
| T3 (v3) | v3 | 11/11 | 0.989 | pass |

| KPI | Target | T1 (v1) | T2 (v2) | T3 (v3) | Status |
| --- | --- | --- | --- | --- | --- |
| Energy | 425 kcal/100 g | 426.078 | 427.293 | 434.064 | inside tolerance |
| Protein | 15 g/100 g | 14.647 | 15.047 | 15.06 | inside tolerance |
| Total fat | 8 g/100 g | 8.129 | 8.391 | 8.293 | inside tolerance |
| Carbohydrate | - | 66.705 | 66.62 | 68.035 | - |
| Dietary fibre | 6 g/100 g | 12.274 | 12.243 | 12.349 | inside tolerance |
| Sodium | 480 mg/100 g | 491.679 | 480.283 | 482.975 | inside tolerance |
| Moisture | 3 % | 4.375 | 4.129 | 3.06 | inside tolerance |
| Water activity | 0.35 aw | 0.364 | 0.355 | 0.316 | inside tolerance |
| Texture index | 62 index 0-100 | 65.163 | 65.758 | 65.886 | inside tolerance |
| Indicative hardness | - | 76.067 | 70.401 | 70.255 | - |
| Predicted shelf life | 180 days | 286.278 | 294.408 | 304.734 | inside tolerance |
| Mould / yeast risk | - | 12.459 | 13.05 | 11.335 | - |
| Oxidation risk | - | 12.521 | 11.984 | 12.149 | - |
| Processability | 74 index 0-100 | 76.278 | 75.526 | 75.801 | inside tolerance |
| Ingredient cost | 185 INR/kg | 184.941 | 185.385 | 181.5 | inside tolerance |
| Cost per unit | - | 5.465 | 5.594 | 5.481 | - |

## 4. Diagnosis

**T1 (v1)**

- *Process temperature was below plan, so the product was not dried to specification* (confidence 0.56, category process)
  - The recorded process temperature was 106.0 degC against the planned setting
  - Drying rate falls steeply with temperature, so this alone explains a high moisture result
- *Drying or baking is not removing water at the rate the model assumes* (confidence 0.56, category process)
  - Moisture came in 4.38 % against a predicted 3.15 (+4.2 sigma, high)
  - Water activity came in 0.36 aw against a predicted 0.32 (+1.4 sigma, high)
- *The trial did not run at the planned settings* (confidence 0.43, category process)
  - barrel_temp_c was run at 101.0 against a setpoint of 110.0 (-8.2%)
  - The trial did not run to plan, so its results describe the plant as much as the formulation

**T2 (v2)**

- *Process temperature was below plan, so the product was not dried to specification* (confidence 0.57, category process)
  - The recorded process temperature was 115.0 degC against the planned setting

... 103 further lines trimmed for the printed page.
```

## 7.2  Benchmark run
**Listing 7.2  The trial-efficiency benchmark, as returned by the API**
```

```

## 7.3  The API
Every action in the interface is an HTTP call, which means the agent can also be driven from a script or from another system without the browser.

**Table 7.1  JSON API**
| ['Method and path', 'What it does'] |
| --- |
| ['GET /api/health', 'Service status, knowledge-base summary and record counts'] | ['GET /api/kb', 'Categories, claims, allergen list and the ingredient catalogue'] | ['GET /api/products', 'Every product in the record with its efficiency summary'] | ['POST /api/products', 'Create a product from a brief and optional images; returns the first formulation with its prediction and conflicts'] | ['GET /api/products/{id}', 'The full view: brief, versions, formulation, process, trials, analyses, diagnoses, plans, ledger'] | ['POST /api/products/{id}/predict', 'Re-predict a version, optionally calibrating on the trials so far'] | ['POST /api/products/{id}/trials', 'Log a physical trial of the current version'] | ['POST /api/products/{id}/trials/{trial}/analyse', 'Analyse a trial and return the ranked diagnosis'] | ['POST /api/products/{id}/plan', 'Plan the next formulation and trial'] | ['POST /api/products/{id}/plans/{plan}/accept', 'Accept a plan as the next version'] | ['POST /api/products/{id}/loop', 'Run the closed loop for a trial budget'] | ['GET /api/products/{id}/report', 'The plain-text project report for one product'] | ['GET /api/benchmark', 'The latest trial-efficiency benchmark'] |

**Listing 7.3  Driving the agent from the command line**
```
curl -s http://127.0.0.1:8770/api/health
{"ok": true, "products": 4, "trials": 12, "ingredients": 95, "kpis": 22}

curl -s -X POST http://127.0.0.1:8770/api/products \
  -H "Content-Type: application/json" \
  -d @case.json        # the brief, the category and the plant parameters
{"product_id": 5, "objective": 0.94, "conflicts": []}
```

## 7.4  Screenshots to be added at submission
> **To be supplied:** Insert your own screenshots of the running application here in image format, one per row of the checklist below. The charts in chapter 3 are generated from the record and are current; the browser screenshots are the ones only you can take from your own machine, by starting the application and capturing each screen.
**Table 7.2  Screenshot checklist**
| ['Figure', 'Screen', 'What to capture'] |
| --- |
| ['7.1', 'Products', 'The seeded cases with their trial counts and efficiency'] | ['7.2', 'New product', 'A brief pasted in, the inferred targets, and any open questions'] | ['7.3', 'Formulation', 'The ingredient table with shares, contributions and the process plan'] | ['7.4', 'Trial analysis', 'Residuals against the published intervals and the ranked cause with its evidence'] | ['7.5', 'Reformulation plan', 'Dose deltas, parameter corrections, pass probabilities and the DOE'] | ['7.6', 'Benchmark', 'The trial-efficiency comparison and the conflict demonstration'] |


# CHAPTER 8

# References
- [1] Tiny Dot Foods, *Problem Statement PS-1: AI-Powered Food Product Development Agent*, internship brief, 2026.
- [2] D. C. Montgomery, *Design and Analysis of Experiments*, 10th ed., Wiley, 2019.
- [3] R. H. Myers, D. C. Montgomery and C. M. Anderson-Cook, *Response Surface Methodology: Process and Product Optimization Using Designed Experiments*, 4th ed., Wiley, 2016.
- [4] G. E. P. Box and K. B. Wilson, "On the experimental attainment of optimum conditions", *Journal of the Royal Statistical Society: Series B*, vol. 13, no. 1, pp. 1-45, 1951.
- [5] T. G. Kolda, R. M. Lewis and V. Torczon, "Optimization by direct search: new perspectives on some classical and modern methods", *SIAM Review*, vol. 45, no. 3, pp. 385-482, 2003.
- [6] W. O. Atwater and F. G. Benedict, *Experiments on the Metabolism of Matter and Energy in the Human Body*, U.S. Department of Agriculture, 1899 (Atwater factors for food energy).
- [7] T. P. Labuza, "Sorption phenomena in foods", *Food Technology*, vol. 22, pp. 263-272, 1968.
- [8] G. V. Barbosa-Cánovas, A. J. Fontana, S. J. Schmidt and T. P. Labuza (eds.), *Water Activity in Foods: Fundamentals and Applications*, Blackwell Publishing, 2007.
- [9] T. Ross, "Indices for performance evaluation of predictive models in food microbiology", *Journal of Applied Bacteriology*, vol. 81, pp. 501-508, 1996.
- [10] J. Baranyi and T. A. Roberts, "A dynamic approach to predicting bacterial growth in food", *International Journal of Food Microbiology*, vol. 23, pp. 277-294, 1994.
- [11] Food Safety and Standards Authority of India, *Food Safety and Standards (Food Products Standards and Food Additives) Regulations*, 2011, as amended.
- [12] Food Safety and Standards Authority of India, *Food Safety and Standards (Packaging and Labelling) Regulations*, 2011, as amended (claim thresholds used by the claim checker).
- [13] Codex Alimentarius Commission, *General Standard for Food Additives (CXS 192-1995)*, FAO/WHO.
- [14] International Organization for Standardization, *ISO 4121:2003 Sensory analysis - Guidelines for the use of quantitative response scales*.
- [15] Python Software Foundation, *The Python Standard Library*, Python 3.14 documentation, 2026. https://docs.python.org/3/library/
- [16] D. Richard Hipp, *SQLite Documentation*. https://www.sqlite.org/docs.html
- [17] Ecma International, *ECMA-376: Office Open XML File Formats*, 5th ed., 2021 (the format used to write this report as a Word document).
- [18] S. Seabold and J. Perktold, "statsmodels: econometric and statistical modeling with Python", in *Proceedings of the 9th Python in Science Conference*, 2010 (reference implementation for the ridge regression used by the trial-calibrated surrogate).

## 8.1  Software and tools used
**Table 8.1  Software used**
| Tool | Version | Use |
| --- | --- | --- |
| Python | 3.14.6 (64-bit) | Language and runtime for the whole application |
| SQLite | 3.50.4 | The development record |
| Pillow | 12.3.0 | Image measurement and figure rendering |
| Pygments | 2.21.0 | Syntax highlighting in the code listings |
| Git | not a repository | Version control of the source |

