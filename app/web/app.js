/* FormuSense UI - one file, no framework, talking to the JSON API.
 *
 * The UI deliberately owns no domain logic. It renders what the service returns:
 * the prediction, the interval, the objective, the diagnosis and the plan are all
 * computed server-side and shown as they are, so what is on screen is what is on
 * record. Where the UI formats a number it says so; where it judges it does not.
 */
(function () {
  "use strict";

  var state = {
    catalog: null,
    cases: [],
    products: [],
    product: null,
    benchmark: null,
    images: [],
    busy: 0
  };

  /* ------------------------------------------------------------------ util */
  function esc(value) { return Charts.esc(value); }

  function num(value, digits) {
    if (value === null || value === undefined || value === "") return "-";
    if (typeof value !== "number") return esc(value);
    if (!isFinite(value)) return "-";
    var d = digits === undefined ? 2 : digits;
    return value.toFixed(d);
  }

  function pct(value, digits) {
    if (value === null || value === undefined) return "-";
    return num(value * 100, digits === undefined ? 0 : digits) + "%";
  }

  function statusTag(status) {
    if (!status) return "";
    return "<span class='tag " + esc(status) + "'>" + esc(status) + "</span>";
  }

  /* Ingredient ids are stable keys; the label is what a development team reads. */
  function ingredientName(id) {
    if (!state.ingredients) {
      state.ingredients = {};
      ((state.catalog || {}).ingredients || []).forEach(function (ing) { state.ingredients[ing.id] = ing; });
    }
    var ingredient = state.ingredients[id];
    return ingredient ? ingredient.name : id;
  }

  function ingredientGroup(id) {
    ingredientName(id);
    var ingredient = state.ingredients[id];
    return ingredient ? ingredient.group : "";
  }

  function tag(text, cls) { return "<span class='tag " + esc(cls || "") + "'>" + esc(text) + "</span>"; }

  function toast(message, bad) {
    var el = document.getElementById("toast");
    el.textContent = message;
    el.className = "toast" + (bad ? " bad" : "");
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.hidden = true; }, bad ? 9000 : 4200);
  }

  function busy(text, hint) {
    state.busy += 1;
    document.getElementById("busy-text").innerHTML = esc(text);
    document.getElementById("busy-hint").innerHTML = esc(hint || "");
    document.getElementById("busy").hidden = false;
  }

  function idle() {
    state.busy = Math.max(0, state.busy - 1);
    if (state.busy === 0) document.getElementById("busy").hidden = true;
  }

  function api(method, path, body) {
    var options = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) {
          var error = new Error(payload.error || ("HTTP " + response.status));
          error.payload = payload;
          throw error;
        }
        return payload;
      });
    });
  }

  function fail(error) {
    var detail = error && error.payload && error.payload.traceback ? error.payload.traceback.join(" | ") : "";
    toast((error && error.message ? error.message : String(error)) + (detail ? " - " + detail : ""), true);
  }

  function withBusy(text, hint, work) {
    busy(text, hint);
    return Promise.resolve()
      .then(work)
      .catch(function (error) { fail(error); })
      .then(function (result) { idle(); return result; });
  }

  /* A very small Markdown renderer: headings, tables, bullets, bold, italics. */
  function markdown(source) {
    var lines = String(source || "").split("\n");
    var out = [];
    var index = 0;
    function inline(text) {
      return esc(text)
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|\s)\*([^*]+)\*/g, "$1<em>$2</em>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
    }
    while (index < lines.length) {
      var line = lines[index];
      if (/^\s*\|/.test(line) && /^\s*\|[-: |]+\|\s*$/.test(lines[index + 1] || "")) {
        var header = line.split("|").slice(1, -1);
        index += 2;
        var rows = [];
        while (index < lines.length && /^\s*\|/.test(lines[index])) {
          rows.push(lines[index].split("|").slice(1, -1));
          index += 1;
        }
        out.push("<div class='scroll'><table class='compact'><thead><tr>" +
          header.map(function (cell) { return "<th>" + inline(cell.trim()) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (row) {
            return "<tr>" + row.map(function (cell) { return "<td>" + inline(cell.trim()) + "</td>"; }).join("") + "</tr>";
          }).join("") + "</tbody></table></div>");
        continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        var items = [];
        while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
          items.push("<li>" + inline(lines[index].replace(/^\s*[-*]\s+/, "")) + "</li>");
          index += 1;
        }
        out.push("<ul class='bullets'>" + items.join("") + "</ul>");
        continue;
      }
      if (/^###\s+/.test(line)) { out.push("<h4>" + inline(line.replace(/^###\s+/, "")) + "</h4>"); index += 1; continue; }
      if (/^##\s+/.test(line)) { out.push("<h3>" + inline(line.replace(/^##\s+/, "")) + "</h3>"); index += 1; continue; }
      if (/^#\s+/.test(line)) { out.push("<h2>" + inline(line.replace(/^#\s+/, "")) + "</h2>"); index += 1; continue; }
      if (/^\s*$/.test(line)) { index += 1; continue; }
      out.push("<p>" + inline(line) + "</p>");
      index += 1;
    }
    return out.join("");
  }

  /* ------------------------------------------------------------- rendering */
  function setStatus(health) {
    document.getElementById("status-products").textContent = health.products + " products, " + health.trials + " trials";
    document.getElementById("status-kb").textContent = health.ingredients + " ingredients, " + health.categories + " categories";
    var vision = health.vision || {};
    var el = document.getElementById("status-vision");
    var online = vision.openai || vision.gemini;
    el.textContent = online ? ("vision: " + (vision.provider || "API")) : "vision: Pillow only";
    el.className = "pill" + (online ? " ok" : "");
  }

  function view() { return document.getElementById("view"); }

  function setActiveNav(name) {
    Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"), function (link) {
      link.className = link.getAttribute("data-nav") === name ? "active" : "";
    });
  }

  /* ------------------------------------------------------------------ new */
  function renderNew(prefill) {
    setActiveNav("new");
    var catalog = state.catalog || { categories: [], claims: [], allergens: [] };
    var caseStudy = prefill && prefill.case ? prefill.case : null;
    var spec = prefill && prefill.spec_text ? prefill.spec_text : "";
    var category = prefill && prefill.category ? prefill.category : (catalog.categories[0] || {}).id;
    var claims = (prefill && prefill.claims) || [];
    var plant = (prefill && prefill.plant) || {};
    var html = [];
    html.push("<div class='grid two'>");
    html.push("<div class='card'>");
    html.push("<h2>Describe the target product</h2>");
    html.push("<p class='card-sub'>Write the specification the way a brand team would send it: targets, claims, pack size, cost, shelf life. The agent parses the numbers out of the text and shows you what it understood before anything is formulated.</p>");
    html.push("<label class='field'><span>Product name</span><input type='text' id='f-name' value='" + esc(prefill && prefill.product_name || "") + "' placeholder='e.g. High-protein ragi cookie'></label>");
    html.push("<label class='field'><span>Specification text</span><textarea id='f-spec' placeholder='High protein masala extruded namkeen. 30 g pack. Protein 15 g per 100 g. Moisture 3%. Sodium 480 mg per 100 g. Shelf life 6 months. Ingredient cost not more than INR 185 per kg.'>" + esc(spec) + "</textarea></label>");
    html.push("<div class='inline'>");
    html.push("<label class='field'><span>Category</span><select id='f-category'>" +
      catalog.categories.map(function (c) {
        return "<option value='" + esc(c.id) + "'" + (c.id === category ? " selected" : "") + ">" + esc(c.label) + "</option>";
      }).join("") + "</select></label>");
    html.push("<label class='field'><span>Diet</span><select id='f-diet'>" +
      ["vegetarian", "vegan", "any"].map(function (d) { return "<option>" + d + "</option>"; }).join("") +
      "</select></label>");
    html.push("<label class='field'><span>Unit weight (g)</span><input type='number' id='f-unit' step='1' value='" + esc(prefill && prefill.unit_weight_g || "") + "' placeholder='from the spec text'></label>");
    html.push("</div>");
    html.push("<div class='field'><span>Claims to substantiate</span><div class='checks' id='f-claims'>" +
      (catalog.claims || []).map(function (claim) {
        return "<label><input type='checkbox' value='" + esc(claim.id) + "'" + (claims.indexOf(claim.id) >= 0 ? " checked" : "") + ">" + esc(claim.label || claim.id) + "</label>";
      }).join("") + "</div></div>");
    html.push("<div class='field'><span>Allergens to avoid</span><div class='checks' id='f-allergens'>" +
      Object.keys(catalog.allergens || {}).map(function (id) {
        return "<label><input type='checkbox' value='" + esc(id) + "'>" + esc(catalog.allergens[id]) + "</label>";
      }).join("") + "</div></div>");
    html.push("<div class='field'><span>Reference images (optional)</span><div class='file-drop' id='drop'>Drop product photos here, or click to choose. Offline Pillow analysis always runs; add an OpenAI or Gemini key for the semantic layer.</div><input type='file' id='files' accept='image/*' multiple hidden><div class='thumbs' id='thumbs'></div></div>");
    html.push("<div class='row end'><button class='ghost' id='btn-clear'>Clear</button><button class='primary' id='btn-design'>Design product</button></div>");
    html.push("</div>");

    html.push("<div class='stack'>");
    html.push("<div class='card'><h3>Manufacturing plant</h3><p class='card-sub'>A formulation is designed for a plant. These are the deviations the agent has to plan around - the same knobs the simulated pilot plant uses for the physical trials.</p>" +
      plantFields(plant) + "</div>");
    html.push("<div class='card'><h3>What happens next</h3><ol class='bullets small'>" +
      "<li><strong>Understand:</strong> the specification is parsed into targets, claims, diet and allergens, with the questions that remain open listed for you.</li>" +
      "<li><strong>Design:</strong> a first formulation is generated against those targets and optimised against the models (mass balance, water activity, pH, texture, cost, stability).</li>" +
      "<li><strong>Predict:</strong> every characteristic is predicted with an interval and the method that produced it.</li>" +
      "<li><strong>Trial:</strong> run the physical trial, analyse the residuals against the published intervals, diagnose the probable cause, and reformulate for the next trial.</li>" +
      "</ol></div>");
    if (caseStudy) {
      html.push("<div class='callout'><strong>Loaded case study: " + esc(caseStudy.name) + "</strong><p class='small'>" + esc(caseStudy.narrative || "") + "</p></div>");
    }
    html.push("</div></div>");

    view().innerHTML = html.join("");
    wireNew();
  }

  function plantFields(plant) {
    var presets = [
      { id: "", label: "Standard pilot plant (dryer 94%, oven 5 degC under setpoint)", value: {} },
      { id: "ideal", label: "Perfectly commissioned line (no deviations)", value: { drying_efficiency: 1.0, temp_offset_c: 0, acid_retention: 1.0, sodium_carry: 1.0, sugar_inversion: 1.0 } },
      { id: "rough", label: "Badly commissioned line (dryer 82%, 9 degC under, acid loss)", value: { drying_efficiency: 0.82, temp_offset_c: -9, acid_retention: 0.72, sodium_carry: 1.08, sugar_inversion: 1.06 } }
    ];
    var html = [];
    html.push("<label class='field'><span>Preset</span><select id='p-preset'>" +
      presets.map(function (preset, index) {
        return "<option value='" + index + "'>" + esc(preset.label) + "</option>";
      }).join("") + "</select></label>");
    html.push("<div class='inline'>");
    html.push("<label class='field'><span>Drying duty delivered</span><input type='number' id='p-drying' step='0.01' min='0.5' max='1.1' value='" + (plant.drying_efficiency !== undefined ? plant.drying_efficiency : 0.94) + "'></label>");
    html.push("<label class='field'><span>Temperature offset (degC)</span><input type='number' id='p-temp' step='0.5' min='-20' max='20' value='" + (plant.temp_offset_c !== undefined ? plant.temp_offset_c : -5) + "'></label>");
    html.push("</div><div class='inline'>");
    html.push("<label class='field'><span>Acid retention</span><input type='number' id='p-acid' step='0.01' min='0.2' max='1.1' value='" + (plant.acid_retention !== undefined ? plant.acid_retention : 1.0) + "'></label>");
    html.push("<label class='field'><span>Minor scale carry</span><input type='number' id='p-sodium' step='0.01' min='0.8' max='1.3' value='" + (plant.sodium_carry !== undefined ? plant.sodium_carry : 1.0) + "'></label>");
    html.push("</div><div class='inline'>");
    html.push("<label class='field'><span>Extra sugar inversion</span><input type='number' id='p-inversion' step='0.01' min='0.8' max='1.3' value='" + (plant.sugar_inversion !== undefined ? plant.sugar_inversion : 1.0) + "'></label>");
    html.push("<label class='field'><span>Lab noise scale</span><input type='number' id='p-noise' step='0.05' min='0.2' max='3' value='" + (plant.noise_scale !== undefined ? plant.noise_scale : 1.15) + "'></label>");
    html.push("</div>");
    html.push("<input type='hidden' id='p-name' value='" + esc(plant.name || "Pilot plant (default)") + "'>");
    return html.join("");
  }

  function wireNew() {
    var files = document.getElementById("files");
    var drop = document.getElementById("drop");
    function addFiles(list) {
      Array.prototype.forEach.call(list, function (file) {
        if (!/^image\//.test(file.type)) return;
        var reader = new FileReader();
        reader.onload = function () {
          state.images.push({ name: file.name, data_url: reader.result });
          drawThumbs();
        };
        reader.readAsDataURL(file);
      });
    }
    drop.addEventListener("click", function () { files.click(); });
    drop.addEventListener("dragover", function (event) { event.preventDefault(); drop.classList.add("hot"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("hot"); });
    drop.addEventListener("drop", function (event) {
      event.preventDefault();
      drop.classList.remove("hot");
      addFiles(event.dataTransfer.files);
    });
    files.addEventListener("change", function () { addFiles(files.files); });
    drawThumbs();

    var preset = document.getElementById("p-preset");
    preset.addEventListener("change", function () {
      var values = [null, { drying_efficiency: 1.0, temp_offset_c: 0, acid_retention: 1.0, sodium_carry: 1.0, sugar_inversion: 1.0 },
        { drying_efficiency: 0.82, temp_offset_c: -9, acid_retention: 0.72, sodium_carry: 1.08, sugar_inversion: 1.06 }][Number(preset.value)];
      if (!values) return;
      document.getElementById("p-drying").value = values.drying_efficiency;
      document.getElementById("p-temp").value = values.temp_offset_c;
      document.getElementById("p-acid").value = values.acid_retention;
      document.getElementById("p-sodium").value = values.sodium_carry;
      document.getElementById("p-inversion").value = values.sugar_inversion;
    });
    document.getElementById("btn-clear").addEventListener("click", function () {
      state.images = [];
      location.hash = "#/new";
    });
    document.getElementById("btn-design").addEventListener("click", function () {
      designProduct(collectForm());
    });
  }

  function drawThumbs() {
    var holder = document.getElementById("thumbs");
    if (!holder) return;
    holder.innerHTML = state.images.map(function (image, index) {
      return "<figure><img src='" + image.data_url + "' alt='" + esc(image.name) + "'><figcaption class='small muted'>" +
        esc(image.name.slice(0, 18)) + "<br><a href='#' data-remove='" + index + "'>remove</a></figcaption></figure>";
    }).join("");
    Array.prototype.forEach.call(holder.querySelectorAll("[data-remove]"), function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        state.images.splice(Number(link.getAttribute("data-remove")), 1);
        drawThumbs();
      });
    });
  }

  function collectForm() {
    function checked(id) {
      return Array.prototype.slice.call(document.querySelectorAll("#" + id + " input:checked")).map(function (input) { return input.value; });
    }
    var unit = document.getElementById("f-unit").value;
    return {
      product_name: document.getElementById("f-name").value.trim(),
      spec_text: document.getElementById("f-spec").value.trim(),
      category: document.getElementById("f-category").value,
      diet: document.getElementById("f-diet").value,
      claims: checked("f-claims"),
      allergens_to_avoid: checked("f-allergens"),
      unit_weight_g: unit ? Number(unit) : null,
      images: state.images.slice(),
      plant: {
        name: document.getElementById("p-name").value,
        drying_efficiency: Number(document.getElementById("p-drying").value),
        temp_offset_c: Number(document.getElementById("p-temp").value),
        acid_retention: Number(document.getElementById("p-acid").value),
        sodium_carry: Number(document.getElementById("p-sodium").value),
        sugar_inversion: Number(document.getElementById("p-inversion").value),
        noise_scale: Number(document.getElementById("p-noise").value)
      }
    };
  }

  function designProduct(payload) {
    withBusy("Understanding the brief and designing v1", "Parsing the specification, generating a formulation, optimising against the models and predicting every characteristic.", function () {
      return api("POST", "/api/products", payload).then(function (result) {
        state.images = [];
        return refreshProducts().then(function () {
          location.hash = "#/product/" + result.product_id + "/overview";
          toast("v1 designed: predicted objective " + num(result.evaluation.objective, 3) +
            (result.conflicts.length ? " (" + result.conflicts.length + " target(s) flagged)" : ""));
        });
      });
    });
  }

  /* ------------------------------------------------------------- products */
  function refreshProducts() {
    return api("GET", "/api/products").then(function (payload) {
      state.products = payload.products;
    });
  }

  function renderProducts() {
    setActiveNav("products");
    var html = ["<div class='card'><div class='card-head'><h2>Products</h2><span class='card-sub'>" +
      state.products.length + " product(s) on record</span></div>"];
    if (!state.products.length) {
      html.push("<p class='muted'>Nothing yet. <a href='#/cases'>Load a case study</a> or <a href='#/new'>describe a product</a>.</p>");
    } else {
      html.push("<div class='scroll'><table><thead><tr><th>Product</th><th>Category</th><th class='num'>Versions</th><th class='num'>Trials</th><th class='num'>Predicted objective</th><th class='nowrap'>Created</th><th></th></tr></thead><tbody>");
      state.products.forEach(function (product) {
        html.push("<tr><td class='name'><a href='#/product/" + product.id + "/overview'><strong>" + esc(product.name) + "</strong></a>" +
          (product.plant ? "<br><span class='small muted'>" + esc(product.plant) + "</span>" : "") + "</td>" +
          "<td>" + esc(product.category) + "</td>" +
          "<td class='num'>" + product.versions + "</td>" +
          "<td class='num'>" + product.trials + "</td>" +
          "<td class='num'>" + num(product.predicted_objective, 3) + "</td>" +
          "<td class='small muted nowrap'>" + esc((product.created_at || "").replace("T", " ").slice(0, 16)) + "</td>" +
          "<td><button class='small ghost' data-delete='" + product.id + "'>delete</button></td></tr>");
      });
      html.push("</tbody></table></div>");
    }
    html.push("</div>");
    view().innerHTML = html.join("");
    Array.prototype.forEach.call(view().querySelectorAll("[data-delete]"), function (button) {
      button.addEventListener("click", function () {
        var id = Number(button.getAttribute("data-delete"));
        if (!confirm("Delete product " + id + " and its full record?")) return;
        withBusy("Deleting", "", function () {
          return api("DELETE", "/api/products/" + id).then(function () {
            refreshProducts().then(renderProducts);
          });
        });
      });
    });
  }

  /* --------------------------------------------------------------- cases */
  function renderCases() {
    setActiveNav("cases");
    var html = ["<div class='card'><h2>Seeded case studies</h2><p class='card-sub'>Three Indian snack products, each with the commissioning deviations of the line that makes it. The deviations are what make the loop necessary: a first formulation designed on the models lands outside tolerance on a plant that runs 9 degC cold or a kettle that will not boil off what the recipe assumes. The fourth case is a brief that cannot be satisfied as written - the agent is expected to say so, with numbers.</p></div>"];
    html.push("<div class='grid halves'>");
    state.cases.forEach(function (item) {
      html.push("<div class='card'><div class='card-head'><h3>" + esc(item.name) + "</h3>" + tag(item.feasible ? "feasible" : "conflicting", item.feasible ? "pass" : "fail") + "</div>" +
        "<p class='small'><strong>Category:</strong> " + esc(item.category) + "</p>" +
        "<p class='small muted'>" + esc(item.narrative) + "</p>" +
        "<details><summary class='small'>Specification and plant settings</summary><pre class='code small'>" + esc(item.payload.spec_text) + "\n\nplant " + esc(JSON.stringify(item.plant, null, 1)) + "</pre></details>" +
        "<div class='row end'><button class='primary' data-case='" + esc(item.key) + "'>Design against this case</button></div></div>");
    });
    html.push("</div>");
    view().innerHTML = html.join("");
    Array.prototype.forEach.call(view().querySelectorAll("[data-case]"), function (button) {
      button.addEventListener("click", function () {
        var key = button.getAttribute("data-case");
        withBusy("Loading case study", "Designing the first formulation for " + key + ".", function () {
          return api("POST", "/api/cases/" + key + "/create", {}).then(function (result) {
            return refreshProducts().then(function () {
              location.hash = "#/product/" + result.product_id + "/overview";
              toast("Case loaded: v1 predicted objective " + num(result.evaluation.objective, 3));
            });
          });
        });
      });
    });
  }

  /* ------------------------------------------------------------- benchmark */
  function renderBenchmark() {
    setActiveNav("benchmark");
    var report = state.benchmark;
    var html = ["<div class='card'><div class='card-head'><h2>Trial-efficiency benchmark</h2>" +
      "<div class='row'><label class='field' style='margin:0'><span>Budget (trials per product)</span><input type='number' id='b-trials' min='1' max='10' value='6' style='width:80px'></label>" +
      "<button class='primary' id='b-run'>Run benchmark</button></div></div>" +
      "<p class='card-sub'>Both arms start from the same first formulation, on the same plant, with the same measurement model and the same success gate: every hard target inside its declared tolerance and a measured objective of at least 0.85. The agent diagnoses the residual, corrects a measured process offset, fits a residual model and reformulates with a constrained optimiser. The comparison arm is one-factor-at-a-time, the way a development kitchen works without a diagnosis engine: change the single most promising factor for the worst target, keep it if the batch improves, revert it if it does not. Trials to target is censored at the budget for an arm that never gets there.</p></div>"];
    if (!report) {
      html.push("<div class='card'><p class='muted'>No benchmark has been run in this database yet.</p></div>");
    } else {
      var summary = report.summary || {};
      html.push("<div class='grid halves'>");
      html.push("<div class='card'><h3>Headline</h3><dl class='kv'>" +
        "<dt>Products</dt><dd>" + num(summary.cases, 0) + "</dd>" +
        "<dt>Agent</dt><dd>" + num(summary.agent_mean_trials_to_target) + " trials on average, " + num(summary.agent_successes, 0) + " of " + num(summary.cases, 0) + " reached target</dd>" +
        "<dt>One-factor-at-a-time</dt><dd>" + num(summary.ofat_mean_trials_to_target) + " trials on average (censored), " + num(summary.ofat_successes, 0) + " of " + num(summary.cases, 0) + " reached target</dd>" +
        "<dt>Trials saved</dt><dd><strong>" + num(summary.mean_trials_saved) + " per product</strong> (" + num(summary.trials_saved_total, 0) + " in total)</dd>" +
        "</dl></div>");
      html.push("<div class='card'><h3>Trials to target</h3>" + Charts.pairBars((report.comparison || []).map(function (row) {
        return {
          label: row.case.length > 26 ? row.case.slice(0, 25) + "." : row.case,
          a: row.agent_trials,
          b: row.ofat_trials
        };
      }), { xLabel: "physical trials needed to reach the target (lower is better)" }) +
        "<div class='legend'><span><i style='background:#1f7a4d'></i>agent</span><span><i style='background:#9aa8a0'></i>one-factor-at-a-time</span></div></div>");
      html.push("</div>");
      html.push("<div class='card'><h3>Per product</h3><div class='scroll'><table><thead><tr><th>Product</th><th class='num'>Agent</th><th class='num'>OFAT</th><th class='num'>Saved</th><th class='num'>Agent objective (true)</th><th class='num'>OFAT objective (true)</th></tr></thead><tbody>" +
        (report.comparison || []).map(function (row) {
          return "<tr><td>" + esc(row.case) + "</td>" +
            "<td class='num'>" + (row.agent_trials === null ? "not reached" : row.agent_trials) + "</td>" +
            "<td class='num'>" + (row.ofat_trials === null ? "not reached" : row.ofat_trials) + "</td>" +
            "<td class='num'>" + row.trials_saved + "</td>" +
            "<td class='num'>" + num(row.agent_truth_objective, 3) + "</td>" +
            "<td class='num'>" + num(row.ofat_truth_objective, 3) + "</td></tr>";
        }).join("") + "</tbody></table></div><p class='small muted'>The objective column is scored on the plant's true values at the end of the run, which removes measurement luck from the comparison.</p></div>");
      html.push("<div class='card'><h3>Arm by arm</h3><div class='scroll'><table class='compact'><thead><tr><th>Product</th><th>Arm</th><th class='num'>Trials</th><th>Measured objective by trial</th><th class='num'>Final truth objective</th></tr></thead><tbody>" +
        (report.arms || []).map(function (arm) {
          return "<tr><td>" + esc(arm.case) + "</td><td>" + esc(arm.arm) + "</td><td class='num'>" + arm.trials_used + "</td>" +
            "<td class='mono small'>" + (arm.objective_history || []).join(" &rarr; ") + "</td>" +
            "<td class='num'>" + num(arm.final_truth_objective, 3) + "</td></tr>";
        }).join("") + "</tbody></table></div></div>");
      if (report.conflict_demonstration) {
        var demo = report.conflict_demonstration;
        html.push("<div class='card'><h3>When the brief cannot be met</h3><p class='card-sub'>A deliberately inconsistent brief, run through the same understanding step: the agent reports the conflict instead of quietly missing it.</p>" +
          (demo.conflicts || []).map(function (conflict) {
            return "<div class='callout warn'><strong>" + esc(conflict.label || conflict.kpi) + "</strong><p class='small'>Target " + num(conflict.target) + " " + esc(conflict.unit || "") + ", best achievable " + num(conflict.achieved) + " &middot; " + esc(conflict.recommendation || "") + "</p></div>";
          }).join("") +
          "<p class='small muted'>Predicted objective for this brief: " + num(demo.objective, 3) + "</p></div>");
      }
    }
    view().innerHTML = html.join("");
    var run = document.getElementById("b-run");
    if (run) {
      run.addEventListener("click", function () {
        var budget = Number(document.getElementById("b-trials").value) || 6;
        withBusy("Running the benchmark", "Two arms, three products, up to " + budget + " physical trials each. This takes about twenty seconds.", function () {
          return api("POST", "/api/benchmark", { max_trials: budget, include_conflict: true }).then(function (payload) {
            state.benchmark = payload.benchmark;
            renderBenchmark();
            toast("Benchmark complete: " + num(payload.benchmark.summary.mean_trials_saved) + " trials saved per product");
          });
        });
      });
    }
  }

  /* --------------------------------------------------------------- catalog */
  function renderCatalog() {
    setActiveNav("catalog");
    var catalog = state.catalog;
    if (!catalog) { view().innerHTML = "<div class='card'>Loading the knowledge base...</div>"; return; }
    var html = ["<div class='grid halves'>"];
    html.push("<div class='card'><h3>Product categories</h3><div class='scroll'><table class='compact'><thead><tr><th>Category</th><th class='num'>Slots</th><th class='num'>Process parameters</th><th>Unit operations</th></tr></thead><tbody>" +
      catalog.categories.map(function (category) {
        return "<tr><td><strong>" + esc(category.label) + "</strong><br><span class='small muted'>typical moisture " +
          num(category.typical_moisture_pct[0], 0) + "-" + num(category.typical_moisture_pct[1], 0) + "%, aw " +
          num(category.typical_aw[0], 2) + "-" + num(category.typical_aw[1], 2) + "</span></td>" +
          "<td class='num'>" + category.slots.length + "</td>" +
          "<td class='num'>" + category.parameters.length + "</td>" +
          "<td class='small'>" + esc(category.unit_operations.join(", ")) + "</td></tr>";
      }).join("") + "</tbody></table></div></div>");
    html.push("<div class='card'><h3>Claims the agent can substantiate</h3><div class='scroll'><table class='compact'><thead><tr><th>Claim</th><th>Rule</th></tr></thead><tbody>" +
      (catalog.claims || []).map(function (claim) {
        return "<tr><td><strong>" + esc(claim.label || claim.id) + "</strong></td><td class='small'>" + esc(claim.rule || claim.note || claim.regulation || "") + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      "<h4>Allergens tracked</h4><p class='small'>" + Object.keys(catalog.allergens).map(function (id) {
        return tag(catalog.allergens[id], "monitored");
      }).join(" ") + "</p></div>");
    html.push("</div>");
    html.push("<div class='card'><div class='card-head'><h3>Ingredient database</h3><span class='card-sub'>" + catalog.ingredients.length + " ingredients with composition, cost, inclusion limits and diet flags</span></div>" +
      "<label class='field'><span>Filter</span><input type='text' id='kb-filter' placeholder='name, group or role'></label>" +
      "<div class='scroll' style='max-height:520px'><table class='compact' id='kb-table'><thead><tr><th>Ingredient</th><th>Group</th><th class='num'>kcal</th><th class='num'>Protein</th><th class='num'>Fat</th><th class='num'>Sugar</th><th class='num'>Fibre</th><th class='num'>Na (mg)</th><th class='num'>Cost INR/kg</th><th class='num'>Min %</th><th class='num'>Max %</th><th>Diet</th><th>Allergens</th></tr></thead><tbody>" +
      catalog.ingredients.map(function (ing) {
        return "<tr data-search='" + esc((ing.name + " " + ing.group).toLowerCase()) + "'><td>" + esc(ing.name) + "</td><td>" + esc(ing.group) + "</td>" +
          "<td class='num'>" + num(ing.kcal0 || 0, 0) + "</td>" +
          "<td class='num'>" + num(ing.protein_g, 1) + "</td><td class='num'>" + num(ing.fat_g, 1) + "</td>" +
          "<td class='num'>" + num(ing.sugar_g, 1) + "</td><td class='num'>" + num(ing.fibre_g, 1) + "</td>" +
          "<td class='num'>" + num(ing.sodium_mg, 0) + "</td><td class='num'>" + num(ing.cost_inr_kg, 0) + "</td>" +
          "<td class='num'>" + num(ing.min_pct, 1) + "</td><td class='num'>" + num(ing.max_pct, 1) + "</td>" +
          "<td>" + esc(ing.diet) + "</td><td class='small'>" + esc((ing.allergens || []).join(", ")) + "</td></tr>";
      }).join("") + "</tbody></table></div></div>");
    view().innerHTML = html.join("");
    var filter = document.getElementById("kb-filter");
    filter.addEventListener("input", function () {
      var needle = filter.value.toLowerCase();
      Array.prototype.forEach.call(document.querySelectorAll("#kb-table tbody tr"), function (row) {
        row.hidden = needle.length > 0 && row.getAttribute("data-search").indexOf(needle) < 0;
      });
    });
  }

  /* --------------------------------------------------------------- product */
  var TABS = ["overview", "prediction", "trials", "plan", "process", "report", "ledger"];

  function renderProduct(id, tab) {
    tab = TABS.indexOf(tab) >= 0 ? tab : "overview";
    var product = state.product;
    if (!product || product.product.id !== id) {
      view().innerHTML = "<div class='card'>Loading product " + id + "...</div>";
      return;
    }
    setActiveNav("products");
    var brief = product.brief;
    var latest = product.formulation;
    var record = latest ? (product.predictions_by_version || {})[String(latest.version)] : null;
    var efficiency = product.efficiency || {};
    var html = [];

    html.push("<div class='card'><div class='card-head'><div><h2>" + esc(brief.product_name) + "</h2>" +
      "<p class='card-sub'>" + esc(brief.category_label) + " &middot; " + num(brief.unit_weight_g, 0) + " g unit &middot; " + esc(brief.diet) +
      (brief.claims.length ? " &middot; claims: " + esc(brief.claims.join(", ")) : "") + "</p></div>" +
      "<div class='row'>" +
      "<button data-action='predict'>Re-predict</button>" +
      "<button data-action='trial'>Run physical trial</button>" +
      "<button data-action='plan'>Plan next version</button>" +
      "<button class='primary' data-action='loop'>Run closed loop</button>" +
      "</div></div>");
    html.push("<dl class='kv' style='margin-top:.6rem'>" +
      "<dt>Plant</dt><dd>" + esc((product.product.plant || {}).name || "default") + "</dd>" +
      "<dt>Versions / trials</dt><dd>" + num(efficiency.versions_used, 0) + " versions, " + num(efficiency.trials_used, 0) + " physical trials</dd>" +
      "<dt>Trials to target</dt><dd>" + (efficiency.trials_to_target ? ("<strong>" + efficiency.trials_to_target + "</strong>") : "<span class='muted'>not reached yet</span>") + "</dd>" +
      "<dt>Predicted objective</dt><dd>" + (record ? num(record.objective, 3) : "-") + " <span class='muted small'>(priority-weighted geometric mean of desirability)</span></dd>" +
      "</dl></div>");

    html.push("<div class='tabs'>" + TABS.map(function (name) {
      return "<button data-tab='" + name + "'" + (name === tab ? " class='active'" : "") + ">" + name.charAt(0).toUpperCase() + name.slice(1) + "</button>";
    }).join("") + "</div>");
    html.push("<div id='tab-body'></div>");
    view().innerHTML = html.join("");
    document.getElementById("tab-body").innerHTML = tabBody(tab, product, record);
    wireProduct(id, tab);
  }

  function tabBody(tab, product, record) {
    if (tab === "overview") return tabOverview(product, record);
    if (tab === "prediction") return tabPrediction(product, record);
    if (tab === "trials") return tabTrials(product);
    if (tab === "plan") return tabPlan(product);
    if (tab === "process") return tabProcess(product);
    if (tab === "report") return tabReport(product);
    if (tab === "ledger") return tabLedger(product);
    return "";
  }

  function tabOverview(product, record) {
    var brief = product.brief;
    var html = ["<div class='grid two'>"];
    html.push("<div class='card'><h3>The brief as understood</h3>");
    html.push("<p class='small'>" + esc(brief.description) + "</p>");
    html.push("<div class='scroll'><table class='compact'><thead><tr><th>KPI</th><th class='num'>Target</th><th>Direction</th><th>Class</th><th>Predicted</th><th>Status</th></tr></thead><tbody>");
    var byId = {};
    ((record || {}).predictions || []).forEach(function (row) { byId[row.id] = row; });
    (brief.targets || []).forEach(function (target) {
      var row = byId[target.id] || {};
      html.push("<tr><td>" + esc(target.label) + "</td>" +
        "<td class='num'>" + num(target.target, target.id === "water_activity" || target.id === "ph" ? 2 : 1) + " <span class='muted small'>" + esc(target.unit) + "</span></td>" +
        "<td>" + esc(target.direction) + "</td>" +
        "<td>" + tag(target.hard ? "hard" : "monitored", target.hard ? "hard" : "monitored") + "</td>" +
        "<td class='num'>" + num(row.value, 3) + "</td>" +
        "<td>" + statusTag(row.status) + "</td></tr>");
    });
    html.push("</tbody></table></div>");
    if ((brief.open_questions || []).length) {
      html.push("<h4>Open questions for the team</h4><ul class='bullets small'>" +
        brief.open_questions.map(function (question) { return "<li>" + esc(question) + "</li>"; }).join("") + "</ul>");
    }
    html.push("</div>");

    html.push("<div class='stack'>");
    html.push("<div class='card'><h3>Formulation on record</h3>" + formulationTable(product) + "</div>");
    var conflicts = ((record || {}).conflicts) || [];
    html.push("<div class='card'><h3>Target conflicts</h3>");
    if (!conflicts.length) {
      html.push("<p class='small muted'>Every target is inside its acceptable region for this version.</p>");
    } else {
      conflicts.slice(0, 5).forEach(function (conflict) {
        html.push("<div class='callout " + (conflict.blockers && conflict.blockers.length ? "bad" : "warn") + "'><strong>" + esc(conflict.label) + "</strong> " +
          tag("hard", "hard") + "<p class='small'>Specified " + num(conflict.target) + " " + esc(conflict.unit) + ", predicted " + num(conflict.achieved) +
          " (" + pct(conflict.desirability) + " desirability). " + esc(conflict.recommendation || "") + "</p>" +
          (conflict.blockers || []).map(function (blocker) { return "<p class='small muted'>" + esc(blocker) + "</p>"; }).join("") + "</div>");
      });
    }
    html.push("</div></div></div>");
    return html.join("");
  }

  function formulationTable(product) {
    var formulation = product.formulation;
    if (!formulation) return "<p class='muted'>No formulation yet.</p>";
    var html = ["<div class='scroll'><table class='compact'><thead><tr><th>Ingredient</th><th>Slot</th><th class='num'>% w/w</th></tr></thead><tbody>"];
    formulation.items.slice().sort(function (a, b) { return b.pct - a.pct; }).forEach(function (item) {
      var group = ingredientGroup(item.ingredient_id);
      html.push("<tr><td>" + esc(ingredientName(item.ingredient_id)) +
        (group ? " <span class='muted small'>" + esc(group) + "</span>" : "") + "</td>" +
        "<td>" + esc(item.slot) + "</td><td class='num'>" + num(item.pct, 3) + "</td></tr>");
    });
    html.push("<tr><td colspan='2'><strong>Total</strong></td><td class='num'><strong>" + num(formulation.total_pct, 2) + "</strong></td></tr>");
    html.push("</tbody></table></div>");
    html.push("<p class='small muted'>" + esc(formulation.label) + "</p>");
    var params = formulation.params || {};
    var keys = Object.keys(params);
    if (keys.length) {
      html.push("<h4>Process settings</h4><div class='scroll'><table class='compact'><tbody>" +
        keys.map(function (key) { return "<tr><td class='mono'>" + esc(key) + "</td><td class='num'>" + num(params[key], 2) + "</td></tr>"; }).join("") +
        "</tbody></table></div>");
    }
    return html.join("");
  }

  function tabPrediction(product, record) {
    if (!record) return "<div class='card'><p class='muted'>No prediction on record for the current version. Use Re-predict.</p></div>";
    var brief = product.brief;
    var byId = {};
    (record.predictions || []).forEach(function (row) { byId[row.id] = row; });
    var rows = [];
    (record.predictions || []).forEach(function (row) {
      var target = { lo: row.lo, hi: row.hi };
      var score = row.desirability === undefined ? null : row.desirability;
      rows.push({
        id: row.id, label: row.label, value: row.value, lo: row.lo, hi: row.hi, unit: row.unit,
        target: row.target, status: row.status, method: row.method, score: score
      });
    });
    var html = ["<div class='card'><div class='card-head'><h3>Predicted characteristics</h3><span class='card-sub'>Every value carries the interval published before any trial, and the method that produced it</span></div>"];
    html.push("<div class='scroll'><table><thead><tr><th>KPI</th><th class='num'>Predicted</th><th class='num'>95% interval</th><th class='num'>Target</th><th>Status</th><th>Method</th></tr></thead><tbody>");
    rows.forEach(function (row) {
      html.push("<tr><td>" + esc(row.label) + " <span class='muted small'>" + esc(row.unit) + "</span></td>" +
        "<td class='num'><strong>" + num(row.value, 3) + "</strong></td>" +
        "<td class='num small muted'>" + num(row.lo, 2) + " - " + num(row.hi, 2) + "</td>" +
        "<td class='num'>" + (row.target === null || row.target === undefined ? "<span class='muted'>-</span>" : num(row.target, 2)) + "</td>" +
        "<td>" + statusTag(row.status) + "</td>" +
        "<td class='small muted'>" + esc(row.method) + "</td></tr>");
    });
    html.push("</tbody></table></div>");
    var evaluation = record.evaluation || {};
    html.push("<div class='grid halves' style='margin-top:.8rem'>" +
      "<div><h4>Desirability against the targets</h4>" + Charts.gaugeBars((evaluation.rows || []).map(function (row) {
        return {
          label: row.label, value: row.value, target: row.target, score: row.desirability, status: row.status,
          digits: row.unit === "aw" || row.unit === "pH" ? 3 : 1
        };
      })) + "</div>" +
      "<div><h4>Summary</h4><dl class='kv'>" +
      "<dt>Objective</dt><dd>" + num(evaluation.objective, 3) + "</dd>" +
      "<dt>Hard targets met</dt><dd>" + ((evaluation.rows || []).filter(function (r) { return r.hard && r.status === "on-target"; }).length) +
      " of " + ((evaluation.rows || []).filter(function (r) { return r.hard; }).length) + "</dd>" +
      "<dt>Strict pass</dt><dd>" + (evaluation.strict_pass ? "yes" : "no") + "</dd>" +
      "<dt>Weakest hard target</dt><dd>" + (evaluation.weakest ? esc(evaluation.weakest.label) + " (" + pct(evaluation.weakest.desirability) + ")" : "-") + "</dd>" +
      "</dl></div></div>");
    html.push("</div>");
    if (record.details && record.details.methods) {
      html.push("<div class='card'><h3>How the prediction was made</h3><ul class='bullets small'>" +
        Object.keys(record.details.methods).map(function (key) {
          return "<li><strong>" + esc(key) + ":</strong> " + esc(record.details.methods[key]) + "</li>";
        }).join("") + "</ul></div>");
    }
    if ((record.flags || []).length) {
      html.push("<div class='card'><h3>Model flags</h3><ul class='bullets small'>" +
        record.flags.map(function (flag) {
          return "<li>" + esc(typeof flag === "string" ? flag : flag.message || JSON.stringify(flag)) + "</li>";
        }).join("") + "</ul></div>");
    }
    return html.join("");
  }

  function tabTrials(product) {
    var trials = product.trials || [];
    if (!trials.length) {
      return "<div class='card'><p class='muted'>No physical trial has been run for this product yet. Use <strong>Run physical trial</strong> to make a batch on the simulated line, then read the residuals and the diagnosis.</p></div>";
    }
    var html = ["<div class='card'><div class='card-head'><h3>Trial trajectory</h3><span class='card-sub'>Measured objective of every trial against the pass bar of 0.85</span></div>" +
      Charts.objectiveLine(trials.map(function (trial, index) {
        var analysis = trial.analysis || {};
        var summary = analysis.measured_summary || analysis;
        return {
          label: trial.label || ("T" + (index + 1)),
          value: analysis.measured_objective || 0,
          pass: !!summary.measured_total && summary.measured_on_target === summary.measured_total
        };
      }), { target: 0.85, yLabel: "measured objective" }) +
      "</div>"];

    trials.forEach(function (trial, index) {
      var analysis = trial.analysis || {};
      var diagnosis = trial.diagnosis || {};
      // The analysis stores the measured counts directly; the hard-target split
      // (the gate the loop actually uses) comes from the efficiency history.
      var summary = analysis.measured_summary || analysis;
      var hard = null;
      ((product.efficiency || {}).measured_history || []).forEach(function (row) {
        if (row.trial === (trial.label || "")) hard = row;
      });
      var verdict = (summary.measured_total && summary.measured_on_target === summary.measured_total) ? "pass"
        : ((summary.measured_total && summary.measured_on_target >= summary.measured_total - 1) ? "marginal" : "reformulate");
      html.push("<div class='card'><div class='card-head'><h3>" + esc(trial.label || ("Trial " + (index + 1))) +
        " <span class='small muted'>version " + num(trial.formulation_version, 0) + "</span></h3>" +
        tag(verdict, verdict) + "</div>");
      html.push("<div class='verdict " + verdict + "'><strong>" + num(summary.measured_on_target, 0) + " of " + num(summary.measured_total, 0) +
        " measured KPI(s) inside the target band</strong>" +
        (hard ? " &middot; " + num(hard.hard_on_target, 0) + " of " + num(hard.hard_total, 0) + " hard targets" : "") +
        " &middot; measured objective " + num(analysis.measured_objective, 3) +
        " &middot; " + num(trial.batch_size_kg, 1) + " kg batch on " + esc(trial.operator || "pilot plant") + "</div>");
      if (analysis.summary) html.push("<p class='small muted'>" + esc(analysis.summary) + "</p>");

      var residuals = analysis.residuals || [];
      if (residuals.length) {
        html.push("<h4>Residuals against the published interval</h4><div class='scroll'><table class='compact'><thead><tr><th>KPI</th><th class='num'>Predicted</th><th class='num'>Interval</th><th class='num'>Measured</th><th class='num'>Gap</th><th class='num'>z</th><th>Reading</th></tr></thead><tbody>");
        residuals.forEach(function (row) {
          var klass = Math.abs(row.z) > 3 ? "fail" : (Math.abs(row.z) > 2 ? "off-target" : (Math.abs(row.z) > 1.3 ? "marginal" : "on-target"));
          html.push("<tr><td>" + esc(row.label) + "</td>" +
            "<td class='num'>" + num(row.predicted, 3) + "</td>" +
            "<td class='num small muted'>" + num(row.predicted - row.sigma * 1.96, 2) + " - " + num(row.predicted + row.sigma * 1.96, 2) + "</td>" +
            "<td class='num'><strong>" + num(row.measured, 3) + "</strong></td>" +
            "<td class='num'>" + num(row.measured - row.predicted, 3) + "</td>" +
            "<td class='num'>" + num(row.z, 2) + "</td>" +
            "<td>" + tag(row.direction + (row.on_target ? " / on target" : (row.on_target === null ? "" : " / off target")), klass) + "</td></tr>");
        });
        html.push("</tbody></table></div>");
        html.push("<h4>Interval coverage</h4>" + Charts.intervalPlot(residuals.slice(0, 8).map(function (row) {
          return { label: row.label, predicted: row.predicted, lo: row.predicted - row.sigma * 1.96, hi: row.predicted + row.sigma * 1.96, measured: row.measured };
        }), {}));
        html.push("<p class='small muted'>Grey dot: what the agent predicted. Green dot: what the laboratory reported, inside the interval. Red dot: outside it, which is the evidence the diagnosis works from.</p>");
      }

      var causes = diagnosis.causes || [];
      html.push("<h4>Probable cause</h4>");
      if (!causes.length) {
        html.push("<p class='small muted'>No systematic cause was found: every residual is consistent with the model, so the plan is to re-test rather than to chase a phantom.</p>");
      }
      causes.slice(0, 4).forEach(function (cause, causeIndex) {
        html.push("<div class='callout" + (causeIndex === 0 ? " warn" : "") + "'><strong>" + esc(cause.cause) + "</strong> " +
          tag(pct(cause.confidence), causeIndex === 0 ? "marginal" : "monitored") +
          " <span class='small muted'>" + esc(cause.category || "") + "</span>");
        (cause.evidence || []).forEach(function (item) {
          html.push("<p class='small muted'>" + esc(typeof item === "string" ? item : JSON.stringify(item)) + "</p>");
        });
        if (cause.recommended_action) html.push("<p class='small'>Suggested action: " + esc(cause.recommended_action) + "</p>");
        if ((cause.kpis || []).length) html.push("<p class='small muted'>Explains: " + esc(cause.kpis.join(", ")) + "</p>");
        html.push("</div>");
      });

      var process = trial.process_actuals || {};
      var keys = Object.keys(process);
      if (keys.length) {
        html.push("<h4>Process actually delivered</h4><div class='scroll'><table class='compact'><tbody>" +
          keys.map(function (key) { return "<tr><td class='mono'>" + esc(key) + "</td><td class='num'>" + num(process[key], 2) + "</td></tr>"; }).join("") +
          "</tbody></table></div>");
      }
      html.push("</div>");
    });
    return html.join("");
  }

  function tabPlan(product) {
    var plans = product.plans || [];
    var html = [];
    if (!plans.length) {
      html.push("<div class='card'><p class='muted'>No reformulation plan yet. Run a physical trial first: a plan is a response to evidence, not a guess.</p></div>");
      return html.join("");
    }
    plans.slice().reverse().forEach(function (plan) {
      var payload = plan.payload || plan;
      var accepted = plan.accepted;
      html.push("<div class='card'><div class='card-head'><h3>Plan " + num(plan.id, 0) + ": v" + num(payload.from_version, 0) +
        " &rarr; v" + num(payload.to_version, 0) + "</h3>" + tag(accepted ? "accepted" : "proposed", accepted ? "accepted" : "proposed") + "</div>");
      html.push("<dl class='kv'><dt>Pass probability</dt><dd>" + pct(payload.pass_probability, 0) +
        " <span class='muted small'>of every hard target landing inside tolerance on the next trial</span></dd>" +
        "<dt>Expected objective</dt><dd>" + num(payload.objective_value, 3) + "</dd></dl>");
      (payload.rationale || []).forEach(function (reason) { html.push("<p class='small'>" + esc(reason) + "</p>"); });
      var deltas = payload.deltas || [];
      var paramDeltas = payload.param_deltas || [];
      if (deltas.length || paramDeltas.length) {
        html.push("<div class='grid halves'><div><h4>Recipe changes</h4>");
        if (deltas.length) {
          html.push("<div class='scroll'><table class='compact'><thead><tr><th>Ingredient</th><th class='num'>From</th><th class='num'>To</th><th class='num'>Change</th></tr></thead><tbody>" +
            deltas.slice(0, 10).map(function (delta) {
              return "<tr><td>" + esc(delta.name) + "</td><td class='num'>" + num(delta.from_pct, 2) + "</td><td class='num'>" + num(delta.to_pct, 2) +
                "</td><td class='num'>" + num(delta.delta_pct, 2) + "</td></tr>";
            }).join("") + "</tbody></table></div>");
        } else {
          html.push("<p class='small muted'>No compositional change: the evidence points at the line, not the recipe.</p>");
        }
        html.push("</div><div><h4>Process changes</h4>");
        if (paramDeltas.length) {
          html.push("<div class='scroll'><table class='compact'><thead><tr><th>Setting</th><th class='num'>From</th><th class='num'>To</th><th>Reason</th></tr></thead><tbody>" +
            paramDeltas.map(function (delta) {
              return "<tr><td>" + esc(delta.label || delta.parameter) + " <span class='muted small'>" + esc(delta.unit || "") + "</span></td>" +
                "<td class='num'>" + num(delta.from, 2) + "</td><td class='num'>" + num(delta.to, 2) + "</td>" +
                "<td class='small'>" + esc(delta.reason || "optimiser move") + (delta.measured_offset !== undefined && delta.measured_offset !== null ? " (delivered " + num(delta.measured_offset, 2) + " off setpoint)" : "") + "</td></tr>";
            }).join("") + "</tbody></table></div>");
        } else {
          html.push("<p class='small muted'>No process change was needed.</p>");
        }
        html.push("</div></div>");
      }
      var expected = payload.expected || [];
      if (expected.length) {
        html.push("<h4>Expected performance of the proposed version</h4><div class='scroll'><table class='compact'><thead><tr><th>KPI</th><th class='num'>Expected</th><th class='num'>Interval</th><th class='num'>Target</th><th>Status</th></tr></thead><tbody>" +
          expected.map(function (row) {
            return "<tr><td>" + esc(row.label) + "</td><td class='num'>" + num(row.value, 3) + "</td>" +
              "<td class='num small muted'>" + num(row.lo, 2) + " - " + num(row.hi, 2) + "</td>" +
              "<td class='num'>" + (row.target === null || row.target === undefined ? "-" : num(row.target, 2)) + "</td>" +
              "<td>" + statusTag(row.status) + "</td></tr>";
          }).join("") + "</tbody></table></div>");
      }
      var doe = payload.doe || null;
      if (doe) {
        html.push("<h4>Design of experiments for the trial</h4>" +
          "<p class='small muted'>" + esc(doe.summary || doe.design || "") + "</p>");
        var runs = doe.runs || [];
        if (runs.length) {
          html.push("<div class='scroll'><table class='compact'><thead><tr><th>Run</th><th>Settings</th><th>Purpose</th></tr></thead><tbody>" +
            runs.map(function (run, runIndex) {
              var settings = run.settings || run.factors || {};
              return "<tr><td>" + esc(run.run || run.label || ("R" + (runIndex + 1))) + "</td><td class='mono small'>" +
                esc(Object.keys(settings).map(function (key) { return key + "=" + num(settings[key], 2); }).join(", ")) +
                "</td><td class='small'>" + esc(run.purpose || run.rationale || "") + "</td></tr>";
            }).join("") + "</tbody></table></div>");
        }
      }
      if (!accepted) {
        html.push("<div class='row end'><button class='primary' data-accept='" + num(plan.id, 0) + "'>Accept this plan as the next version</button></div>");
      }
      html.push("</div>");
    });
    return html.join("");
  }

  function tabProcess(product) {
    var process = product.process || null;
    if (!process) return "<div class='card'><p class='muted'>No formulation yet.</p></div>";
    var html = ["<div class='card'><div class='card-head'><h3>Manufacturing process</h3><span class='card-sub'>" +
      num(process.total_process_min, 0) + " minutes of process time from the unit operations of this category</span></div>"];
    html.push("<div class='scroll'><table class='compact'><thead><tr><th>#</th><th>Unit operation</th><th>Equipment</th><th class='num'>Minutes</th></tr></thead><tbody>" +
      (process.unit_operations || []).map(function (step, index) {
        return "<tr><td class='num'>" + (index + 1) + "</td><td><strong>" + esc(step.operation || step.id || "") + "</strong></td>" +
          "<td class='small'>" + esc(step.equipment || "") + "</td>" +
          "<td class='num'>" + num(step.duration_min, 0) + "</td></tr>";
      }).join("") + "</tbody></table></div></div>");
    var parameters = process.parameters || [];
    if (parameters.length) {
      html.push("<div class='grid halves'><div class='card'><h3>Operating parameters</h3><div class='scroll'><table class='compact'><thead><tr><th>Parameter</th><th class='num'>Set</th><th class='num'>Validated range</th></tr></thead><tbody>" +
        parameters.map(function (row) {
          var range = row.validated_range || [row.min, row.max];
          return "<tr><td>" + esc(row.label || row.id) + " <span class='muted small'>" + esc(row.unit || "") + "</span></td>" +
            "<td class='num'><strong>" + num(row.value, 2) + "</strong></td><td class='num small muted'>" + num(range[0], 1) + " - " + num(range[1], 1) +
            (row.at_range_edge ? "<br>at the range edge" : "") + "</td></tr>";
        }).join("") + "</tbody></table></div></div>");
    }
    var targets = process.in_process_targets || {};
    var targetKeys = Object.keys(targets);
    html.push("<div class='card'><h3>In-process targets</h3>");
    if (targetKeys.length) {
      html.push("<div class='scroll'><table class='compact'><thead><tr><th>Check on the line</th><th class='num'>Value the recipe implies</th></tr></thead><tbody>" +
        targetKeys.map(function (key) {
          return "<tr><td>" + esc(key.replace(/_/g, " ")) + "</td><td class='num'>" + num(targets[key], 2) + "</td></tr>";
        }).join("") + "</tbody></table></div>");
    }
    var ccps = process.critical_control_points || [];
    if (ccps.length) {
      html.push("<h4>Critical control points</h4><div class='scroll'><table class='compact'><thead><tr><th>Point</th><th>Critical limit</th><th>Monitoring</th><th>Action if out of limit</th></tr></thead><tbody>" +
        ccps.map(function (row) {
          return "<tr><td><strong>" + esc(row.point || "") + "</strong></td>" +
            "<td class='small'>" + esc(row.limit || "") + "</td>" +
            "<td class='small muted'>" + esc(row.control || "") + "</td>" +
            "<td class='small muted'>" + esc(row.action || "") + "</td></tr>";
        }).join("") + "</tbody></table></div>");
    }
    var packaging = process.packaging;
    if (packaging) {
      html.push("<h4>Packaging</h4>" + (typeof packaging === "string"
        ? "<p class='small'>" + esc(packaging) + "</p>"
        : "<dl class='kv'>" + Object.keys(packaging).map(function (key) {
            return "<dt>" + esc(key.replace(/_/g, " ")) + "</dt><dd>" + esc(typeof packaging[key] === "number" ? num(packaging[key], 1) : packaging[key]) + "</dd>";
          }).join("") + "</dl>"));
    }
    var batch = process.batch;
    if (batch) {
      html.push("<h4>Yield</h4><dl class='kv'>" +
        "<dt>Theoretical yield</dt><dd>" + num(batch.theoretical_yield_pct, 2) + " %</dd>" +
        "<dt>Expected yield</dt><dd>" + num(batch.expected_yield_pct, 2) + " % <span class='muted small'>(before trial data)</span></dd>" +
        "</dl>");
    }
    html.push("</div></div>");
    return html.join("");
  }

  function tabReport(product) {
    return "<div class='card'><div class='card-head'><h3>Development report</h3><span class='card-sub'>Generated from the record: brief, design, trials, diagnosis, changes, efficiency</span></div>" +
      "<div id='report-body'><p class='muted'>Loading...</p></div></div>";
  }

  function tabLedger(product) {
    var entries = product.ledger || [];
    if (!entries.length) return "<div class='card'><p class='muted'>Empty ledger.</p></div>";
    return "<div class='card'><h3>Audit trail</h3><div class='scroll'><table class='compact'><thead><tr><th>When</th><th>Kind</th><th>Entry</th></tr></thead><tbody>" +
      entries.map(function (entry) {
        return "<tr><td class='small muted mono'>" + esc((entry.created_at || "").replace("T", " ")) + "</td><td>" + tag(entry.kind, "monitored") +
          "</td><td class='small'>" + esc(entry.message) + "</td></tr>";
      }).join("") + "</tbody></table></div></div>";
  }

  /* -------------------------------------------------------------- actions */
  function reload(productId, tab) {
    return api("GET", "/api/products/" + productId).then(function (product) {
      state.product = product;
      if (tab === "report") {
        return api("GET", "/api/products/" + productId + "/report").then(function (payload) {
          state.product.reportMarkdown = payload.markdown;
        });
      }
    });
  }

  function wireProduct(productId, tab) {
    if (tab === "report" && state.product.reportMarkdown) {
      var body = document.getElementById("report-body");
      if (body) body.innerHTML = markdown(state.product.reportMarkdown);
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-tab]"), function (button) {
      button.addEventListener("click", function () {
        location.hash = "#/product/" + productId + "/" + button.getAttribute("data-tab");
      });
    });
    var loopButton = document.querySelector("[data-action='loop']");
    if (loopButton) {
      loopButton.addEventListener("click", function () {
        var budget = prompt("How many physical trials may the closed loop use?", "6");
        if (!budget) return;
        withBusy("Running the closed loop", "Trial, measure, diagnose, plan, accept, repeat - up to " + budget + " trials, stopping as soon as the product passes.", function () {
          return api("POST", "/api/products/" + productId + "/loop", { max_trials: Number(budget) }).then(function (result) {
            return reload(productId, tab).then(function () {
              renderProduct(productId, tab);
              toast(result.success
                ? ("Target reached after " + result.history.length + " trial(s), measured objective " + num(result.history[result.history.length - 1].measured_objective, 3))
                : ("Budget of " + result.history.length + " trial(s) used without a full pass - see the trials tab"));
            });
          });
        });
      });
    }
    var predictButton = document.querySelector("[data-action='predict']");
    if (predictButton) {
      predictButton.addEventListener("click", function () {
        withBusy("Predicting", "Recomputing every characteristic of the current version with the residual model fitted from the trials on record.", function () {
          return api("POST", "/api/products/" + productId + "/predict", {}).then(function () {
            return reload(productId, tab).then(function () { renderProduct(productId, tab); });
          });
        });
      });
    }
    var trialButton = document.querySelector("[data-action='trial']");
    if (trialButton) {
      trialButton.addEventListener("click", function () {
        withBusy("Running the physical trial", "Making a batch on the simulated line, measuring it and analysing the residuals against the interval published before the trial.", function () {
          return api("POST", "/api/products/" + productId + "/trials", {}).then(function (trial) {
            return api("POST", "/api/products/" + productId + "/trials/" + trial.trial_id + "/analyse", {}).then(function (analysis) {
              return reload(productId, "trials").then(function () {
                location.hash = "#/product/" + productId + "/trials";
                var causes = (analysis.diagnosis.causes || []);
                toast(causes.length ? ("" + trial.trial.label + ": top cause - " + causes[0].cause) : (trial.trial.label + ": no systematic cause found"));
              });
            });
          });
        });
      });
    }
    var planButton = document.querySelector("[data-action='plan']");
    if (planButton) {
      planButton.addEventListener("click", function () {
        withBusy("Planning the next version", "Fitting the residual model, compensating the measured process offset and re-optimising the formulation under the brief's constraints.", function () {
          return api("POST", "/api/products/" + productId + "/plans", {}).then(function (result) {
            return reload(productId, "plan").then(function () {
              location.hash = "#/product/" + productId + "/plan";
              toast("Plan ready: pass probability " + pct(result.plan.pass_probability, 0));
            });
          });
        });
      });
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-accept]"), function (button) {
      button.addEventListener("click", function () {
        var planId = Number(button.getAttribute("data-accept"));
        withBusy("Accepting the plan", "Promoting the proposed formulation to the next version, with its predictions stored before the trial.", function () {
          return api("POST", "/api/products/" + productId + "/plans/" + planId + "/accept", {}).then(function (result) {
            return reload(productId, tab).then(function () {
              renderProduct(productId, tab);
              toast("Version " + result.version + " is now the formulation to make");
            });
          });
        });
      });
    });
  }

  /* --------------------------------------------------------------- router */
  function route() {
    var hash = location.hash.replace(/^#\/?/, "");
    var parts = hash.split("/").filter(function (part) { return part !== ""; });
    var head = parts[0] || "products";
    if (head === "new") { renderNew(); return Promise.resolve(); }
    if (head === "cases") { renderCases(); return Promise.resolve(); }
    if (head === "catalog") { renderCatalog(); return Promise.resolve(); }
    if (head === "benchmark") { renderBenchmark(); return Promise.resolve(); }
    if (head === "product") {
      var id = Number(parts[1]);
      var tab = parts[2] || "overview";
      if (!id) { location.hash = "#/products"; return Promise.resolve(); }
      if (state.product && state.product.product.id === id && state.product.reportMarkdown) delete state.product.reportMarkdown;
      return reload(id, tab).then(function () { renderProduct(id, tab); }).catch(function (error) {
        fail(error);
        location.hash = "#/products";
      });
    }
    return refreshProducts().then(renderProducts);
  }

  function boot() {
    busy("Starting", "Loading the knowledge base and the products on record.", "");
    Promise.all([
      api("GET", "/api/health"),
      api("GET", "/api/catalog"),
      api("GET", "/api/cases"),
      api("GET", "/api/products"),
      api("GET", "/api/benchmark")
    ]).then(function (results) {
      setStatus(results[0]);
      state.catalog = results[1];
      state.cases = results[2].cases;
      state.products = results[3].products;
      state.benchmark = results[4].benchmark;
      idle();
      window.addEventListener("hashchange", function () { route(); });
      if (!location.hash) location.hash = "#/products";
      return route();
    }).catch(function (error) {
      idle();
      fail(error);
      view().innerHTML = "<div class='card'><h3>The interface could not start</h3><p class='small'>" + esc(error.message) + "</p></div>";
    });
  }

  boot();
})();