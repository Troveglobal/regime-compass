/* ============================================================
   Regime Compass — shared shell (nav, footer, loader, banner)
   Include synchronously right after <body>:
     <script src="/shell.js?v=4.0"></script>
   Injects the header chrome where the tag sits, appends the
   footer at end of body, and exposes small utilities on RC.
   ============================================================ */
(function () {
  "use strict";

  var LOGO =
    '<svg class="logo-svg" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-label="Regime Compass logo">' +
    '<circle cx="12" cy="12" r="10" class="ring" fill="none" stroke="currentColor" stroke-width="0.6" opacity="0.5"/>' +
    '<path d="M12 2.5 L9.6 11 L14.4 11 Z" fill="#27ae60"/>' +
    '<path d="M12 21.5 L9.6 13 L14.4 13 Z" fill="#c0392b"/>' +
    '<rect x="19" y="11.4" width="2.7" height="1.2" fill="#d4a017"/>' +
    '<rect x="2.3" y="11.4" width="2.7" height="1.2" fill="#d4a017"/>' +
    '<circle cx="12" cy="12" r="1.4" class="needle" fill="currentColor"/>' +
    "</svg>";

  var NAV =
    '<div class="page-loader" id="page-loader"></div>' +
    '<div class="stale-banner" id="stale-banner"></div>' +
    '<nav class="top"><div class="inner">' +
    '<a class="brand" href="/">' + LOGO + " Regime Compass</a>" +
    '<button class="nav-burger" type="button" aria-label="Menu" aria-expanded="false"><span></span><span></span><span></span></button>' +
    '<div class="links">' +
    '<a href="/" data-nav="/">Home</a>' +
    '<span class="nav-dd"><span class="dd-trigger">Signals <span class="chev">&#9662;</span></span><span class="dd-menu">' +
    '<a href="/composite" data-nav="/composite">Risk Score <span class="dd-note">Composite</span></a>' +
    '<a href="/hmm" data-nav="/hmm">HMM <span class="dd-note">Hidden Markov Model</span></a>' +
    '<a href="/ma" data-nav="/ma">MA <span class="dd-note">Moving Average</span></a>' +
    '<a href="/ema" data-nav="/ema">EMA <span class="dd-note">Exponential Moving Average</span></a>' +
    "</span></span>" +
    '<span class="nav-dd"><span class="dd-trigger">Smart Money <span class="chev">&#9662;</span></span><span class="dd-menu">' +
    '<a href="/smartmoney" data-nav="/smartmoney">🇮🇳 India <span class="dd-note">deals · flows · stakes</span></a>' +
    '<a href="/smartmoney/tw" data-nav="/smartmoney/tw">🇹🇼 Taiwan <span class="dd-note">TWSE block deals</span></a>' +
    '<a href="/smartmoney/id" data-nav="/smartmoney/id">🇮🇩 Indonesia <span class="dd-note">IDX negotiated deals</span></a>' +
    '<a href="/smartmoney/us" data-nav="/smartmoney/us">🇺🇸 United States <span class="dd-note">insiders · Congress</span></a>' +
    "</span></span>" +
    '<span class="nav-dd"><span class="dd-trigger">Markets <span class="chev">&#9662;</span></span><span class="dd-menu">' +
    '<a href="/sectors" data-nav="/sectors">Sector Heatmap <span class="dd-note">India · daily</span></a>' +
    '<a href="/correlations" data-nav="/correlations">Correlations <span class="dd-note">cross-asset matrix</span></a>' +
    '<a href="/volatility" data-nav="/volatility">Volatility <span class="dd-note">stress percentile</span></a>' +
    '<a href="/yields" data-nav="/yields">Yield Curve</a>' +
    '<a href="/valuation" data-nav="/valuation">Valuation</a>' +
    '<a href="/seasonality" data-nav="/seasonality">Seasonality</a>' +
    '<a href="/calendar" data-nav="/calendar">Calendar</a>' +
    "</span></span>" +
    '<a href="/news" data-nav="/news">News</a>' +
    '<a href="/changes" data-nav="/changes">Changes</a>' +
    '<a href="/subscribe" data-nav="/subscribe">Alerts</a>' +
    '<a href="/about" data-nav="/about">About</a>' +
    "</div></div></nav>";

  var FOOTER =
    '<footer class="site">' +
    '<div class="inner">' +
    '<div class="f-brand">' +
    '<span class="brand-line">' + LOGO + " Regime Compass</span>" +
    "<p>Daily regime classification across eleven global markets. Three independent models, one honest snapshot. An iQuant Labs project.</p>" +
    "</div>" +
    '<div class="f-col"><h5>Signals</h5>' +
    '<a href="/today">Daily Brief</a><a href="/composite">Risk Score</a><a href="/hmm">HMM</a><a href="/ma">Moving Average</a><a href="/ema">EMA</a><a href="/smartmoney">Smart Money India</a><a href="/smartmoney/tw">Smart Money Taiwan</a><a href="/smartmoney/id">Smart Money Indonesia</a><a href="/smartmoney/us">Smart Money US</a></div>' +
    '<div class="f-col"><h5>Markets</h5>' +
    '<a href="/sectors">Sector Heatmap</a><a href="/correlations">Correlations</a><a href="/volatility">Volatility</a><a href="/yields">Yield Curve</a><a href="/valuation">Valuation</a><a href="/seasonality">Seasonality</a><a href="/calendar">Calendar</a><a href="/news">News</a></div>' +
    '<div class="f-col"><h5>Company</h5>' +
    '<a href="/about">About</a><a href="/methodology">Methodology</a><a href="/subscribe">Alerts</a><a href="/disclaimer">Disclaimer</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a></div>' +
    "</div>" +
    '<div class="f-bottom">' +
    '<span class="built-by">Built by <a href="https://www.linkedin.com/in/aditya-s1/" target="_blank" rel="noopener">Aditya Sahasrabuddhe' +
    '<svg class="icon-li" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.063 2.063 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg></a></span>' +
    '<span>Not investment advice.</span>' +
    '<span style="margin-left:auto">&copy; ' + new Date().getFullYear() + " iQuant Labs</span>" +
    "</div></footer>";

  /* ---- inject header where this script tag sits ---- */
  var me = document.currentScript;
  me.insertAdjacentHTML("beforebegin", NAV);

  var nav = document.querySelector("nav.top");
  var inner = nav.querySelector(".inner");

  /* active link + category highlight */
  var path = location.pathname.replace(/\/+$/, "") || "/";
  nav.querySelectorAll("[data-nav]").forEach(function (a) {
    if (a.getAttribute("data-nav") === path) {
      a.classList.add("active");
      var dd = a.closest(".nav-dd");
      if (dd) dd.classList.add("cat-active");
    }
  });

  /* burger + tap-toggle dropdowns */
  var burger = nav.querySelector(".nav-burger");
  burger.addEventListener("click", function (e) {
    e.stopPropagation();
    var open = inner.classList.toggle("menu-open");
    burger.setAttribute("aria-expanded", open ? "true" : "false");
  });
  nav.querySelectorAll(".nav-dd > .dd-trigger").forEach(function (t) {
    t.addEventListener("click", function (e) {
      e.stopPropagation();
      var dd = t.parentNode, was = dd.classList.contains("open");
      nav.querySelectorAll(".nav-dd.open").forEach(function (o) { o.classList.remove("open"); });
      if (!was) dd.classList.add("open");
    });
  });
  document.addEventListener("click", function () {
    nav.querySelectorAll(".nav-dd.open").forEach(function (o) { o.classList.remove("open"); });
    inner.classList.remove("menu-open");
    burger.setAttribute("aria-expanded", "false");
  });

  /* scroll-aware elevation */
  var elevate = function () { nav.classList.toggle("scrolled", window.scrollY > 8); };
  window.addEventListener("scroll", elevate, { passive: true });
  elevate();

  /* ---- footer + boot tasks on DOM ready ---- */
  document.addEventListener("DOMContentLoaded", function () {
    document.body.insertAdjacentHTML("beforeend", FOOTER);

    /* loader: keep visible a beat so fast loads still feel alive */
    setTimeout(function () { document.body.classList.add("loaded"); }, 250);

    /* scroll reveal */
    RC.reveal();

    /* stale-data banner */
    fetch("/api/freshness").then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.any_stale) {
        var b = document.getElementById("stale-banner");
        b.innerHTML = "<strong>Data delayed.</strong> Latest fetch is " + d.max_age_hours.toFixed(0) +
          " hours old. Yahoo Finance may be temporarily unavailable; showing most recent cached values.";
        b.classList.add("show");
      }
    }).catch(function () {});

    /* cookie consent */
    var s = document.createElement("script");
    s.src = "/consent.js?v=3.1";
    document.body.appendChild(s);
  });

  /* ============================================================
     RC — shared utilities
     ============================================================ */
  var RC = (window.RC = window.RC || {});

  /* css var reader */
  RC.cssVar = function (name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  };

  /* IntersectionObserver scroll reveal: .reveal → .in */
  RC.reveal = function (root) {
    var els = (root || document).querySelectorAll(".reveal:not(.in)");
    /* background tabs get no IO callbacks — content must never stay hidden */
    if (!("IntersectionObserver" in window) || document.hidden) {
      els.forEach(function (el) { el.classList.add("in"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -40px 0px", threshold: 0.05 });
    els.forEach(function (el) { io.observe(el); });
  };

  /* animated count-up. opts: {decimals, prefix, suffix, duration, signed} */
  RC.countUp = function (el, target, opts) {
    opts = opts || {};
    var dur = opts.duration || 900;
    var dec = opts.decimals != null ? opts.decimals : 0;
    var pre = opts.prefix || "", suf = opts.suffix || "";
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var fmt = function (v) {
      var s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
      var sign = v < 0 ? "−" : (opts.signed && v > 0 ? "+" : "");
      return pre + sign + s + suf;
    };
    /* hidden tabs never get animation frames — land on the final value directly */
    if (reduced || document.hidden || !isFinite(target)) { el.textContent = fmt(target); return; }
    var t0 = null;
    var ease = function (t) { return 1 - Math.pow(1 - t, 3); };
    var step = function (ts) {
      if (!t0) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      el.textContent = fmt(target * ease(p));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  /* sliding indicator for .tabs — call after building tabs */
  RC.tabs = function (tabsEl, onChange) {
    var ind = tabsEl.querySelector(".tab-ind");
    if (!ind) {
      ind = document.createElement("span");
      ind.className = "tab-ind";
      tabsEl.prepend(ind);
    }
    var position = function () {
      var act = tabsEl.querySelector(".tab.active");
      if (!act) return;
      ind.style.left = act.offsetLeft + "px";
      ind.style.width = act.offsetWidth + "px";
    };
    tabsEl.querySelectorAll(".tab").forEach(function (t) {
      t.addEventListener("click", function () {
        if (t.classList.contains("active")) return;
        tabsEl.querySelectorAll(".tab.active").forEach(function (a) { a.classList.remove("active"); });
        t.classList.add("active");
        position();
        if (onChange) onChange(t.dataset.tab || t.textContent.trim(), t);
      });
    });
    window.addEventListener("resize", position);
    /* fonts shift widths after load */
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(position);
    position();
    return { position: position };
  };

  /* formatting helpers */
  RC.fmt = {
    pct: function (v, d) { return (v * 100).toFixed(d != null ? d : 1) + "%"; },
    signedPct: function (v, d) { return (v >= 0 ? "+" : "") + v.toFixed(d != null ? d : 2) + "%"; },
    inr: function (cr) {
      /* crores → compact Indian-market notation */
      if (Math.abs(cr) >= 1000) return "₹" + (cr / 1000).toFixed(2) + "k cr";
      return "₹" + cr.toFixed(cr < 10 ? 1 : 0) + " cr";
    },
    num: function (v, d) { return v.toLocaleString("en-US", { maximumFractionDigits: d != null ? d : 0 }); },
  };
})();
