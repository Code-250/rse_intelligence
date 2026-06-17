"""
GitHub delivery — agents push real code as pull requests.

Because the Railway container has an ephemeral filesystem (and usually no usable
git remote), we commit through the GitHub REST API instead of local git. Given a
set of files the agent wrote, we:

    1. Read the base branch's head SHA
    2. Create a new branch off it
    3. Commit each file to that branch (Contents API, with the agent's identity)
    4. Open a pull request from the branch into the base

Pushing to a feature branch — never main — means this does NOT trigger a Railway
redeploy, so there's no build loop.

Environment variables:
    GITHUB_TOKEN        — PAT with repo scope (classic) or fine-grained with
                          Contents: read/write + Pull requests: read/write
    GITHUB_REPO         — "owner/repo", e.g. "richardmunyemana/rse_intelligence"
    GITHUB_BASE_BRANCH  — base branch for PRs (default "main")
"""

import base64
import logging
import os
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API = "https://api.github.com"


# Each agent acts as its own GitHub account, so commits/PRs come from the right
# person and the PM can review work he didn't author. Set one token per agent;
# anything unset falls back to the shared GITHUB_TOKEN.
AGENT_TOKEN_ENV = {
    "backend-ai-dev":      "GITHUB_TOKEN_KWAME",
    "mobile-frontend-dev": "GITHUB_TOKEN_SOFIA",
    "deployment":          "GITHUB_TOKEN_LUCA",
    "project-manager":     "GITHUB_TOKEN_MARCUS",
    "coordinator":         "GITHUB_TOKEN_ARIA",
}


def agent_token(agent_name: str) -> str:
    """GitHub token for a specific agent, falling back to the shared GITHUB_TOKEN."""
    env = AGENT_TOKEN_ENV.get(agent_name, "")
    return (os.getenv(env, "") if env else "") or os.getenv("GITHUB_TOKEN", "")


def _any_token() -> bool:
    return bool(os.getenv("GITHUB_TOKEN") or any(os.getenv(v) for v in AGENT_TOKEN_ENV.values()))


def github_configured() -> bool:
    return bool(os.getenv("GITHUB_REPO") and _any_token())


def _cfg() -> tuple[str, str, str]:
    repo = os.getenv("GITHUB_REPO", "")  # "owner/repo"
    base = os.getenv("GITHUB_BASE_BRANCH", "main")
    owner, _, name = repo.partition("/")
    return owner, name, base


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "ticket"


def open_pr_for_ticket(
    ticket_id: str,
    title: str,
    agent_name: str,
    agent_display_name: str,
    agent_email: str,
    files: dict[str, str],
    summary: str,
    token: str,
    how_tested: str = "",
) -> dict:
    """
    Commit `files` ({repo_relative_path: content}) to a new branch and open a PR,
    authenticated as the agent (`token` is that agent's GitHub token).

    Returns {"ok": True, "pr_url": ..., "pr_number": ..., "branch": ...} on success,
            {"ok": False, "error": "..."} otherwise.
    """
    if not token:
        return {"ok": False, "error": f"No GitHub token for {agent_name} — set its GITHUB_TOKEN_* (or shared GITHUB_TOKEN)."}
    if not os.getenv("GITHUB_REPO"):
        return {"ok": False, "error": "GITHUB_REPO not set."}
    if not files:
        return {"ok": False, "error": "No files to commit."}

    owner, repo, base = _cfg()
    if not owner or not repo:
        return {"ok": False, "error": f"GITHUB_REPO must be 'owner/repo' (got '{os.getenv('GITHUB_REPO','')}')."}

    h = _headers(token)
    branch = f"agent/{ticket_id.lower()}-{_slug(title)}-{int(time.time())}"
    author = {"name": agent_display_name, "email": agent_email}

    try:
        # 1. Base head SHA
        r = requests.get(f"{API}/repos/{owner}/{repo}/git/ref/heads/{base}", headers=h, timeout=30)
        if r.status_code != 200:
            return {"ok": False, "error": f"Could not read base branch '{base}': {r.status_code} {r.text[:200]}"}
        base_sha = r.json()["object"]["sha"]

        # 2. Create branch
        r = requests.post(
            f"{API}/repos/{owner}/{repo}/git/refs", headers=h, timeout=30,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if r.status_code not in (200, 201):
            return {"ok": False, "error": f"Could not create branch: {r.status_code} {r.text[:200]}"}

        # 3. Commit each file to the branch
        committed = []
        for path, content in files.items():
            path = path.lstrip("/")
            # Does the file already exist on the branch? (need its sha to update)
            existing = requests.get(
                f"{API}/repos/{owner}/{repo}/contents/{path}",
                headers=h, params={"ref": branch}, timeout=30,
            )
            sha = existing.json().get("sha") if existing.status_code == 200 else None

            payload = {
                "message": f"{ticket_id}: {path}",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
                "author": author,
                "committer": author,
            }
            if sha:
                payload["sha"] = sha

            pr = requests.put(
                f"{API}/repos/{owner}/{repo}/contents/{path}",
                headers=h, json=payload, timeout=60,
            )
            if pr.status_code not in (200, 201):
                logger.warning("[GitHub] Failed to commit %s: %s %s", path, pr.status_code, pr.text[:200])
            else:
                committed.append(path)

        if not committed:
            return {"ok": False, "error": "Branch created but no files committed (check token write scope)."}

        # 4. Open the PR — best-practices description
        files_md = "\n".join(f"- `{p}`" for p in committed)
        body = (
            f"## {ticket_id} — {title}\n\n"
            f"### Summary\n{summary}\n\n"
            f"### Changes ({len(committed)} files)\n{files_md}\n\n"
            f"### How this was tested\n{how_tested or '_Not specified by the implementer._'}\n\n"
            f"### Preview / screenshots\n"
            f"_CI builds the web preview and attaches screenshots to this PR automatically "
            f"(see the CI comment / Checks tab once it finishes)._\n\n"
            f"### Reviewer checklist\n"
            f"- [ ] Meets every acceptance criterion in the ticket\n"
            f"- [ ] Tests pass in CI\n"
            f"- [ ] Preview screenshot looks correct\n"
            f"- [ ] No secrets or debug code committed\n\n"
            f"---\n**Implemented by:** {agent_display_name} · _opened automatically by the RSE Intelligence orchestrator._"
        )
        r = requests.post(
            f"{API}/repos/{owner}/{repo}/pulls", headers=h, timeout=30,
            json={"title": f"{ticket_id}: {title}", "head": branch, "base": base, "body": body},
        )
        if r.status_code not in (200, 201):
            # Branch + commits exist even if PR creation failed — report the branch.
            return {"ok": False, "error": f"Files committed to '{branch}' but PR creation failed: {r.status_code} {r.text[:200]}", "branch": branch}

        pr_data = r.json()
        pr_url = pr_data.get("html_url", "")
        pr_number = pr_data.get("number")

        # Request Richard as the reviewer so the PR lands in his review queue.
        # (Only works when the PR author — the bot token's account — is different
        # from the reviewer; you can't be asked to review your own PR.)
        reviewer = os.getenv("GITHUB_REVIEWER", "").strip()
        if reviewer and pr_number:
            rr = requests.post(
                f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
                headers=h, json={"reviewers": [reviewer]}, timeout=30,
            )
            if rr.status_code not in (200, 201):
                logger.warning("[GitHub] Could not request reviewer '%s': %s %s", reviewer, rr.status_code, rr.text[:150])

        logger.info("[GitHub] PR opened for %s: %s", ticket_id, pr_url)
        return {"ok": True, "pr_url": pr_url, "pr_number": pr_number, "branch": branch, "files": committed}

    except Exception as e:
        logger.error("[GitHub] PR push failed for %s: %s", ticket_id, e)
        return {"ok": False, "error": f"GitHub push error: {e}"}


def post_review(pr_number: int, token: str, event: str, body: str) -> dict:
    """
    Post a review on a PR as the token's account (e.g. Marcus the PM).

    event: "APPROVE", "REQUEST_CHANGES", or "COMMENT". GitHub forbids reviewing
    your own PR, so the reviewer's token must differ from the author's.
    Returns {"ok": bool, "error"?: str}.
    """
    if not token or not pr_number:
        return {"ok": False, "error": "Missing reviewer token or PR number."}
    owner, repo, _ = _cfg()
    event = (event or "COMMENT").upper()
    if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        event = "COMMENT"
    try:
        r = requests.post(
            f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers=_headers(token), timeout=30,
            json={"event": event, "body": body[:60000]},
        )
        if r.status_code in (200, 201):
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"review error: {e}"}
