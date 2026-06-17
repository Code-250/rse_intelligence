"""
Claude (Anthropic SDK) agent runner with full usage tracking.

Replaces the NIM runner for all agent calls. Every call records:
  - input tokens, output tokens
  - cost in USD (calculated from current Claude pricing)
  - agent name, model used, timestamp

Model routing:
  - Chat / quick responses : claude-haiku-4-5-20251001  (fast, cheap ~$0.001/msg)
  - Ticket implementation  : claude-sonnet-4-6          (best coding quality)
  - Architecture decisions : claude-sonnet-4-6          (strong reasoning)

Usage is stored in orch_usage table and exposed via /api/usage.
WhatsApp alert fires when monthly spend crosses MONTHLY_BUDGET_USD.

Environment variables required:
  ANTHROPIC_API_KEY     — from console.anthropic.com (new accounts get $5 free)
  MONTHLY_BUDGET_USD    — alert threshold (default: 20.0)
  CLAUDE_CHAT_MODEL     — override chat model (optional)
  CLAUDE_CODE_MODEL     — override coding model (optional)
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agents.identities import get_identity, persona_prefix

logger = logging.getLogger(__name__)

# ── Model configuration ───────────────────────────────────────────────────────

# Fast + cheap for chat — Haiku is ~20x cheaper than Sonnet
CHAT_MODEL = os.getenv("CLAUDE_CHAT_MODEL", "claude-haiku-4-5-20251001")

# Best coding quality for ticket implementation
CODE_MODEL = os.getenv("CLAUDE_CODE_MODEL", "claude-sonnet-4-6")

# Pricing per million tokens (USD) — update if Anthropic changes rates
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-opus-4-8":           {"input": 15.00, "output": 75.00},
}
DEFAULT_PRICING = {"input": 3.00, "output": 15.00}  # fallback for unknown models

MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "20.0"))

# ── Anthropic client ──────────────────────────────────────────────────────────

def _get_client():
    """Return Anthropic client, or None if key not set."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic package not installed. Run: pip install anthropic")
        return None


def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    rates = PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


# ── Core runner ───────────────────────────────────────────────────────────────

def run_claude(
    agent_name: str,
    user_message: str,
    history: list[dict],
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> tuple[str, dict]:
    """
    Run an agent call via Anthropic Claude.

    Returns:
        (response_text, usage_dict)

    usage_dict contains:
        input_tokens, output_tokens, cost_usd, model, agent_name
    """
    client = _get_client()
    if not client:
        return (
            "⚠️  ANTHROPIC_API_KEY is not set. "
            "Get your free key at console.anthropic.com and add it to Railway Variables.",
            {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": "none", "agent_name": agent_name},
        )

    # Load system prompt (persona + CLAUDE.md + project state)
    system_prompt = _load_system_prompt(agent_name)
    chosen_model = model or CHAT_MODEL

    messages = history + [{"role": "user", "content": user_message}]

    try:
        response = client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )

        text = response.content[0].text if response.content else ""
        input_tok = response.usage.input_tokens
        output_tok = response.usage.output_tokens
        cost = _calculate_cost(chosen_model, input_tok, output_tok)

        usage = {
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "cost_usd": cost,
            "model": chosen_model,
            "agent_name": agent_name,
        }

        logger.info(
            "[Claude] %s | %s | in=%d out=%d | $%.5f",
            agent_name, chosen_model, input_tok, output_tok, cost,
        )

        # Persist usage and check budget
        _record_usage(usage)

        return text.strip(), usage

    except Exception as e:
        logger.error("[Claude] Error for %s: %s", agent_name, e)
        err_msg = f"⚠️  Claude API error: {e}"
        return err_msg, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": chosen_model, "agent_name": agent_name}


def run_claude_for_ticket(
    agent_name: str,
    ticket_context: str,
    history: list[dict],
) -> tuple[str, dict]:
    """
    Use the higher-quality Sonnet model for ticket implementation.
    Called by the execution loop, not the chat interface.
    """
    return run_claude(
        agent_name=agent_name,
        user_message=ticket_context,
        history=history,
        model=CODE_MODEL,
        max_tokens=4096,
    )


# ── System prompt (same as NIM runner) ───────────────────────────────────────

def _load_system_prompt(agent_name: str) -> str:
    """Build persona + CLAUDE.md + live project state."""
    REPO_ROOT = Path(__file__).resolve().parents[2]
    identity = get_identity(agent_name)

    persona = (
        f"## YOUR IDENTITY\n"
        f"{persona_prefix(agent_name)}\n\n"
        f"Your full name is **{identity['name']}**. "
        f"Your title is {identity['title']}. "
        f"Your email is {identity['email']}. "
        f"Always write as {identity['name']} — a real professional, not an AI. "
        f"Never refer to yourself as an AI, agent, or assistant.\n\n---\n"
    )

    claude_md = REPO_ROOT / "agents" / agent_name / "CLAUDE.md"
    role = claude_md.read_text(encoding="utf-8") if claude_md.exists() else f"You are the {agent_name} for RSE Intelligence."

    # Live sprint context
    context_lines = []
    tickets_dir = REPO_ROOT / "products" / "financial-doc-analyzer" / "tickets"
    sprint = tickets_dir / "SPRINT-01.md"
    if sprint.exists():
        content = sprint.read_text(encoding="utf-8")
        if "| Ticket |" in content:
            context_lines.append("### Active Sprint")
            context_lines.append(content[content.index("| Ticket |"):content.index("| Ticket |")+800])
    pr_queue = tickets_dir / "PR_QUEUE.md"
    if pr_queue.exists():
        context_lines.append("\n### PR Queue")
        context_lines.append(pr_queue.read_text(encoding="utf-8")[:400])

    context = "\n".join(context_lines) or "No active sprint data."
    return f"{persona}{role}\n\n---\n## CURRENT PROJECT STATE\n{context}"


# ── Usage persistence ─────────────────────────────────────────────────────────

def _record_usage(usage: dict) -> None:
    """Save usage to DB and fire budget alert if needed."""
    try:
        from orchestrator.db.usage import save_usage, get_monthly_spend
        save_usage(usage)

        monthly = get_monthly_spend()
        if monthly >= MONTHLY_BUDGET_USD * 0.80:
            _maybe_alert_budget(monthly)
    except Exception as e:
        logger.warning("[Claude] Could not record usage: %s", e)


_last_alert_spend: float = 0.0

def _maybe_alert_budget(monthly_spend: float) -> None:
    """Send a WhatsApp alert when spend hits 80% or 100% of monthly budget — once per threshold."""
    global _last_alert_spend
    pct = monthly_spend / MONTHLY_BUDGET_USD * 100

    thresholds = [80, 100, 120]
    for t in thresholds:
        if pct >= t and _last_alert_spend < MONTHLY_BUDGET_USD * t / 100:
            _last_alert_spend = MONTHLY_BUDGET_USD * t / 100
            try:
                from orchestrator.channels.whatsapp import send_whatsapp
                emoji = "⚠️" if t < 100 else "🚨"
                send_whatsapp(
                    f"{emoji} *Claude API Budget Alert*\n\n"
                    f"Monthly spend: ${monthly_spend:.2f} of ${MONTHLY_BUDGET_USD:.2f} budget ({pct:.0f}%)\n\n"
                    f"Check the Usage tab on your dashboard for a breakdown by agent.\n\n"
                    f"To raise the limit: update MONTHLY_BUDGET_USD in Railway Variables."
                )
                logger.warning("[Budget] Alert sent at %.0f%% spend ($%.2f)", pct, monthly_spend)
            except Exception as e:
                logger.error("[Budget] Could not send alert: %s", e)
            break
