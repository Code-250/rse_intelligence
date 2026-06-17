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

import requests

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
# Reverse lookup: sprint-table owner keyword → implementer agent name
KEYWORD_OWNER = {v: k for k, v in OWNER_KEYWORD.items()}
# Agents allowed to run the execution loop, in auto-pick priority order
IMPLEMENTER_ORDER = ["backend-ai-dev", "deployment", "mobile-frontend-dev"]


def agent_for_owner_kw(owner_kw: str) -> str:
    """Resolve the implementer agent for an owner keyword.

    Falls back to the backend engineer for anything unrecognised so revision
    work is never dropped just because a label is unexpected.
    """
    return KEYWORD_OWNER.get((owner_kw or "").lower(), "backend-ai-dev")


# ── GitHub Issues are the source of truth ─────────────────────────────────────
# Each backlog item is a GitHub issue. An "agent label" routes it to an engineer.
# Marcus (PM) opens issues; engineers pick the next eligible one and open a PR
# that closes it. Status lives in GitHub, so it survives redeploys for free.
ISSUE_AGENT_LABELS = {
    "backend": "backend-ai-dev",
    "mobile": "mobile-frontend-dev",
    "frontend": "mobile-frontend-dev",
    "deployment": "deployment",
    "devops": "deployment",
}
# Label applied while an agent is actively building an issue (prevents double-pick).
BUILDING_LABEL = os.getenv("ISSUE_BUILDING_LABEL", "building")
# Optional readiness gate: when required, only issues carrying this label are picked.
READY_LABEL = os.getenv("ISSUE_READY_LABEL", "agent-ready")
REQUIRE_READY = os.getenv("ISSUE_REQUIRE_READY_LABEL", "false").lower() == "true"


def _agent_from_labels(labels: list[str]) -> Optional[str]:
    """Return the implementer agent for an issue's labels, or None if not routable."""
    for lbl in labels:
        agent = ISSUE_AGENT_LABELS.get((lbl or "").lower())
        if agent:
            return agent
    return None


def _parse_blocked_by(body: str) -> list[int]:
    """Extract blocker issue numbers from a 'Blocked by: #N, #M' line in the body."""
    if not body:
        return []
    m = re.search(r"blocked\s*by\s*:?\s*(.+)", body, re.I)
    if not m:
        return []
    return [int(n) for n in re.findall(r"#(\d+)", m.group(1))]


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


def _issue_to_ticket(issue: dict, agent: str) -> dict:
    """Adapt a GitHub issue into the ticket dict the build loop expects."""
    return {
        "id": f"#{issue['number']}",
        "issue_number": issue["number"],
        "title": issue["title"],
        "owner_kw": OWNER_KEYWORD.get(agent, ""),
        "blocked_by": _parse_blocked_by(issue["body"]),
        "full_text": f"Issue #{issue['number']}: {issue['title']}\n\n{issue['body']}",
        "labels": issue.get("labels", []),
        "html_url": issue.get("html_url", ""),
    }


def get_next_ticket(agent_name: Optional[str] = None) -> Optional[dict]:
    """
    Return the next GitHub ISSUE an agent should implement, or None.

    GitHub Issues are the source of truth. An issue is eligible when it is open,
    routable to an agent via an agent label, not already being built (no 'building'
    label and no open PR), carries the readiness label if one is required, and every
    issue it is "Blocked by: #N" is already closed. Issues are taken in ascending
    number (creation) order. Because state lives in GitHub, this is correct across
    redeploys with no local bookkeeping.
    """
    from orchestrator.channels.github import (
        github_configured, list_open_issues, issues_with_open_prs,
    )
    if not github_configured():
        return None

    open_issues = sorted(list_open_issues(), key=lambda i: i["number"])
    open_numbers = {i["number"] for i in open_issues}
    pr_issue_nums = issues_with_open_prs()

    for issue in open_issues:
        labels = issue["labels"]
        agent = _agent_from_labels(labels)
        if not agent:
            continue  # not an agent-actionable issue
        if agent_name and agent != agent_name:
            continue
        if REQUIRE_READY and READY_LABEL not in labels:
            continue
        if BUILDING_LABEL in labels or issue["number"] in pr_issue_nums:
            continue  # already in progress
        blockers = _parse_blocked_by(issue["body"])
        if any(b in open_numbers for b in blockers):
            continue  # a blocker is still open
        return _issue_to_ticket(issue, agent)
    return None


def get_ticket(ticket_ref) -> Optional[dict]:
    """Look up an issue's spec by number (int) or '#N' string.

    Used to fetch the spec for an issue that already has an open PR so the agent
    can revise it. Returns a ticket dict or None.
    """
    from orchestrator.channels.github import get_issue
    num = ticket_ref
    if isinstance(ticket_ref, str):
        m = re.search(r"\d+", ticket_ref)
        if not m:
            return None
        num = int(m.group(0))
    issue = get_issue(int(num))
    if not issue:
        return None
    agent = _agent_from_labels(issue["labels"]) or "backend-ai-dev"
    return _issue_to_ticket(issue, agent)


def reconcile_building_labels() -> int:
    """Clear stale 'building' labels left by a redeploy that died mid-build.

    GitHub issues are durable, so completed (closed) and in-progress (open PR)
    state already survives a redeploy with no action needed. The only ephemeral
    risk is an issue left labelled 'building' when the container stopped before a
    PR was opened. On startup we remove 'building' from any open issue that has NO
    open PR, so it becomes eligible again instead of being stuck. Returns the count
    of issues un-stuck.
    """
    from orchestrator.channels.github import (
        github_configured, list_open_issues, issues_with_open_prs, remove_issue_label,
        agent_token,
    )
    if not github_configured():
        return 0

    pr_issue_nums = issues_with_open_prs()
    cleared = 0
    for issue in list_open_issues([BUILDING_LABEL]):
        if issue["number"] in pr_issue_nums:
            continue  # genuinely in progress — a PR exists
        agent = _agent_from_labels(issue["labels"]) or "backend-ai-dev"
        if remove_issue_label(issue["number"], BUILDING_LABEL, agent_token(agent)):
            cleared += 1
            logger.info("[Executor] reconcile: cleared stale 'building' on issue #%s", issue["number"])
    if cleared:
        log_activity_safe("coordinator", "reconcile",
                          f"Cleared {cleared} stale 'building' label(s) on startup — those issues are eligible again")
    return cleared


def _mark_building(issue_number: int, agent: str) -> None:
    """Label an issue as actively being built (best-effort)."""
    try:
        from orchestrator.channels.github import add_issue_labels, ensure_label, agent_token
        token = agent_token(agent)
        ensure_label(BUILDING_LABEL, token, color="fbca04")  # create if missing
        add_issue_labels(issue_number, [BUILDING_LABEL], token)
    except Exception as e:
        logger.debug("[Executor] could not add building label to #%s: %s", issue_number, e)


def _unmark_building(issue_number: int, agent: str) -> None:
    """Remove the 'building' label from an issue (best-effort)."""
    try:
        from orchestrator.channels.github import remove_issue_label, agent_token
        remove_issue_label(issue_number, BUILDING_LABEL, agent_token(agent))
    except Exception as e:
        logger.debug("[Executor] could not remove building label from #%s: %s", issue_number, e)


def set_ticket_status(ticket_id: str, status_text: str) -> bool:
    """Rewrite the status cell for a ticket in the summary table. Returns success.

    Retained for the sprint-import migration (which reads the legacy markdown
    board); the live issue-driven loop no longer uses it.
    """
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
        "description": "Call this when the ticket is fully implemented AND you have verified it (run tests with run_command first). Provide a summary, the files changed, and exactly how you tested it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "2-4 sentence summary of what was implemented and how it meets the acceptance criteria"},
                "files_changed": {"type": "array", "items": {"type": "string"}, "description": "Repo-relative paths of files created or modified"},
                "how_tested": {"type": "string", "description": "Exactly how you verified the work: commands you ran (e.g. `pytest`), their results, endpoints exercised, or why testing wasn't possible. Be specific and honest."},
            },
            "required": ["summary", "files_changed", "how_tested"],
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
        f"- Implement EVERY acceptance criterion in the ticket. Always include tests (pytest for "
        f"backend, jest for mobile/web) — the ticket is not done without them.\n"
        f"- VERIFY before finishing: use run_command to run the tests you wrote (e.g. `pytest -q` from "
        f"the backend dir). If the toolchain is missing, note that honestly in how_tested. CI will also "
        f"run tests and build a web preview screenshot on the PR.\n"
        f"- Follow PR best practices: write small, focused, complete files and clear code so the preview "
        f"and review go smoothly.\n"
        f"- When everything is done and verified, call finish() with: a summary, the list of files you "
        f"changed, and how_tested (the exact commands you ran and their results, or why you couldn't). "
        f"Do not call finish until the work is complete and tested.\n"
        f"Keep going autonomously — do not ask questions. Make reasonable engineering decisions and proceed."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  The execution loop
# ══════════════════════════════════════════════════════════════════════════════

# ══ Provider selection ════════════════════════════════════════════════════════
#   "nim"        → free NVIDIA NIM models — validate the pipeline at ~$0
#   "anthropic"  → paid Claude API — best coding quality
# Switch with CODE_PROVIDER in Railway Variables. Defaults to free NIM.
CODE_PROVIDER = os.getenv("CODE_PROVIDER", "nim").lower()

NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Tool-calling-capable models, tried in order on 404 / tool-unsupported.
# Default: Nemotron 3 Ultra (NVIDIA's strongest reasoning/coding model) with a
# Llama fallback in case the slug is unavailable on the account or rejects tools.
CODE_NIM_MODELS = [m.strip() for m in os.getenv(
    "CODE_NIM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b,meta/llama-3.3-70b-instruct"
).split(",") if m.strip()]

# OpenAI-style tool schema (what NIM expects) derived from the Anthropic TOOLS
OPENAI_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
    for t in TOOLS
]


def _apply_tool(name: str, args: dict, files_changed: list, written_files: dict):
    """Execute one tool call (shared by both providers). Returns (result_text, is_finish, finish_args)."""
    if name == "finish":
        return "Acknowledged — ticket marked complete.", True, args
    func = _TOOL_FUNCS.get(name)
    if not func:
        return f"(unknown tool: {name})", False, None
    try:
        result = func(args)
        if name == "write_file" and args.get("path"):
            files_changed.append(args["path"])
            written_files[args["path"].lstrip("/")] = args.get("content", "")
    except Exception as e:
        result = f"(tool error: {e})"
    return str(result), False, None


def _ticket_user_prompt(ticket: dict) -> str:
    return (
        f"Implement this ticket now, end to end:\n\n{ticket['full_text']}\n\n"
        "Explore the repo, write all required files, and call the finish function "
        "when every acceptance criterion is satisfied."
    )


def _run_loop_anthropic(client, agent_name: str, system_prompt: str, user_prompt: str, task_label: str = "task") -> dict:
    """Agentic tool-use loop on the paid Claude API."""
    messages = [{"role": "user", "content": user_prompt}]
    total_cost = 0.0
    files_changed: list[str] = []
    written_files: dict[str, str] = {}
    finish_summary = ""
    how_tested = ""
    finished = False

    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            resp = client.messages.create(
                model=CODE_MODEL, max_tokens=8000,
                system=system_prompt, messages=messages, tools=TOOLS,
            )
        except Exception as e:
            return {"error": f"Claude API error: {e}", "finished": False, "finish_summary": "",
                    "files_changed": files_changed, "written_files": written_files, "total_cost": total_cost}

        in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
        cost = _calculate_cost(CODE_MODEL, in_tok, out_tok)
        total_cost += cost
        _record_usage({"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost,
                       "model": CODE_MODEL, "agent_name": agent_name})

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            messages.append({"role": "user", "content": "If the ticket is complete, call finish(). Otherwise continue implementing."})
            if iteration >= 2:
                break
            continue

        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            res, is_finish, fargs = _apply_tool(block.name, block.input or {}, files_changed, written_files)
            if is_finish:
                finish_summary = fargs.get("summary", "")
                how_tested = fargs.get("how_tested", "")
                files_changed[:] = fargs.get("files_changed", []) or files_changed
                finished = True
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": res})
        messages.append({"role": "user", "content": tool_results})

        if finished:
            break
        if total_cost >= COST_CAP_USD:
            logger.warning("[Executor] Cost cap hit on %s ($%.2f)", task_label, total_cost)
            break

    return {"finished": finished, "finish_summary": finish_summary, "how_tested": how_tested,
            "files_changed": files_changed, "written_files": written_files,
            "total_cost": total_cost, "error": None}


def _nim_chat(messages: list, tools: list):
    """One NIM chat-completions call, walking the model chain. Returns (assistant_msg, usage) or (None, error_str)."""
    headers = {"Authorization": f"Bearer {NIM_API_KEY}", "Content-Type": "application/json"}
    last_err = "no NIM model responded"
    for model in CODE_NIM_MODELS:
        payload = {"model": model, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "max_tokens": 4096, "temperature": 0.2}
        try:
            r = requests.post(f"{NIM_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=120)
        except Exception as e:
            last_err = f"request failed: {e}"
            continue
        if r.status_code == 200:
            data = r.json()
            msg = data["choices"][0]["message"]
            usage = data.get("usage", {}) or {}
            return msg, {"model": model, "input_tokens": usage.get("prompt_tokens", 0),
                         "output_tokens": usage.get("completion_tokens", 0)}
        # 404 = model missing, 400 = tools unsupported → try the next model
        last_err = f"HTTP {r.status_code} on {model}: {r.text[:150]}"
        logger.warning("[Executor/NIM] %s", last_err)
    return None, last_err


def _run_loop_nim(agent_name: str, system_prompt: str, user_prompt: str, task_label: str = "task") -> dict:
    """Agentic tool-use loop on free NVIDIA NIM models (OpenAI-style function calling)."""
    if not NIM_API_KEY:
        return {"error": "NVIDIA_NIM_API_KEY not set", "finished": False, "finish_summary": "",
                "files_changed": [], "written_files": {}, "total_cost": 0.0}

    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    files_changed: list[str] = []
    written_files: dict[str, str] = {}
    finish_summary = ""
    how_tested = ""
    finished = False

    for iteration in range(1, MAX_ITERATIONS + 1):
        msg, usage = _nim_chat(messages, OPENAI_TOOLS)
        if msg is None:
            return {"error": f"NIM error: {usage}", "finished": False, "finish_summary": "",
                    "files_changed": files_changed, "written_files": written_files, "total_cost": 0.0}

        _record_usage({"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                       "cost_usd": 0.0, "model": usage["model"], "agent_name": agent_name})

        tool_calls = msg.get("tool_calls") or []
        # Echo the assistant message back (with tool_calls so NIM can match tool replies)
        if tool_calls:
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        else:
            messages.append({"role": "assistant", "content": msg.get("content") or ""})

        if not tool_calls:
            messages.append({"role": "user", "content": "If the ticket is complete, call the finish function. Otherwise keep implementing using the tools."})
            if iteration >= 2:
                break
            continue

        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            res, is_finish, fargs = _apply_tool(name, args, files_changed, written_files)
            if is_finish:
                finish_summary = fargs.get("summary", "")
                how_tested = fargs.get("how_tested", "")
                files_changed[:] = fargs.get("files_changed", []) or files_changed
                finished = True
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": res})

        if finished:
            break

    return {"finished": finished, "finish_summary": finish_summary, "how_tested": how_tested,
            "files_changed": files_changed, "written_files": written_files,
            "total_cost": 0.0, "error": None}


def implement_next_ticket(agent_name: Optional[str] = None) -> dict:
    """
    Pick the next eligible ticket and have the assigned agent implement it.

    Uses CODE_PROVIDER ("nim" = free, "anthropic" = paid Claude) for the build loop.

    Returns a dict describing the outcome. status in
      {"completed", "stopped_early", "pr_failed", "no_ticket", "no_api_key",
       "budget_reached", "github_not_configured", "error"}
    """
    client = None
    if CODE_PROVIDER == "anthropic":
        client = _get_client()
        if not client:
            return {"status": "no_api_key",
                    "message": "CODE_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set. Add it in Railway Variables."}
    else:
        if not NIM_API_KEY:
            return {"status": "no_api_key",
                    "message": "CODE_PROVIDER=nim but NVIDIA_NIM_API_KEY is not set. Get a free key at build.nvidia.com and add it in Railway Variables."}

    # Budget gate — only meaningful for the PAID provider. NIM is free, so it has
    # no spend to gate (and shouldn't be blocked by earlier paid spend today).
    if CODE_PROVIDER == "anthropic":
        from orchestrator.db.usage import get_monthly_spend, get_today_spend
        monthly_budget = float(os.getenv("MONTHLY_BUDGET_USD", "20.0"))
        daily_cap = float(os.getenv("AUTOBUILD_DAILY_BUDGET_USD", "3.0"))
        monthly_spend, today_spend = get_monthly_spend(), get_today_spend()
        if monthly_spend >= monthly_budget:
            return {"status": "budget_reached",
                    "message": f"Monthly budget reached (${monthly_spend:.2f} of ${monthly_budget:.2f}). Raise MONTHLY_BUDGET_USD to continue."}
        if today_spend >= daily_cap:
            return {"status": "budget_reached",
                    "message": f"Daily cap reached (${today_spend:.2f} of ${daily_cap:.2f}). Resets tomorrow, or raise AUTOBUILD_DAILY_BUDGET_USD."}

    # GitHub pre-check — fail BEFORE spending anything if we can't deliver the
    # result. (Agents write to an ephemeral container, so a PR is the only way
    # the code reaches Richard.) This avoids paying for a build we can't ship.
    from orchestrator.channels.github import github_configured
    if not github_configured():
        return {"status": "github_not_configured",
                "message": "Set GITHUB_TOKEN and GITHUB_REPO in Railway Variables so agents can open PRs with their code."}

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
    issue_number = ticket["issue_number"]
    logger.info("[Executor] %s picking up issue %s — %s", identity["name"], ticket["id"], ticket["title"])

    # Mark the issue 'building' so a concurrent cycle doesn't double-pick it.
    _mark_building(issue_number, chosen_agent)
    log_activity_safe(chosen_agent, "ticket_started", f"{ticket['id']} — {ticket['title']}")

    system_prompt = _build_executor_system_prompt(chosen_agent, ticket)

    # Run the agentic build loop on the selected provider.
    user_prompt = _ticket_user_prompt(ticket)
    if CODE_PROVIDER == "anthropic":
        loop = _run_loop_anthropic(client, chosen_agent, system_prompt, user_prompt, ticket["id"])
    else:
        loop = _run_loop_nim(chosen_agent, system_prompt, user_prompt, ticket["id"])

    if loop.get("error"):
        _unmark_building(issue_number, chosen_agent)  # release so it can be retried
        logger.error("[Executor] %s loop error: %s", ticket["id"], loop["error"])
        return {"status": "error", "ticket_id": ticket["id"], "agent_name": chosen_agent,
                "message": loop["error"], "cost_usd": round(loop.get("total_cost", 0.0), 4)}

    finished = loop["finished"]
    finish_summary = loop["finish_summary"]
    how_tested = loop.get("how_tested", "")
    files_changed = loop["files_changed"]
    written_files = loop["written_files"]
    total_cost = loop["total_cost"]

    files_changed = sorted(set(files_changed))
    pr_url = ""
    pr_error = ""

    if finished:
        # Push the agent's work as a PR that CLOSES the issue. Merging the PR will
        # auto-close the issue (our "done" signal). Only release the issue if the PR
        # actually opens — otherwise the code (on the ephemeral container) is lost
        # and the issue should remain retryable.
        from orchestrator.channels.github import open_pr_for_ticket, github_configured, agent_token
        dev_token = agent_token(chosen_agent)
        if not github_configured():
            pr_error = "GitHub not configured — set GITHUB_REPO and a token in Railway Variables."
        elif not dev_token:
            pr_error = f"No GitHub token for {identity['name']} — set GITHUB_TOKEN_* for {chosen_agent} (or shared GITHUB_TOKEN)."
        elif not written_files:
            pr_error = "Agent reported done but wrote no files."
        else:
            pr = open_pr_for_ticket(
                ticket_id=ticket["id"],
                title=ticket["title"],
                agent_name=chosen_agent,
                agent_display_name=identity["name"],
                agent_email=identity.get("github_email", identity.get("email", "agent@rse-intelligence.ai")),
                files=written_files,
                summary=finish_summary,
                how_tested=how_tested,
                token=dev_token,
                closes_issue=issue_number,
            )
            if pr.get("ok"):
                pr_url = pr["pr_url"]
            else:
                pr_error = pr.get("error", "PR push failed.")

        if pr_url:
            # An open PR now represents progress (issues_with_open_prs), so drop the
            # transient 'building' label. The issue closes when the PR is merged.
            _unmark_building(issue_number, chosen_agent)
            log_activity_safe(chosen_agent, "pr_opened", f"PR opened for {ticket['id']}: {pr_url}")
            log_activity_safe(chosen_agent, "ticket_completed", f"{ticket['id']} done — {len(files_changed)} files")
            _pm_review_pr(pr, ticket, chosen_agent, dev_token, finish_summary, files_changed)
            status = "completed"
        else:
            # Built but couldn't deliver — release so it can be retried once GitHub is fixed.
            _unmark_building(issue_number, chosen_agent)
            logger.warning("[Executor] %s built but not delivered: %s", ticket["id"], pr_error)
            log_activity_safe(chosen_agent, "ticket_paused", f"{ticket['id']} built but PR not opened: {pr_error[:120]}")
            status = "pr_failed"
    else:
        # Did not finish within the budget — release the issue so it isn't stuck
        # blocking the rest of the backlog. Partial work is discarded (no PR).
        _unmark_building(issue_number, chosen_agent)
        log_activity_safe(chosen_agent, "ticket_paused", f"{ticket['id']} stopped early ({len(files_changed)} files, ${total_cost:.2f}) — released")
        status = "stopped_early"

    return {
        "status": status,
        "ticket_id": ticket["id"],
        "ticket_title": ticket["title"],
        "agent_name": chosen_agent,
        "agent_display_name": identity["name"],
        "summary": finish_summary or "Work in progress — did not reach completion within the loop budget.",
        "files_changed": files_changed,
        "pr_url": pr_url,
        "pr_error": pr_error,
        "cost_usd": round(total_cost, 4),
    }


def _run_provider_loop(agent_name: str, system_prompt: str, user_prompt: str, label: str) -> dict:
    """Run the agentic build/revision loop on the configured provider.

    Returns the same dict shape as the underlying loops, always including an
    `error` key (None on success).
    """
    if CODE_PROVIDER == "anthropic":
        client = _get_client()
        if not client:
            return {"error": "ANTHROPIC_API_KEY not set", "finished": False, "finish_summary": "",
                    "how_tested": "", "files_changed": [], "written_files": {}, "total_cost": 0.0}
        return _run_loop_anthropic(client, agent_name, system_prompt, user_prompt, label)
    return _run_loop_nim(agent_name, system_prompt, user_prompt, label)


def _revision_user_prompt(ticket: dict, feedback_text: str, existing_files: dict[str, str]) -> str:
    """Prompt the agent to revise its PR in response to review feedback."""
    if existing_files:
        blob = "\n\n".join(
            f"--- {path} ---\n{content[:8000]}" for path, content in existing_files.items()
        )
    else:
        blob = "(could not load current branch files — use list_dir/read_file to inspect the repo)"
    return (
        f"Your pull request for ticket {ticket['id']} — {ticket['title']} received review "
        f"feedback that you must address before it can be merged.\n\n"
        f"## The ticket\n{ticket['full_text']}\n\n"
        f"## Review feedback to resolve (reviewer comments, change requests, and/or failing CI)\n"
        f"{feedback_text}\n\n"
        f"## Current files on your PR branch\n{blob}\n\n"
        "Address EVERY point of the feedback. Rewrite each file you change in full with write_file "
        "(repo-relative paths under products/, shared/, or .github/). Add or fix tests as needed and "
        "verify with run_command where possible. When everything in the feedback is resolved, call "
        "finish() with a summary of exactly what you changed in response to the review and how you tested it."
    )


def revise_one_pr(pr: dict) -> dict:
    """Have the authoring agent address review feedback on one open PR.

    Pulls the PR's feedback (only items newer than its latest commit), re-runs the
    agent with the ticket spec + current files + feedback, then commits the result
    to the SAME branch so the existing PR updates in place (no new PR). Returns a
    status dict; status is one of {"revised", "no_feedback", "revise_failed",
    "skipped", "error"}.
    """
    from orchestrator.channels.github import (
        get_pr_feedback, get_branch_files, commit_files_to_branch, comment_on_pr, agent_token,
    )

    branch = pr.get("branch", "")
    pr_number = pr.get("number")
    head_sha = pr.get("head_sha", "")

    m = re.search(r"issue-(\d+)", branch, re.I)
    if not m:
        return {"status": "skipped", "branch": branch, "reason": "no issue number in branch"}
    issue_number = int(m.group(1))
    ticket_id = f"#{issue_number}"

    # Only act when there is NEW feedback since the last commit (prevents loops).
    feedback = get_pr_feedback(pr_number, head_sha)
    if not feedback.get("needs_revision"):
        return {"status": "no_feedback", "ticket_id": ticket_id, "pr_number": pr_number}

    ticket = get_ticket(issue_number)
    if not ticket:
        return {"status": "skipped", "ticket_id": ticket_id, "reason": "issue not found on GitHub"}

    agent_name = agent_for_owner_kw(ticket["owner_kw"])
    identity = get_identity(agent_name)
    dev_token = agent_token(agent_name)
    if not dev_token:
        return {"status": "revise_failed", "ticket_id": ticket_id,
                "message": f"No GitHub token for {identity['name']} to push the revision."}

    logger.info("[Executor] %s revising %s (PR #%s) after review", identity["name"], ticket_id, pr_number)
    log_activity_safe(agent_name, "pr_revision_started", f"{ticket_id} — addressing review on PR #{pr_number}")

    existing_files = get_branch_files(branch)
    system_prompt = _build_executor_system_prompt(agent_name, ticket)
    user_prompt = _revision_user_prompt(ticket, feedback["text"], existing_files)

    loop = _run_provider_loop(agent_name, system_prompt, user_prompt, f"{ticket_id}-revision")
    if loop.get("error"):
        return {"status": "revise_failed", "ticket_id": ticket_id, "pr_number": pr_number,
                "message": loop["error"], "cost_usd": round(loop.get("total_cost", 0.0), 4)}

    written = loop.get("written_files") or {}
    if not written:
        # Nothing changed — leave a note so the reviewer knows it was seen.
        comment_on_pr(pr_number, dev_token,
                      f"**{identity['name']}**\n\nI reviewed the feedback but did not change any files. "
                      f"Could you clarify what you'd like adjusted? \n\n_Summary:_ {loop.get('finish_summary','(none)')}")
        return {"status": "revise_failed", "ticket_id": ticket_id, "pr_number": pr_number,
                "message": "agent produced no file changes"}

    author = {"name": identity["name"],
              "email": identity.get("github_email", identity.get("email", "agent@rse-intelligence.ai"))}
    commit = commit_files_to_branch(
        branch, written, dev_token, author, f"{ticket_id}: address review feedback",
    )
    if not commit.get("ok"):
        return {"status": "revise_failed", "ticket_id": ticket_id, "pr_number": pr_number,
                "message": "commit to PR branch failed (check token write scope)"}

    summary = loop.get("finish_summary") or "Revised in response to review feedback."
    how_tested = loop.get("how_tested", "")
    body = (
        f"**{identity['name']} — revision**\n\n{summary}\n\n"
        f"**Files updated ({len(commit['committed'])}):**\n"
        + "\n".join(f"- `{p}`" for p in commit["committed"])
        + (f"\n\n**How tested:** {how_tested}" if how_tested else "")
        + "\n\n_Pushed to this PR branch — CI will re-run automatically._"
    )
    comment_on_pr(pr_number, dev_token, body)
    log_activity_safe(agent_name, "pr_revised", f"{ticket_id} — updated PR #{pr_number} ({len(commit['committed'])} files)")

    return {
        "status": "revised",
        "ticket_id": ticket_id,
        "pr_number": pr_number,
        "pr_url": pr.get("html_url", ""),
        "agent_name": agent_name,
        "agent_display_name": identity["name"],
        "summary": summary,
        "files_changed": commit["committed"],
        "cost_usd": round(loop.get("total_cost", 0.0), 4),
    }


def revise_open_prs(max_prs: int = 3) -> list[dict]:
    """Address review feedback across the agents' open PRs, oldest first.

    Returns one result per PR that actually had new feedback (revised or failed);
    PRs with no new feedback are skipped silently. Best-effort — never raises.
    """
    from orchestrator.channels.github import github_configured, list_open_agent_prs

    if not github_configured():
        return []

    results: list[dict] = []
    for pr in list_open_agent_prs()[:max_prs]:
        try:
            r = revise_one_pr(pr)
        except Exception as e:
            logger.error("[Executor] revision error on %s: %s", pr.get("branch"), e)
            r = {"status": "error", "branch": pr.get("branch"), "message": str(e)}
        if r.get("status") in ("revised", "revise_failed", "error"):
            results.append(r)
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  Marcus (PM) — create the backlog as GitHub issues
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_agent_labels(token: str) -> None:
    """Make sure the agent/ready/building labels exist so filtering works."""
    from orchestrator.channels.github import ensure_label
    palette = {
        "backend": "1d76db", "mobile": "0e8a16", "deployment": "5319e7",
        READY_LABEL: "0e8a16", BUILDING_LABEL: "fbca04",
    }
    for name, color in palette.items():
        ensure_label(name, token, color=color)


def pm_import_sprint_to_issues() -> dict:
    """Migrate the legacy SPRINT-01.md tickets into GitHub issues (Marcus authors).

    One issue per ticket, labelled with its agent + the readiness label, with
    'Blocked by: #N' links resolved between the created issues. Idempotent: tickets
    whose FDA id already appears in an existing issue title are skipped.
    """
    from orchestrator.channels.github import (
        github_configured, create_issue, update_issue, list_issues, get_issue,
    )
    if not github_configured():
        return {"ok": False, "error": "GitHub not configured (set GITHUB_REPO + a token)."}
    pm_token = agent_token("project-manager")
    if not pm_token:
        return {"ok": False, "error": "No PM token — set GITHUB_TOKEN_MARCUS (or shared GITHUB_TOKEN)."}
    if not SPRINT_FILE.exists():
        return {"ok": False, "error": "SPRINT-01.md not found."}

    _ensure_agent_labels(pm_token)
    text = SPRINT_FILE.read_text(encoding="utf-8")
    rows = _parse_summary_table(text)
    details = _parse_detail_blocks(text)
    existing_titles = " ".join(i["title"] for i in list_issues("all"))

    created: dict[str, int] = {}   # FDA id -> issue number
    results: list[dict] = []
    for row in rows:
        fda = row["id"]
        if fda in existing_titles:
            results.append({"ticket": fda, "skipped": "already imported"})
            continue
        detail = details.get(fda, {})
        owner_kw = (row["owner_kw"] or "").lower()
        labels = [owner_kw] if owner_kw in ISSUE_AGENT_LABELS else []
        if READY_LABEL:
            labels.append(READY_LABEL)
        title = f"{fda} {detail.get('title') or row['title']}".strip()
        body = detail.get("full_text", "") or title
        r = create_issue(title, body, labels, pm_token)
        if r.get("ok"):
            created[fda] = r["number"]
            results.append({"ticket": fda, "issue": r["number"], "url": r["url"]})
        else:
            results.append({"ticket": fda, "error": r.get("error")})

    # Second pass: link blockers now that every FDA id has an issue number.
    for row in rows:
        fda = row["id"]
        if fda not in created:
            continue
        blk_nums = [created[b] for b in details.get(fda, {}).get("blocked_by", []) if b in created]
        if not blk_nums:
            continue
        issue = get_issue(created[fda])
        if not issue:
            continue
        new_body = issue["body"].rstrip() + "\n\nBlocked by: " + ", ".join(f"#{n}" for n in blk_nums)
        update_issue(created[fda], pm_token, body=new_body)

    log_activity_safe("project-manager", "issues_created",
                      f"Imported {len(created)} sprint ticket(s) into GitHub issues")
    return {"ok": True, "created": len(created), "results": results}


def pm_create_issues(goal: str) -> dict:
    """Marcus breaks a goal into tickets and opens them as GitHub issues."""
    import json as _json

    from orchestrator.channels.github import github_configured, create_issue, update_issue
    from orchestrator.agents.runner import run_agent

    if not github_configured():
        return {"ok": False, "error": "GitHub not configured (set GITHUB_REPO + a token)."}
    pm_token = agent_token("project-manager")
    if not pm_token:
        return {"ok": False, "error": "No PM token — set GITHUB_TOKEN_MARCUS (or shared GITHUB_TOKEN)."}

    prompt = (
        f"You are Marcus, the PM. Break this goal into discrete, independently shippable "
        f"engineering tickets:\n\n{goal}\n\n"
        "Return ONLY a JSON array — no prose. Each element:\n"
        '{"title": "short imperative title", '
        '"agent": "backend" | "mobile" | "deployment", '
        '"body": "markdown with a Description and numbered Acceptance Criteria", '
        '"blocked_by": ["exact titles of other tickets in this list, or empty"]}\n'
        "Keep each ticket small. Maximum 8 tickets."
    )
    raw = run_agent("project-manager", prompt, [])
    try:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        drafts = _json.loads(match.group(0)) if match else []
    except Exception as e:
        return {"ok": False, "error": f"Could not parse PM output as JSON: {e}", "raw": raw[:500]}

    if not isinstance(drafts, list) or not drafts:
        return {"ok": False, "error": "PM produced no tickets.", "raw": raw[:500]}

    _ensure_agent_labels(pm_token)
    created: dict[str, int] = {}   # title -> number
    results: list[dict] = []
    for d in drafts[:8]:
        agent_label = str(d.get("agent", "")).lower()
        labels = [agent_label] if agent_label in ISSUE_AGENT_LABELS else []
        if READY_LABEL:
            labels.append(READY_LABEL)
        title = (d.get("title") or "Untitled ticket").strip()
        body = d.get("body") or title
        r = create_issue(title, body, labels, pm_token)
        if r.get("ok"):
            created[title] = r["number"]
            results.append({"title": title, "issue": r["number"], "url": r["url"]})
        else:
            results.append({"title": title, "error": r.get("error")})

    # Resolve blocked_by (by title) into "Blocked by: #N" links.
    for d in drafts[:8]:
        title = (d.get("title") or "").strip()
        if title not in created:
            continue
        blk_nums = [created[t] for t in (d.get("blocked_by") or []) if t in created]
        if not blk_nums:
            continue
        from orchestrator.channels.github import get_issue
        issue = get_issue(created[title])
        if issue:
            new_body = issue["body"].rstrip() + "\n\nBlocked by: " + ", ".join(f"#{n}" for n in blk_nums)
            update_issue(created[title], pm_token, body=new_body)

    log_activity_safe("project-manager", "issues_created", f"Opened {len(created)} issue(s) from goal")
    return {"ok": True, "created": len(created), "results": results}


def _pm_review_pr(pr: dict, ticket: dict, author_agent: str, author_token: str,
                  finish_summary: str, files_changed: list) -> None:
    """
    Marcus (PM) reviews the developer's PR using his OWN GitHub account.

    GitHub blocks reviewing your own PR, so this only runs when Marcus has a
    distinct token from the author. The review text is generated by the PM agent
    (free on NIM). Best-effort — never blocks the build.
    """
    try:
        from orchestrator.channels.github import agent_token, post_review
        pm_token = agent_token("project-manager")
        pr_number = pr.get("pr_number")
        if not pm_token or not pr_number or pm_token == author_token:
            return  # no separate PM account → skip (can't self-review)

        from orchestrator.agents.runner import run_agent
        author_name = get_identity(author_agent)["name"]
        prompt = (
            f"You are reviewing a pull request for ticket {ticket['id']} — {ticket['title']}.\n"
            f"Engineer: {author_name}\n"
            f"Files changed: {', '.join(files_changed) or '(none listed)'}\n"
            f"Engineer's summary: {finish_summary}\n\n"
            "Acceptance criteria for the ticket:\n"
            f"{ticket.get('full_text', '')[:2000]}\n\n"
            "Write a concise PR review (3-6 sentences): note what looks good and anything to "
            "watch or improve against the acceptance criteria. Be specific and professional."
        )
        review_body = (run_agent("project-manager", prompt, []) or "").strip()

        # Never approve with an error body. If the model call failed, post a
        # neutral COMMENT asking for manual review instead of rubber-stamping.
        low = review_body.lower()
        is_error = (
            not review_body
            or review_body.startswith("⚠️")
            or "could not reach any nim" in low
            or "api error" in low[:60]
            or "is not set" in low[:60]
        )
        if is_error:
            event = "COMMENT"
            body = (f"**Marcus Webb — PM**\n\n_Automated review couldn't be generated right now "
                    f"({review_body[:160] or 'model unavailable'}). Please review this PR manually._")
        else:
            event = os.getenv("PM_REVIEW_EVENT", "APPROVE").upper()
            body = f"**Marcus Webb — PM review of {ticket['id']}**\n\n{review_body}"

        res = post_review(pr_number, pm_token, event, body)
        if res.get("ok"):
            log_activity_safe("project-manager", "pr_reviewed", f"Reviewed {ticket['id']} ({event})")
        else:
            logger.warning("[Executor] PM review failed for %s: %s", ticket["id"], res.get("error"))
    except Exception as e:
        logger.warning("[Executor] PM review error for %s: %s", ticket["id"], e)


def log_activity_safe(agent_name: str, action_type: str, summary: str) -> None:
    """Log to the activity feed without crashing if the DB is unavailable."""
    try:
        from orchestrator.db.activity import log_activity
        log_activity(agent_name, action_type, summary)
    except Exception as e:
        logger.debug("[Executor] activity log skipped: %s", e)
