"""
RSE Intelligence — Agent Orchestrator

FastAPI service that:
  - Serves the mobile web dashboard (/)
  - Exposes REST API for agent chat (/api/chat)
  - Handles WhatsApp webhook (/webhook/whatsapp)
  - Runs scheduled tasks in background (daily stand-up, weekly marketing, blocker checks)

Run locally:
  uvicorn orchestrator.main:app --reload --port 8080

Deploy:
  Railway — see SETUP.md for one-command deployment
"""
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from orchestrator.agents.runner import (
    AGENT_NAMES,
    resolve_agent,
    run_agent,
)
from orchestrator.channels.whatsapp import (
    META_VERIFY_TOKEN,
    parse_inbound_message,
    send_agent_response,
    send_whatsapp_meta,
    verify_webhook,
)
from orchestrator.db.activity import (
    get_conversation_history,
    get_recent_activity,
    get_daily_summary,
    init_db,
    log_activity,
    save_message,
    get_or_create_session,
    db_available,
)
from orchestrator.scheduler.tasks import run_scheduler

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

RICHARD_PHONE = os.getenv("WHATSAPP_PHONE", "").replace("+", "")  # Meta needs no +
DASHBOARD_PATH = Path(__file__).parent / "dashboard" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB and start background scheduler."""
    logger.info("RSE Intelligence Orchestrator starting...")
    init_db()
    run_scheduler()
    logger.info("Orchestrator ready")
    yield
    logger.info("Orchestrator shutting down")


app = FastAPI(
    title="RSE Intelligence Orchestrator",
    description="Multi-agent operating system for RSE Intelligence products",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["infra"])
async def health():
    return {
        "status": "ok",
        "agents": AGENT_NAMES,
        "database": "connected" if db_available() else "unavailable — set DATABASE_URL in Railway Variables",
        "nim_key_set": bool(os.getenv("NVIDIA_NIM_API_KEY")),
    }


# ── Dashboard (mobile web PWA) ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Serve the mobile-responsive agent dashboard."""
    if DASHBOARD_PATH.exists():
        return HTMLResponse(content=DASHBOARD_PATH.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


# ── Chat API ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    agent: str = "coordinator"      # Agent name or alias
    session_id: str = ""            # Empty = new session


class ChatResponse(BaseModel):
    response: str
    agent_name: str
    session_id: str


@app.post("/api/chat", response_model=ChatResponse, tags=["agents"])
async def chat(req: ChatRequest):
    """
    Send a message to an agent and get a response.

    The agent is specified by name or alias:
      coordinator / coord / cto
      backend-ai-dev / backend / ai
      mobile-frontend-dev / mobile / frontend
      project-manager / pm / manager
      sales-marketing / marketing / sales
      deployment / deploy / devops

    Session ID maintains conversation history across requests.
    Leave empty to start a new session.
    """
    agent_name = resolve_agent(req.agent)
    session_id = req.session_id or f"web:{uuid.uuid4().hex[:12]}"

    get_or_create_session(session_id, channel="web", agent_name=agent_name)
    history = get_conversation_history(session_id)

    response = run_agent(agent_name, req.message, history)

    save_message(session_id, "user",      agent_name, req.message)
    save_message(session_id, "assistant", agent_name, response)
    log_activity(agent_name, "message", f"Chat: {req.message[:80]}")

    return ChatResponse(response=response, agent_name=agent_name, session_id=session_id)


# ── Activity feed ─────────────────────────────────────────────────────────────

@app.get("/api/activity", tags=["dashboard"])
async def activity(hours: int = Query(24, ge=1, le=168)):
    """Recent agent activity for the dashboard feed."""
    events = get_recent_activity(hours=hours)
    return {"events": events, "hours": hours}


@app.get("/api/summary", tags=["dashboard"])
async def summary():
    """Daily summary stats for the dashboard header."""
    return get_daily_summary()


@app.get("/api/tickets", tags=["dashboard"])
async def tickets():
    """Read Sprint 1 tickets for the dashboard ticket tracker."""
    sprint_file = Path(__file__).parents[1] / "products" / "financial-doc-analyzer" / "tickets" / "SPRINT-01.md"
    if not sprint_file.exists():
        return {"content": "No sprint file found.", "tickets": []}

    content = sprint_file.read_text(encoding="utf-8")

    # Parse the summary table for structured data
    parsed = []
    in_table = False
    for line in content.splitlines():
        if line.startswith("| FDA-") or line.startswith("| [FDA-"):
            in_table = True
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 4:
                status = parts[3] if len(parts) > 3 else "Unknown"
                done = "✅" in status
                parsed.append({
                    "id":     parts[0].replace("[", "").replace("]", ""),
                    "title":  parts[1],
                    "owner":  parts[2],
                    "done":   done,
                    "status": "Completed" if done else "In Progress",
                })

    return {"tickets": parsed, "raw": content}


@app.get("/api/pr-queue", tags=["dashboard"])
async def pr_queue():
    """Read PR queue for the dashboard."""
    pr_file = Path(__file__).parents[1] / "products" / "financial-doc-analyzer" / "tickets" / "PR_QUEUE.md"
    if not pr_file.exists():
        return {"content": "No PRs yet.", "prs": []}
    return {"content": pr_file.read_text(encoding="utf-8"), "prs": []}


# ── WhatsApp Webhook ──────────────────────────────────────────────────────────

@app.get("/webhook/whatsapp", tags=["whatsapp"])
async def whatsapp_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta WhatsApp Cloud API webhook verification (GET)."""
    challenge = verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if challenge:
        return HTMLResponse(content=challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook/whatsapp", tags=["whatsapp"])
async def whatsapp_inbound(request: Request):
    """
    Receive inbound WhatsApp messages from Richard and route to agents.

    Message routing:
      @coordinator [msg]  → Coordinator Agent
      @pm [msg]           → Project Manager Agent
      @backend [msg]      → Backend/AI Agent
      @mobile [msg]       → Mobile/Frontend Agent
      @marketing [msg]    → Sales & Marketing Agent
      @deploy [msg]       → Deployment Agent
      [msg] (no prefix)   → Coordinator Agent (default)
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"})  # Always 200 to Meta

    result = parse_inbound_message(payload)
    if not result:
        return JSONResponse({"status": "ok"})

    sender_phone, text = result

    # Security: only accept messages from Richard's number
    if RICHARD_PHONE and sender_phone.replace("+", "") != RICHARD_PHONE.replace("+", ""):
        logger.warning("[WhatsApp] Message from unknown number: %s — ignored", sender_phone)
        return JSONResponse({"status": "ok"})

    # Route to agent based on @mention prefix
    agent_name = "coordinator"
    message = text.strip()

    if message.startswith("@"):
        parts = message.split(None, 1)
        if len(parts) == 2:
            agent_name = resolve_agent(parts[0][1:])  # strip @
            message = parts[1]
        else:
            agent_name = resolve_agent(parts[0][1:])
            message = "What is your current status?"

    # Maintain per-phone conversation history
    session_key = f"whatsapp:{sender_phone}"
    get_or_create_session(session_key, channel="whatsapp", agent_name=agent_name)
    history = get_conversation_history(session_key, limit=10)

    response = run_agent(agent_name, message, history)

    save_message(session_key, "user",      agent_name, message)
    save_message(session_key, "assistant", agent_name, response)
    log_activity(agent_name, "message", f"WhatsApp: {message[:80]}")

    # Reply via Meta Cloud API (or CallMeBot fallback) with real agent identity
    from agents.identities import format_whatsapp_message
    send_whatsapp_meta(sender_phone, format_whatsapp_message(agent_name, response))

    return JSONResponse({"status": "ok"})
