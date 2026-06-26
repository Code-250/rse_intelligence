# ClariFi MVP — deploy & measure demand

A no-login web app: upload a financial PDF → AI plain-English analysis. Free
NVIDIA NIM does the analysis (≈ $0 while you validate). Traffic is measured with
Google Analytics 4 plus a first-party event log.

App lives in `products/financial-doc-analyzer/backend/`:
`main.py` (API + page), `analyzer.py` (PDF → NIM), `storage.py` (waitlist +
events), `static/index.html` (the page), `Dockerfile`.

---

## 1. Deploy on Railway (new service, same repo)

1. Railway → your project → **New** → **GitHub Repo** → pick `rse_intelligence`.
2. Open the new service → **Settings**:
   - **Root Directory:** `products/financial-doc-analyzer/backend`
   - Railway auto-detects the `Dockerfile` there. (Health check path: `/health`.)
3. **Variables** tab:

   | Variable | Value |
   |---|---|
   | `NVIDIA_NIM_API_KEY` | your free key from build.nvidia.com |
   | `GA_MEASUREMENT_ID` | your GA4 Measurement ID, `G-XXXXXXXXXX` (step 2) |
   | `DATABASE_URL` | your Railway Postgres URL (optional, enables waitlist + first-party stats) |

4. **Deploy**, then open the generated URL. Settings → **Networking** → *Generate Domain*
   for a public link, or attach your own domain.
5. Smoke test: `GET /health` → `{"status":"ok",...}`; open `/` and analyze a sample PDF.

> The autonomous-agent system (the old `agents/` + `orchestrator/`) has been
> removed from the repo — this product is the focus now, so nothing runs in the
> background or spends on your behalf.

## 2. Google Analytics 4 (traffic)

1. analytics.google.com → **Admin** → **Create property** → add a **Web** data stream
   for your app's domain.
2. Copy the **Measurement ID** (`G-XXXXXXXXXX`) → set it as `GA_MEASUREMENT_ID` in Railway.
3. Redeploy. The page then sends:
   - `page_view` (automatic) — your traffic number.
   - `sample_viewed` — clicked a demo analysis (top-of-funnel value moment).
   - `file_selected`, `analyze_started`, **`analyze_succeeded`**, `analyze_failed` — the
     funnel: how many visitors actually try it and succeed.
   - `ask_started` / `ask_succeeded` — used the follow-up Q&A (engagement depth).
   - `analysis_copied`, `analysis_downloaded` — kept the output (keep-value signal).
   - `waitlist_signup` — intent to keep using it.
4. In GA4: **Reports → Realtime** to confirm events flow; **Engagement → Events** for totals.
   For ad attribution, check **Acquisition → Traffic acquisition** (source/medium).

> First-party backup: `GET /api/stats` (keep private) returns waitlist count,
> analyses in the last 24h, and event totals straight from your DB — useful because
> ad-blockers strip GA for a chunk of users.

## 2b. Custom domain + SEO (so Google and browsers find you)

1. **Buy a short domain** (Namecheap/Cloudflare/Google Domains), e.g. `clarifi.app`.
2. **Attach it in Railway:** service → **Settings → Networking → Custom Domain** →
   enter the domain → add the CNAME record Railway shows you at your registrar.
   Wait for it to verify (usually minutes).
3. **Set the URL as a variable:** add `PUBLIC_BASE_URL=https://clarifi.app` in the
   service **Variables** tab, then redeploy. This makes the app emit:
   - a **canonical** link + Open Graph URL (so Google indexes the right address),
   - **`/robots.txt`** (allows crawlers, points to the sitemap),
   - **`/sitemap.xml`** (lists the home page),
   - **JSON-LD structured data** (`WebApplication`) already in the page `<head>`.
4. **Tell Google about it:** create a property in
   [Google Search Console](https://search.google.com/search-console), verify the
   domain, and submit `https://clarifi.app/sitemap.xml`. This is what gets you
   indexed and gives you the search-impressions data to pair with GA4.
5. **Confirm:** open `/robots.txt` and `/sitemap.xml` on the live domain; use
   Search Console's URL Inspection on the home page to request indexing.

> The page already ships SEO-friendly meta (title, description, keywords, Open
> Graph, Twitter card) and semantic content (how-it-works, use cases, FAQ) that
> give Google real text to rank. Setting `PUBLIC_BASE_URL` is the one switch that
> turns on canonicalization and the sitemap once your domain is live.

## 3. Advertise

- Point ads at the app URL with UTM tags, e.g.
  `?utm_source=facebook&utm_medium=cpc&utm_campaign=launch` — GA4 splits results by source.
- The key signal to watch: **`analyze_succeeded` per 100 visitors** (do people get value?)
  and **`waitlist_signup`** (do they want more?). Those tell you whether to keep building.

## 4. What's deliberately NOT in the MVP
Login, payment, saved history, and the mobile app are left out on purpose to keep
the funnel wide and the test cheap. If the numbers are good, those are the next build.
