/* ============================================================
   Regime Compass — Chart.js v4 shared theme
   Load AFTER chart.umd.min.js and /shell.js:
     <script src="/charts.js?v=4.0"></script>
   Reads design tokens from CSS custom properties and applies
   them as Chart.js defaults; exposes helpers on RCCharts.
   ============================================================ */
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;

  var cssVar = function (n) {
    return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  };
  var alpha = function (hex, a) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  };

  var T = {
    text: cssVar("--text") || "#e8edf4",
    muted: cssVar("--muted") || "#8b95a3",
    grid: "rgba(255,255,255,0.045)",
    surface: cssVar("--bg") || "#07090e",
    fontUI: '"Inter", system-ui, sans-serif',
    fontMono: '"JetBrains Mono", ui-monospace, monospace',
  };

  var RCCharts = (window.RCCharts = {
    alpha: alpha,
    /* regime marks — CVD-validated on the dark surface */
    regime: {
      bear: cssVar("--bear-chart") || "#e35454",
      neutral: cssVar("--neutral-chart") || "#b8860b",
      bull: cssVar("--bull-chart") || "#2fae63",
    },
    /* UI-bright regime variants (pills, emphasis lines) */
    regimeUI: {
      bear: cssVar("--bear") || "#e35454",
      neutral: cssVar("--neutral") || "#d4a017",
      bull: cssVar("--bull") || "#34c673",
    },
    /* fixed categorical order — never cycled */
    cat: [
      cssVar("--cat-1") || "#8b6ff0",
      cssVar("--cat-2") || "#3f9ae8",
      cssVar("--cat-3") || "#2fae63",
      cssVar("--cat-4") || "#b8860b",
      cssVar("--cat-5") || "#e35454",
      cssVar("--cat-6") || "#d1568b",
    ],
    accent: cssVar("--accent") || "#a78bfa",
    textColor: T.text,
    mutedColor: T.muted,
  });

  /* vertical gradient wash under a line (series hue → transparent) */
  RCCharts.gradient = function (ctx, color, opacity) {
    var g = null, w = 0, h = 0;
    return function (context) {
      var area = context.chart.chartArea;
      if (!area) return alpha(color, 0.06);
      if (!g || w !== area.width || h !== area.height) {
        w = area.width; h = area.height;
        g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
        g.addColorStop(0, alpha(color, opacity != null ? opacity : 0.18));
        g.addColorStop(1, alpha(color, 0));
      }
      return g;
    };
  };

  /* ---- global defaults ---- */
  Chart.defaults.color = T.muted;
  Chart.defaults.borderColor = T.grid;
  Chart.defaults.font.family = T.fontUI;
  Chart.defaults.font.size = 11;
  Chart.defaults.animation.duration = 700;
  Chart.defaults.animation.easing = "easeOutQuart";
  Chart.defaults.responsive = true;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.interaction = { mode: "index", intersect: false };

  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.line.borderJoinStyle = "round";
  Chart.defaults.elements.line.borderCapStyle = "round";
  Chart.defaults.elements.line.tension = 0.3;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 4;
  Chart.defaults.elements.point.hoverBorderWidth = 2;
  Chart.defaults.elements.point.hoverBorderColor = T.surface;
  Chart.defaults.elements.bar.borderRadius = { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 };
  Chart.defaults.elements.bar.borderSkipped = "bottom";

  Chart.defaults.plugins.legend.position = "top";
  Chart.defaults.plugins.legend.align = "end";
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.pointStyle = "line";
  Chart.defaults.plugins.legend.labels.boxWidth = 18;
  Chart.defaults.plugins.legend.labels.boxHeight = 2;
  Chart.defaults.plugins.legend.labels.padding = 16;
  Chart.defaults.plugins.legend.labels.color = T.muted;

  Chart.defaults.scale.grid = { color: T.grid, drawTicks: false };
  Chart.defaults.scale.border = { display: false };
  Chart.defaults.scale.ticks.color = T.muted;
  Chart.defaults.scale.ticks.font = { family: T.fontMono, size: 10.5 };
  Chart.defaults.scale.ticks.maxRotation = 0;
  Chart.defaults.scale.ticks.padding = 8;

  /* ---- glass external tooltip (#rc-tooltip, styled in styles.css) ---- */
  var ttEl = null;
  var getTT = function () {
    if (!ttEl) {
      ttEl = document.createElement("div");
      ttEl.id = "rc-tooltip";
      document.body.appendChild(ttEl);
    }
    return ttEl;
  };

  RCCharts.externalTooltip = function (context) {
    var tt = context.tooltip;
    var el = getTT();
    if (tt.opacity === 0) { el.classList.remove("show"); return; }

    el.textContent = "";
    if (tt.title && tt.title.length) {
      var t = document.createElement("div");
      t.className = "tt-title";
      t.textContent = tt.title.join(" ");
      el.appendChild(t);
    }
    (tt.dataPoints || []).forEach(function (dp) {
      var row = document.createElement("div");
      row.className = "tt-row";
      var key = document.createElement("span");
      key.className = "tt-key";
      var c = dp.dataset.borderColor || dp.dataset.backgroundColor;
      key.style.background = typeof c === "string" ? c : RCCharts.accent;
      var lab = document.createElement("span");
      lab.className = "tt-label";
      lab.textContent = dp.dataset.label || "";
      var val = document.createElement("span");
      val.className = "tt-val";
      var v = dp.parsed.y != null ? dp.parsed.y : dp.parsed;
      var fmt = dp.dataset.rcFormat;
      val.textContent = fmt ? fmt(v, dp) : (typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(v));
      row.appendChild(key); row.appendChild(lab); row.appendChild(val);
      el.appendChild(row);
    });

    var pos = context.chart.canvas.getBoundingClientRect();
    var x = pos.left + tt.caretX, y = pos.top + tt.caretY;
    var w = el.offsetWidth, hh = el.offsetHeight;
    var left = x + 14;
    if (left + w > window.innerWidth - 12) left = x - w - 14;
    var top = y - hh / 2;
    top = Math.max(12, Math.min(top, window.innerHeight - hh - 12));
    el.style.left = left + "px";
    el.style.top = top + "px";
    el.classList.add("show");
  };

  Chart.defaults.plugins.tooltip.enabled = false;
  Chart.defaults.plugins.tooltip.external = RCCharts.externalTooltip;

  /* ---- crosshair plugin (vertical hairline finds the X) ---- */
  Chart.register({
    id: "rcCrosshair",
    afterDatasetsDraw: function (chart) {
      var active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      if (chart.config.type === "bar" && !(chart.config.options.rcCrosshair)) return;
      var x = active[0].element.x;
      var area = chart.chartArea;
      var ctx = chart.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(255,255,255,0.14)";
      ctx.stroke();
      ctx.restore();
    },
  });

  /* ---- sparkline factory: tiny unadorned line in a canvas ---- */
  RCCharts.sparkline = function (canvas, values, opts) {
    opts = opts || {};
    var color = opts.color || RCCharts.accent;
    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: values.map(function (_, i) { return i; }),
        datasets: [{
          data: values,
          borderColor: color,
          borderWidth: 1.5,
          backgroundColor: opts.fill ? RCCharts.gradient(canvas.getContext("2d"), color, 0.14) : "transparent",
          fill: !!opts.fill,
          pointRadius: 0,
          tension: 0.35,
        }],
      },
      options: {
        responsive: false,
        animation: { duration: 500 },
        plugins: { legend: { display: false }, tooltip: { enabled: false, external: null } },
        scales: { x: { display: false }, y: { display: false } },
        events: [],
      },
    });
  };
})();
