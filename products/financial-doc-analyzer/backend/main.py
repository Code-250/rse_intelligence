"""
Financial Document Analyzer — MVP web app.

A frictionless single-page tool: a visitor uploads a financial PDF and gets an
AI-generated plain-English analysis. No login (keeps the funnel wide for the ad
test). Traffic is measured with Google Analytics 4 (client-side) plus a
cookie-less first-party event log (server-side, ad-blocker-proof).

Run locally:
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr

from analyzer import AnalysisError, analyze_pdf_bytes
from storage import get_stats, init_storage, record_event, save_waitlist_email

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

MAX_MB = int(os.getenv("FDA_MAX_FILE_SIZE_MB", "15"))
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "").strip()
RATE_LIMIT_PER_HOUR = int(os.getenv("FDA_RATE_LIMIT_PER_HOUR", "20"))
INDEX_PATH = Path(__file__).parent / "static" / "index.html"

# Simple in-memory per-IP throttle for /api/analyze (best-effort abuse guard).
_HITS: dict[str, deque] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_storage()
    logger.info("Financial Document Analyzer ready (GA=%s)", "on" if GA_MEASUREMENT_ID else "off")
    yield


app = FastAPI(title="Financial Document Analyzer", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", summary="Health check", tags=["infra"])
async def health():
    """Returns 200 if the service is running. Used by smoke tests and uptime checks."""
    return {"status": "ok", "service": "financial-doc-analyzer"}


# ── Landing + app page ────────────────────────────────────────────────────────
def _render_index() -> str:
    html = INDEX_PATH.read_text(encoding="utf-8")
    # Inject the GA4 snippet only when a measurement ID is configured.
    if GA_MEASUREMENT_ID:
        snippet = (
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>\n'
            "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            f"gtag('js',new Date());gtag('config','{GA_MEASUREMENT_ID}');</script>"
        )
    else:
        snippet = "<!-- GA4 disabled: set GA_MEASUREMENT_ID to enable -->"
    return html.replace("<!--GA_SNIPPET-->", snippet)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    if not INDEX_PATH.exists():
        return HTMLResponse("<h1>Financial Document Analyzer</h1><p>UI not found.</p>", status_code=200)
    return HTMLResponse(_render_index())


# ── Analyze ───────────────────────────────────────────────────────────────────
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown"))


def _rate_limited(ip: str) -> bool:
    now = time.time()
    hits = _HITS[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_PER_HOUR:
        return True
    hits.append(now)
    return False


@app.post("/api/analyze", tags=["app"])
async def analyze(request: Request, file: UploadFile = File(...)):
    """Accept a PDF upload and return an AI financial analysis."""
    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="You've hit the hourly limit. Please try again later.")

    name = file.filename or "document.pdf"
    if not name.lower().endswith(".pdf") and (file.content_type or "") != "application/pdf":
        raise HTTPException(status_code=415, detail="Please upload a PDF file.")

    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_MB:
        raise HTTPException(status_code=413, detail=f"File is too large (max {MAX_MB} MB).")
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    record_event("analyze_started", name[:120])
    try:
        result = analyze_pdf_bytes(data, filename=name)
    except AnalysisError as e:
        record_event("analyze_failed", str(e)[:120])
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("Unexpected analyze error: %s", e)
        record_event("analyze_failed", "internal")
        raise HTTPException(status_code=500, detail="Something went wrong analysing this document.")

    record_event("analyze_succeeded", f"{result['pages']}p {result['model_used']}")
    return JSONResponse(result)


# ── Waitlist ──────────────────────────────────────────────────────────────────
class WaitlistRequest(BaseModel):
    email: EmailStr


@app.post("/api/waitlist", tags=["app"])
async def waitlist(req: WaitlistRequest):
    """Capture an email for product updates / early access."""
    stored = save_waitlist_email(str(req.email))
    record_event("waitlist_signup", str(req.email))
    return {"ok": True, "stored": stored}


# ── Internal stats (first-party) ──────────────────────────────────────────────
@app.get("/api/stats", tags=["infra"])
async def stats():
    """First-party usage counts (backup to GA4). Keep this URL private."""
    return get_stats()
