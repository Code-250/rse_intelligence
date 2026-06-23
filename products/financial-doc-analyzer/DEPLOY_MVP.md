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
   - `file_selected`, `analyze_started`, **`analyze_succeeded`**, `analyze_failed` — the
     funnel: how many visitors actually try it and succeed.
   - `waitlist_signup` — intent to keep using it.
4. In GA4: **Reports → Realtime** to confirm events flow; **Engagement → Events** for totals.
   For ad attribution, check **Acquisition → Traffic acquisition** (source/medium).

> First-party backup: `GET /api/stats` (keep private) returns waitlist count,
> analyses in the last 24h, and event totals straight from your DB — useful because
> ad-blockers strip GA for a chunk of users.

## 3. Advertise

- Point ads at the app URL with UTM tags, e.g.
  `?utm_source=facebook&utm_medium=cpc&utm_campaign=launch` — GA4 splits results by source.
- The key signal to watch: **`analyze_succeeded` per 100 visitors** (do people get value?)
  and **`waitlist_signup`** (do they want more?). Those tell you whether to keep building.

## 4. What's deliberately NOT in the MVP
Login, payment, saved history, and the mobile app are left out on purpose to keep
the funnel wide and the test cheap. If the numbers are good, those are the next build.
