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


def github_configured() -> bool:
    return bool(os.getenv("GITHUB_TOKEN") and os.getenv("GITHUB_REPO"))


def _cfg() -> tuple[str, str, str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")  # "owner/repo"
    base = os.getenv("GITHUB_BASE_BRANCH", "main")
    owner, _, name = repo.partition("/")
    return token, owner, name, base


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
) -> dict:
    """
    Commit `files` ({repo_relative_path: content}) to a new branch and open a PR.

    Returns {"ok": True, "pr_url": ..., "branch": ...} on success, or
            {"ok": False, "error": "..."} on failure / not configured.
    """
    if not github_configured():
        return {"ok": False, "error": "GitHub not configured (set GITHUB_TOKEN and GITHUB_REPO)."}
    if not files:
        return {"ok": False, "error": "No files to commit."}

    token, owner, repo, base = _cfg()
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

        # 4. Open the PR
        body = (
            f"## {ticket_id} — {title}\n\n"
            f"{summary}\n\n"
            f"**Implemented by:** {agent_display_name}\n"
            f"**Files ({len(committed)}):**\n" + "\n".join(f"- `{p}`" for p in committed) +
            "\n\n_Opened automatically by the RSE Intelligence agent orchestrator._"
        )
        r = requests.post(
            f"{API}/repos/{owner}/{repo}/pulls", headers=h, timeout=30,
            json={"title": f"{ticket_id}: {title}", "head": branch, "base": base, "body": body},
        )
        if r.status_code not in (200, 201):
            # Branch + commits exist even if PR creation failed — report the branch.
            return {"ok": False, "error": f"Files committed to '{branch}' but PR creation failed: {r.status_code} {r.text[:200]}", "branch": branch}

        pr_url = r.json().get("html_url", "")
        logger.info("[GitHub] PR opened for %s: %s", ticket_id, pr_url)
        return {"ok": True, "pr_url": pr_url, "branch": branch, "files": committed}

    except Exception as e:
        logger.error("[GitHub] PR push failed for %s: %s", ticket_id, e)
        return {"ok": False, "error": f"GitHub push error: {e}"}
