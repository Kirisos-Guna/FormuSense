/* The in-app user guide: how to use FormuSense, written for a first-time user.
 *
 * The guide owns its prose; app.js owns the wiring of the buttons it emits, and
 * the state it renders from is passed in by the caller. Every figure the guide
 * shows is a value already on screen (the header pills and the benchmark
 * headline), so the guide cannot drift from the record. It is a document, not a
 * second copy of the numbers.
 *
 * Nothing here loads from the network: the page is served from the same origin,
 * offline, like the rest of the interface.
 */
(function () {
  "use strict";

  function esc(value) { return Charts.esc(value); }

  function num(value, digits, fallback) {
    var empty = fallback === undefined ? "-" : fallback;
    if (value === null || value === undefined || value === "") return empty;
    if (typeof value !== "number" || !isFinite(value)) return empty;
    return value.toFixed(digits === undefined ? 2 : digits);
  }

  /* --------------------------------------------------------------- the loop */
  var LOOP = [
    { title: "1. Understand", body: "Your specification text is parsed into numeric targets with tolerances, the category, the diet, the allergens and the claims. Anything the text does not answer becomes an open question for you, listed on the Overview tab." },
    { title: "2. Design", body: "A first formulation is generated against those targets: the category's slots are filled by intent and reconciled to 100 %. You see every ingredient and its share, never a black box." },
    { title: "3. Predict", body: "Every characteristic is predicted with a 95 % interval and the method that produced it. The interval is published before any trial, which is what makes the residuals below checkable." },
    { title: "4. Trial", body: "Run a physical trial on the simulated plant. It makes a batch, measures it with analytical noise, and compares the measured values with the interval published before the batch." },
    { title: "5. Diagnose", body: "Residuals are converted to sigma units and matched against signature rules - a thermal offset, a drying shortfall, acid loss, sodium carry-over. Each probable cause carries evidence and a confidence." },
    { title: "6. Plan", body: "The measured process offset is corrected first, then the recipe is re-optimised under the brief's constraints. You get a recipe delta, a process delta, and a pass probability for the next trial." },
    { title: "7. Accept", body: "Accepting a plan promotes it to the next version and stores its predictions *before* the trial, so the next batch is genuinely a test of a forecast rather than a story told afterwards." },
    { title: "8. Repeat", body: "Loop until every hard target is inside tolerance and the measured objective reaches the pass bar of 0.85. 'Run closed loop' does steps 4-7 for you, up to a trial budget you set." }
  ];

  /* ------------------------------------------------------------- the screens */
  var VIEWS = [
    { nav: "New product", href: "#/new", what: "Start here for your own product.", press: "Upload the R&D product report (.docx, .xlsx, .pdf, .txt or .csv) and the form fills itself, or describe the product the way a brand team would send it - targets, pack size, cost, shelf life - then press **Design product**.", see: "A parsed brief and a first formulation, opening on the Overview tab." },
    { nav: "Products", href: "#/products", what: "Everything on record.", press: "Click a product name to open it.", see: "One row per product: category, versions, trials, the latest predicted objective and when it was created." },
    { nav: "Case studies", href: "#/cases", what: "Four worked examples with a story.", press: "Press **Design against this case** on any card.", see: "A product created end-to-end from a fixed brief and a plant with known faults - the fastest way to see the whole loop." },
    { nav: "Benchmark", href: "#/benchmark", what: "The evidence that the loop works.", press: "Set a trial budget and press **Run benchmark**.", see: "The agent against one-factor-at-a-time on the same cases, same budget, same pass gate - trials to target, trials saved, and the objective scored on the plant's true values." },
    { nav: "Model", href: "#/model", what: "The locally trained acceptance model.", press: "Read it; train or retrain from the command line.", see: "RMSE, ROC-AUC and Brier on the held-out test fold, each next to its baseline, plus the features that drive the objective." },
    { nav: "Knowledge base", href: "#/catalog", what: "The ingredients and the rules behind the design.", press: "Filter the ingredient table by name, group or role.", see: "Categories and their unit operations, the claims the agent can substantiate, the allergens it tracks, and 90+ ingredients with composition, cost, inclusion limits and diet flags." },
    { nav: "How to use", href: "#/guide", what: "This page.", press: "Follow the quickstart below, or jump to the screen you are on.", see: "A five-minute path to a first product, then a reference for every screen and the numbers they show." }
  ];

  var TABS = [
    { tab: "Overview", what: "The brief as understood, next to the formulation on record.", press: "Read the target table, then the recipe below it.", see: "Each KPI with its target, whether it is a hard or a monitored target, the predicted value and its status. Open questions and any target conflicts appear here too." },
    { tab: "Prediction", what: "Everything the agent predicts for the current version.", press: "Nothing - it is already computed. Press **Re-predict** in the header after new trials.", see: "Predicted values with 95 % intervals and the method for each, a desirability bar per target, the objective, and how many hard targets are met." },
    { tab: "Populations", what: "What one serving delivers to each population group.", press: "Nothing - it is computed from the composition.", see: "Protein per serving against the ICMR-NIN 2020 reference for children by age, adult men and women, pregnant and lactating women and adults 60+, with the share of the daily requirement, servings to reach it, and the cautions for that group." },
    { tab: "Trials", what: "What actually happened, batch by batch.", press: "Press **Run physical trial** in the header.", see: "A trajectory of measured objective against the 0.85 pass bar, then per trial the residual table in sigma units, an interval-coverage plot, and the ranked probable causes." },
    { tab: "Plan", what: "The proposed next version and its evidence.", press: "Press **Plan next version**, then review and accept or ignore it.", see: "A pass probability, a recipe delta, a process delta with the measured offset named, the expected performance of the proposed version, and a small design of experiments for the trial." },
    { tab: "Process", what: "How the product would actually be made.", press: "Nothing.", see: "Unit operations with equipment and durations, operating parameters against their validated ranges, in-process targets, critical control points, packaging and expected yield." },
    { tab: "Report", what: "The development report for this product.", press: "Nothing - it is generated from the record.", see: "The brief, design, predictions, trials, diagnosis, changes and efficiency, written from the stored rows rather than retyped." },
    { tab: "Ledger", what: "The audit trail.", press: "Nothing.", see: "Every recorded event with its timestamp. This is what makes an efficiency claim checkable after the fact." },
    { tab: "Ask", what: "Questions answered from this product's own record.", press: "Type a question and press Ask, once a model key is configured.", see: "An answer drawn from the stored brief, formulation, prediction, trials and plan, with the parts of the record it used, and the model that produced it. It cannot change the record: the question and answer only add a ledger line." }
  ];

  var GLOSSARY = [
    { term: "Brief", text: "The written specification, and what the agent understood from it: targets with tolerances, diet, allergens, claims, pack size." },
    { term: "Slot", text: "A role in the recipe for a category - the protein slot, the sweetener slot, and so on. Ingredients compete for slots under their inclusion limits." },
    { term: "KPI", text: "A measurable characteristic of the product: protein, moisture, water activity, pH, sodium, cost, texture." },
    { term: "Hard target", text: "A KPI that must be inside its tolerance for the product to pass. A monitored target is watched and scored but does not block the gate." },
    { term: "Objective", text: "One number that summarises the whole formulation: a priority-weighted geometric mean of how desirable each KPI is. Higher is better, and 1.0 is every target exactly on its ideal." },
    { term: "Desirability", text: "How satisfied one KPI is, from 0 (outside the acceptable region) to 1 (on the ideal). The objective is built from these." },
    { term: "95 % interval", text: "The range the agent publishes before a trial, from the model's uncertainty. A measured value inside it is consistent with the model; outside it is evidence." },
    { term: "Residual / z", text: "Measured minus predicted. Divided by the published sigma it becomes a z-score: above about 2 sigma is worth a look, above 3 sigma is a systematic fault rather than noise." },
    { term: "Pass gate", text: "Every hard target on target *and* a measured objective of at least 0.85. Both arms of the benchmark face the same gate." },
    { term: "Trials to target", text: "How many physical batches were needed to reach the gate. Lower is better; it is the number the whole project is about." },
    { term: "OFAT", text: "One-factor-at-a-time, the comparison arm: change the single most promising factor for the worst target, keep the batch if it improves, revert it if it does not." },
    { term: "%RDA", text: "The share of a population group's daily protein requirement that one serving delivers, against the ICMR-NIN 2020 reference values. Development guidance, not medical advice." },
    { term: "CCP", text: "Critical control point: a step on the line with a critical limit, a way to monitor it, and an action if it is exceeded." },
    { term: "Ledger", text: "The append-only audit trail of everything recorded for a product." },
    { term: "Model layer (optional)", text: "A configured API key lets a model describe the reference photographs, read an uploaded R&D document alongside the rule-based parser, review the specification against what that parser already found, and answer questions about the record. It runs only when the switch on the New product form is ticked, it never sets a target or a cost, and an hourly ceiling plus a reply cache bound what it can spend. Reading a document follows the same contract: the specification text that reaches the parser is the document's own words, a model may only name what the document already says, and the single figure it may transcribe - a declared pack size the parser did not recognise - is labelled as read rather than parsed and waits in the form for you to confirm it. With no key the application is exactly what it is offline: deterministic, reproducible and complete." },
    { term: "Prompt cache", text: "Every model reply is stored against a hash of the request, image bytes included. Asking the same question about the same photograph is answered from the record instead of the provider, so a repeat costs nothing and returns the same words." }
  ];

  /* ------------------------------------------------------------ live facts */
  function facts(state) {
    state = state || {};
    var health = state.health || {};
    var cases = state.cases || [];
    var benchmark = state.benchmark || null;
    var summary = (benchmark && benchmark.summary) || {};
    return {
      products: num(health.products, 0, "0"),
      trials: num(health.trials, 0, "0"),
      ingredients: num(health.ingredients, 0, "90+"),
      categories: num(health.categories, 0, "several"),
      groups: num(health.population_groups, 0, "15"),
      case_count: num(cases.filter(function (c) { return c.feasible; }).length, 0, String(cases.length || 4)),
      has_benchmark: !!benchmark,
      agent_mean: num(summary.agent_mean_trials_to_target, 2),
      ofat_mean: num(summary.ofat_mean_trials_to_target, 2),
      saved: num(summary.mean_trials_saved, 2),
      bench_cases: num(summary.cases, 0),
      agent_wins: num(summary.agent_successes, 0),
      ofat_wins: num(summary.ofat_successes, 0)
    };
  }

  /* -------------------------------------------------------------- sections */
  function quickstart(f, state) {
    var firstProduct = (state.products || [])[0];
    var steps = [
      { title: "Start the app", body: "Run <code>python run.py</code> (or <code>python run.py --open</code>) and open <code>http://127.0.0.1:8770</code>. The header pills confirm the database, the knowledge base and the vision mode as soon as the page answers.", action: null },
      { title: "Load a case study, or describe your own", body: "**Case studies** creates a product from a fixed brief in about a second and is the fastest way to see the whole loop. **New product** takes your own specification text - or the R&D team's own document, which the agent reads and turns into that form, listing what the document does not state. If you would rather not write one at all, press an example brief chip to fill the form from a seeded case.", action: { label: "Open the case studies", href: "#/cases" } },
      { title: "Look at what was understood", body: "The product opens on **Overview**: every target the agent parsed, whether it is hard or monitored, the predicted value and the status. Anything the text could not answer is listed as an open question.", action: { label: "Describe a product", href: "#/new" } },
      { title: "Run a physical trial", body: "Press **Run physical trial**. A batch is made on the simulated plant, measured with analytical noise, and compared with the interval that was published before the batch. You land on the **Trials** tab.", action: firstProduct ? { label: "Open a product", href: "#/product/" + firstProduct.id + "/overview" } : null },
      { title: "Read the diagnosis, then let it plan", body: "The residual table says what went wrong; the probable-cause cards say why, with evidence. Press **Plan next version** to correct the line and rewrite the recipe, then accept the plan to make it the next version.", action: null },
      { title: "Or close the loop in one press", body: "**Run closed loop** repeats trial, diagnosis, plan and accept up to a budget you set, stopping the moment the product passes. Then open **Benchmark** to see the same loop measured against one-factor-at-a-time.", action: { label: "Open the benchmark", href: "#/benchmark" } }
    ];
    var html = ["<ol class='steps'>"];
    steps.forEach(function (step) {
      html.push("<li><div class='step-row'><div class='step-text'><strong>" + step.title + "</strong><p>" + inline(step.body) + "</p></div>" +
        (step.action ? "<a class='cta' href='" + esc(step.action.href) + "'>" + esc(step.action.label) + "</a>" : "") + "</div></li>");
    });
    html.push("</ol>");
    return html.join("");
  }

  /* The guide's own inline formatter: bold and code, and nothing else. It is
   * deliberately smaller than the report renderer in app.js - the guide is
   * written here in one place and does not need headings or tables. */
  function inline(text) {
    return esc(text)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function loopSection() {
    var html = ["<div class='loop-strip'>"];
    LOOP.forEach(function (step, index) {
      html.push("<div class='loop-step'><span class='step-badge'>" + (index + 1) + "</span>" +
        "<strong>" + esc(step.title.replace(/^\d+\.\s*/, "")) + "</strong><p class='small muted'>" + esc(step.body) + "</p></div>");
    });
    html.push("</div>");
    return html.join("");
  }

  function screens(state) {
    var html = ["<h4>Screens</h4><div class='scroll'><table class='compact'><thead><tr><th>Screen</th><th>What it is for</th><th>What to press</th><th>What you will see</th></tr></thead><tbody>"];
    VIEWS.forEach(function (row) {
      html.push("<tr><td class='name'><a href='" + esc(row.href) + "'><strong>" + esc(row.nav) + "</strong></a></td>" +
        "<td class='small'>" + inline(row.what) + "</td>" +
        "<td class='small'>" + inline(row.press) + "</td>" +
        "<td class='small muted'>" + inline(row.see) + "</td></tr>");
    });
    html.push("</tbody></table></div>");
    html.push("<h4>Inside a product</h4><p class='small muted'>A product page has " + TABS.length + " tabs. The header buttons - Re-predict, Run physical trial, Plan next version and Run closed loop - are available from every tab, and the plain-language next step at the top of the page tells you which one it wants next.</p>");
    html.push("<div class='scroll'><table class='compact'><thead><tr><th>Tab</th><th>What it is for</th><th>What to press</th><th>What you will see</th></tr></thead><tbody>");
    TABS.forEach(function (row) {
      html.push("<tr><td class='name'><strong>" + esc(row.tab) + "</strong></td>" +
        "<td class='small'>" + inline(row.what) + "</td>" +
        "<td class='small'>" + inline(row.press) + "</td>" +
        "<td class='small muted'>" + inline(row.see) + "</td></tr>");
    });
    html.push("</tbody></table></div>");
    return html.join("");
  }

  function numbers(f, state) {
    var html = ["<p class='small'>A short reading guide to the figures on screen. Every number is computed server-side from the record; the interface formats what it is given and never fills a gap with a guess.</p>"];
    html.push("<dl class='kv'>" +
      "<dt>Objective</dt><dd>A priority-weighted geometric mean of desirability, from 0 to 1. The pass bar is <strong>0.85</strong>. It rewards balance: one badly missed KPI drags the whole score down.</dd>" +
      "<dt>95 % interval</dt><dd>The range the model expects before the trial. Judge a result against it, not against the target alone: a measured value inside the interval means the model was right, even if the target was missed.</dd>" +
      "<dt>Residual z</dt><dd>How far the measurement fell from the prediction, in units of the published sigma. |z| beyond about 2 is a signal; beyond 3 is a systematic fault, and the diagnosis will name a cause.</dd>" +
      "<dt>Hard vs monitored</dt><dd>Hard targets gate the pass. Monitored targets are scored and shown, but a miss there does not by itself fail the batch.</dd>" +
      "<dt>Protein %RDA</dt><dd>One serving's protein as a share of a population group's daily requirement, against " + "<strong>ICMR-NIN 2020</strong> reference values. It is development guidance, not medical advice, and the Populations tab prints the reference it used next to every percentage.</dd>" +
      "<dt>Trials to target</dt><dd>Physical batches needed to reach the gate. Censored at the trial budget when an arm never gets there - \"not reached\" is a result, not a blank.</dd>" +
      "</dl>");
    if (f.has_benchmark) {
      html.push("<div class='callout good'><strong>The current record</strong><p class='small'>" +
        "The agent has needed <strong>" + f.agent_mean + " trials</strong> on average against <strong>" + f.ofat_mean + "</strong> for one-factor-at-a-time across the " +
        f.bench_cases + " benchmarked product(s) in this database - <strong>" + f.saved + " trials saved per product</strong>, with the agent reaching the gate in " +
        f.agent_wins + " of " + f.bench_cases + " and the comparison arm in " + f.ofat_wins + " of " + f.bench_cases + ".</p>" +
        "<p class='small muted'>These figures are read live from the stored benchmark, so they will change the moment you run a new one. <a href='#/benchmark'>Open the benchmark</a>.</p></div>");
    } else {
      html.push("<div class='callout'><strong>No benchmark in this database yet</strong><p class='small'>Open <strong>Benchmark</strong> and press <em>Run benchmark</em> to produce the comparison, or run <code>python run.py --benchmark</code> from the command line. The headline numbers on this page fill in from the stored record as soon as one exists.</p></div>");
    }
    html.push("<div class='callout'><strong>This record right now</strong><p class='small'>" +
      f.products + " product(s) and " + f.trials + " physical trial(s) stored, over " + f.categories + " categories and " +
      f.ingredients + " ingredients, with " + f.groups + " population groups in the reference set and " + f.case_count +
      " seeded case studies ready to load.</p></div>");
    return html.join("");
  }

  function glossary() {
    var html = ["<dl class='kv glossary'>"];
    GLOSSARY.forEach(function (row) {
      html.push("<dt>" + esc(row.term) + "</dt><dd>" + esc(row.text) + "</dd>");
    });
    html.push("</dl>");
    return html.join("");
  }

  function limits(state) {
    var f = facts(state);
    var html = ["<p class='small'>Where to look when something is not working, and what this app honestly does not do.</p>"];
    html.push("<dl class='kv'>" +
      "<dt>Nothing loads</dt><dd>Check the server is running and look at the terminal it was started from. If the port is taken, start it elsewhere: <code>python run.py --port 9000</code>.</dd>" +
      "<dt>Header pill says \"AI: off\"</dt><dd>No model key is configured, which is the default and changes nothing except the optional descriptions. The offline image analysis needs the optional Pillow install. The rest of the pipeline never needs an API key or the network.</dd>" +
      "<dt>The Model tab is empty</dt><dd>No model has been trained in this database yet. Build the dataset and train it offline: <code>python run.py --build-dataset</code> then <code>python run.py --train</code>.</dd>" +
      "<dt>A brief cannot be satisfied</dt><dd>That is a result, not a bug. The agent reports the target, the best achievable value and the reason instead of quietly missing it.</dd>" +
      "<dt>\"Wrong database\" or a Postgres error</dt><dd>The default is one SQLite file, nothing to install. PostgreSQL is opt-in through <code>FORMUSENSE_DB_URL</code>; migrations for both dialects live in <code>db/migrations/</code>.</dd>" +
      "<dt>What it does not do</dt><dd>It does not replace a pilot plant. Predictions, objectives and probabilities are model output with published intervals; the physical trial is still the test, and the design the agent proposes still has to be run.</dd>" +
      "</dl>");
    html.push("<p class='small muted'>The same guide is kept as a document at <code>docs/USER_GUIDE.md</code> in the repository, and the full design is described in <code>README.md</code>.</p>");
    return html.join("");
  }

  /* ---------------------------------------------------------------- render */
  function render(state) {
    var f = facts(state);
    var toc = [
      { id: "g-quickstart", label: "Five-minute quickstart" },
      { id: "g-loop", label: "The loop, step by step" },
      { id: "g-screens", label: "Every screen" },
      { id: "g-numbers", label: "Reading the numbers" },
      { id: "g-glossary", label: "Glossary" },
      { id: "g-limits", label: "Troubleshooting and limits" }
    ];
    var html = ["<div class='card guide-head'><div class='card-head'><div><h2>How to use FormuSense</h2>" +
      "<p class='card-sub'>From a written brief to a validated formulation in as few physical trials as possible. Read the quickstart once; the rest of the page is a reference you can come back to.</p></div>" +
      "<div class='row'><a class='cta primary' href='#/new'>Describe a product</a><a class='cta' href='#/cases'>Load a case study</a></div></div>" +
      "<ul class='guide-facts'>" +
      "<li><span>" + f.products + "</span> products on record</li>" +
      "<li><span>" + f.trials + "</span> physical trials</li>" +
      "<li><span>" + f.ingredients + "</span> ingredients</li>" +
      "<li><span>" + f.groups + "</span> population groups</li>" +
      "</ul></div>"];

    html.push("<div class='guide-layout'>");
    html.push("<nav class='card tight guide-toc' aria-label='Guide contents'><h4>On this page</h4><ul class='bullets small'>" +
      toc.map(function (item) { return "<li><a href='#" + item.id + "'>" + esc(item.label) + "</a></li>"; }).join("") +
      "</ul></nav>");
    html.push("<div class='guide-body'>");
    html.push("<section class='card guide-section' id='g-quickstart'><h3>Five-minute quickstart</h3>" + quickstart(f, state || {}) + "</section>");
    html.push("<section class='card guide-section' id='g-loop'><h3>The loop, step by step</h3>" + loopSection() + "</section>");
    html.push("<section class='card guide-section' id='g-screens'><h3>Every screen</h3>" + screens(state) + "</section>");
    html.push("<section class='card guide-section' id='g-numbers'><h3>Reading the numbers</h3>" + numbers(f, state) + "</section>");
    html.push("<section class='card guide-section' id='g-glossary'><h3>Glossary</h3>" + glossary() + "</section>");
    html.push("<section class='card guide-section' id='g-limits'><h3>Troubleshooting and limits</h3>" + limits(state) + "</section>");
    html.push("</div></div>");
    return html.join("");
  }

  window.Guide = {
    render: render,
    tabs: TABS.map(function (row) { return row.tab; }),
    views: VIEWS.map(function (row) { return row.nav; })
  };
})();
