/* ============================================================
   Regime Compass — shared regime-ribbon sparkline
   Extracted from index.html so hub pages reuse one implementation.
   Usage:  RCRibbon.build(containerEl, market, stateLabels)
     market = {index_key, name, points: [[date, close, stateIdx], ...]}
     (the /api/sparklines feed shape)
   ============================================================ */
(function () {
  "use strict";

  var BAND = ["rgba(227,84,84,0.15)", "rgba(212,160,23,0.13)", "rgba(52,198,115,0.15)"]; // bear, neutral, bull
  var SVG_NS = "http://www.w3.org/2000/svg";

  /* base styles (skipped if the page already defines them, e.g. index.html) */
  if (!document.getElementById("rc-ribbon-css")) {
    var css = document.createElement("style");
    css.id = "rc-ribbon-css";
    css.textContent =
      ".spark-ribbon{width:100%;border-radius:6px;overflow:hidden;border:1px solid var(--hairline,rgba(255,255,255,0.06));}" +
      ".spark-ribbon svg{display:block;width:100%;height:34px;}" +
      "#spark-tt{position:fixed;z-index:50;pointer-events:none;background:var(--bg-elev,#0c0f16);border:1px solid var(--border,rgba(255,255,255,0.1));" +
      "border-radius:8px;padding:6px 9px;font-size:11px;line-height:1.5;color:var(--text,#e8edf4);opacity:0;transition:opacity .15s;font-family:var(--font-mono,monospace);}" +
      "#spark-tt.show{opacity:1;}#spark-tt .d{color:var(--muted,#8b93a1);}";
    document.head.appendChild(css);
  }

  function build(container, mkt, stateLabels) {
    var pts = mkt.points; // [[date, close, stateIdx], ...]
    if (!pts || pts.length < 2) return;
    var W = 200, H = 34, PAD = 3;
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-label", mkt.name + " — 90-session price with regime shading");

    var x = function (i) { return (i / (pts.length - 1)) * W; };

    /* contiguous same-state runs merge into single background rects */
    var runStart = 0;
    for (var i = 1; i <= pts.length; i++) {
      if (i === pts.length || pts[i][2] !== pts[runStart][2]) {
        var rect = document.createElementNS(SVG_NS, "rect");
        var x0 = x(runStart === 0 ? 0 : runStart - 0.5);
        rect.setAttribute("x", x0);
        rect.setAttribute("width", x(i === pts.length ? pts.length - 1 : i - 0.5) - x0);
        rect.setAttribute("y", 0); rect.setAttribute("height", H);
        rect.setAttribute("fill", BAND[pts[runStart][2]]);
        svg.appendChild(rect);
        runStart = i;
      }
    }

    /* price line, normalized to its own min/max */
    var closes = pts.map(function (p) { return p[1]; });
    var lo = Math.min.apply(null, closes), hi = Math.max.apply(null, closes);
    var span = hi - lo || 1;
    var y = function (v) { return H - PAD - ((v - lo) / span) * (H - 2 * PAD); };
    var line = document.createElementNS(SVG_NS, "polyline");
    line.setAttribute("points", pts.map(function (p, i) { return x(i).toFixed(1) + "," + y(p[1]).toFixed(1); }).join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "rgba(198,207,218,0.85)");
    line.setAttribute("stroke-width", "2");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("stroke-linejoin", "round");
    svg.appendChild(line);

    var wrap = document.createElement("div");
    wrap.className = "spark-ribbon";
    wrap.appendChild(svg);
    container.appendChild(wrap);

    /* hover tooltip — desktop only */
    if (window.matchMedia("(hover: hover)").matches) {
      var tt = document.getElementById("spark-tt");
      if (!tt) { tt = document.createElement("div"); tt.id = "spark-tt"; document.body.appendChild(tt); }
      svg.addEventListener("mousemove", function (e) {
        var r = svg.getBoundingClientRect();
        var i = Math.max(0, Math.min(pts.length - 1, Math.round(((e.clientX - r.left) / r.width) * (pts.length - 1))));
        var p = pts[i];
        tt.innerHTML = '<span class="d">' + p[0] + "</span><br>" +
          p[1].toLocaleString("en-US", { maximumFractionDigits: 2 }) + " · " + stateLabels[p[2]];
        tt.style.left = Math.min(e.clientX + 12, window.innerWidth - tt.offsetWidth - 10) + "px";
        tt.style.top = (r.top - tt.offsetHeight - 8) + "px";
        tt.classList.add("show");
      });
      svg.addEventListener("mouseleave", function () { tt.classList.remove("show"); });
      window.addEventListener("scroll", function () { tt.classList.remove("show"); }, { passive: true });
    }
  }

  window.RCRibbon = { build: build, BAND: BAND };
})();
