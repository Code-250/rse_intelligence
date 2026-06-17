"""
Agent runner — the core intelligence layer.

Each agent is a Claude-compatible conversation powered by NVIDIA NIM.
The agent's CLAUDE.md is injected as the system prompt.
Conversation history is pulled from the database for continuity across sessions.

Supported agents:
  coordinator       — CTO, orchestrates everything
  backend-ai-dev    — Python, FastAPI, NVIDIA NIM
  mobile-frontend-dev — React Native, React Web
  project-manager   — Tickets, PR reviews, bug triage
  sales-marketing   — Analytics, ad strategy, revenue
  deployment        — CI/CD, staging, production
"""
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

# Identity registry lives at repo root agents/identities.py
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agents.identities import get_identity, persona_prefix

load_dotenv()
logger = logging.getLogger(__name__)

# ── NIM configuration ─────────────────────────────────────────────────────────
NIM_API_KEY  = os.getenv("NVIDIA_NIM_API_KEY", "")
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
MAX_TOKENS   = 1500
TEMPERATURE  = 0.3

# Models tried in order — first one that responds wins (rotates on 404).
# Override with AGENT_NIM_MODEL (comma-separated). Nemotron 3 Ultra leads, with
# widely-available Llama models as fallbacks so chat/reviews still work if the
# Ultra slug isn't enabled on the account.
AGENT_MODEL_CHAIN: list[str] = [
    m.strip() for m in os.getenv(
        "AGENT_NIM_MODEL",
        "nvidia/nemotron-3-ultra-550b-a55b,meta/llama-3.3-70b-instruct,meta/llama-3.1-70b-instruct",
    ).split(",") if m.strip()
]

# ── Agent registry ────────────────────────────────────────────────────────────
AGENT_NAMES = [
    "coordinator",
    "backend-ai-dev",
    "mobile-frontend-dev",
    "project-manager",
    "sales-marketing",
    "deployment",
]

# Map short names / aliases used in chat to canonical agent names
AGENT_ALIASES: dict[str, str] = {
    "coord":      "coordinator",
    "cto":        "coordinator",
    "backend":    "backend-ai-dev",
    "ai":         "backend-ai-dev",
    "mobile":     "mobile-frontend-dev",
    "frontend":   "mobile-frontend-dev",
    "pm":         "project-manager",
    "manager":    "project-manager",
    "marketing":  "sales-marketing",
    "sales":      "sales-marketing",
    "deploy":     "deployment",
    "devops":     "deployment",
}

# Root of the monorepo (this file is at orchestrator/agents/runner.py)
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_system_prompt(agent_name: str) -> str:
    """
    Build the full system prompt for an agent:
      1. Persona paragraph  — who this person IS (name, personality, sign-off)
      2. CLAUDE.md          — role mandate, standards, ownership
      3. Live project state — current sprint tickets and PR queue
    """
    identity = get_identity(agent_name)

    # 1. Persona — always first so the LLM stays in character throughout
    persona = (
        f"## YOUR IDENTITY\n"
        f"{persona_prefix(agent_name)}\n\n"
        f"Your full name is **{identity['name']}**. "
        f"Your title is {identity['title']}. "
        f"Your email is {identity['email']}. "
        f"When you write commit messages, PR descriptions, GitHub comments, or WhatsApp messages, "
        f"always write as {identity['name']} — a real person, not a bot. "
        f"Never refer to yourself as an AI, agent, or assistant. "
        f"You are a senior professional on the RSE Intelligence team.\n\n"
        f"---\n"
    )

    # 2. Role mandate from CLAUDE.md
    claude_md = REPO_ROOT / "agents" / agent_name / "CLAUDE.md"
    if claude_md.exists():
        role_prompt = claude_md.read_text(encoding="utf-8")
    else:
        logger.warning("No CLAUDE.md found for agent: %s", agent_name)
        role_prompt = f"You own the {agent_name} function for RSE Intelligence."

    # 3. Live project context
    context = _build_project_context(agent_name)

    return f"{persona}{role_prompt}\n\n---\n## CURRENT PROJECT STATE\n{context}"


def _build_project_context(agent_name: str) -> str:
    """Read sprint tickets and PR queue to give the agent live context."""
    lines = []
    tickets_dir = REPO_ROOT / "products" / "financial-doc-analyzer" / "tickets"

    sprint_file = tickets_dir / "SPRINT-01.md"
    if sprint_file.exists():
        # Extract just the summary table from the sprint file (last ~20 lines)
        content = sprint_file.read_text(encoding="utf-8")
        lines.append("### Active Sprint (Financial Doc Analyzer — Sprint 1)")
        # Find the summary table
        if "| Ticket |" in content:
            table_start = content.index("| Ticket |")
            lines.append(content[table_start:table_start + 800])

    pr_queue = tickets_dir / "PR_QUEUE.md"
    if pr_queue.exists():
        lines.append("\n### PR Queue")
        lines.append(pr_queue.read_text(encoding="utf-8")[:500])

    return "\n".join(lines) if lines else "No active sprint data found."


def resolve_agent(raw_name: str) -> str:
    """Resolve an alias or partial name to a canonical agent name."""
    name = raw_name.lower().strip()
    if name in AGENT_NAMES:
        return name
    if name in AGENT_ALIASES:
        return AGENT_ALIASES[name]
    # Fuzzy: check if any canonical name starts with or contains the input
    for canonical in AGENT_NAMES:
        if canonical.startswith(name) or name in canonical:
            return canonical
    return "coordinator"  # Default to coordinator if unrecognised


def _call_nim_model(model: str, system_prompt: str, messages: list[dict]) -> tuple[str | None, bool]:
    """
    Attempt a single NIM call with a specific model.

    Returns (response_text, should_try_next) where:
      should_try_next=True  means 404/model-not-found — caller should try the next model
      should_try_next=False means success, auth error, or non-recoverable — stop trying
    """
    headers = {
        "Authorization": f"Bearer {NIM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    try:
        resp = requests.post(
            f"{NIM_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            logger.info("[NIM] ✓ %s — %d chars", model, len(text))
            return text.strip(), False

        if resp.status_code == 404:
            logger.warning("[NIM] 404 — model not found: %s — trying next in chain", model)
            return None, True  # try next model

        if resp.status_code == 401:
            logger.error("[NIM] 401 Unauthorised — check NVIDIA_NIM_API_KEY")
            return None, False  # bad key — no point trying more models

        logger.error("[NIM] HTTP %d for %s: %s", resp.status_code, model, resp.text[:200])
        return None, False

    except requests.exceptions.Timeout:
        logger.warning("[NIM] Timeout on %s", model)
        return None, False
    except Exception as e:
        logger.error("[NIM] Error on %s: %s", model, e)
        return None, False


def call_nim(system_prompt: str, messages: list[dict]) -> str:
    """
    Call NVIDIA NIM, walking the model fallback chain on 404.
    Returns the first successful response, or a user-facing error string.
    """
    if not NIM_API_KEY:
        return (
            "⚠️  NVIDIA_NIM_API_KEY is not set. "
            "Add it to your .env file to activate the agents."
        )

    for model in AGENT_MODEL_CHAIN:
        text, try_next = _call_nim_model(model, system_prompt, messages)
        if text:
            return text
        if not try_next:
            break  # non-404 error — stop immediately

    logger.error("[NIM] All models in chain failed: %s", AGENT_MODEL_CHAIN)
    return (
        "⚠️  Could not reach any NIM model. "
        f"Tried: {', '.join(AGENT_MODEL_CHAIN)}. "
        "Check your NVIDIA_NIM_API_KEY and Railway logs."
    )
def run_agent(agent_name: str, user_message: str, history: list[dict]) -> str:
    """
    Run an agent with the given message and conversation history.

    Args:
        agent_name:   Canonical agent name (e.g. "coordinator")
        user_message: The user's latest message
        history:      Prior conversation [{role, content}, ...]

    Returns:
        The agent's response as a string.
    """
    system_prompt = load_system_prompt(agent_name)
    messages = history + [{"role": "user", "content": user_message}]
    return call_nim(system_prompt, messages)
