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
from orchestrator.agents.claude_runner import run_claude
from orchestrator.db.usage import init_usage_table, get_usage_breakdown
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
    init_usage_table()
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

    # Use Claude if API key is set, fall back to NIM otherwise
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        response, _usage = run_claude(agent_name, req.message, history)
    else:
        response = run_agent(agent_name, req.message, history)

    save_message(session_id, "user",      agent_name, req.message)
    save_message(session_id, "assistant", agent_name, response)
    log_activity(agent_name, "message", f"Chat: {req.message[:80]}")

    return ChatResponse(response=response, agent_name=agent_name, session_id=session_id)


# ── Group Chat API ────────────────────────────────────────────────────────────

GROUP_SESSION = "group:rse-intelligence-team"

class GroupMessage(BaseModel):
    message: str
    sender: str = "richard"   # "richard" or an agent_name


class GroupPost(BaseModel):
    sender: str
    agent_name: str | None   # None when sender is Richard
    content: str
    timestamp: str


@app.post("/api/group/message", tags=["group"])
async def group_message(req: GroupMessage):
    """
    Post a message to the RSE Intelligence team group.

    When Richard posts, the Coordinator decides which agents should respond
    (could be one, could be several). Each responding agent posts a reply.

    When an agent posts (used by scheduler for stand-ups), it's stored directly.
    """
    import asyncio
    from datetime import datetime, timezone
    from orchestrator.agents.runner import AGENT_NAMES

    ts = datetime.now(timezone.utc).isoformat()
    posts = []

    if req.sender == "richard":
        # Save Richard's message to group history
        save_message(GROUP_SESSION, "user", "richard", req.message)
        log_activity("coordinator", "message", f"Group: Richard — {req.message[:80]}")

        # Ask Coordinator which agents should respond and what they'd say
        coord_prompt = (
            f"Richard posted this to the RSE Intelligence team group: \"{req.message}\"\n\n"
            "Decide which team members should respond. "
            "You can respond yourself and/or ask 1-2 others to chime in. "
            "Reply in this exact JSON format (no other text):\n"
            '[\n'
            '  {"agent": "coordinator", "message": "your response here"},\n'
            '  {"agent": "project-manager", "message": "marcus response if relevant"}\n'
            ']\n'
            "Only include agents whose input is genuinely relevant. Maximum 3 agents."
        )
        group_history = get_conversation_history(GROUP_SESSION, limit=10)
        raw = run_agent("coordinator", coord_prompt, group_history)

        # Parse JSON responses
        import json, re
        try:
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            responses = json.loads(match.group(0)) if match else []
        except Exception:
            responses = [{"agent": "coordinator", "message": raw}]

        for r in responses[:3]:
            agent_name = resolve_agent(r.get("agent", "coordinator"))
            content = r.get("message", "").strip()
            if not content:
                continue
            save_message(GROUP_SESSION, "assistant", agent_name, content)
            log_activity(agent_name, "message", f"Group reply: {content[:80]}")
            posts.append({"sender": agent_name, "content": content, "timestamp": ts})

    else:
        # An agent is posting directly (e.g. scheduler posting stand-up)
        agent_name = resolve_agent(req.sender)
        save_message(GROUP_SESSION, "assistant", agent_name, req.message)
        log_activity(agent_name, "message", f"Group post: {req.message[:80]}")
        posts.append({"sender": agent_name, "content": req.message, "timestamp": ts})

    return {"posts": posts}


@app.get("/api/group/messages", tags=["group"])
async def group_messages(limit: int = Query(50, ge=1, le=200)):
    """Return recent group messages for the dashboard group chat view."""
    from datetime import datetime, timezone

    history = get_conversation_history(GROUP_SESSION, limit=limit)
    # history is [{role, content}] — we need to re-fetch with agent names
    # Pull from DB with full metadata
    if not db_available():
        return {"messages": [], "note": "Database not connected"}

    from orchestrator.db.activity import get_conn
    from psycopg2.extras import RealDictCursor
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT role, agent_name, content, created_at
        FROM orch_messages
        WHERE session_key = %s
        ORDER BY created_at ASC
        LIMIT %s
    """, (GROUP_SESSION, limit))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()

    messages = []
    for r in rows:
        messages.append({
            "sender":     "richard" if r["role"] == "user" else r["agent_name"],
            "is_richard": r["role"] == "user",
            "content":    r["content"],
            "timestamp":  r["created_at"].isoformat() if r["created_at"] else "",
        })
    return {"messages": messages}


@app.post("/api/group/standup", tags=["group"])
async def trigger_standup():
    """
    Manually trigger a stand-up — each agent posts a brief update to the group.
    The scheduler calls this automatically at 08:00 daily.
    """
    from datetime import datetime, timezone
    from orchestrator.agents.runner import AGENT_NAMES

    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    posts = []

    for agent_name in AGENT_NAMES:
        prompt = (
            f"Today is {today}. Post your daily stand-up update to the RSE Intelligence team group. "
            "Keep it to 2-3 sentences max. Cover: what you did yesterday, what you're doing today, any blockers. "
            "Write as yourself — your name, your voice. No headers, no lists. Just a brief natural message "
            "like you'd send in a team group chat."
        )
        response = run_agent(agent_name, prompt, [])
        save_message(GROUP_SESSION, "assistant", agent_name, response)
        log_activity(agent_name, "message", f"Stand-up: {response[:80]}")
        posts.append({"sender": agent_name, "content": response})

    return {"posts": posts, "date": today}


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
    """Ticket tracker for the dashboard — sourced from GitHub Issues.

    Open issues with an agent label are the backlog; a closed issue is Completed;
    an issue with an open PR or the 'building' label is In Progress.
    """
    from orchestrator.channels.github import github_configured, list_issues, issues_with_open_prs
    from orchestrator.agents.executor import _agent_from_labels, OWNER_KEYWORD, BUILDING_LABEL

    if not github_configured():
        return {"tickets": [], "note": "Connect GitHub (GITHUB_REPO + a token) — issues are the backlog."}

    pr_nums = issues_with_open_prs()
    parsed = []
    for it in sorted(list_issues(state="all"), key=lambda i: i["number"]):
        agent = _agent_from_labels(it["labels"])
        if not agent:
            continue  # only show agent-actionable issues
        done = it["state"] == "closed"
        in_progress = (not done) and (it["number"] in pr_nums or BUILDING_LABEL in it["labels"])
        parsed.append({
            "id":     f"#{it['number']}",
            "title":  it["title"],
            "owner":  OWNER_KEYWORD.get(agent, agent),
            "done":   done,
            "status": "Completed" if done else ("In Progress" if in_progress else "Open"),
            "url":    it.get("html_url", ""),
        })
    return {"tickets": parsed}


@app.get("/api/usage", tags=["dashboard"])
async def usage(days: int = Query(30, ge=1, le=90)):
    """
    Claude API usage breakdown for the dashboard.
    Returns per-agent costs, model breakdown, daily chart data,
    monthly spend vs budget, and totals.
    """
    return get_usage_breakdown(days=days)


class RunTicketRequest(BaseModel):
    agent: str = ""   # empty = auto-pick the highest-priority unblocked ticket


@app.get("/api/next-ticket", tags=["agents"])
async def next_ticket(agent: str = Query("", description="Agent name/alias, or empty to auto-pick")):
    """Preview which ticket an agent would pick up next, without running it."""
    from orchestrator.agents.executor import get_next_ticket
    agent_name = resolve_agent(agent) if agent else None
    ticket = get_next_ticket(agent_name)
    if not ticket:
        return {"ticket": None, "message": "No eligible ticket — all done, in progress, or blocked."}
    return {"ticket": {"id": ticket["id"], "title": ticket["title"], "owner": ticket["owner_kw"], "blocked_by": ticket["blocked_by"]}}


@app.post("/api/agents/run-ticket", tags=["agents"])
async def run_ticket(req: RunTicketRequest):
    """
    Have an agent actually implement its next ticket.

    The assigned engineer (Kwame / Sofia / Luca) runs an agentic tool-use loop:
    explores the repo, writes real code files, optionally runs tests, then marks
    the ticket done and adds a PR-queue entry. Every Claude turn is metered into
    the usage tracker. Pass an empty agent to auto-pick the next unblocked ticket.

    This can take a while (the agent writes multiple files), so call with a long
    client timeout.
    """
    import anyio
    from orchestrator.agents.executor import implement_next_ticket

    agent_name = resolve_agent(req.agent) if req.agent else None
    # Run the blocking loop in a worker thread so the event loop stays responsive
    result = await anyio.to_thread.run_sync(implement_next_ticket, agent_name)
    return result


@app.post("/api/agents/revise-prs", tags=["agents"])
async def revise_prs():
    """
    Have agents address review feedback on their open PRs.

    For each open agent PR with NEW feedback (a change request, a reviewer/Copilot
    comment, or a failing CI check since its last commit), the authoring agent
    revises the code and pushes to the same branch — updating the existing PR in
    place rather than opening a new one. The autobuild worker also does this
    automatically each cycle; this endpoint is a manual trigger.
    """
    import anyio
    from orchestrator.agents.executor import revise_open_prs

    results = await anyio.to_thread.run_sync(revise_open_prs)
    revised = [r for r in results if r.get("status") == "revised"]
    return {"revised_count": len(revised), "results": results}


class CreateIssuesRequest(BaseModel):
    goal: str


@app.post("/api/pm/create-issues", tags=["agents"])
async def pm_create_issues_endpoint(req: CreateIssuesRequest):
    """Marcus (PM) breaks a goal into tickets and opens them as GitHub issues.

    Each issue gets an agent label (backend/mobile/deployment) so an engineer can
    pick it up, plus the readiness label, with 'Blocked by: #N' links between them.
    """
    import anyio
    from orchestrator.agents.executor import pm_create_issues

    return await anyio.to_thread.run_sync(pm_create_issues, req.goal)


@app.post("/api/pm/import-sprint", tags=["agents"])
async def pm_import_sprint_endpoint():
    """One-time migration: turn the legacy SPRINT-01.md tickets into GitHub issues.

    Use this once to seed the issue backlog from the existing sprint file. Safe to
    re-run — tickets already imported (by FDA id in an issue title) are skipped.
    """
    import anyio
    from orchestrator.agents.executor import pm_import_sprint_to_issues

    return await anyio.to_thread.run_sync(pm_import_sprint_to_issues)


@app.get("/api/pr-queue", tags=["dashboard"])
async def pr_queue():
    """PR review queue for the dashboard — the agents' open PRs awaiting your review."""
    import re
    from orchestrator.channels.github import github_configured, list_open_agent_prs

    if not github_configured():
        return {"prs": [], "note": "Connect GitHub (GITHUB_REPO + a token) to see open PRs."}

    prs = []
    for pr in list_open_agent_prs():
        m = re.search(r"issue-(\d+)", pr.get("branch", ""))
        prs.append({
            "id":      f"PR #{pr.get('number')}",
            "title":   pr.get("title", ""),
            "agent":   "",  # PR author isn't needed for the queue view
            "risk":    "Standard",
            "opened":  "",
            "summary": f"Closes #{m.group(1)}" if m else pr.get("branch", ""),
            "url":     pr.get("html_url", ""),
        })
    return {"prs": prs}


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
