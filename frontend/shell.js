/* ============================================================
   Regime Compass — shared shell (nav, palette, footer, loader)
   Include synchronously right after <body>:
     <script src="/shell.js?v=6.0"></script>
   Injects the header chrome where the tag sits, appends the
   footer at end of body, and exposes small utilities on RC.

   v6 — IA overhaul: six intent-based categories, ⌘K command
   palette, account slot (auth/custom dashboards coming soon).
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

  /* platform-aware shortcut label: Mac gets ⌘K, everyone else Ctrl K */
  var IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  var KBD = IS_MAC ? "\u2318K" : "Ctrl K";

  /* ============================================================
     Navigation model — one source of truth for nav, footer and
     the command palette. Each item: [href, label, note, keywords]
     ============================================================ */
  var CATS = [
    { label: "Today", href: "/today" },
    {
      label: "Regime",
      items: [
        ["/composite", "Risk Score", "blended 0–100 gauge", "composite risk on off"],
        ["/hmm", "HMM", "probabilistic · 3-state", "hidden markov model"],
        ["/ma", "SMA", "price vs trailing average", "simple moving average trend"],
        ["/ema", "EMA", "faster-reacting trend", "exponential moving average"],
        ["/movers", "Regime Movers", "today's abnormal moves", "sigma z-score movers"],
        ["/changes", "Regime Changes", "every flip, logged", "changes log history flips"],
        ["/validation", "Validation", "walk-forward accuracy", "audit accuracy backtest"],
      ],
    },
    {
      label: "Risk",
      items: [
        ["/systemic", "Systemic Risk", "turbulence · absorption", "fragility turbulence absorption"],
        ["/geometry", "Market Geometry", "topology · market tree", "tda topology persistent homology mst tree network geometry"],
        ["/credit", "Credit Stress", "HY spread · equity overlay", "high yield spreads credit"],
        ["/volatility", "Volatility", "stress percentile", "vix vol stress"],
        ["/correlations", "Correlations", "cross-asset matrix", "correlation diversification"],
      ],
    },
    {
      label: "Macro",
      items: [
        ["/macro", "US Macro Surprise", "FRED surprise meter", "macro economic surprise fred"],
        ["/yields", "Yield Curve", "US · India", "yield curve inversion rates"],
        ["/valuation", "Valuation", "where markets are rich", "pe valuation cape"],
        ["/sectors", "Sector Heatmap", "India · daily", "sector rotation heatmap nifty"],
        ["/seasonality", "Seasonality", "monthly patterns", "seasonality month patterns"],
        ["/calendar", "Calendar", "what's ahead", "economic calendar events"],
      ],
    },
    {
      label: "Smart Money",
      items: [
        ["/smartmoney", "🇮🇳 India", "deals · flows · stakes", "nse bulk block fii dii india"],
        ["/smartmoney/tw", "🇹🇼 Taiwan", "TWSE block deals", "taiwan twse block"],
        ["/smartmoney/id", "🇮🇩 Indonesia", "IDX negotiated deals", "indonesia idx negotiated"],
        ["/smartmoney/us", "🇺🇸 United States", "insiders · Congress", "insider congress filings us"],
      ],
    },
    { label: "Strategies", href: "/strategies" },
    { label: "Research", href: "/research" },
    {
      label: "Explore",
      items: [
        ["/countries", "All Countries", "regime · GDP · news", "country hubs economies"],
        ["/assets", "All Assets", "regime · vol · correlations", "asset hubs"],
        ["/country/usa", "🇺🇸 United States", "", "usa america country"],
        ["/country/india", "🇮🇳 India", "", "india country nifty"],
        ["/asset/bitcoin", "₿ Bitcoin", "", "btc crypto bitcoin"],
        ["/asset/gold", "🥇 Gold", "", "gold commodity"],
        ["/asset/oil", "🛢️ Crude Oil", "", "wti oil energy crude"],
        ["/asset/treasuries", "🏛️ US 10Y Treasuries", "", "bonds rates treasury 10y"],
        ["/asset/dollar", "💵 US Dollar Index", "", "dxy dollar fx currency"],
      ],
    },
    { label: "News", href: "/news" },
  ];

  /* pages reachable via palette / footer but not the top bar */
  var EXTRA_PAGES = [
    ["/", "Home", "Overview", "home start landing"],
    ["/today", "Daily Brief", "Today", "today brief snapshot daily"],
    ["/strategies", "Strategies", "Portfolios", "strategies model portfolios backtest track record smart money credit regime"],
    ["/research", "Research", "Research", "research notes articles blog ideas"],
    ["/research/why-regimes-matter", "Regime investing for the risk-averse", "Research", "drawdown sequence risk regime article"],
    ["/research/three-models-one-verdict", "Why three models beat one", "Research", "hmm sma ema ensemble article"],
    ["/research/smart-money-paper-trail", "Smart money leaves a paper trail", "Research", "insider bulk block deals article"],
    ["/research/credit-knows-first", "Credit usually knows first", "Research", "credit spreads early warning article"],
    ["/research/anatomy-of-fragility", "Turbulence, absorption & fragility", "Research", "systemic turbulence absorption article"],
    ["/research/honest-backtest", "The honest backtest", "Research", "walk forward validation backtest article"],
    ["/news", "News", "News", "headlines news"],
    ["/ma/backtest", "SMA Backtest", "Regime", "sma backtest performance"],
    ["/ema/backtest", "EMA Backtest", "Regime", "ema backtest performance"],
    ["/country/eurozone", "🇪🇺 Eurozone", "Explore", "europe eurozone stoxx"],
    ["/country/japan", "🇯🇵 Japan", "Explore", "japan nikkei"],
    ["/country/south-korea", "🇰🇷 South Korea", "Explore", "korea kospi"],
    ["/country/china", "🇨🇳 China", "Explore", "china shanghai"],
    ["/asset/ethereum", "Ξ Ethereum", "Explore", "eth crypto ethereum"],
    ["/asset/silver", "🥈 Silver", "Explore", "silver commodity"],
    ["/asset/copper", "🔶 Copper", "Explore", "copper dr commodity growth"],
    ["/country/hong-kong", "🇭🇰 Hong Kong", "Explore", "hong kong hang seng hsi"],
    ["/country/united-kingdom", "🇬🇧 United Kingdom", "Explore", "uk ftse gilts"],
    ["/country/brazil", "🇧🇷 Brazil", "Explore", "brazil bovespa latam"],
    ["/country/saudi-arabia", "🇸🇦 Saudi Arabia", "Explore", "saudi tadawul gcc aramco"],
    ["/country/taiwan", "🇹🇼 Taiwan", "Explore", "taiwan taiex twii semiconductors"],
    ["/subscribe", "Alerts", "Account", "subscribe email alerts notify"],
    ["/feedback", "Feedback & Early Access", "Account", "feedback waitlist early access suggest missing"],
    ["/about", "About", "Company", "about iquant aditya"],
    ["/methodology", "Methodology", "Company", "methodology how it works models"],
    ["/disclaimer", "Disclaimer", "Company", "legal disclaimer"],
    ["/terms", "Terms", "Company", "legal terms"],
    ["/privacy", "Privacy", "Company", "legal privacy cookies"],
  ];

  /* prefix → category for active-state highlighting on spoke pages */
  var PREFIX_CAT = {
    "/research": "Research",
    "/smartmoney": "Smart Money",
    "/country": "Explore",
    "/countries": "Explore",
    "/asset": "Explore",
    "/assets": "Explore",
    "/ma": "Regime",
    "/ema": "Regime",
  };

  /* ---------- build nav html from the model ---------- */
  function ddItem(it) {
    var note = it[2] ? '<span class="dd-note">' + it[2] + "</span>" : "";
    return '<a href="' + it[0] + '" data-nav="' + it[0] + '">' + it[1] + note + "</a>";
  }

  var linksHtml = CATS.map(function (c) {
    if (c.href) return '<a href="' + c.href + '" data-nav="' + c.href + '">' + c.label + "</a>";
    return (
      '<span class="nav-dd" data-cat="' + c.label + '"><span class="dd-trigger">' + c.label +
      ' <span class="chev">&#9662;</span></span><span class="dd-menu">' +
      c.items.map(ddItem).join("") +
      "</span></span>"
    );
  }).join("");

  var NAV =
    '<div class="page-loader" id="page-loader"></div>' +
    '<div class="stale-banner" id="stale-banner"></div>' +
    '<nav class="top"><div class="inner">' +
    '<a class="brand" href="/">' + LOGO + " Regime Compass</a>" +
    '<button class="nav-burger" type="button" aria-label="Menu" aria-expanded="false"><span></span><span></span><span></span></button>' +
    '<div class="links">' + linksHtml + "</div>" +
    '<div class="nav-right">' +
    '<button class="nav-search" type="button" id="nav-search" aria-label="Search tools (' + KBD + ')" title="Jump to any tool (' + KBD + ')">' +
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>' +
    '<span class="kbd-hint">' + KBD + '</span></button>' +
    '<span class="nav-dd dd-right"><span class="dd-trigger nav-account" aria-label="Account">' +
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"/></svg>' +
    '<span class="chev">&#9662;</span></span><span class="dd-menu">' +
    '<span class="dd-head">Personalization is coming</span>' +
    '<span class="dd-disabled">My Dashboard <span class="soon-pill">soon</span><span class="dd-note">compose your own tools</span></span>' +
    '<span class="dd-disabled">Sign in <span class="soon-pill">soon</span><span class="dd-note">accounts &amp; saved layouts</span></span>' +
    '<a href="/feedback" data-nav="/feedback">Early Access <span class="soon-pill">waitlist</span></a>' +
    '<span class="dd-sep"></span>' +
    '<a href="/subscribe" data-nav="/subscribe">Email Alerts</a>' +
    '<a href="/about" data-nav="/about">About</a>' +
    '<a href="/methodology" data-nav="/methodology">Methodology</a>' +
    "</span></span>" +
    "</div></div></nav>";

  /* ---------- footer, re-grouped to the same taxonomy ---------- */
  var FOOTER =
    '<footer class="site">' +
    '<div class="inner">' +
    '<div class="f-brand">' +
    '<span class="brand-line">' + LOGO + " Regime Compass</span>" +
    "<p>Daily regime classification across 20 global markets — equities, rates, currencies, commodities and crypto. Three independent models, one honest snapshot. An iQuant Labs project.</p>" +
    '<p class="f-soon">Accounts &amp; custom dashboards are in the works — <a href="/feedback">sign up for early access</a>.</p>' +
    "</div>" +
    '<div class="f-col"><h5>Regime</h5>' +
    '<a href="/today">Daily Brief</a><a href="/strategies">Strategies</a><a href="/composite">Risk Score</a><a href="/hmm">HMM</a><a href="/ma">SMA</a><a href="/ema">EMA</a><a href="/movers">Regime Movers</a><a href="/changes">Regime Changes</a><a href="/validation">Validation</a></div>' +
    '<div class="f-col"><h5>Risk &amp; Macro</h5>' +
    '<a href="/systemic">Systemic Risk</a><a href="/geometry">Market Geometry</a><a href="/credit">Credit Stress</a><a href="/volatility">Volatility</a><a href="/correlations">Correlations</a><a href="/macro">US Macro</a><a href="/yields">Yield Curve</a><a href="/valuation">Valuation</a><a href="/sectors">Sectors</a><a href="/seasonality">Seasonality</a><a href="/calendar">Calendar</a></div>' +
    '<div class="f-col"><h5>Smart Money &amp; Explore</h5>' +
    '<a href="/smartmoney">India</a><a href="/smartmoney/tw">Taiwan</a><a href="/smartmoney/id">Indonesia</a><a href="/smartmoney/us">United States</a><a href="/countries">Countries</a><a href="/assets">Assets</a><a href="/news">News</a></div>' +
    '<div class="f-col"><h5>Company</h5>' +
    '<a href="/research">Research</a><a href="/about">About</a><a href="/methodology">Methodology</a><a href="/subscribe">Alerts</a><a href="/feedback">Feedback</a><a href="/disclaimer">Disclaimer</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a></div>' +
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

  /* active link + category highlight (exact match, then prefix fallback) */
  var path = location.pathname.replace(/\/+$/, "") || "/";
  var matched = false;
  nav.querySelectorAll("[data-nav]").forEach(function (a) {
    if (a.getAttribute("data-nav") === path) {
      matched = true;
      a.classList.add("active");
      var dd = a.closest(".nav-dd");
      if (dd) dd.classList.add("cat-active");
    }
  });
  if (!matched) {
    var seg = "/" + (path.split("/")[1] || "");
    var cat = PREFIX_CAT[seg];
    if (cat) {
      var dd = nav.querySelector('.nav-dd[data-cat="' + cat + '"]');
      if (dd) dd.classList.add("cat-active");
      else {
        /* flat top-level link (e.g. Research on /research/{slug}) */
        var flat = nav.querySelector('.links > a[data-nav="' + seg + '"]');
        if (flat) flat.classList.add("active");
      }
    }
  }

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

  /* ============================================================
     Command palette (⌘K / Ctrl-K) — jump to any tool
     ============================================================ */
  var PALETTE = [];
  CATS.forEach(function (c) {
    if (c.items) c.items.forEach(function (it) {
      PALETTE.push({ href: it[0], label: it[1], cat: c.label, kw: (it[2] || "") + " " + (it[3] || "") });
    });
  });
  EXTRA_PAGES.forEach(function (it) {
    if (PALETTE.some(function (p) { return p.href === it[0]; })) return;
    PALETTE.push({ href: it[0], label: it[1], cat: it[2], kw: it[3] || "" });
  });

  var palEl = null, palInput = null, palList = null, palSel = 0, palRows = [];

  function palBuild() {
    if (palEl) return;
    palEl = document.createElement("div");
    palEl.className = "cmdk";
    palEl.innerHTML =
      '<div class="cmdk-box" role="dialog" aria-label="Jump to tool">' +
      '<div class="cmdk-inputwrap">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>' +
      '<input type="text" class="cmdk-input" placeholder="Jump to any tool…" spellcheck="false" autocomplete="off"/>' +
      '<span class="cmdk-esc">esc</span></div>' +
      '<div class="cmdk-list"></div>' +
      '<div class="cmdk-foot"><span>↑↓ navigate</span><span>↵ open</span><span class="cmdk-soon">Custom dashboards — coming soon</span></div>' +
      "</div>";
    document.body.appendChild(palEl);
    palInput = palEl.querySelector(".cmdk-input");
    palList = palEl.querySelector(".cmdk-list");
    palEl.addEventListener("click", function (e) { if (e.target === palEl) palClose(); });
    palInput.addEventListener("input", function () { palRender(palInput.value); });
    palInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); palMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); palMove(-1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        var r = palRows[palSel];
        if (r) window.location.href = r.href;
      } else if (e.key === "Escape") palClose();
    });
  }

  function palFilter(q) {
    q = q.trim().toLowerCase();
    if (!q) return PALETTE.slice();
    var starts = [], inLabel = [], inKw = [];
    PALETTE.forEach(function (p) {
      var l = p.label.toLowerCase(), hay = (l + " " + p.cat + " " + p.kw).toLowerCase();
      if (l.indexOf(q) === 0) starts.push(p);
      else if (l.indexOf(q) >= 0) inLabel.push(p);
      else if (q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; })) inKw.push(p);
    });
    return starts.concat(inLabel, inKw);
  }

  function palRender(q) {
    palRows = palFilter(q);
    palSel = 0;
    if (!palRows.length) {
      palList.innerHTML = '<div class="cmdk-empty">No matching tool</div>';
      return;
    }
    palList.innerHTML = palRows.map(function (p, i) {
      return '<a class="cmdk-row' + (i === palSel ? " sel" : "") + '" href="' + p.href + '" data-i="' + i + '">' +
        "<span>" + p.label + "</span><span class='cmdk-cat'>" + p.cat + "</span></a>";
    }).join("");
    palList.querySelectorAll(".cmdk-row").forEach(function (row) {
      row.addEventListener("mouseenter", function () { palSelect(+row.dataset.i); });
    });
  }

  function palSelect(i) {
    palSel = i;
    palList.querySelectorAll(".cmdk-row").forEach(function (r, j) { r.classList.toggle("sel", j === palSel); });
  }

  function palMove(d) {
    if (!palRows.length) return;
    palSelect((palSel + d + palRows.length) % palRows.length);
    var el = palList.querySelector(".cmdk-row.sel");
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function palOpen() {
    palBuild();
    palEl.classList.add("show");
    palInput.value = "";
    palRender("");
    palInput.focus();
    setTimeout(function () { palInput.focus(); }, 20);
  }
  function palClose() { if (palEl) palEl.classList.remove("show"); }

  document.getElementById("nav-search").addEventListener("click", function (e) {
    e.stopPropagation(); palOpen();
  });
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (palEl && palEl.classList.contains("show")) palClose(); else palOpen();
    }
  });

  /* ---- footer + boot tasks on DOM ready ---- */
  document.addEventListener("DOMContentLoaded", function () {
    document.body.insertAdjacentHTML("beforeend", FOOTER);

    /* pages hardcode ⌘K in hints — rewrite for the actual platform */
    if (!IS_MAC) document.querySelectorAll(".kbd-hint").forEach(function (el) { el.textContent = KBD; });

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

  /* open the command palette programmatically */
  RC.palette = palOpen;

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
