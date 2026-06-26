# ClariFi — Review & MVP Roadmap

*Prepared June 2026 · Financial Document Analyzer (Phase 1 product)*

## What ClariFi is today

A no-login web app: a visitor drops a financial PDF and gets a plain-English AI
analysis — summary, key figures, risks, and a bottom line — in under a minute.
The stack is lean and sensible: FastAPI serving a single static page, a two-path
extractor (native text via pypdfium2, OCR via Tesseract for scanned filings),
NVIDIA NIM for the analysis with a three-model fallback chain, and GA4 plus a
cookie-less first-party event log for measuring real demand. There's a waitlist
to capture intent, an in-memory per-IP rate limit, and a Dockerfile ready for
Railway.

This is a genuinely good MVP foundation. The architecture is honest about what it
is — a cheap demand test — and the code is clean, well-commented, and degrades
gracefully when optional pieces (database, GA) aren't configured.

## Honest assessment

What's strong: the OCR fallback is a real differentiator (most "summarize a PDF"
toys break on scanned filings, which are common in finance); cost is near zero on
NIM's free tier while you validate; the funnel is wide by design (no login, no
payment); and the analytics are already wired to answer the only question that
matters right now — *do people get value and want more?*

What was holding it back, and what I changed in this pass:

- **The biggest leak was the empty-handed visitor.** Most people who land won't
  have a financial PDF open in the next 30 seconds, so they bounce before ever
  seeing what the tool does. I added a **sample analyses** feature: three
  one-click demos (a high-growth SaaS company, a distressed retailer with red
  flags, and a scanned bank statement) that render instant, realistic output
  through the exact same UI path. This converts "looks interesting" into "I get
  it" with zero friction — and it's the single highest-leverage change for ad
  conversion.
- **No way to keep the output.** People who got a good analysis had nothing to do
  with it. I added **Copy** and **Download** (Markdown) buttons, which also create
  a natural sharing loop (someone sends a friend the analysis → free distribution).
- **The page was a bare uploader.** A cold visitor from an ad had no context. I
  rebuilt the landing into a proper conversion page: sharper hero, a "how it
  works" strip, a "who it's for" section, an FAQ that pre-empts the trust
  questions (*is this advice? do you store my file? how accurate is it?*), and a
  cleaner results view. Still a single self-contained `index.html` that drops
  straight into FastAPI — no build step, no new dependencies.

What's still deliberately missing (and should stay missing until the numbers
justify it): login, payment, saved history, and the mobile app. Keeping those out
keeps the test cheap and the funnel wide. Don't build them on a hunch — build them
when the metrics below say people want them.

## What I added this pass (shipped, tested)

| Change | File(s) | Why it matters |
|---|---|---|
| Sample analyses endpoint + 3 canned demos | `backend/samples.py`, `backend/main.py` | Lets every visitor see real output in one click — biggest funnel lever |
| Rebuilt landing + app UI | `backend/static/index.html` | Conversion-focused page: hero, how-it-works, use cases, FAQ, trust |
| Copy + Download analysis | `backend/static/index.html` | Output becomes keepable and shareable (organic distribution) |
| `sample_viewed` analytics event | `backend/main.py`, frontend `track()` | Measures demo engagement as a funnel step before upload |
| Tests for new endpoints | `backend/tests/test_app.py` | 8/8 passing; covers catalogue, detail, 404, and UI wiring |

All changes are additive and backward-compatible. No new runtime dependencies; the
existing `requirements.txt`, Dockerfile, and Railway deploy guide are unchanged.

## MVP roadmap — what to add next, in priority order

Priority is set by one rule: ship the cheapest thing that moves
`analyze_succeeded per 100 visitors` or `waitlist_signup`, and don't build paid
infrastructure until those signals are good.

### Tier 1 — ship before / at launch (days, not weeks)

1. **A handful of real sample PDFs to download and try.** The canned demos prove
   the concept; offering 2–3 actual downloadable sample PDFs lets a curious
   visitor complete a *real* upload flow. Closes the loop for people testing on
   their phone.
2. **"Ask a follow-up question" on a result.** After the analysis, a single text
   box — "What's their debt-to-equity?" / "Explain the going-concern note." One
   more NIM call against the already-extracted text. Hugely increases perceived
   value and time-on-page for near-zero cost.
3. **Shareable result link (read-only).** Store an analysis under a random slug so
   `clarifi.app/r/ab12cd` shows it. Turns every happy user into a distribution
   channel and gives you a viral coefficient to measure. (You already have the DB
   plumbing in `storage.py`.)

### Tier 2 — build once Tier-1 traffic shows intent

4. **Export to clean PDF/Docx**, not just Markdown — the format finance people
   actually circulate. (You have the skills/infra for this.)
5. **Document-type awareness.** Detect 10-K vs. bank statement vs. earnings
   release and tailor the section template (e.g. cash-flow focus for statements,
   covenant/debt focus for filings). Better output, same pipeline.
6. **Side-by-side comparison.** Upload this year vs. last year, or two companies —
   the AI highlights what changed. This is the first feature worth gating behind
   the waitlist/paywall, and it's already promised in the waitlist copy.

### Tier 3 — monetization, only after retention shows up

7. **Soft account + saved history** (magic-link, no password). The minimum needed
   to support a subscription.
8. **Pay-per-doc ($2) and the $15/mo plan** from the roadmap — wire Stripe once
   you see repeat usage from the same users (the signal that saved history and
   batch are worth paying for).
9. **Batch upload / portfolio mode** — the headline paid feature the waitlist
   already references.

### What to cut or defer

- **The native mobile app** (referenced in old git history) — defer indefinitely.
  The web app is mobile-responsive; a native app is a huge build with no validated
  demand. Kill it from the near-term plan.
- **Don't add login before payment.** Login with nothing to save behind it only
  adds friction and depresses the funnel you're trying to measure.

## How to measure success (the only dashboard that matters now)

The app already emits the right events. Watch, per traffic source (UTM):

- **`page_view` → `sample_viewed` / `file_selected`** — did the landing earn a try?
- **`analyze_succeeded` per 100 visitors** — the core value signal.
- **`waitlist_signup` rate** — do they want more?
- **`analysis_copied` / `analysis_downloaded`** — did the output have keep-value?
  (new events, proxy for "this was actually useful")

A rough read: if **>15 of 100 visitors** reach `analyze_succeeded` *or*
`sample_viewed`, and **>3–5%** join the waitlist, the concept has legs — start
Tier 2. If those are flat after a few hundred ad-driven visitors, the problem is
positioning or audience, not features — revisit the marketing plan before building
more.

## Running it

Nothing about deployment changed. From `products/financial-doc-analyzer/backend`:

```bash
pip install -r requirements.txt
cp .env.example .env          # set NVIDIA_NIM_API_KEY (+ GA_MEASUREMENT_ID)
uvicorn main:app --reload --port 8000
python -m pytest -q            # 8 passing
```

Sample demos work with no API key (they're pre-computed), so the page is fully
explorable even before NIM is configured — useful for screenshots and ad creative.
