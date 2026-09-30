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
    health: null,
    welcome: false,
    images: [],
    // The uploaded R&D document, if one was read: kept only so the product that comes
    // out of it can record which file the brief was read from.
    document: null,
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

  /* Local storage is a convenience, never a requirement: a browser that blocks
   * it (private mode, a locked-down policy) must still boot the whole app. */
  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (error) { return null; }
  }

  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (error) { /* not fatal */ }
  }

  var VISITED_KEY = "formusense.visited";

  /* Move the reading position to the heading of the new view, so a route change
   * is announced to a screen reader instead of silently swapping the page. */
  function focusView() {
    var node = document.querySelector("#view h2, #view h3");
    if (!node) return;
    node.setAttribute("tabindex", "-1");
    try { node.focus({ preventScroll: true }); } catch (error) { node.focus(); }
  }

  /* The seeded case studies, offered as one-click example briefs: filling the
   * form from a real case is friendlier than an empty textarea, and it cannot
   * drift from the cases the service actually offers. */
  function exampleCases() {
    return (state.cases || []).filter(function (item) { return item.feasible; });
  }

  function exampleBriefs() {
    var examples = exampleCases();
    if (!examples.length) return "";
    return "<div class='examples'><span class='small muted'>Not sure what to write? Load an example brief:</span>" +
      examples.map(function (item, index) {
        return "<button type='button' class='chip' data-example='" + index + "'>" + esc(item.name) + "</button>";
      }).join("") + "</div>";
  }

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
    var budget = vision.budget || {};
    var el = document.getElementById("status-vision");
    // Report what is really configured. This used to read a "provider" key that
    // nothing ever set, so a deployment with a working key still said "vision: API".
    // The badge answers one question - is a model available for this run - so it says
    // on or off and leaves the vendor, and the size of the model chain, to the record.
    var online = !!vision.configured;
    var label = online ? "AI: on"
      : (vision.pillow ? "AI: off (Pillow analysis only)" : "AI: off");
    el.textContent = label;
    el.className = "pill" + (online ? " ok" : (vision.pillow ? "" : " warn"));
    el.title = online
      ? ("Model: " + (vision.model || "an API model") +
         (budget.cap ? ". " + budget.remaining + " of " + budget.cap + " calls left this hour." : ". No hourly cap is set."))
      : "No model key is set (see the README). Everything works without one.";
  }

  function view() { return document.getElementById("view"); }

  function setActiveNav(name) {
    Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"), function (link) {
      link.className = link.getAttribute("data-nav") === name ? "active" : "";
    });
  }

  /* The pack size is a volume for a drink and a weight for everything else, and the
     form says which. Asking for a "unit weight (g)" on a beverage makes the
     formulator convert a bottle into grams by hand, and the number then disagrees
     with the label printed on the pack. */
  function categoryInfo(categoryId) {
    var list = (state.catalog || {}).categories || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].id === categoryId) return list[i];
    }
    return {};
  }

  /* The model layer is opt-in for each run: it costs a call and takes seconds, so it is
     a decision the person pressing the button makes, never a side effect of attaching a
     photograph. With no key configured the switch is disabled and says why, because a
     control that silently does nothing is worse than no control at all. */
  function aiInfo() { return (state.catalog || {}).ai || {}; }

  /* One line naming the model, and nothing more: the switch is the decision, and the
     guarantee a reader needs beside it is that no number in the record comes from the
     model - not which vendor serves it or how many models stand behind it. */
  function modelField() {
    var info = aiInfo();
    var on = !!info.configured;
    var detail = on
      ? ("Model: " + esc(info.model || "") + ". It only adds a description and a review; no number in the record comes from it.")
      : "No model is configured, so this run is fully offline: the images are measured locally and the specification is parsed by rule.";
    return "<div class='field'><span>Reference images (optional)</span>" +
      "<div class='file-drop' id='drop'>Drop product photos here, or click to choose. Offline image analysis always runs; a configured model adds a semantic description on top.</div>" +
      "<input type='file' id='files' accept='image/*' multiple hidden><div class='thumbs' id='thumbs'></div>" +
      "<div class='checks'><label><input type='checkbox' id='f-use-ai'" + (on ? "" : " disabled") +
      ">Use the model for this run</label></div>" +
      "<p class='small muted'>" + detail + "</p></div>";
  }

  function packUnit(categoryId) {
    return categoryInfo(categoryId).pack_unit === "ml" ? "ml" : "g";
  }

  function packFieldLabel(categoryId) {
    return packUnit(categoryId) === "ml" ? "Unit volume (ml)" : "Unit weight (g)";
  }

  function packPlaceholder(categoryId) {
    var info = categoryInfo(categoryId);
    var unit = packUnit(categoryId);
    return info.typical_unit_weight_g
      ? "typically " + info.typical_unit_weight_g + " " + unit + ", or from the spec text"
      : "from the spec text";
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
    // The R&D team usually has this written down already, so the form offers the
    // document before it offers the textarea. What comes back fills the form below and
    // nothing else: the reading is a draft until the person presses the button.
    var docInfo = catalog.documents || {};
    var docAccept = docInfo.accept || ".docx,.xlsx,.pdf,.txt,.csv";
    html.push("<div class='file-drop' id='doc-drop'><strong>Does the R&amp;D team already have this written down?</strong>" +
      "<p class='small muted'>Upload their product report or specification sheet (" + esc(docAccept) +
      ", up to " + num(docInfo.limit_mb || 10, 0) + " MB). The agent reads the specification out of it and fills this form for you to check.</p>" +
      "<div class='row'><button class='ghost' type='button' id='doc-pick'>Choose the document</button>" +
      "<span class='small muted' id='doc-state'>Or just type the specification below.</span></div>" +
      "<input type='file' id='doc-file' accept='" + esc(docAccept) + "' hidden></div>" +
      "<div id='doc-review'></div>");
    html.push("<label class='field'><span>Product name</span><input type='text' id='f-name' value='" + esc(prefill && prefill.product_name || "") + "' placeholder='e.g. High-protein ragi cookie'></label>");
    html.push("<label class='field'><span>Specification text</span><textarea id='f-spec' placeholder='High protein masala extruded namkeen. 30 g pack. Protein 15 g per 100 g. Moisture 3%. Sodium 480 mg per 100 g. Shelf life 6 months. Ingredient cost not more than INR 185 per kg.'>" + esc(spec) + "</textarea></label>");
    html.push(exampleBriefs());
    html.push("<div class='inline'>");
    html.push("<label class='field'><span>Category</span><select id='f-category'>" +
      catalog.categories.map(function (c) {
        return "<option value='" + esc(c.id) + "'" + (c.id === category ? " selected" : "") + ">" + esc(c.label) + "</option>";
      }).join("") + "</select></label>");
    html.push("<label class='field'><span>Diet</span><select id='f-diet'>" +
      ["vegetarian", "vegan", "any"].map(function (d) { return "<option>" + d + "</option>"; }).join("") +
      "</select></label>");
    html.push("<label class='field'><span id='f-unit-label'>" + esc(packFieldLabel(category)) + "</span><input type='number' id='f-unit' step='1' value='" + esc(prefill && prefill.unit_weight_g || "") + "' placeholder='" + esc(packPlaceholder(category)) + "'></label>");
    html.push("</div>");
    html.push("<div class='field'><span>Claims to substantiate</span><div class='checks' id='f-claims'>" +
      (catalog.claims || []).map(function (claim) {
        return "<label><input type='checkbox' value='" + esc(claim.id) + "'" + (claims.indexOf(claim.id) >= 0 ? " checked" : "") + ">" + esc(claim.label || claim.id) + "</label>";
      }).join("") + "</div></div>");
    html.push("<div class='field'><span>Allergens to avoid</span><div class='checks' id='f-allergens'>" +
      Object.keys(catalog.allergens || {}).map(function (id) {
        return "<label><input type='checkbox' value='" + esc(id) + "'>" + esc(catalog.allergens[id]) + "</label>";
      }).join("") + "</div></div>");
    html.push(modelField());
    html.push("<div class='row end'><span class='small muted' id='design-hint'>A specification text is required.</span>" +
      "<button class='ghost' id='btn-clear'>Clear</button><button class='primary' id='btn-design'>Design product</button></div>");
    html.push("</div>");

    html.push("<div class='stack'>");
    // The plant is real but advanced: most users want the standard pilot plant,
    // so the deviations live behind one disclosure instead of six blank fields.
    var plantCustom = !!(plant && Object.keys(plant).length);
    html.push("<div class='card'><h3>Manufacturing plant</h3><p class='card-sub'>A formulation is designed for a plant. These are the deviations the agent has to plan around - the same knobs the simulated pilot plant uses for the physical trials.</p>" +
      "<details class='advanced'" + (plantCustom ? " open" : "") + "><summary>Advanced: plant settings<span class='small muted'> - standard pilot plant unless you change something</span></summary>" +
      plantFields(plant) + "</details></div>");
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

    var docFile = document.getElementById("doc-file");
    var docDrop = document.getElementById("doc-drop");
    if (docDrop && docFile) {
      // The button and the zone are the same affordance; the guard stops the input's
      // own click from bubbling back into the zone and opening the picker twice.
      docDrop.addEventListener("click", function (event) {
        if (event.target === docFile) return;
        docFile.click();
      });
      docDrop.addEventListener("dragover", function (event) { event.preventDefault(); docDrop.classList.add("hot"); });
      docDrop.addEventListener("dragleave", function () { docDrop.classList.remove("hot"); });
      docDrop.addEventListener("drop", function (event) {
        event.preventDefault();
        docDrop.classList.remove("hot");
        var dropped = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        if (dropped) readDocument(dropped);
      });
      docFile.addEventListener("change", function () {
        if (docFile.files && docFile.files[0]) readDocument(docFile.files[0]);
      });
    }

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
    var categorySelect = document.getElementById("f-category");
    function refreshPackField() {
      var label = document.getElementById("f-unit-label");
      var field = document.getElementById("f-unit");
      if (label) label.textContent = packFieldLabel(categorySelect.value);
      if (field) field.placeholder = packPlaceholder(categorySelect.value);
    }
    if (categorySelect) {
      categorySelect.addEventListener("change", refreshPackField);
      refreshPackField();
    }

    var specField = document.getElementById("f-spec");
    var design = document.getElementById("btn-design");
    var hint = document.getElementById("design-hint");
    function refreshDesignState() {
      var ready = specField.value.trim().length > 0;
      design.disabled = !ready;
      hint.textContent = ready ? "" : "Write (or load) a specification text first.";
    }
    specField.addEventListener("input", refreshDesignState);
    refreshDesignState();

    var examples = exampleCases();
    Array.prototype.forEach.call(document.querySelectorAll("[data-example]"), function (chip) {
      chip.addEventListener("click", function () {
        var item = examples[Number(chip.getAttribute("data-example"))];
        if (!item) return;
        var payload = item.payload || {};
        document.getElementById("f-name").value = payload.product_name || item.name || "";
        specField.value = payload.spec_text || "";
        if (payload.category) document.getElementById("f-category").value = payload.category;
        if (payload.diet) document.getElementById("f-diet").value = payload.diet;
        if (payload.unit_weight_g) document.getElementById("f-unit").value = payload.unit_weight_g;
        refreshPackField();
        Array.prototype.forEach.call(document.querySelectorAll("#f-claims input"), function (input) {
          input.checked = (payload.claims || []).indexOf(input.value) >= 0;
        });
        // The example replaces whatever a document proposed, so the record must stop
        // claiming the brief was read from an upload.
        state.document = null;
        var review = document.getElementById("doc-review");
        if (review) review.innerHTML = "";
        refreshDesignState();
        toast("Example brief loaded - edit it, or press Design product");
      });
    });

    document.getElementById("btn-clear").addEventListener("click", function () {
      state.images = [];
      state.document = null;
      location.hash = "#/new";
    });
    design.addEventListener("click", function () {
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

  /* An uploaded R&D document, read into the form.
     The upload stores nothing: the server parses the document and hands back what it
     found, and the file itself is kept only when the product it described is created. */
  function readDocument(file) {
    var info = (state.catalog || {}).documents || {};
    var line = document.getElementById("doc-state");
    function say(text) { if (line) line.textContent = text; }
    var limit = (info.limit_mb || 10) * 1024 * 1024;
    if (file.size > limit) {
      say(file.name + " is " + num(file.size / 1048576, 1) + " MB; the limit is " + num(info.limit_mb || 10, 0) + " MB.");
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      say("Reading " + file.name + "...");
      var useAi = !!((document.getElementById("f-use-ai") || {}).checked);
      api("POST", "/api/brief/from-document", {
        filename: file.name, data_url: reader.result, use_ai: useAi
      }).then(function (proposal) {
        var info2 = proposal.document || {};
        state.document = {
          name: file.name,
          format: info2.format,
          characters: info2.characters,
          data_url: reader.result,
          use_ai: useAi
        };
        applyProposal(proposal);
        say("Read " + num(info2.characters, 0) + " characters from " + file.name + ".");
        toast("Specification read from " + file.name + " - check the form, then press Design product");
      }).catch(function (error) {
        say("Could not read " + file.name + ".");
        fail(error);
      });
    };
    reader.readAsDataURL(file);
  }

  function applyProposal(proposal) {
    var fields = proposal.fields || {};
    if (fields.product_name) document.getElementById("f-name").value = fields.product_name;
    var spec = document.getElementById("f-spec");
    if (fields.spec_text) spec.value = fields.spec_text;
    if (fields.category) document.getElementById("f-category").value = fields.category;
    if (fields.diet) document.getElementById("f-diet").value = fields.diet;
    if (fields.unit_weight_g !== null && fields.unit_weight_g !== undefined) {
      document.getElementById("f-unit").value = fields.unit_weight_g;
    }
    Array.prototype.forEach.call(document.querySelectorAll("#f-claims input"), function (input) {
      input.checked = (fields.claims || []).indexOf(input.value) >= 0;
    });
    Array.prototype.forEach.call(document.querySelectorAll("#f-allergens input"), function (input) {
      input.checked = (fields.allergens_to_avoid || []).indexOf(input.value) >= 0;
    });
    // The pack field relabels itself for a drink and the button follows the
    // specification field. Both are the form's own listeners, so both are told.
    document.getElementById("f-category").dispatchEvent(new Event("change", { bubbles: true }));
    spec.dispatchEvent(new Event("input", { bubbles: true }));
    renderDocumentReview(proposal);
  }

  function renderDocumentReview(proposal) {
    var holder = document.getElementById("doc-review");
    if (!holder) return;
    var info = proposal.document || {};
    var html = ["<div class='callout'><strong>Read from " + esc(info.name || "your document") + "</strong>" +
      "<p class='small muted'>" + esc(info.format_label || info.format || "") + " &middot; " +
      num(info.characters, 0) + " characters read. Everything below is in the form for you to check; nothing is designed until you press the button.</p>"];
    if ((proposal.found || []).length) {
      html.push("<p class='small'><strong>Filled in for you:</strong></p><ul class='bullets small'>" +
        proposal.found.map(function (item) {
          return "<li>" + esc(item.field) + ": <strong>" + esc(item.value) + "</strong> " +
            "<span class='muted'>(" + (item.how === "model" ? "read by the model" : "read by rule") +
            (item.evidence ? ": " + esc(item.evidence) : "") + ")</span></li>";
        }).join("") + "</ul>");
    }
    if ((proposal.missing || []).length) {
      html.push("<p class='small'><strong>The document does not state:</strong></p><ul class='bullets small'>" +
        proposal.missing.map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") + "</ul>");
    }
    (proposal.notes || []).forEach(function (note) {
      html.push("<p class='small muted'>" + esc(note) + "</p>");
    });
    if ((proposal.model || {}).note) {
      html.push("<p class='small muted'>" + esc(proposal.model.note) + ".</p>");
    }
    html.push("<details><summary class='small'>Show the text that was read</summary><pre class='code'>" +
      esc(info.text || "") + "</pre></details></div>");
    holder.innerHTML = html.join("");
  }

  function collectForm() {
    function checked(id) {
      return Array.prototype.slice.call(document.querySelectorAll("#" + id + " input:checked")).map(function (input) { return input.value; });
    }
    var unit = document.getElementById("f-unit").value;
    var categoryField = document.getElementById("f-category");
    return {
      product_name: document.getElementById("f-name").value.trim(),
      spec_text: document.getElementById("f-spec").value.trim(),
      category: document.getElementById("f-category").value,
      diet: document.getElementById("f-diet").value,
      claims: checked("f-claims"),
      allergens_to_avoid: checked("f-allergens"),
      unit_weight_g: unit ? Number(unit) : null,
      // The number in that field is in the unit the form labelled it with, so the
      // payload says which one it is.
      unit: packUnit(categoryField.value),
      // Off unless the box is ticked, so an unattended run never spends a call.
      use_ai: !!((document.getElementById("f-use-ai") || {}).checked),
      images: state.images.slice(),
      // Which document the brief was read from, so the record can name it. The bytes
      // travel again here because this is the moment the file becomes evidence.
      source_document: state.document ? {
        name: state.document.name,
        format: state.document.format,
        characters: state.document.characters,
        data_url: state.document.data_url,
        use_ai: state.document.use_ai
      } : null,
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
        state.document = null;
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
    if (state.welcome) {
      html.push("<div class='welcome'><h3>Welcome to FormuSense</h3><p>New here? The guide takes you from a written brief to a first formulation in about five minutes, and the case studies turn a complete product and its trial history in one press.</p>" +
        "<div class='row'><a class='cta primary' href='#/guide'>Read the five-minute guide</a><a class='cta' href='#/cases'>Load a case study</a>" +
        "<button class='ghost' data-dismiss-welcome>Dismiss</button></div></div>");
    }
    if (!state.products.length) {
      html.push("<div class='empty'><p><strong>No products yet.</strong> Start from a brief of your own, or load one of the seeded case studies and watch the loop run.</p>" +
        "<div class='row'><a class='cta primary' href='#/new'>Describe a product</a><a class='cta' href='#/cases'>Load a case study</a>" +
        "<a class='cta' href='#/guide'>How to use the app</a></div></div>");
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
    Array.prototype.forEach.call(view().querySelectorAll("[data-dismiss-welcome]"), function (button) {
      button.addEventListener("click", function () {
        state.welcome = false;
        storageSet(VISITED_KEY, "1");
        renderProducts();
      });
    });
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
      html.push("<div class='empty'><p><strong>No benchmark has been run in this database yet.</strong> It compares the agent's closed loop with one-factor-at-a-time on the same cases, budget and pass gate.</p>" +
        "<p class='small muted'>Set a budget above and press <em>Run benchmark</em>, or run <code>python run.py --benchmark</code> from the command line.</p></div>");
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
        withBusy("Running the benchmark", "Two arms, same cases, same budget of " + budget + " physical trials each. This takes about twenty seconds.", function () {
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
  var TABS = ["overview", "prediction", "populations", "trials", "plan", "process", "report", "ledger", "ask"];

  /* What the record says the next useful action is. This is advice drawn from
   * the stored state, not a judgement: it mirrors the header buttons and never
   * invents a step the data does not support. */
  function nextStep(product, record) {
    var id = product.product.id;
    var efficiency = product.efficiency || {};
    var trials = (product.trials || []).length;
    var proposed = (product.plans || []).filter(function (plan) { return !plan.accepted; });
    if (efficiency.trials_to_target) {
      return {
        tone: "good",
        title: "Target reached",
        body: "Every hard target is inside its tolerance and the measured objective is at or above the pass bar. Keep the record, or open the report for the full story.",
        action: { label: "Open the report", href: "#/product/" + id + "/report" }
      };
    }
    if (proposed.length) {
      return {
        tone: "warn",
        title: "A plan is waiting for a decision",
        body: "The agent has proposed the next version with a pass probability. Review the recipe and process changes and accept the plan, or leave it and plan again.",
        action: { label: "Review the plan", href: "#/product/" + id + "/plan" }
      };
    }
    if (!trials) {
      return {
        tone: "info",
        title: "Next step: run the first physical trial",
        body: "The formulation is predicted but not yet measured. A trial makes a batch, compares the measurements with the interval published before the batch and names the probable cause of any miss.",
        action: { label: "Run physical trial", hook: "trial" }
      };
    }
    return {
      tone: "info",
      title: "Next step: plan the next version",
      body: "The trial evidence is in. Planning corrects the measured process offset, rewrites the recipe under the brief's constraints and estimates the chance of a pass on the next batch.",
      action: { label: "Plan next version", hook: "plan" }
    };
  }

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

    html.push("<nav class='crumbs small muted' aria-label='Breadcrumb'><a href='#/products'>Products</a> <span>&rsaquo;</span> <span>" + esc(brief.product_name) + "</span></nav>");
    html.push("<div class='card'><div class='card-head'><div><h2>" + esc(brief.product_name) + "</h2>" +
      "<p class='card-sub'>" + esc(brief.category_label) + " &middot; " + num(brief.declared_unit_size || brief.unit_weight_g, 0) + " " + esc(brief.declared_unit || "g") + " unit &middot; " + esc(brief.diet) +
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

    var step = nextStep(product, record);
    html.push("<div class='next-step " + esc(step.tone) + "'><div class='next-step-text'><strong>" + esc(step.title) + "</strong><p class='small'>" + esc(step.body) + "</p></div>" +
      (step.action
        ? (step.action.href
          ? "<a class='cta" + (step.tone === "warn" ? " primary" : "") + "' href='" + esc(step.action.href) + "'>" + esc(step.action.label) + "</a>"
          : "<button class='" + (step.tone === "warn" ? "primary" : "") + "' data-action='" + esc(step.action.hook) + "'>" + esc(step.action.label) + "</button>")
        : "") +
      "</div>");

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
    if (tab === "populations") return tabPopulations(product);
    if (tab === "trials") return tabTrials(product);
    if (tab === "plan") return tabPlan(product);
    if (tab === "process") return tabProcess(product);
    if (tab === "report") return tabReport(product);
    if (tab === "ledger") return tabLedger(product);
    if (tab === "ask") return tabAsk(product);
    return "";
  }

  /* Ask the record. The answer comes from a bounded extract of this product's stored
     brief, formulation, prediction, trials and plan - the same rows the other tabs
     render - so it can quote the record but not add to it. The citations are shown
     because an answer that names its sources is checkable, and one that does not is a
     story. */
  function tabAsk(product) {
    var info = aiInfo();
    var html = ["<div class='card'><h3>Ask the record</h3>"];
    html.push("<p class='card-sub'>Answers are drawn from this product's own brief, formulation, prediction, trials and plan, and from nothing else. Ask which targets the last trial missed, what changed between versions, or why a plan proposes what it does.</p>");
    if (!info.configured) {
      html.push("<p class='small muted'>No model is configured, so the record cannot be asked. Set a model key in .env and reload. Every other tab is unaffected: all of them are computed locally.</p>");
    }
    html.push("<label class='field'><span>Your question</span><textarea id='ask-q' placeholder='e.g. Which targets did the last trial miss, and by how much?'>" + esc(state.askDraft || "") + "</textarea></label>");
    html.push("<div class='row end'><span class='small muted'>Answers quote the record. They cannot change it.</span>" +
      "<button class='primary' data-action='ask'" + (info.configured ? "" : " disabled") + ">Ask</button></div>");
    var answer = state.askAnswer;
    if (answer) {
      html.push("<div class='callout'><strong>Answer</strong>");
      html.push("<p class='small'>" + esc(answer.answer || answer.note || "No answer.") + "</p>");
      if ((answer.citations || []).length) {
        html.push("<p class='small muted'>From: " + answer.citations.map(function (citation) {
          return "<span class='tag'>" + esc(citation) + "</span>";
        }).join(" ") + "</p>");
      }
      html.push("<p class='small muted'>" + esc(answer.model ? (answer.model + (answer.cached ? ", served from the cache" : "")) : "no model was called") +
        ". The question and answer are in the ledger.</p></div>");
    }
    html.push("</div>");
    return html.join("");
  }

  function tabOverview(product, record) {
    var brief = product.brief;
    var html = ["<div class='grid two'>"];
    html.push("<div class='card'><h3>The brief as understood</h3>");
    html.push("<p class='small'>" + esc(brief.description) + "</p>");
    var vision = product.vision || {};
    if (vision.available && (vision.lines || []).length) {
      html.push("<div class='callout'><strong>Reference image, as the model described it</strong>" +
        "<ul class='bullets small'>" + vision.lines.map(function (line) { return "<li>" + esc(line) + "</li>"; }).join("") + "</ul>" +
        "<p class='small muted'>" + esc(vision.model || "") +
        ", recorded " + esc(vision.created_at || "") + ". A semantic description only: it adds no numbers to the brief.</p></div>");
    }
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
      "<div><h4>Desirability against the targets</h4><div class='scroll'>" + Charts.gaugeBars((evaluation.rows || []).map(function (row) {
        return {
          label: row.label, value: row.value, target: row.target, score: row.desirability, status: row.status,
          digits: row.unit === "aw" || row.unit === "pH" ? 3 : 1
        };
      })) + "</div></div>" +
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

  /* Population guidance: what one serving delivers to each group's daily need.
   * The numbers are computed server-side from the ICMR-NIN reference set; this
   * view only lays them out, and it always shows the reference value next to the
   * percentage so no figure appears without its standard. */
  function tabPopulations(product) {
    var guide = product.populations || null;
    if (!guide || !(guide.groups || []).length) {
      return "<div class='card'><p class='muted'>No population guidance is available for this product yet.</p></div>";
    }
    var serving = guide.serving || {};
    var html = ["<div class='card'><div class='card-head'><h3>Consumption guidance by population</h3><span class='card-sub'>" +
      esc(guide.serving_label || "one serving") + "</span></div>"];
    html.push("<dl class='kv'>" +
      "<dt>Protein per serving</dt><dd><strong>" + num(serving.protein_g, 1) + " g</strong></dd>" +
      "<dt>Energy per serving</dt><dd>" + num(serving.energy_kcal, 0) + " kcal</dd>" +
      "<dt>Sugars per serving</dt><dd>" + num(serving.sugar_g, 1) + " g</dd>" +
      "<dt>Protein energy share</dt><dd>" + num(guide.protein_energy_pct, 1) + " %</dd>" +
      "</dl>" +
      (guide.protein_energy_pct_note ? "<p class='small muted'>" + esc(guide.protein_energy_pct_note) + "</p>" : "") +
      "</div>");
    html.push("<div class='card'><div class='scroll'><table class='compact'><thead><tr>" +
      "<th>Population</th><th class='num'>Daily requirement</th><th class='num'>Protein per serving</th>" +
      "<th class='num'>% of requirement</th><th class='num'>Servings to reach it</th><th>Reading</th></tr></thead><tbody>");
    (guide.groups || []).forEach(function (row) {
      var klass = row.pct_rda_per_serving >= 50 ? "marginal" : (row.pct_rda_per_serving >= 20 ? "pass" : "monitored");
      html.push("<tr><td><strong>" + esc(row.label) + "</strong><br><span class='small muted'>" + esc(row.age_range || "") +
        " &middot; reference weight " + num(row.ref_weight_kg, 0) + " kg</span></td>" +
        "<td class='num'>" + num(row.rda_g_day, 0) + " g<br><span class='small muted'>" + num(row.rda_g_kg_day, 2) + " g/kg</span></td>" +
        "<td class='num'>" + num(row.protein_per_serving_g, 1) + " g</td>" +
        "<td class='num'>" + tag(num(row.pct_rda_per_serving, 0) + "%", klass) + "</td>" +
        "<td class='num'>" + num(row.servings_for_rda, 1) + "</td>" +
        "<td class='small'>" + esc(row.reading || "") +
        (row.suggested_target_g_day ? "<br><span class='small muted'>older-adult guidance target " + num(row.suggested_target_g_day, 0) + " g/day</span>" : "") +
        (row.flags || []).map(function (flag) { return "<div class='callout warn small'>" + esc(flag) + "</div>"; }).join("") +
        (row.cautions || []).map(function (caution) { return "<p class='small muted'>" + esc(caution) + "</p>"; }).join("") +
        "</td></tr>");
    });
    html.push("</tbody></table></div>");
    html.push("<p class='small muted'>Reference: " + esc(guide.source || "") + "</p>");
    html.push("<p class='small muted'>" + esc(guide.disclaimer || "") + "</p>");
    html.push("</div>");
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
    var ask = document.querySelector("[data-action='ask']");
    if (ask) {
      var question = document.getElementById("ask-q");
      if (question) {
        question.addEventListener("input", function () { state.askDraft = question.value; });
      }
      ask.addEventListener("click", function () {
        var asked = (question ? question.value : "").trim();
        if (!asked) return;
        withBusy("Asking the record", "Assembling this product's stored brief, formulation, prediction, trials and plan, then answering from that extract alone.", function () {
          // The question is a POST because it leaves a trace: the question and its
          // answer go into the ledger, which is what makes the chat auditable rather
          // than a conversation nobody can check afterwards.
          return api("POST", "/api/products/" + productId + "/ask", { question: asked, use_ai: true }).then(function (result) {
            state.askAnswer = result;
            state.askDraft = asked;
            return reload(productId, "ask").then(function () {
              renderProduct(productId, "ask");
              if (!result.answer) toast(result.note || "The record could not be asked.");
            });
          });
        });
      });
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-action='loop']"), function (loopButton) {
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
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-action='predict']"), function (predictButton) {
      predictButton.addEventListener("click", function () {
        withBusy("Predicting", "Recomputing every characteristic of the current version with the residual model fitted from the trials on record.", function () {
          return api("POST", "/api/products/" + productId + "/predict", {}).then(function () {
            return reload(productId, tab).then(function () { renderProduct(productId, tab); });
          });
        });
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-action='trial']"), function (trialButton) {
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
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-action='plan']"), function (planButton) {
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
    });
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

  /* ---------------------------------------------------------------- model */
  function renderModel() {
    setActiveNav("model");
    view().innerHTML = "<div class='card'>Loading the acceptance model...</div>";
    api("GET", "/api/model").then(function (payload) {
      if (!payload.available) {
        view().innerHTML = "<div class='card'><h2>Acceptance model</h2>" +
          "<div class='empty'><p>" + esc(payload.note || "No model has been trained yet.") + "</p>" +
          "<p class='small muted'>Train it offline, with no API key and no network: <code>python run.py --build-dataset</code>, then <code>python run.py --train</code>. The metrics appear here as soon as a bundle exists.</p>" +
          "<p><a class='cta' href='#/guide'>How to use the app</a></p></div></div>";
        return;
      }
      var metrics = payload.metrics || {};
      var test = metrics.test || {};
      var reg = test.regression || {};
      var baseReg = test.baseline_regression || {};
      var clf = test.classification || {};
      var baseClf = test.baseline_classification || {};
      var beats = metrics.beats_baseline || {};
      var rows = metrics.rows || {};
      var dataset = payload.dataset || {};
      var provenance = dataset.provenance || {};
      var html = ["<div class='card'><div class='card-head'><h2>Acceptance model</h2>" +
        tag(payload.name + "/" + payload.version, "monitored") + "</div>" +
        "<p class='card-sub'>A locally trained model that predicts how a formulation will score and whether it will pass. Trained offline on a versioned dataset - no API key, no network. Every number below is measured on the held-out test fold.</p>"];
      html.push("<dl class='kv'>" +
        "<dt>Dataset</dt><dd>" + esc(String(dataset.name || "")) + "/" + esc(String(dataset.version || "")) + " &middot; " + num(dataset.rows, 0) + " rows</dd>" +
        "<dt>Split</dt><dd>by " + esc(String(metrics.split || "")) + " &middot; train " + num(rows.train, 0) + ", validation " + num(rows.validation, 0) + ", test " + num(rows.test, 0) + "</dd>" +
        "<dt>Trained</dt><dd>" + esc(payload.created_at || "") + "</dd>" +
        "<dt>Provenance</dt><dd class='small muted'>" + esc(provenance.note || provenance.source || "") + "</dd>" +
        "</dl></div>");
      html.push("<div class='grid halves'>");
      html.push("<div class='card'><h3>Objective (ridge regression)</h3><div class='scroll'><table class='compact'><thead><tr><th>Metric</th><th class='num'>Model</th><th class='num'>Baseline</th></tr></thead><tbody>" +
        "<tr><td>RMSE</td><td class='num'>" + num(reg.rmse, 4) + "</td><td class='num muted'>" + num(baseReg.rmse, 4) + "</td></tr>" +
        "<tr><td>MAE</td><td class='num'>" + num(reg.mae, 4) + "</td><td class='num muted'>" + num(baseReg.mae, 4) + "</td></tr>" +
        "<tr><td>R&sup2;</td><td class='num'>" + num(reg.r2, 3) + "</td><td class='num muted'>" + num(baseReg.r2, 3) + "</td></tr>" +
        "</tbody></table></div></div>");
      html.push("<div class='card'><h3>Pass / fail (calibrated logistic)</h3><div class='scroll'><table class='compact'><thead><tr><th>Metric</th><th class='num'>Model</th><th class='num'>Baseline</th></tr></thead><tbody>" +
        "<tr><td>ROC-AUC</td><td class='num'>" + num(clf.roc_auc, 3) + "</td><td class='num muted'>" + num(baseClf.roc_auc, 3) + "</td></tr>" +
        "<tr><td>Accuracy</td><td class='num'>" + num(clf.accuracy, 3) + "</td><td class='num muted'>" + num(baseClf.accuracy, 3) + "</td></tr>" +
        "<tr><td>Brier score</td><td class='num'>" + num(clf.brier, 3) + "</td><td class='num muted'>" + num(baseClf.brier, 3) + "</td></tr>" +
        "<tr><td>Base rate</td><td class='num'>" + num(clf.base_rate, 3) + "</td><td class='num muted'>-</td></tr>" +
        "</tbody></table></div>" +
        "<p class='small'>Beats the baseline - RMSE " + (beats.rmse ? "yes" : "no") + ", ROC-AUC " + (beats.roc_auc ? "yes" : "no") + ", Brier " + (beats.brier ? "yes" : "no") + ".</p></div>");
      html.push("</div>");
      var drivers = payload.drivers || [];
      if (drivers.length) {
        html.push("<div class='card'><h3>What drives the objective</h3><div class='scroll'><table class='compact'><thead><tr><th>Feature</th><th class='num'>Standardised coefficient</th></tr></thead><tbody>" +
          drivers.map(function (row) {
            return "<tr><td class='mono small'>" + esc(row.feature) + "</td><td class='num'>" + num(row.objective_coefficient, 4) + "</td></tr>";
          }).join("") + "</tbody></table></div></div>");
      }
      view().innerHTML = html.join("");
    }).catch(function (error) { fail(error); });
  }

  /* ---------------------------------------------------------------- guide */
  function renderGuide() {
    setActiveNav("guide");
    if (!window.Guide) {
      view().innerHTML = "<div class='card'><h3>Guide unavailable</h3><p class='small'>The guide script did not load. The rest of the interface is unaffected.</p></div>";
      return;
    }
    // The guide renders from the same state the header and the benchmark view
    // use, so its figures are live rather than copied into the prose.
    view().innerHTML = Guide.render({
      health: state.health,
      cases: state.cases,
      benchmark: state.benchmark,
      products: state.products
    });
    // The contents links anchor within the page, so they must not reach the hash
    // router: "#g-numbers" is not a route, and the router would render Products
    // over the guide instead of scrolling to the section.
    Array.prototype.forEach.call(view().querySelectorAll("a[href^='#g-']"), function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        var section = document.getElementById(link.getAttribute("href").slice(1));
        if (!section) return;
        // Instant rather than smooth: a contents link that does nothing when the
        // compositor is off, or when the reader asked for reduced motion, is
        // worse than one that jumps.
        section.scrollIntoView({ block: "start" });
        section.setAttribute("tabindex", "-1");
        try { section.focus({ preventScroll: true }); } catch (error) { section.focus(); }
      });
    });
  }

  /* --------------------------------------------------------------- router */
  function route() {
    return routeInner().then(function (result) { focusView(); return result; });
  }

  function routeInner() {
    var hash = location.hash.replace(/^#\/?/, "");
    var parts = hash.split("/").filter(function (part) { return part !== ""; });
    var head = parts[0] || "products";
    if (head === "new") { renderNew(); return Promise.resolve(); }
    if (head === "cases") { renderCases(); return Promise.resolve(); }
    if (head === "catalog") { renderCatalog(); return Promise.resolve(); }
    if (head === "benchmark") { renderBenchmark(); return Promise.resolve(); }
    if (head === "model") { renderModel(); return Promise.resolve(); }
    if (head === "guide" || head === "help" || head === "how") { renderGuide(); return Promise.resolve(); }
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
      state.health = results[0];
      state.catalog = results[1];
      state.cases = results[2].cases;
      state.products = results[3].products;
      state.benchmark = results[4].benchmark;
      // First visit and nothing on record: offer the guide and the case studies
      // once, then remember the visit so the card does not nag.
      state.welcome = !storageGet(VISITED_KEY) && state.products.length === 0;
      storageSet(VISITED_KEY, "1");
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