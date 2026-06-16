"""
Git commit helper — every agent commits as their human identity.

Usage (called by Developer Agents when they push code):

    from agents.git_commit import commit_as, push_as

    # Stage and commit files as Kwame Asante (Backend Agent)
    commit_as(
        agent_name="backend-ai-dev",
        message="[FDA-003] Add JWT authentication middleware\n\nImplements stateless JWT auth on all /api/v1/ endpoints.\nTests: 9 unit tests, all passing.",
        files=["products/financial-doc-analyzer/backend/routers/auth.py"],
    )

    # Push to origin
    push_as("backend-ai-dev", branch="feature/FDA-003-jwt-auth")
"""
import logging
import os
import subprocess
from pathlib import Path

from agents.identities import get_identity, get_git_env

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], env: dict, cwd: Path = REPO_ROOT) -> tuple[int, str, str]:
    """Run a git command with the given env, return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def configure_git_identity(agent_name: str, scope: str = "local") -> bool:
    """
    Set git user.name and user.email in the repo's local config for an agent.
    Call this once when an agent starts a sprint.

    scope: "local" (this repo only) | "global" (system-wide, not recommended)
    """
    identity = get_identity(agent_name)
    env = {**os.environ}

    cmds = [
        ["git", "config", f"--{scope}", "user.name",  identity["name"]],
        ["git", "config", f"--{scope}", "user.email", identity["github_email"]],
    ]
    for cmd in cmds:
        code, _, err = _run(cmd, env)
        if code != 0:
            logger.error("[git] Config failed for %s: %s", agent_name, err)
            return False

    logger.info("[git] Identity set: %s <%s>", identity["name"], identity["github_email"])
    return True


def stage_files(files: list[str] | None = None) -> bool:
    """
    Stage specific files, or all changes if files is None.
    """
    env = {**os.environ}
    cmd = ["git", "add"] + (files if files else ["-A"])
    code, _, err = _run(cmd, env)
    if code != 0:
        logger.error("[git] Stage failed: %s", err)
        return False
    return True


def commit_as(agent_name: str, message: str, files: list[str] | None = None) -> bool:
    """
    Stage and commit as the given agent's human identity.

    The commit will show as authored by the agent's name and email in GitHub,
    making it appear as a real team member's contribution.

    Args:
        agent_name: e.g. "backend-ai-dev"
        message:    Full commit message (subject + optional body)
        files:      Specific files to stage, or None to stage all changes
    """
    identity = get_identity(agent_name)
    env = {**os.environ, **get_git_env(agent_name)}

    # Stage
    if not stage_files(files):
        return False

    # Check if there's anything to commit
    code, status, _ = _run(["git", "status", "--porcelain"], env)
    if not status:
        logger.info("[git] Nothing to commit for %s", identity["name"])
        return True

    # Commit
    code, out, err = _run(["git", "commit", "-m", message], env)
    if code != 0:
        logger.error("[git] Commit failed for %s: %s", identity["name"], err)
        return False

    logger.info("[git] %s committed: %s", identity["name"], message.splitlines()[0])
    return True


def push_as(agent_name: str, branch: str, remote: str = "origin") -> bool:
    """
    Push the current branch to origin.
    The commit history will show the agent's human name on GitHub.
    """
    identity = get_identity(agent_name)
    env = {**os.environ, **get_git_env(agent_name)}

    code, out, err = _run(["git", "push", remote, branch, "--set-upstream"], env)
    if code != 0:
        logger.error("[git] Push failed for %s: %s", identity["name"], err)
        return False

    logger.info("[git] %s pushed branch: %s", identity["name"], branch)
    return True


def create_branch(branch_name: str) -> bool:
    """Create and checkout a new branch."""
    env = {**os.environ}
    code, _, err = _run(["git", "checkout", "-b", branch_name], env)
    if code != 0:
        logger.error("[git] Branch creation failed: %s", err)
        return False
    logger.info("[git] Branch created: %s", branch_name)
    return True


def format_commit_message(ticket_id: str, summary: str, body: str = "", agent_name: str = "") -> str:
    """
    Build a standardised commit message.

    Format:
        [FDA-003] Add JWT authentication middleware

        Implements stateless JWT auth on all /api/v1/ endpoints.
        Tests: 9 unit tests, all passing.

        Co-authored-by: Aria Chen <aria@rse-intelligence.ai>
    """
    identity = get_identity(agent_name) if agent_name else None
    coordinator = get_identity("coordinator")

    lines = [f"[{ticket_id}] {summary}"]
    if body:
        lines += ["", body]

    # Add Co-authored-by trailer so GitHub shows both the agent and coordinator
    if identity and identity["name"] != coordinator["name"]:
        lines += [
            "",
            f"Co-authored-by: {coordinator['name']} <{coordinator['github_email']}>",
        ]

    return "\n".join(lines)
