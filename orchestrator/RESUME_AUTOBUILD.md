# Autonomous agents — how they keep working

The agents build and maintain tickets on the **deployed Railway service**. The
worker thread (`orchestrator/scheduler/tasks.py → run_autobuild_worker`) starts
~20s after boot and, each cycle, does two things in order:

1. **Revise open PRs that got feedback** — if a reviewer requested changes, left
   a comment, or CI failed, the authoring agent updates that PR in place.
2. **Build the next eligible ticket** — only if there were no revisions to make.

It only acts when there's an eligible ticket/feedback **and** budget remains.

## 1. Agents respond to PR reviews (revision loop)

`executor.revise_open_prs()` → for each open `agent/*` PR:

- `get_pr_feedback(pr, head_sha)` collects review change-requests, reviewer/Copilot
  comments, and failing CI checks — **only items newer than the PR's last commit**,
  so a fix never re-triggers the same feedback (no infinite loop).
- The authoring agent re-runs with the ticket spec + the PR's current files +
  the feedback, then commits the fix to the **same branch** (`commit_files_to_branch`)
  and posts a comment explaining what changed. The existing PR updates; CI re-runs.

Trigger manually any time: `POST /api/agents/revise-prs`.

## 2. A redeploy never restarts completed work

GitHub PRs are the durable record of progress (the container's `SPRINT-01.md`
is rebuilt from the repo on every deploy and can look "Not started").

- On startup the worker calls `reconcile_sprint_from_github()`, which marks every
  ticket that already has an open/merged PR as ✅ Done in `SPRINT-01.md`.
- `get_next_ticket()` independently skips any ticket with an open/merged PR and
  treats a blocker as satisfied once it has a PR.

Net effect: after a redeploy the agents pick up from the next unbuilt ticket and
never rebuild finished ones.

## 3. Coding model — Nemotron 3 Ultra

Ticket implementation and PR revisions use `CODE_NIM_MODEL`, now defaulting to
`nvidia/nemotron-3-ultra-550b-a55b` (with a Llama 3.3 70B fallback if the Ultra
slug is unavailable on the account or rejects tool calls).

> This applies when `CODE_PROVIDER=nim` (the default). If you set
> `CODE_PROVIDER=anthropic`, coding uses `CLAUDE_CODE_MODEL` (Claude) instead —
> keep `CODE_PROVIDER=nim` to use Nemotron.

## What to set in Railway → Variables

| Variable | Value | Why |
|---|---|---|
| `CODE_PROVIDER` | `nim` | Use Nemotron for coding (default) |
| `NVIDIA_NIM_API_KEY` | your NIM key | Required for `nim` provider |
| `CODE_NIM_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b,meta/llama-3.3-70b-instruct` | Coding model + fallback (already the default) |
| `GITHUB_REPO` | `Code-250/rse_intelligence` | Where PRs are opened/updated |
| `GITHUB_BASE_BRANCH` | `main` | PR target |
| `GITHUB_TOKEN_KWAME` | PAT (repo scope) | Backend engineer — authors/revises backend PRs |
| `GITHUB_TOKEN_LUCA` | PAT | DevOps — CI/infra PRs |
| `GITHUB_TOKEN_SOFIA` | PAT | Mobile engineer — mobile PRs |
| `GITHUB_TOKEN_MARCUS` | PAT (different account) | PM — reviews PRs (GitHub blocks self-review, so this must differ from author tokens) |
| `GITHUB_REVIEWER` | your GitHub username | You're auto-requested as reviewer; final merge stays with you |
| `AUTOBUILD_ENABLED` | `true` | Master switch (default) |
| `DATABASE_URL` | Railway Postgres URL | Activity/usage tracking (degrades gracefully if unset) |

A single shared `GITHUB_TOKEN` also works, but then Marcus can't review (same
account can't review its own PR) and all commits show one author.

## Security — do before the next push
A real **Gmail app password** is committed in `backend/.env` and a NIM key in
`orchestrator/.env.example`. Rotate both, then `git rm --cached backend/.env`.
The root `.gitignore` prevents recommitting it.
