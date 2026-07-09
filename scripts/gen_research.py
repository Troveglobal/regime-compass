#!/usr/bin/env python3
"""Generate the Research section: frontend/research.html (index) +
frontend/research/<slug>.html (articles).

Articles live in ARTICLES below as structured dicts; run this script after
editing to regenerate every page from the shared template. Keeps the whole
section visually consistent and makes adding an article a copy-paste job.

    python3 scripts/gen_research.py
"""
from __future__ import annotations

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, "..", "frontend")
OUT_DIR = os.path.join(FRONTEND, "research")

SITE = "https://www.regimecompass.com"
SHELL_V = "6.4"
STYLES_V = "6.3"

# ─────────────────────────────────────────────────────────────────
# Articles. body is HTML (h2/p/ul/blockquote). Idea-level, never
# recipe-level: discuss what and why, link the live tool for the how.
# ─────────────────────────────────────────────────────────────────
ARTICLES = [
    {
        "slug": "topology-audit",
        "tag": "Method",
        "title": "We tested topological crash indicators. Here's what survived.",
        "dek": "Persistent homology and market trees are the frontier of quantitative risk. We audited both against our own gauges before shipping — one earned a place, one earned a caveat.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 6,
        "cta_href": "/geometry",
        "cta_label": "See the live Market Geometry page →",
        "body": """
<h2>Why topology, and why suspicion</h2>
<p>Most market indicators are statistics of size: how far did prices move, how wide is the spread, how big is the variance. Topological methods ask a stranger question — what <em>shape</em> are the dynamics making? Take a sliding window of daily returns across several markets: each day is a point, the window is a cloud. Persistent homology (the core tool of topological data analysis) measures the cloud's robust geometric structure — loops, clusters, voids — in a way that's invariant to the distortions that fool linear statistics.</p>
<p>The landmark result is Gidea &amp; Katz (2018): a norm built on this machinery rose <em>months</em> before the 2000 and 2008 crashes, before volatility itself moved. Alongside it sits an older, more institutional cousin — Mantegna's (1999) minimum spanning tree, the correlation network's skeleton, which famously contracts toward a star when diversification dies. Central-bank financial-stability research has used it for two decades.</p>
<p>Both are impressive. Both are also exactly the kind of thing a quant site adds because it sounds sophisticated. So before shipping either, we ran the test that matters: <strong>do they know anything our existing gauges — Turbulence and the Absorption Ratio — don't?</strong></p>

<h2>The protocol</h2>
<ul>
<li>Nine markets (US, European and Asian equities, gold, silver — crypto excluded for calendar reasons), daily, 2010–2026.</li>
<li>Literature parameters, no optimisation: 60-day windows for the TDA norm, 90-day correlations for the tree. Choosing parameters that make the result look good is how this kind of study lies.</li>
<li>Trailing percentiles only — nothing in the study sees the future.</li>
<li>Three tests: overlap with the incumbents, an event study around the five worst drawdowns in the sample, and the decisive one — <em>incremental</em> forward-drawdown information after controlling for turbulence.</li>
</ul>

<h2>What survived</h2>
<p><strong>The TDA gauge earned a place.</strong> Its overlap with turbulence is low (rank correlation 0.24) — it is genuinely measuring something different. It rose to the 90th percentile before the 2015–16 global drawdown and climbed from the 40s to the 90s into the 2021–22 top, both while turbulence read calm. And on the decisive test: on days when turbulence saw nothing, a hot TDA reading preceded roughly <strong>55% worse average 60-day drawdowns</strong> than a calm one. That is incremental information, from geometry alone.</p>
<blockquote>It also failed twice — silent before the 2018 Q4 slide and the February 2025 break. We publish that on the tool's own page, because a fragility gauge you trust blindly is worse than none.</blockquote>
<p><strong>The market tree survived as a picture, not a signal.</strong> Its tightness does contract in every stress episode — but once turbulence is controlled for, the statistical signal essentially dissolves. Mantegna's tree is on the page because seeing <em>which</em> markets carry the connections, and watching the network collapse toward a hub in real time, is structural information a percentile can't convey. The caveat is printed next to it, not buried.</p>

<h2>What we took from the exercise</h2>
<ul>
<li><strong>Novelty is not evidence.</strong> The fancier the mathematics, the more an indicator needs to prove against boring incumbents — variance-based gauges are hard to beat.</li>
<li><strong>Independence is the prize.</strong> A mediocre indicator that's uncorrelated with your existing ones adds more than a brilliant one that's redundant.</li>
<li><strong>Every gauge misses something.</strong> TDA led 2015 and 2022; turbulence led 2025; nothing led 2018. That is the argument for a panel of independent lenses, which is what this site is.</li>
</ul>
<p>The audit code, parameters and the episodes where the new gauge failed are all described on the live page. Five episodes is a small sample; we'll re-run the study as the record grows, and the page will say whatever the data says.</p>
<h2>Addendum: we also tested per-market topology. It failed.</h2>
<p>The obvious extension is a market-specific version — rebuild each market's own dynamics via time-delay embedding and ask whether <em>that</em> geometry warns before <em>that</em> market's drawdowns. We ran the same audit on the S&amp;P&nbsp;500, Nifty&nbsp;50, Euro&nbsp;Stoxx&nbsp;50 and Nikkei&nbsp;225, against each market's own realized volatility.</p>
<p>It failed on every count that matters. Correlation with the market's own volatility ran 0.45–0.64 — mostly re-measuring vol with extra mathematics. The event studies were inconsistent (Nifty's gauge read the <em>0th</em> percentile before the 2015 slide). And the incremental test flipped sign across markets: hot readings preceded worse drawdowns in the S&amp;P, <em>milder</em> ones in the Nifty and Nikkei. A signal whose sign depends on which market you ask is noise in a topology costume.</p>
<blockquote>The failure is informative: strip the "cross" out of cross-market geometry and the information disappears. The joint gauge's edge lives in how markets move relative to each other — which is exactly what a single series cannot see.</blockquote>
<p>So the per-market table doesn't ship, the system-level gauge stands, and this note will keep saying whatever the data says.</p>

<h2>Update: re-audited on the 20-market board — including a retraction</h2>
<p>When the board expanded to twenty markets (July 2026), the study was re-run — first on the full 18-market panel, then, after a calendar data-quality fix, per asset class. Two findings survived everything: the <strong>TDA gauge works for equities and the cross-asset panel</strong> (hot readings while turbulence was calm preceded a &gt;5% drawdown within 60 days ~44–48% of the time, versus ~17–20% from quiet readings, with forward volatility ~30% higher), and <strong>hub concentration works only inside commodities</strong> (a commodity tree collapsing onto one hub preceded &gt;10% drawdowns 44% of the time vs 15%).</p>
<p>One retraction, disclosed in full: an interim version of this study reported cross-asset hub concentration at ~2× worse drawdowns. That result was an artifact — mixed Sun–Thu/Mon–Fri trading calendars had been silently deleting days from the panel, and after the fix the effect vanished. Commodity TDA also carries no drawdown information despite producing impressive-looking percentiles. The <a href="/geometry">Market Geometry</a> page now shows each asset class one verdict, from the one signal that passed that class's audit, with the historical odds printed next to it — and lists the failures underneath. A conservative block bootstrap puts the surviving spreads at p≈0.10–0.16: odds-shifters from ~a dozen episodes, not certainties, and the page says that too.</p>

""",
    },
    {
        "slug": "why-regimes-matter",
        "tag": "Regime",
        "title": "Regime investing: what the risk-averse actually get",
        "dek": "Regime strategies usually earn less than buy-and-hold. The case for them is what happens to your worst year — and the arithmetic of drawdowns most investors never do.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 5,
        "cta_href": "/strategies",
        "cta_label": "See the model portfolios, losses included →",
        "body": """
<h2>The idea</h2>
<p>Markets don't produce one stream of returns. They alternate between environments — long stretches where volatility is low and trends persist, and shorter, violent stretches where volatility triples and everything falls together. Statisticians call these <strong>regimes</strong>. Most of the damage done to long-term portfolios happens inside a small number of bear regimes; most of the compounding happens inside long bull ones.</p>
<p>Regime investing is the discipline of knowing which environment you're in <em>right now</em>, and sizing risk accordingly. Not forecasting — the honest version makes no claim about next month. It classifies the present, which turns out to be valuable enough.</p>

<h2>The arithmetic nobody does</h2>
<p>Losses and gains are not symmetric. Lose 20% and you need +25% to recover. Lose 33% and you need +50%. Lose half and you need a double. This convexity is why two portfolios with the same average return can leave you with very different wealth — the one with deeper drawdowns compounds from lower bases at the worst possible times.</p>
<blockquote>A strategy that gives up some upside but reliably shrinks the worst drawdown isn't being timid. It's exploiting the asymmetry of percentage losses.</blockquote>
<p>It matters even more if you ever withdraw money. Anyone drawing on a portfolio — retirees, family offices funding obligations, anyone between jobs — faces <strong>sequence risk</strong>: a deep drawdown early in the withdrawal phase does permanent damage that later gains can't repair. For these investors, drawdown control isn't a preference. It's the whole game.</p>

<h2>What the evidence actually says</h2>
<p>We tested our own regime models the way a skeptic would — walk-forward, refit only on data available at the time, costs charged on every switch. The honest result across twenty global markets: the regime strategy usually <strong>earns less</strong> than buy-and-hold, and in exchange it cut the maximum drawdown in <strong>fifteen of twenty markets</strong> and volatility in all twenty.</p>
<p>We publish that trade-off rather than hiding half of it, because it's exactly the trade a risk-averse investor wants to see priced: how many points of annual return does a smaller worst-case cost? For maximum-compounding investors with iron stomachs and no withdrawals, buy-and-hold wins and we say so. For everyone else, the brake has a price and a benefit, and both are measurable.</p>

<h2>What regime investing won't do</h2>
<ul>
<li>It won't catch tops or bottoms. Regime models confirm changes after they begin — days late by construction, because they react to evidence, not prophecy.</li>
<li>It won't beat the index in a relentless bull market. A brake can't add speed.</li>
<li>It won't work as a trading signal. Regimes change a handful of times a year; anything more frequent is noise.</li>
</ul>
<p>What it does is narrow the distribution of outcomes — fewer catastrophic paths, at the cost of some spectacular ones. That is a choice about the shape of your returns, and it deserves to be made deliberately rather than by default.</p>
""",
    },
    {
        "slug": "three-models-one-verdict",
        "tag": "Regime",
        "title": "Why we run three models instead of one",
        "dek": "Every regime model has a blind spot. Running a probabilistic model against two transparent trend filters turns disagreement itself into information.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 4,
        "cta_href": "/composite",
        "cta_label": "See all three models on one gauge →",
        "body": """
<h2>The problem with one model</h2>
<p>Any single regime model is a lens with a defect. A moving-average filter is transparent and robust, but it only sees price trend — it will call a slow-motion top "bull" all the way down the first leg. A statistical model like a Hidden Markov Model sees more — volatility, cross-market stress — but it's a black box to most users, and black boxes fail silently.</p>
<p>The uncomfortable truth of quantitative finance is that model risk doesn't diversify away inside one model, no matter how sophisticated. It diversifies across <em>genuinely different</em> models.</p>

<h2>Three lenses, deliberately unlike each other</h2>
<p>Regime Compass runs three classifiers per market, chosen for how differently they fail:</p>
<ul>
<li><strong>A 3-state Hidden Markov Model</strong> — probabilistic, regime-switching, driven by the joint behaviour of returns and volatility. Fast to smell stress, occasionally jumpy.</li>
<li><strong>Simple moving-average filters</strong> — the sixty-year-old workhorse of trend following. Slow, transparent, nearly impossible to fool for long.</li>
<li><strong>Exponential moving-average filters</strong> — the same logic with more weight on recent prices. Faster to turn, at the cost of more whipsaws.</li>
</ul>
<p>None of these is secret — the value isn't in any one model's cleverness. It's in the <strong>structure of the disagreement</strong>.</p>

<h2>Agreement is conviction; disagreement is information</h2>
<p>When all three call the same regime, the classification is about as reliable as this kind of inference gets — that's when our blended risk score reads decisively. When they split, that's not a failure of the system. It's usually the most interesting moment on the board: turning points look exactly like a fast model flipping while a slow one holds.</p>
<blockquote>A unanimous board tells you what the market is. A split board tells you what it might be becoming.</blockquote>
<p>This is why the site shows the models side by side instead of averaging them into false precision. The blended gauge summarises; the disagreement underneath is where a careful reader looks next.</p>

<h2>The check we impose on ourselves</h2>
<p>Multiple models create a temptation: quietly lean on whichever looked best recently. We avoid it by auditing the primary model walk-forward — refit quarterly on data available at the time, signals applied the next day, costs included — and publishing the results, including the markets where it loses to buy-and-hold. A model you're allowed to see fail is worth more than one that's never wrong on its own website.</p>
""",
    },
    {
        "slug": "smart-money-paper-trail",
        "tag": "Smart Money",
        "title": "Smart money leaves a paper trail",
        "dek": "Big investors move quietly — but exchanges make them disclose. What bulk deals, insider filings and congressional trades can and cannot tell you.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 5,
        "cta_href": "/smartmoney",
        "cta_label": "Open the Smart Money tracker →",
        "body": """
<h2>The idea</h2>
<p>The most informed participants in any market — institutions building positions, insiders who know their company, foreign funds reallocating across countries — cannot move size invisibly. Exchanges and regulators force disclosure: bulk and block deals on the NSE, negotiated trades in Jakarta, block transactions in Taipei, insider filings and congressional disclosures in the US.</p>
<p>Each disclosure is a fact, not an opinion. Someone with more information than you, or at least more money, did something specific at a specific price. Smart-money tracking is the discipline of reading that paper trail systematically instead of anecdotally.</p>

<h2>Why it works — when it works</h2>
<p>The economic logic is information asymmetry. An institution accumulating a mid-cap over weeks has usually done work the market hasn't priced; an insider buying their own stock with their own money is making an unusually honest statement. Academic literature has documented persistent, modest signals in insider buying for decades. The signal is real. It is also <strong>noisy, slow, and heavily diluted</strong> by trades that mean nothing — index rebalancing, estate sales, collateral moves.</p>
<blockquote>The deal feed is not a stock-tip machine. It's a sentiment instrument built from actions instead of surveys.</blockquote>

<h2>The filter is the strategy</h2>
<p>We ran the experiment on our own India data and published both versions. Following <em>every</em> qualifying institutional deal barely distinguishes itself from the index. Adding selectivity — size thresholds, a momentum condition, a market-regime condition — materially changed the outcome in our test window. The interesting finding isn't that smart money is smart; it's that <strong>the edge lives in the entry filters, not the data feed</strong>. Anyone can buy the feed. The conditions under which a disclosed deal is worth following are the actual intellectual property, which is why we discuss them at the level of ideas and publish the results rather than the recipe.</p>
<p>One honesty note we insist on: our tracked window is short. We treat it as a developing record and label it that way on the site — a longer paper trail is the only cure.</p>

<h2>How to read a deal feed like an adult</h2>
<ul>
<li><strong>Aggregate before you believe.</strong> One deal is noise; a month of net accumulation in one sector is a statement.</li>
<li><strong>Respect the disclosure lag.</strong> You are always the second-to-know at best. The signal that survives a lag is positioning, not timing.</li>
<li><strong>Watch the seller too.</strong> Every block trade has both sides; "accumulation" only means something net of distribution.</li>
<li><strong>Condition on regime.</strong> Institutional buying in a bear regime is a knife catch as often as a bottom call.</li>
</ul>
""",
    },
    {
        "slug": "credit-knows-first",
        "tag": "Risk",
        "title": "Credit usually knows first",
        "dek": "Bond investors have no upside to be optimistic about — which is exactly why credit spreads are one of the oldest early-warning systems for equity investors.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 4,
        "cta_href": "/credit",
        "cta_label": "Open the live credit gauge and backtest →",
        "body": """
<h2>The asymmetry that makes credit honest</h2>
<p>An equity holder gets the upside; a bondholder gets their coupon back, at best. That payoff asymmetry makes credit investors professionally paranoid — they are paid to obsess over the downside, because it's the only side they have. When the market's most downside-sensitive participants start demanding more compensation to hold corporate risk, spreads widen. That widening is a price, set by people whose entire job is smelling trouble.</p>
<p>History is generous with examples. Credit spreads began deteriorating in mid-2007, months before equity indices peaked. The same pattern — credit cracking first, equities catching down later — repeats often enough that "watch the credit market" has been professional folklore for generations. Folklore, however, isn't a rule, so we tested it.</p>

<h2>Turning folklore into a rule</h2>
<p>The idea is simple to state: hold equities while the equity trend is up <em>and</em> credit is calm; step aside when either breaks. Two independent tripwires, one of which — credit — tends to fire early precisely because of who sets its price.</p>
<blockquote>The equity trend tells you what stocks are doing. The credit spread tells you what the people with no upside think happens next.</blockquote>
<p>We ran that rule over fifteen years of S&amp;P 500 data with a forty-year credit series and published the full result on the site. The shape of the outcome, which is what matters here: the overlay gave up a meaningful share of buy-and-hold's total return — and cut the maximum drawdown by roughly two-thirds, roughly doubling the risk-adjusted return. It also improved on the pure trend rule, which is the real test: credit added information the price trend didn't have.</p>

<h2>What credit signals won't do</h2>
<ul>
<li><strong>They fire late in fast crashes.</strong> Credit led in 2007; in February 2020 everything broke at once. An early-warning system built on prices can't warn about an overnight shock.</li>
<li><strong>They cry wolf.</strong> Spreads widen in every growth scare; only some become bear markets. The cost of the discipline is sitting out some rallies.</li>
<li><strong>They say nothing about single stocks.</strong> This is a system-level gauge — it informs how much risk to carry, not what to buy.</li>
</ul>
<p>We treat credit as one of four independent lenses precisely because it's fallible alone and valuable in combination — a second opinion from a witness with different incentives.</p>
""",
    },
    {
        "slug": "anatomy-of-fragility",
        "tag": "Risk",
        "title": "Turbulence, absorption, and the anatomy of fragility",
        "dek": "Crashes are rarely about how far markets fall on a given day. They're about markets starting to move together — and there are ways to measure that before it hurts.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 5,
        "cta_href": "/systemic",
        "cta_label": "See today's turbulence and absorption readings →",
        "body": """
<h2>Diversification dies exactly when you need it</h2>
<p>The promise of a diversified portfolio rests on assets not moving together. The tragedy of every major crisis is that they do: in calm markets cross-asset correlations are low and forgiving; in stressed markets they lurch toward one, and a portfolio that looked diversified behaves like a single leveraged bet. Fragility, properly defined, isn't how much markets are falling — it's how <strong>unified</strong> they've become.</p>
<p>That's measurable, and the measurements have been in the institutional literature for years. Regime Compass publishes two of them daily across its eleven markets.</p>

<h2>Turbulence: how abnormal is today?</h2>
<p>Statistical turbulence asks a subtler question than "how big was the move?" It asks how <em>unusual</em> today's pattern of moves is, given history — a day when equities, gold and crypto all lurch in unfamiliar directions relative to each other scores high even if no single move looks dramatic. Days like that are how regime breaks announce themselves: relationships fail before levels do.</p>
<p>We report it as a percentile against each market's own history, because "turbulence at the 95th percentile" is a sentence an investment committee can act on, and raw index values aren't.</p>

<h2>Absorption: how concentrated is the system?</h2>
<p>The absorption ratio measures how much of the system's total variance is being driven by a small number of common factors. Low absorption means markets are dancing to their own tunes — idiosyncratic, healthy, diversifiable. Rising absorption means one force is increasingly driving everything, and the system has become brittle: a shock to the common factor propagates everywhere at once.</p>
<blockquote>Turbulence tells you the storm has arrived. Absorption tells you the forest has gone dry.</blockquote>
<p>The two together are more useful than either alone. Quiet markets with rising absorption are the genuinely dangerous configuration — nothing looks wrong on the surface while the preconditions for contagion assemble underneath. That combination is what our systemic dashboard is built to surface, and it's a reading a price chart simply cannot give you.</p>

<h2>How we'd suggest using it</h2>
<ul>
<li>Treat percentile spikes in turbulence as a prompt to check the regime board, not as a sell signal by themselves.</li>
<li>Watch the <em>trend</em> in absorption more than the level — unification is a process, and the direction is the warning.</li>
<li>When both are elevated and the regime models start splitting, that confluence deserves more respect than any single indicator on this site.</li>
</ul>
""",
    },
    {
        "slug": "honest-backtest",
        "tag": "Method",
        "title": "The honest backtest: why we publish our losses",
        "dek": "Most published backtests are marketing. The difference between in-sample flattery and walk-forward truth — and why a model you can watch fail is worth more.",
        "date": "2026-07-09",
        "date_h": "July 2026",
        "read": 5,
        "cta_href": "/validation",
        "cta_label": "Read the full walk-forward audit →",
        "body": """
<h2>Why most backtests flatter</h2>
<p>Take any strategy, fit it to twenty years of history, and report the result: you have just measured how well your model memorised the past, not how it handles the future. The model chose its parameters <em>after</em> seeing the answers. Add survivorship in the assets chosen, ignore transaction costs, let the researcher quietly discard the forty variants that didn't work, and you get the backtests that fill marketing decks — precise, impressive, and close to meaningless.</p>
<p>None of this requires dishonesty. It's the default outcome of doing the easy thing. Honesty in backtesting is a protocol, not a personality trait.</p>

<h2>The protocol</h2>
<p>Walk-forward validation simulates the only thing that matters: what a model would have said <em>at the time</em>. Ours works the way a skeptical auditor would demand:</p>
<ul>
<li><strong>The model is refit quarterly using only data available on that date.</strong> No future information reaches any decision.</li>
<li><strong>Signals apply the next trading day.</strong> No same-close execution fantasy.</li>
<li><strong>Every regime switch pays a cost.</strong> Ten basis points per turn, every time.</li>
<li><strong>Every market is reported.</strong> All seventeen — not a curated highlight reel.</li>
</ul>

<h2>What honesty produces</h2>
<p>Numbers that look worse and mean more. Out of sample, our regime strategy earns less than buy-and-hold in most markets. It cuts the maximum drawdown in fifteen of twenty and volatility in all twenty. On Sharpe ratio, it beats buy-and-hold in just four markets of twenty — a statistic we display in large type on our own strategies page.</p>
<blockquote>If a result would look better with a detail hidden, the detail is the result.</blockquote>
<p>Why publish that? Because the alternative is a website whose model has never been wrong, and every professional reader knows exactly what that means. Publishing the losses does something subtle: it makes the wins legible. When the same audit that shows four Sharpe wins in twenty also shows drawdowns shrinking almost everywhere, a reader can finally see what the tool is <em>for</em> — it's a brake, not an engine — and decide with open eyes whether that trade suits them.</p>

<h2>Questions to ask of any backtest</h2>
<ul>
<li>Were parameters chosen before or after seeing the test period?</li>
<li>Are costs, slippage and delay modelled — and how generously?</li>
<li>Where are the failures? A methodology with no losing markets, periods or regimes hasn't been tested; it's been curated.</li>
<li>Would the publisher's incentives survive the full table being shown?</li>
</ul>
<p>We built the validation page so those questions have public answers here. Hold us to it.</p>
""",
    },
]

# ─────────────────────────────────────────────────────────────────
# Shared page chrome
# ─────────────────────────────────────────────────────────────────
GTAG = """  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-94DE53GC07"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('consent', 'default', { analytics_storage: 'denied' });
    gtag('js', new Date());
    gtag('config', 'G-94DE53GC07');
  </script>"""

SHARE_CSS = """
    .art-share { display: inline-flex; align-items: center; gap: 6px; }
    .art-share .lbl { font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.3px; color: var(--muted); margin-right: 4px; }
    .art-share button {
      display: inline-flex; align-items: center; justify-content: center;
      width: 32px; height: 32px; border-radius: var(--r-sm); padding: 0;
      cursor: pointer; border: 1px solid var(--border); transition: var(--transition);
      background: var(--panel-2); color: var(--muted);
    }
    .art-share button:hover { background: var(--panel); color: var(--text-strong); border-color: var(--border-strong); }
"""

SHARE_HTML = """<div class="art-share" id="art-share"><span class="lbl">Share</span>
  <button onclick="artShare('li')" title="Share on LinkedIn"><svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.063 2.063 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg></button>
  <button onclick="artShare('x')" title="Post on X"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></button>
  <button onclick="artShare('copy')" id="art-copy" title="Copy link"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg></button>
</div>"""

SHARE_JS = """
function artShare(mode) {
  var url = document.querySelector('link[rel="canonical"]').href;
  var title = document.title;
  if (mode === 'li') {
    window.open('https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(url), '_blank', 'width=640,height=520');
  } else if (mode === 'x') {
    window.open('https://x.com/intent/tweet?url=' + encodeURIComponent(url) + '&text=' + encodeURIComponent(title), '_blank', 'width=640,height=440');
  } else {
    navigator.clipboard.writeText(url).then(function () {
      var b = document.getElementById('art-copy'), o = b.innerHTML;
      b.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--bull)" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>';
      setTimeout(function () { b.innerHTML = o; }, 1500);
    });
  }
}
"""


def article_page(a: dict, idx: int) -> str:
    others = [x for x in ARTICLES if x["slug"] != a["slug"]][:3]
    next_html = "".join(
        f'<a class="next-card" href="/research/{o["slug"]}"><span class="nc-tag">{o["tag"]}</span>'
        f'<span class="nc-title">{o["title"]}</span></a>'
        for o in others
    )
    url = f"{SITE}/research/{a['slug']}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{GTAG}
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{a['title']} — Regime Compass Research</title>
  <meta name="description" content="{a['dek']}" />
  <meta property="og:type" content="article" />
  <meta property="og:site_name" content="Regime Compass" />
  <meta property="og:title" content="{a['title']}" />
  <meta property="og:description" content="{a['dek']}" />
  <meta property="og:image" content="{SITE}/og-image.png" />
  <meta property="og:url" content="{url}" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{a['title']}" />
  <meta name="twitter:description" content="{a['dek']}" />
  <meta name="twitter:image" content="{SITE}/og-image.png" />
  <meta name="theme-color" content="#07090e" />
  <link rel="canonical" href="{url}" />
  <link rel="stylesheet" href="/styles.css?v={STYLES_V}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="manifest" href="/manifest.json" />
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "{a['title']}",
    "description": "{a['dek']}",
    "datePublished": "{a['date']}",
    "dateModified": "{a['date']}",
    "url": "{url}",
    "image": "{SITE}/og-image.png",
    "author": {{ "@type": "Person", "name": "Aditya Sahasrabuddhe", "url": "{SITE}/about" }},
    "publisher": {{ "@type": "Organization", "name": "iQuant Labs", "url": "{SITE}" }}
  }}
  </script>
  <style>
    .art-meta {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin: 14px 0 6px; font-size: 12.5px; color: var(--muted); }}
    .art-tag {{
      display: inline-block; padding: 3px 10px; border-radius: 12px;
      font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.2px;
      background: var(--accent-dim); color: var(--accent-strong); border: 1px solid var(--accent-border);
    }}
    .art-body {{ max-width: 720px; }}
    .art-body h2 {{
      font-family: var(--font-display); font-size: 22px; font-weight: 600;
      color: var(--text-strong); letter-spacing: -0.01em; margin: 34px 0 12px;
    }}
    .art-body p {{ font-size: 15px; line-height: 1.75; color: var(--text-body); margin: 0 0 16px; }}
    .art-body ul {{ margin: 0 0 16px 20px; padding: 0; }}
    .art-body li {{ font-size: 14.5px; line-height: 1.7; color: var(--text-body); margin-bottom: 8px; }}
    .art-body strong {{ color: var(--text-strong); }}
    .art-body blockquote {{
      margin: 22px 0; padding: 14px 20px;
      border-left: 3px solid var(--accent); border-radius: 0 var(--r-md) var(--r-md) 0;
      background: var(--accent-dim);
      font-family: var(--font-display); font-size: 17px; line-height: 1.55; color: var(--text-strong);
    }}
    .art-cta {{
      display: block; margin: 30px 0; padding: 18px 22px;
      border: 1px solid var(--accent-border); border-radius: var(--r-lg);
      background: linear-gradient(135deg, var(--accent-dim), transparent 65%);
      font-size: 14.5px; font-weight: 600; color: var(--accent-strong);
      transition: var(--transition);
    }}
    .art-cta:hover {{ transform: translateY(-2px); text-decoration: none; box-shadow: var(--shadow-md); }}
    .next-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 14px; }}
    @media (max-width: 760px) {{ .next-grid {{ grid-template-columns: 1fr; }} }}
    .next-card {{
      display: flex; flex-direction: column; gap: 7px; padding: 16px 18px;
      border: 1px solid var(--border); border-radius: var(--r-lg);
      background: var(--panel-2); transition: var(--transition); text-decoration: none;
    }}
    .next-card:hover {{ border-color: var(--accent-border); transform: translateY(-2px); text-decoration: none; }}
    .next-card .nc-tag {{ font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.3px; color: var(--accent-strong); }}
    .next-card .nc-title {{ font-size: 13.5px; font-weight: 600; color: var(--text-strong); line-height: 1.45; }}
    .next-h {{ font-family: var(--font-display); font-size: 19px; font-weight: 600; color: var(--text-strong); margin: 40px 0 0; }}
{SHARE_CSS}
  </style>
</head>
<body>
<script src="/shell.js?v={SHELL_V}"></script>

<div class="wrap narrow" style="max-width: 860px;">
  <div style="display: flex; align-items: center; gap: 18px; flex-wrap: wrap; margin-bottom: 14px;">
    <span style="font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.5px;"><a href="/research" style="color: inherit;">Research</a></span>
    <span style="font-size: 13px; color: var(--muted);">›</span>
    <span style="font-size: 13px; color: var(--text); font-weight: 600;">{a['title']}</span>
  </div>

  <div class="hero" style="margin-bottom: 8px;">
    <h1 style="font-size: 40px; line-height: 1.1;">{a['title']}</h1>
    <div class="tagline" style="max-width: 720px;">{a['dek']}</div>
    <div class="art-meta">
      <span class="art-tag">{a['tag']}</span>
      <span>{a['date_h']}</span><span>·</span><span>{a['read']} min read</span>
      <span style="flex: 1 1 auto;"></span>
      {SHARE_HTML}
    </div>
  </div>

  <div class="art-body reveal">
{a['body']}
    <a class="art-cta" href="{a['cta_href']}">{a['cta_label']}</a>
  </div>

  <div class="callout reveal" style="max-width: 720px;">
    Research notes discuss ideas, not recommendations. Nothing here is investment advice — see the
    <a href="/disclaimer">Disclaimer</a> and <a href="/methodology">Methodology</a>.
  </div>

  <h2 class="next-h">Keep reading</h2>
  <div class="next-grid reveal">
{next_html}
  </div>
</div>

<script>{SHARE_JS}</script>
</body>
</html>
"""


def index_page() -> str:
    cards = "".join(
        f"""      <a class="res-card reveal" href="/research/{a['slug']}">
        <span class="art-tag">{a['tag']}</span>
        <span class="rc-title">{a['title']}</span>
        <span class="rc-dek">{a['dek']}</span>
        <span class="rc-meta">{a['date_h']} · {a['read']} min read</span>
      </a>
"""
        for a in ARTICLES
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{GTAG}
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Research — Regime Compass</title>
  <meta name="description" content="Research notes from iQuant Labs: regime investing, smart-money tracking, credit stress, systemic fragility, and honest backtesting — the ideas behind the tools." />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="Regime Compass" />
  <meta property="og:title" content="Research — the ideas behind Regime Compass" />
  <meta property="og:description" content="Regime investing, smart money, credit stress, systemic fragility, honest backtesting — one readable note per idea." />
  <meta property="og:image" content="{SITE}/og-image.png" />
  <meta property="og:url" content="{SITE}/research" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="theme-color" content="#07090e" />
  <link rel="canonical" href="{SITE}/research" />
  <link rel="stylesheet" href="/styles.css?v={STYLES_V}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="manifest" href="/manifest.json" />
  <style>
    .art-tag {{
      display: inline-block; padding: 3px 10px; border-radius: 12px; align-self: flex-start;
      font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.2px;
      background: var(--accent-dim); color: var(--accent-strong); border: 1px solid var(--accent-border);
    }}
    .res-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    @media (max-width: 800px) {{ .res-grid {{ grid-template-columns: 1fr; }} }}
    .res-card {{
      display: flex; flex-direction: column; gap: 10px; padding: 22px 24px;
      border: 1px solid var(--border); border-radius: var(--r-lg);
      background: linear-gradient(180deg, rgba(255,255,255,0.015), transparent 40%), var(--panel);
      box-shadow: inset 0 1px 0 var(--edge-light);
      transition: var(--transition); text-decoration: none;
    }}
    .res-card:hover {{ border-color: var(--accent-border); transform: translateY(-2px); box-shadow: var(--shadow-md), inset 0 1px 0 var(--edge-light); text-decoration: none; }}
    .res-card .rc-title {{ font-family: var(--font-display); font-size: 19px; font-weight: 600; color: var(--text-strong); letter-spacing: -0.01em; line-height: 1.3; }}
    .res-card .rc-dek {{ font-size: 13px; color: var(--muted); line-height: 1.6; }}
    .res-card .rc-meta {{ font-size: 11.5px; color: var(--muted); font-family: var(--font-mono); margin-top: auto; }}
  </style>
</head>
<body>
<script src="/shell.js?v={SHELL_V}"></script>

<div class="wrap" style="max-width: 1020px;">
  <div class="hero" style="margin-bottom: 30px;">
    <div class="kicker">Research &middot; iQuant Labs</div>
    <h1>The ideas behind <span class="grad-text">the tools.</span></h1>
    <div class="tagline" style="max-width: 760px;">One readable note per idea — what each tool measures, why it works, and where it fails.
    Written the way the site is built: plain language, mechanisms over mystique, losses included.</div>
  </div>

  <div class="res-grid">
{cards}  </div>

  <div class="callout reveal" style="margin-top: 30px;">
    Research notes discuss ideas, not recommendations. Nothing here is investment advice — see the
    <a href="/disclaimer">Disclaimer</a>. New notes are added as the work earns them; get them via
    <a href="/subscribe">alerts</a> or check back.
  </div>
</div>
</body>
</html>
"""


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for i, a in enumerate(ARTICLES):
        assert re.fullmatch(r"[a-z0-9-]+", a["slug"]), a["slug"]
        path = os.path.join(OUT_DIR, a["slug"] + ".html")
        with open(path, "w") as f:
            f.write(article_page(a, i))
        print("wrote", path)
    with open(os.path.join(FRONTEND, "research.html"), "w") as f:
        f.write(index_page())
    print("wrote", os.path.join(FRONTEND, "research.html"))


if __name__ == "__main__":
    main()
