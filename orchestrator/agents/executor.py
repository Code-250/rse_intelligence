"""
Agent execution loop — agents actually implement tickets.

This is the layer that turns a chat agent into a working engineer. Given a
ticket from SPRINT-01.md, the assigned agent (Kwame / Sofia / Luca) runs an
agentic tool-use loop powered by Claude (the same mechanism Claude Code uses):

    1. Pick the next unblocked, not-started ticket assigned to the agent
    2. Mark it "🏗️ In progress" in SPRINT-01.md
    3. Run Claude with real tools — list_dir, read_file, write_file, run_command
    4. The agent reads the acceptance criteria and writes actual code into the repo
    5. When the agent calls finish(), mark the ticket "✅ Done" and add a PR_QUEUE entry
    6. Every Claude turn is metered → usage recorded → budget alerts fire

Safety:
    - File writes are sandboxed to products/, shared/, and .github/ under the repo root
    - run_command is confined to the product directory with a bl[ocklist + timeout
    - A per-ticket cost cap and iteration cap stop runaway loops

Entry point:
    implement_next_ticket(agent_name) -> dict
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from agents.identities import get_identity, persona_prefix, get_git_env  # noqa: E402

logger = logging.getLogger(__name__)

# Reuse model + pricing + usage plumbing from the chat runner
from orchestrator.agents.claude_runner import (  # noqa: E402
    CODE_MODEL,
    _calculate_cost,
    _record_usage,
    _get_client,
)

# ── Paths ───────────────────────────────────────────────────────────────────
PRODUCT_ROOT = REPO_ROOT / "products" / "financial-doc-analyzer"
TICKETS_DIR = PRODUCT_ROOT / "tickets"
SPRINT_FILE = TICKETS_DIR / "SPRINT-01.md"
PR_QUEUE_FILE = TICKETS_DIR / "PR_QUEUE.md"

# Dirs the agent is allowed to WRITE into (resolved, must stay inside one of these)
ALLOWED_WRITE_ROOTS = [
    REPO_ROOT / "products",
    REPO_ROOT / "shared",
    REPO_ROOT / ".github",
]
# Dirs excluded from reads (infra / vendored / vcs)
READ_DENY = {".git", ".venv", "node_modules", "__pycache__"}

# ── Loop guards ──────────────────────────────────────────────────────────────
MAX_ITERATIONS = int(os.getenv("EXECUTOR_MAX_ITERS", "40"))
COST_CAP_USD = float(os.getenv("EXECUTOR_COST_CAP_USD", "3.00"))
COMMAND_TIMEOUT_S = 120
COMMAND_BLOCKLIST = ["rm -rf /", "rm -rf ~", "sudo ", ":(){", "mkfs", "dd if=", "> /dev", "shutdown", "reboot", "git push"]

# Map implementer agents to the owner keyword used in the sprint table
OWNER_KEYWORD = {
    "backend-ai-dev": "backend",
    "mobile-frontend-dev": "mobile",
    "deployment": "deployment",
}
# Agents allowed to run the execution loop, in auto-pick priority order
IMPLEMENTER_ORDER = ["backend-ai-dev", "deployment", "mobile-frontend-dev"]


# ══════════════════════════════════════════════════════════════════════════════
#  Ticket parsing & status
# ══════════════════════════════════════════════════════════════════════════════

def _parse_summary_table(text: str) -> list[dict]:
    """Parse the '## Sprint 1 Summary' table into ordered ticket rows."""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("| FDA-") and not s.startswith("| [FDA-"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 4:
            continue
        id_cell, owner_cell, due_cell, status_cell = cells[0], cells[1], cells[2], cells[3]
        m = re.search(r"FDA-\d+", id_cell)
        if not m:
            continue
        ticket_id = m.group(0)
        status_l = status_cell.lower()
        rows.append({
            "id": ticket_id,
            "title": id_cell.replace(ticket_id, "").strip(),
            "owner_kw": owner_cell.lower(),
            "due": due_cell,
            "status_raw": status_cell,
            "done": "✅" in status_cell or "done" in status_l,
            "in_progress": "🏗️" in status_cell or "progress" in status_l,
        })
    return rows


def _parse_detail_blocks(text: str) -> dict:
    """Parse the detailed ticket sections for full text and blocked-by deps."""
    blocks: dict[str, dict] = {}
    # Split on the detailed headers like "### [FDA-001] Title"
    parts = re.split(r"\n###\s+\[(FDA-\d+)\]", text)
    # parts = [preamble, id1, body1, id2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        ticket_id = parts[i]
        body = parts[i + 1]
        title_line = body.splitlines()[0].strip() if body.strip() else ""
        # Blocked by
        blocked_by: list[str] = []
        bm = re.search(r"\*\*Blocked by:\*\*\s*(.+)", body)
        if bm:
            blocked_by = re.findall(r"FDA-\d+", bm.group(1))
        # Assigned
        assigned = ""
        am = re.search(r"\*\*Assigned to:\*\*\s*(.+)", body)
        if am:
            assigned = am.group(1).strip()
        blocks[ticket_id] = {
            "title": title_line,
            "blocked_by": blocked_by,
            "assigned": assigned,
            "full_text": f"### [{ticket_id}]{body}".strip(),
        }
    return blocks


def get_next_ticket(agent_name: Optional[str] = None) -> Optional[dict]:
    """
    Return the next ticket an agent should implement, or None.

    A ticket is eligible when it is not started, not in progress, and every
    ticket it is blocked by is already done. Tickets are considered in sprint
    order. If agent_name is given, only that agent's tickets are considered.
    """
    if not SPRINT_FILE.exists():
        return None
    text = SPRINT_FILE.read_text(encoding="utf-8")
    rows = _parse_summary_table(text)
    details = _parse_detail_blocks(text)
    done_ids = {r["id"] for r in rows if r["done"]}

    owner_kw = OWNER_KEYWORD.get(agent_name) if agent_name else None

    for row in rows:
        if owner_kw and row["owner_kw"] != owner_kw:
            continue
        if row["done"] or row["in_progress"]:
            continue
        blockers = details.get(row["id"], {}).get("blocked_by", [])
        if all(b in done_ids for b in blockers):
            detail = details.get(row["id"], {})
            return {
                "id": row["id"],
                "title": detail.get("title") or row["title"],
                "owner_kw": row["owner_kw"],
                "blocked_by": blockers,
                "full_text": detail.get("full_text", ""),
            }
    return None


def set_ticket_status(ticket_id: str, status_text: str) -> bool:
    """Rewrite the status cell for a ticket in the summary table. Returns success."""
    if not SPRINT_FILE.exists():
        return False
    lines = SPRINT_FILE.read_text(encoding="utf-8").splitlines()
    changed = False
    for i, line in enumerate(lines):
        s = line.strip()
        if (s.startswith("| FDA-") or s.startswith("| [FDA-")) and ticket_id in s:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 4:
                cells[3] = status_text
                lines[i] = "| " + " | ".join(cells) + " |"
                changed = True
                break
    if changed:
        SPRINT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def add_pr_queue_entry(ticket_id: str, title: str, agent_name: str, summary: str, files: list[str]) -> None:
    """Append a row to the PR queue table so it surfaces for Richard's approval."""
    if not PR_QUEUE_FILE.exists():
        return
    identity = get_identity(agent_name)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    one_line = summary.replace("\n", " ").strip()
    if len(one_line) > 160:
        one_line = one_line[:157] + "…"
    files_note = f" ({len(files)} files)" if files else ""
    new_row = f"| {ticket_id} | {title}{files_note} | {identity['name']} | Standard | {today} | {one_line} |"

    text = PR_QUEUE_FILE.read_text(encoding="utf-8")
    placeholder = "| —    | —     | —     | —    | —      | —       |"
    if placeholder in text:
        text = text.replace(placeholder, new_row)
    else:
        # Insert after the header separator row of the "Awaiting" table
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("|------"):
                lines.insert(i + 1, new_row)
                break
        text = "\n".join(lines)
    # Clear the "no PRs yet" note
    text = text.replace("_No PRs in queue yet. Sprint 1 has just started._", f"_Last updated {today}._")
    PR_QUEUE_FILE.write_text(text, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  Sandboxed tools
# ══════════════════════════════════════════════════════════════════════════════

def _resolve(rel_path: str) -> Path:
    """Resolve a repo-relative path safely (no escaping the repo)."""
    p = (REPO_ROOT / rel_path).resolve()
    if REPO_ROOT not in p.parents and p != REPO_ROOT:
        raise ValueError(f"Path escapes repository: {rel_path}")
    return p


def _check_writable(p: Path) -> None:
    if not any(root.resolve() in p.parents or root.resolve() == p for root in ALLOWED_WRITE_ROOTS):
        allowed = ", ".join(str(r.relative_to(REPO_ROOT)) for r in ALLOWED_WRITE_ROOTS)
        raise ValueError(f"Writes are only allowed under: {allowed}")


def tool_list_dir(rel_path: str = ".") -> str:
    p = _resolve(rel_path)
    if not p.exists():
        return f"(path does not exist: {rel_path})"
    if p.is_file():
        return f"(this is a file, not a directory: {rel_path})"
    entries = []
    for child in sorted(p.iterdir()):
        if child.name in READ_DENY:
            continue
        entries.append(child.name + ("/" if child.is_dir() else ""))
    return "\n".join(entries) if entries else "(empty)"


def tool_read_file(rel_path: str) -> str:
    p = _resolve(rel_path)
    if any(part in READ_DENY for part in p.parts):
        return f"(reading {rel_path} is not permitted)"
    if not p.exists() or not p.is_file():
        return f"(file not found: {rel_path})"
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"(could not read {rel_path}: {e})"
    if len(content) > 60_000:
        content = content[:60_000] + "\n…(truncated)"
    return content


def tool_write_file(rel_path: str, content: str) -> str:
    p = _resolve(rel_path)
    _check_writable(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {rel_path}"


def tool_run_command(command: str, cwd: str = "products/financial-doc-analyzer") -> str:
    low = command.lower()
    for bad in COMMAND_BLOCKLIST:
        if bad in low:
            return f"(command blocked for safety: contains '{bad.strip()}')"
    workdir = _resolve(cwd)
    if not workdir.exists():
        workdir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(workdir),
            capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out[-8000:] if len(out) > 8000 else out
        return f"exit={proc.returncode}\n{out}".strip()
    except subprocess.TimeoutExpired:
        return f"(command timed out after {COMMAND_TIMEOUT_S}s)"
    except Exception as e:
        return f"(command failed: {e})"


TOOLS = [
    {
        "name": "list_dir",
        "description": "List files and folders at a repo-relative path. Use this to explore the project structure before reading or writing.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repo-relative directory path, e.g. 'products/financial-doc-analyzer'"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file at a repo-relative path. Read existing code, the ticket spec, or the relevant CLAUDE.md before writing.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repo-relative file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content. Writes are only allowed under products/, shared/, and .github/. Always write complete file contents, never partial snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative file path under products/, shared/, or .github/"},
                "content": {"type": "string", "description": "Full file contents"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command (e.g. pytest, ruff check, npm test) from within the product directory. Use to verify your work. Best-effort: the toolchain may not be installed in every environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "cwd": {"type": "string", "description": "Repo-relative working directory (default: products/financial-doc-analyzer)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "finish",
        "description": "Call this when the ticket is fully implemented and you have verified it against the acceptance criteria. Provide a concise summary of what you built and the list of files you created or changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "2-4 sentence summary of what was implemented and how it meets the acceptance criteria"},
                "files_changed": {"type": "array", "items": {"type": "string"}, "description": "Repo-relative paths of files created or modified"},
            },
            "required": ["summary", "files_changed"],
        },
    },
]

_TOOL_FUNCS = {
    "list_dir": lambda a: tool_list_dir(a.get("path", ".")),
    "read_file": lambda a: tool_read_file(a["path"]),
    "write_file": lambda a: tool_write_file(a["path"], a.get("content", "")),
    "run_command": lambda a: tool_run_command(a["command"], a.get("cwd", "products/financial-doc-analyzer")),
}


# ══════════════════════════════════════════════════════════════════════════════
#  System prompt
# ══════════════════════════════════════════════════════════════════════════════

def _build_executor_system_prompt(agent_name: str, ticket: dict) -> str:
    identity = get_identity(agent_name)
    claude_md = REPO_ROOT / "agents" / agent_name / "CLAUDE.md"
    role = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""

    return (
        f"## YOUR IDENTITY\n{persona_prefix(agent_name)}\n\n"
        f"You are **{identity['name']}**, {identity['title']} at RSE Intelligence. "
        f"You are implementing a real ticket on a real codebase, working as a senior engineer — "
        f"not an AI assistant. Write production-quality code.\n\n"
        f"---\n## YOUR ROLE MANDATE\n{role}\n\n"
        f"---\n## HOW YOU WORK\n"
        f"You have real tools: list_dir, read_file, write_file, run_command, and finish.\n"
        f"- Explore the existing structure with list_dir before writing.\n"
        f"- Write COMPLETE files with write_file (paths are repo-relative; you may write under "
        f"products/, shared/, and .github/ only).\n"
        f"- The product backend lives in products/financial-doc-analyzer/backend, mobile in "
        f"products/financial-doc-analyzer/mobile, shared libs in shared/.\n"
        f"- Implement EVERY acceptance criterion in the ticket. Include tests where the ticket asks for them.\n"
        f"- Optionally run_command (e.g. pytest) to sanity-check, but don't block on a missing toolchain.\n"
        f"- When everything is done and matches the acceptance criteria, call finish() with a summary "
        f"and the list of files you changed. Do not call finish until the work is complete.\n"
        f"Keep going autonomously — do not ask questions. Make reasonable engineering decisions and proceed."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  The execution loop
# ══════════════════════════════════════════════════════════════════════════════

def implement_next_ticket(agent_name: Optional[str] = None) -> dict:
    """
    Pick the next eligible ticket and have the assigned agent implement it.

    Returns a dict describing the outcome:
        {status, ticket_id, agent_name, summary, files_changed, cost_usd, iterations}
      status in {"completed", "stopped_early", "no_ticket", "no_api_key", "error"}
    """
    client = _get_client()
    if not client:
        return {
            "status": "no_api_key",
            "message": "ANTHROPIC_API_KEY is not set. Add it in Railway Variables to let agents implement tickets.",
        }

    # Auto-pick an implementer + ticket if no agent specified
    if agent_name:
        ticket = get_next_ticket(agent_name)
        chosen_agent = agent_name
    else:
        ticket, chosen_agent = None, None
        for cand in IMPLEMENTER_ORDER:
            t = get_next_ticket(cand)
            if t:
                ticket, chosen_agent = t, cand
                break

    if not ticket:
        return {
            "status": "no_ticket",
            "message": "No eligible tickets — everything assigned is done, in progress, or blocked.",
            "agent_name": agent_name,
        }

    identity = get_identity(chosen_agent)
    logger.info("[Executor] %s picking up %s — %s", identity["name"], ticket["id"], ticket["title"])

    # Mark in progress so concurrent runs don't double-pick
    set_ticket_status(ticket["id"], "🏗️ In progress")
    log_activity_safe(chosen_agent, "ticket_started", f"{ticket['id']} — {ticket['title']}")

    system_prompt = _build_executor_system_prompt(chosen_agent, ticket)
    messages = [{
        "role": "user",
        "content": (
            f"Implement this ticket now, end to end:\n\n{ticket['full_text']}\n\n"
            "Explore the repo, write all required files, and call finish() when every "
            "acceptance criterion is satisfied."
        ),
    }]

    total_cost = 0.0
    files_changed: list[str] = []
    finish_summary = ""
    finished = False

    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            resp = client.messages.create(
                model=CODE_MODEL,
                max_tokens=8000,
                system=system_prompt,
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:
            logger.error("[Executor] Claude error on %s: %s", ticket["id"], e)
            set_ticket_status(ticket["id"], "🔲 Not started")  # revert so it can be retried
            return {"status": "error", "ticket_id": ticket["id"], "agent_name": chosen_agent,
                    "message": f"Claude API error: {e}", "cost_usd": round(total_cost, 4)}

        # Meter this turn
        in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
        cost = _calculate_cost(CODE_MODEL, in_tok, out_tok)
        total_cost += cost
        _record_usage({
            "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost,
            "model": CODE_MODEL, "agent_name": chosen_agent,
        })

        # Record assistant turn
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            # Agent stopped without calling finish — nudge once, else stop
            messages.append({"role": "user", "content": "If the ticket is complete, call finish(). Otherwise continue implementing."})
            if iteration >= 2:
                break
            continue

        # Execute every tool call in this turn
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name, args, use_id = block.name, block.input or {}, block.id

            if name == "finish":
                finish_summary = args.get("summary", "")
                files_changed = args.get("files_changed", []) or files_changed
                finished = True
                tool_results.append({"type": "tool_result", "tool_use_id": use_id, "content": "Acknowledged — ticket marked complete."})
                continue

            func = _TOOL_FUNCS.get(name)
            if not func:
                tool_results.append({"type": "tool_result", "tool_use_id": use_id, "content": f"(unknown tool: {name})", "is_error": True})
                continue
            try:
                result = func(args)
                if name == "write_file" and args.get("path"):
                    files_changed.append(args["path"])
            except Exception as e:
                result = f"(tool error: {e})"
            tool_results.append({"type": "tool_result", "tool_use_id": use_id, "content": str(result)})

        messages.append({"role": "user", "content": tool_results})

        if finished:
            break

        if total_cost >= COST_CAP_USD:
            logger.warning("[Executor] Cost cap hit on %s ($%.2f)", ticket["id"], total_cost)
            break

    files_changed = sorted(set(files_changed))

    if finished:
        set_ticket_status(ticket["id"], "✅ Done")
        add_pr_queue_entry(ticket["id"], ticket["title"], chosen_agent, finish_summary, files_changed)
        log_activity_safe(chosen_agent, "ticket_completed", f"{ticket['id']} done — {len(files_changed)} files")
        log_activity_safe(chosen_agent, "pr_opened", f"PR ready for {ticket['id']}: {ticket['title']}")
        status = "completed"
    else:
        # Leave it in progress so a follow-up run can resume; flag for Richard
        log_activity_safe(chosen_agent, "ticket_paused", f"{ticket['id']} stopped early ({len(files_changed)} files, ${total_cost:.2f})")
        status = "stopped_early"

    return {
        "status": status,
        "ticket_id": ticket["id"],
        "ticket_title": ticket["title"],
        "agent_name": chosen_agent,
        "agent_display_name": identity["name"],
        "summary": finish_summary or "Work in progress — did not reach completion within the loop budget.",
        "files_changed": files_changed,
        "cost_usd": round(total_cost, 4),
    }


def log_activity_safe(agent_name: str, action_type: str, summary: str) -> None:
    """Log to the activity feed without crashing if the DB is unavailable."""
    try:
        from orchestrator.db.activity import log_activity
        log_activity(agent_name, action_type, summary)
    except Exception as e:
        logger.debug("[Executor] activity log skipped: %s", e)
