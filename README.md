# RSE Intelligence

Two focused projects live in this repo.

## 1. ClariFi — Financial Document Analyzer (web MVP)
`products/financial-doc-analyzer/backend/`

A no-login web app: a visitor uploads a financial PDF and gets a plain-English AI
analysis (summary, key figures, risks, bottom line). FastAPI + NVIDIA NIM, with
Google Analytics 4 and a first-party event log for measuring real usage.

```bash
cd products/financial-doc-analyzer/backend
pip install -r requirements.txt
cp .env.example .env          # set NVIDIA_NIM_API_KEY (+ GA_MEASUREMENT_ID)
uvicorn main:app --reload --port 8000   # open http://localhost:8000
```

Deploy + analytics + advertising guide: `products/financial-doc-analyzer/DEPLOY_MVP.md`.
Tests: `pip install -r requirements-dev.txt && python -m pytest -q`.

## 2. RSE pipeline (Rwanda Stock Exchange document automation)
`backend/`

Pulls PDFs from email, classifies them (daily report / trade confirmation /
statement / announcement), parses them into a database, runs a signal engine, and
sends WhatsApp alerts. Includes an experimental `rl/` module.

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env           # fill in credentials
python run_pipeline.py         # full pipeline (see file header for flags)
```

## Notes
- **Secrets** live in `.env` files and are git-ignored — never commit them.
- **Sample data** in `data/` is kept locally and git-ignored (not in the repo).
- An earlier multi-agent build system (`agents/` + `orchestrator/`) was removed in
  favor of building these products directly. It remains in git history if needed.
