# Deployment Guide — RSE Intelligence Orchestrator

This is your step-by-step guide to get all agents live on Railway so you can talk to them from any device.

**Total time: ~20 minutes**

---

## Step 1 — Push the code to GitHub

If you haven't already:
```bash
cd /Users/richardmunyemana/Desktop/projects/explore/rse-intelligence
git init
git add .
git commit -m "feat: multi-agent orchestrator + product scaffolds"
git remote add origin https://github.com/YOUR_USERNAME/rse-intelligence.git
git push -u origin main
```

---

## Step 2 — Deploy to Railway

1. Go to **railway.app** → New Project → Deploy from GitHub repo
2. Select `rse-intelligence`
3. Railway will auto-detect the Dockerfile at `orchestrator/Dockerfile`
4. In the **Variables** tab, add all values from `orchestrator/.env.example`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Your existing Railway PostgreSQL URL |
| `NVIDIA_NIM_API_KEY` | From build.nvidia.com (Get API Key button) |
| `WHATSAPP_PHONE` | +250785653763 |
| `WHATSAPP_API_KEY` | 5205291 |
| `WHATSAPP_META_VERIFY_TOKEN` | rse-intelligence-verify |

5. Click **Deploy**. Railway builds the Docker image and starts the service.
6. Copy your Railway URL (e.g. `https://rse-intelligence-prod.up.railway.app`)

**Test it:**
```
https://YOUR-RAILWAY-URL/health
→ {"status":"ok","agents":["coordinator","backend-ai-dev",...]}
```

---

## Step 3 — Add the dashboard to your phone home screen

1. Open `https://YOUR-RAILWAY-URL` in Safari (iOS) or Chrome (Android)
2. iOS: tap Share → "Add to Home Screen" → "Add"
3. Android: tap ⋮ → "Add to Home Screen"

The dashboard is now an app icon on your phone. Tap it to talk to your agents, check tickets, and track activity — no App Store needed.

---

## Step 4 — Enable two-way WhatsApp (optional, ~15 minutes)

This lets you send WhatsApp messages TO the agents directly from your phone's WhatsApp app.

### 4a. Get a Meta WhatsApp Cloud API account (free)
1. Go to **developers.facebook.com** → My Apps → Create App
2. Choose "Business" → give it a name → Create
3. In the app dashboard: Add Product → WhatsApp → Set Up
4. Under "Getting Started": copy your **Temporary access token** and **Phone number ID**

### 4b. Add to Railway Variables
| Variable | Value |
|---|---|
| `WHATSAPP_META_TOKEN` | Your temporary token (or permanent token after verification) |
| `WHATSAPP_META_PHONE_ID` | Your Phone number ID from Meta |

Redeploy Railway (it auto-deploys on variable changes).

### 4c. Configure the webhook in Meta
1. In Meta Developer Console → WhatsApp → Configuration → Webhook
2. **Callback URL**: `https://YOUR-RAILWAY-URL/webhook/whatsapp`
3. **Verify token**: `rse-intelligence-verify`
4. Click "Verify and Save"
5. Subscribe to: `messages`

### 4d. Send a message to your agents
From your WhatsApp, message your Meta test number:
```
What is the status of Sprint 1?
```
→ The Coordinator Agent responds within 10 seconds.

**To address a specific agent:**
```
@pm show me open tickets
@backend what are you working on?
@marketing what platform should we advertise on first?
@deploy what is the deployment status?
```

---

## Step 5 — Verify scheduled messages

The orchestrator sends automatic WhatsApp messages on schedule:

| Time | Message |
|------|---------|
| 08:00 daily | PM Agent daily stand-up |
| 09:00 Monday | Marketing Agent weekly report |
| Every 30 min | Coordinator blocker check (silent if no blockers) |
| 22:00 daily | Evening agent activity digest |

To test manually, call:
```
POST https://YOUR-RAILWAY-URL/api/chat
{"message": "Write me today's stand-up", "agent": "project-manager"}
```

---

## Daily Usage

**From your phone (WhatsApp):**
```
What did the agents do today?
@pm are there any PRs waiting for me?
@coordinator is Sprint 1 on track?
```

**From the web dashboard (any browser):**
- Open `https://YOUR-RAILWAY-URL`
- Tap an agent card → type your message
- Check "Activity" tab for a live feed of everything agents have done
- Check "Tickets" tab to see Sprint 1 progress and PR queue

**From another laptop:**
Same URL — the dashboard works everywhere.

---

## Costs

| Service | Cost |
|---|---|
| Railway (Hobby plan) | USD 5/month |
| NVIDIA NIM API | Free tier: 1,000 calls/day. Paid: per token after that |
| WhatsApp Cloud API (Meta) | Free: 1,000 conversations/month |
| CallMeBot (outbound) | Free |

**Total: USD 5/month** to run all six agents autonomously.
