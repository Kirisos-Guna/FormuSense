/* Small SVG charts, written by hand so the UI has no dependencies.
 *
 * Four shapes cover every figure the agent needs:
 *   objectiveLine  - did the product get closer to target, trial by trial?
 *   pairBars       - agent versus one-factor-at-a-time, the headline comparison
 *   intervalPlot   - one trial, every KPI: the interval published before the
 *                    trial, and the value the laboratory reported
 *   gaugeBars      - desirability of every target for the current version
 *
 * Each function returns an SVG string, so a caller can drop it into innerHTML
 * and let the browser lay it out. Colours come from the stylesheet classes.
 */
(function () {
  "use strict";

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function scale(domain, range) {
    var d0 = domain[0], d1 = domain[1];
    var r0 = range[0], r1 = range[1];
    if (d1 - d0 === 0) d1 = d0 + 1;
    return function (value) { return r0 + ((value - d0) / (d1 - d0)) * (r1 - r0); };
  }

  function niceDomain(values, pad) {
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (lo === hi) { lo -= 1; hi += 1; }
    var margin = (hi - lo) * (pad === undefined ? 0.12 : pad);
    return [lo - margin, hi + margin];
  }

  /* -------------------------------------------------- objective trajectory */
  function objectiveLine(points, options) {
    options = options || {};
    // A wide viewBox: the SVG is scaled to the panel width, so a 4:1 canvas is
    // what keeps a chart on a desktop screen about 300 px tall instead of 460.
    var width = 1200, height = options.height || 300;
    var pad = { l: 62, r: 22, t: 22, b: 44 };
    if (!points || points.length === 0) {
      return "<p class='muted small'>No trials have been run yet.</p>";
    }
    var values = points.map(function (p) { return p.value; });
    if (options.target !== undefined) values.push(options.target);
    var domain = niceDomain(values, 0.15);
    domain[0] = Math.max(0, domain[0]);
    var x = scale([0, Math.max(points.length - 1, 1)], [pad.l, width - pad.r]);
    var y = scale(domain, [height - pad.b, pad.t]);
    var out = ["<svg class='chart' viewBox='0 0 " + width + " " + height + "' role='img'>"];

    // y grid
    for (var i = 0; i <= 4; i++) {
      var value = domain[0] + ((domain[1] - domain[0]) * i) / 4;
      out.push("<line class='grid-line' x1='" + pad.l + "' y1='" + y(value) + "' x2='" + (width - pad.r) + "' y2='" + y(value) + "'/>");
      out.push("<text x='" + (pad.l - 6) + "' y='" + (y(value) + 3) + "' text-anchor='end'>" + value.toFixed(2) + "</text>");
    }
    if (options.target !== undefined) {
      out.push("<line class='target-line' x1='" + pad.l + "' y1='" + y(options.target) + "' x2='" + (width - pad.r) + "' y2='" + y(options.target) + "'/>");
      out.push("<text x='" + (width - pad.r) + "' y='" + (y(options.target) - 4) + "' text-anchor='end'>pass bar " + options.target + "</text>");
    }
    var path = [];
    points.forEach(function (point, index) {
      var cx = points.length === 1 ? (pad.l + width - pad.r) / 2 : x(index);
      path.push((index === 0 ? "M" : "L") + cx + " " + y(point.value));
    });
    out.push("<path class='series' d='" + path.join(" ") + "'/>");
    points.forEach(function (point, index) {
      var cx = points.length === 1 ? (pad.l + width - pad.r) / 2 : x(index);
      out.push("<circle class='point" + (point.pass ? " pass" : "") + "' cx='" + cx + "' cy='" + y(point.value) + "' r='4.5'/>");
      out.push("<text x='" + cx + "' y='" + (height - pad.b + 15) + "' text-anchor='middle'>" + esc(point.label) + "</text>");
      out.push("<text x='" + cx + "' y='" + (y(point.value) - 9) + "' text-anchor='middle'>" + point.value.toFixed(3) + "</text>");
    });
    if (options.yLabel) {
      out.push("<text x='10' y='" + (height / 2) + "' transform='rotate(-90 10 " + (height / 2) + ")' text-anchor='middle'>" + esc(options.yLabel) + "</text>");
    }
    out.push("</svg>");
    return out.join("");
  }

  /* ------------------------------------------------------------- pair bars */
  function pairBars(rows, options) {
    options = options || {};
    var width = 1200, rowHeight = 40, pad = { l: 240, r: 90, t: 10, b: 34 };
    var height = pad.t + pad.b + Math.max(rows.length, 1) * rowHeight;
    if (!rows.length) return "<p class='muted small'>No benchmark has been run yet.</p>";
    var max = options.max || Math.max.apply(null, rows.map(function (r) {
      return Math.max(r.a === null ? 0 : r.a, r.b === null ? 0 : r.b);
    }).concat([1]));
    var out = ["<svg class='chart' viewBox='0 0 " + width + " " + height + "' role='img'>"];
    rows.forEach(function (row, index) {
      var top = pad.t + index * rowHeight;
      var barHeight = 12;
      out.push("<text x='" + (pad.l - 8) + "' y='" + (top + 16) + "' text-anchor='end'>" + esc(row.label) + "</text>");
      [[row.a, "bar-agent", 0], [row.b, "bar-ofat", 15]].forEach(function (pair) {
        var value = pair[0];
        var yTop = top + pair[2];
        var drawn = value === null ? 0 : Math.max(2, ((width - pad.l - pad.r) * value) / max);
        var cls = value === null ? "bar-ofat" : pair[1];
        out.push("<rect class='" + cls + "' x='" + pad.l + "' y='" + yTop + "' width='" + drawn + "' height='" + barHeight + "' rx='3'/>");
        out.push("<text x='" + (pad.l + drawn + 6) + "' y='" + (yTop + 9) + "'>" +
          (value === null ? "not reached" : value) + "</text>");
      });
    });
    out.push("<text x='" + pad.l + "' y='" + (height - 8) + "'>" + esc(options.xLabel || "trials to target") + "</text>");
    out.push("</svg>");
    return out.join("");
  }

  /* --------------------------------------------------------- interval plot */
  function intervalPlot(rows, options) {
    options = options || {};
    var width = 1200, rowHeight = 34, pad = { l: 200, r: 90, t: 12, b: 30 };
    var height = pad.t + pad.b + Math.max(rows.length, 1) * rowHeight;
    if (!rows.length) return "<p class='muted small'>Nothing measured yet.</p>";
    var out = ["<svg class='chart' viewBox='0 0 " + width + " " + height + "' role='img'>"];
    rows.forEach(function (row, index) {
      var top = pad.t + index * rowHeight + 14;
      // The domain has to contain the measured value as well as the interval: a
      // measurement *outside* the interval is the single most interesting point on
      // this chart, and clipping it was hiding exactly the evidence the diagnosis
      // is built on.
      var pad0 = (row.hi - row.lo) * 0.3;
      var low = Math.min(row.lo - pad0, row.measured - pad0 * 0.5);
      var high = Math.max(row.hi + pad0, row.measured + pad0 * 0.5);
      var scaleRow = scale([low, high], [pad.l, width - pad.r]);
      out.push("<text x='" + (pad.l - 8) + "' y='" + (top + 4) + "' text-anchor='end'>" + esc(row.label) + "</text>");
      out.push("<line class='axis' x1='" + scaleRow(row.lo) + "' y1='" + top + "' x2='" + scaleRow(row.hi) + "' y2='" + top + "' stroke-width='6' stroke='#dfe8e2'/>");
      out.push("<circle cx='" + scaleRow(row.predicted) + "' cy='" + top + "' r='4' fill='#8f9f96'/>");
      var inside = row.measured >= row.lo && row.measured <= row.hi;
      out.push("<circle cx='" + scaleRow(row.measured) + "' cy='" + top + "' r='5.5' fill='" + (inside ? "#1f7a4d" : "#b3261e") + "'/>");
      out.push("<text x='" + (width - pad.r + 6) + "' y='" + (top + 4) + "'>" + row.measured.toFixed(2) + "</text>");
    });
    out.push("</svg>");
    return out.join("");
  }

  function gaugeBars(rows) {
    if (!rows.length) return "";
    var out = ["<table class='compact'><thead><tr><th>Target</th><th class='num'>Value</th><th class='num'>Target</th><th>Desirability</th><th></th></tr></thead><tbody>"];
    rows.forEach(function (row) {
      var pct = Math.round(Math.max(0, Math.min(1, row.score)) * 100);
      out.push("<tr><td>" + esc(row.label) + "</td>" +
        "<td class='num'>" + row.value.toFixed(row.digits === undefined ? 2 : row.digits) + "</td>" +
        "<td class='num'>" + (row.target === null || row.target === undefined ? "-" : row.target) + "</td>" +
        "<td><div class='bar " + esc(row.status) + "'><span style='width:" + pct + "%'></span></div></td>" +
        "<td class='num'>" + pct + "%</td></tr>");
    });
    out.push("</tbody></table>");
    return out.join("");
  }

  window.Charts = {
    objectiveLine: objectiveLine,
    pairBars: pairBars,
    intervalPlot: intervalPlot,
    gaugeBars: gaugeBars,
    esc: esc
  };
})();
