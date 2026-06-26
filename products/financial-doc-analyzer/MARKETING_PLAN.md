# ClariFi — Go-to-Market Plan

*Phase 1 product · goal: prove people get value and will pay, as cheaply as possible*

## The recommendation up front

**Beachhead: English-speaking DIY retail investors and finance students, reached
through organic content (Reddit, YouTube, X) before paid ads.** Layer your Rwanda /
East-Africa network on top as a warm launch channel, but don't make East Africa the
primary acquisition market for *this* product.

### Why this audience, not the others

You floated three options. Here's the honest case:

- **Global retail investors & finance students (recommended).** This is where the
  pain ("I have to read a 200-page 10-K and I don't know what matters") meets a
  large, searchable, English-speaking online population that already congregates in
  exactly findable places (r/investing, r/stocks, r/SecurityAnalysis, finance
  YouTube, FinTwit). Zero regulatory complexity, global from day one, and the
  product's "plain-English, no jargon" angle is precisely what a non-expert wants.
  Low ticket but enormous top-of-funnel — perfect for a free demand test.
- **Rwanda / East Africa first.** Your real edge is distribution here (the RSE
  pipeline, local network, credibility). But the *generic* document analyzer isn't
  the product with the strongest local pull — the RSE Investor App (Phase 2) is.
  Use East Africa as a **warm launch and word-of-mouth channel**, not the paid
  acquisition engine, and save the localized push for the RSE-specific product.
- **SMEs & accountants.** Higher willingness to pay and a great *Phase 2* market
  (that's the Document Vault), but a longer, higher-touch sale that's slow and
  expensive to test. Wrong shape for a wide, cheap MVP funnel.

So: **retail investors / students as the acquisition beachhead, East-Africa network
as the warm-start amplifier, SMEs noted as the upsell to revisit in Phase 2.**

### One-line positioning

> **ClariFi reads any financial document and tells you what matters — in plain
> English, in under a minute. No sign-up.**

Supporting differentiator to hammer everywhere: **"even scanned PDFs"** (the OCR
path) — it's a concrete capability most competitors lack and it earns trust.

## Pre-launch checklist (before spending a cent on ads)

1. **Deploy and get a clean domain.** Railway per the deploy guide; a short, brandable
   domain (e.g. `clarifi.app` / `tryclarifi.com`). The bare `.railway.app` URL hurts
   trust in ads.
2. **Turn on GA4 + the first-party DB** so every visitor and event is measured by
   source.
3. **Make 3–4 pieces of "proof" creative** from the built-in sample analyses — a
   screen recording of clicking a sample and getting instant output is your single
   best ad and social asset. (Samples render with no API key, so this is easy.)
4. **Write the landing's social-proof line** once you have any numbers ("1,200
   documents analyzed this week").

## Phase 0 — Organic launch (week 1–4, ~$0)

The cheapest, highest-signal traffic. Goal: 500–1,000 real visitors and a first read
on the funnel before paying for anything.

- **Reddit (primary).** Be genuinely useful, not spammy. In r/investing,
  r/stocks, r/SecurityAnalysis, r/personalfinance, answer real "how do I read this
  filing?" threads and, where it fits, show a ClariFi analysis of the exact document
  being discussed. A "I built a free tool that summarizes annual reports — no signup,
  here's it analyzing $TSLA's latest 10-K" post in the right subreddit, done
  authentically, can drive a launch spike. Read each sub's self-promo rules first.
- **Show HN / Product Hunt / Indie Hackers.** "Show HN: ClariFi — plain-English AI
  analysis of any financial PDF (even scanned)." These reward the no-login,
  try-it-instantly design you already have.
- **YouTube & FinTwit outreach.** DM small/mid finance-education creators (10k–100k)
  with a free, no-strings tool their audience would like. Even a one-line mention
  ("there's a free tool that does this") converts well.
- **Short-form demo clips.** A 20-second screen capture — paste an annual report,
  get the risks section — posted to YouTube Shorts, TikTok, X, LinkedIn. The sample
  feature makes these trivial to produce.

**Decision gate:** if organic gets `analyze_succeeded`/`sample_viewed` above ~15 per
100 visitors and any waitlist signups, proceed to paid. If not, fix positioning
(headline, ad angle, target sub) before spending.

## Phase 1 — Paid validation (week 3–8, ~$300–600 total test budget)

Small, instrumented, UTM-tagged. You're buying *learning*, not scale.

- **Reddit Ads** — best targeting for this audience; aim ads at finance/investing
  subreddits. Likely your most efficient paid channel.
- **Google Search Ads** on high-intent terms: "summarize annual report,"
  "understand 10-K," "read financial statements," "annual report summary tool."
  Search intent here is gold and cheap relative to finance keywords for paid
  products.
- **Meta (Facebook/Instagram)** as a secondary test for the broader "anyone with a
  statement" angle.
- Tag every link: `?utm_source=reddit&utm_medium=cpc&utm_campaign=launch`. GA4 splits
  the funnel by source so you can kill losers fast.

Start at ~$10–20/day per channel for a week, compare `cost per analyze_succeeded` and
`cost per waitlist_signup` across sources, then double down on the winner.

## Phase 2 — Warm East-Africa amplification (parallel, ~$0)

Run alongside the above, using your existing network rather than paid spend:

- Personal LinkedIn/WhatsApp/X launch post to your network and the RSE community.
- Pitch it to Kigali tech/finance communities, university finance clubs, and any
  local fintech or investing groups as a free tool.
- This won't be your biggest volume, but it's free, high-trust, and seeds testimonials
  and early waitlist signups — and it warms the channel for the RSE Investor App later.

## Messaging & creative angles (test several)

- **Time-saving:** "Read a 200-page annual report in 60 seconds."
- **Plain-English:** "Financial documents, explained like a human would."
- **Risk-spotting:** "Spot the red flags before you invest." (pairs with the
  distressed-retailer sample)
- **Capability flex:** "Even reads scanned PDFs." (OCR differentiator)
- **Zero-friction:** "No sign-up. No payment. Just drop a PDF."

Lead with the time-saving and risk-spotting angles for investors; the plain-English
angle for students and the broader audience.

## Budget summary

| Phase | Spend | Goal |
|---|---|---|
| Pre-launch | $0 (+ ~$10–15 domain) | Deploy, instrument, build sample-based creative |
| Phase 0 — organic | ~$0 | 500–1,000 visitors, first funnel read |
| Phase 1 — paid test | $300–600 | Find the cheapest source of value-getting users |
| Phase 2 — warm network | $0 | Trust, testimonials, RSE-channel warm-up |

Total cash to a confident go/no-go: **under ~$650.** If the paid funnel works, scale
the winning channel; if it doesn't, you've learned that cheaply and can re-aim before
building Tier-2 features.

## What success looks like (go/no-go for Phase 2 of the roadmap)

- **Value:** `analyze_succeeded` or `sample_viewed` > 15 per 100 visitors.
- **Demand for more:** waitlist signup > 3–5% of visitors.
- **Keep-value:** meaningful `analysis_copied` / `analysis_downloaded` rates.
- **Efficiency:** a paid channel delivering value-getting users at a cost you'd be
  comfortable scaling.

Hit those and you've earned the right to build comparison + export + a paywall (Tier 2
of the roadmap) and start charging. Miss them and the lesson is cheap: change the
audience or the angle, not the codebase.
