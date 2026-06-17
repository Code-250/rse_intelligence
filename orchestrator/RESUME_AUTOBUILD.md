# Autonomous agents — how they keep working

**GitHub Issues are the source of truth for the backlog.** Marcus (PM) opens
issues; the engineers pick the next eligible one, build it, and open a PR that
`Closes #N`. Merging the PR closes the issue. The old `SPRINT-01.md` board is no
longer read by the build loop (kept only for the one-time import).

The worker thread (`orchestrator/scheduler/tasks.py → run_autobuild_worker`)
starts ~20s after boot and, each cycle, does two things in order:

1. **Revise open PRs that got feedback** — if a reviewer requested changes, left
   a comment, or CI failed, the authoring agent updates that PR in place.
2. **Build the next eligible issue** — only if there were no revisions to make.

It only acts when there's eligible work **and** budget remains.

## 0. The issue workflow

- **Marcus creates issues.** `POST /api/pm/create-issues {"goal": "..."}` has the
  PM break a goal into labelled issues. To seed the backlog from the existing
  sprint file once, call `POST /api/pm/import-sprint`.
- **Routing by label:** `backend` → Kwame, `mobile`/`frontend` → Sofia,
  `deployment`/`devops` → Luca. Marcus also adds `agent-ready`.
- **Eligibility:** an issue is picked when it's open, has an agent label, isn't
  already building (no `building` label, no open PR), has the `agent-ready` label
  if `ISSUE_REQUIRE_READY_LABEL=true`, and every `Blocked by: #N` issue is closed.
- **Done = the issue is closed** (which merging the PR does automatically).

## 1. Agents respond to PR reviews AND merge conflicts (revision loop)

`executor.revise_open_prs()` → for each open `agent/*` PR, in priority order:

- **Merge conflicts first.** `pr_mergeable()` checks the PR's mergeable state. If
  it's `dirty` (conflicts with `main`), the agent fetches its branch files + the
  latest `main` versions, reconciles them, and `resolve_branch_conflicts()` writes
  a real **merge commit** (tree = latest base + the agent's reconciled files,
  parents = branch head + base head) so GitHub sees the PR as mergeable again.
- **Then review/CI feedback.** `get_pr_feedback(pr, head_sha)` collects review
  change-requests, reviewer/Copilot comments, and failing CI checks — **only items
  newer than the PR's last commit**, so a fix never re-triggers the same feedback
  (no infinite loop). The agent re-runs with the ticket spec + current files + the
  feedback, commits the fix to the **same branch**, and comments what changed.

Either way the existing PR updates in place and CI re-runs. Trigger manually any
time: `POST /api/agents/revise-prs`.

> The PR author's token needs `repo` scope (it writes blobs/trees/commits via the
> Git Data API to build the conflict-resolution merge commit).

## 2. A redeploy never restarts completed work

Because state lives in GitHub Issues (not on the container), this is automatic:

- A **closed** issue is done — `get_next_ticket()` only considers open issues.
- An issue with an **open PR** counts as in progress and is skipped
  (`issues_with_open_prs()`).
- On startup the worker calls `reconcile_building_labels()`, which only clears a
  stale `building` label left by a container that died mid-build (no PR yet), so
  that issue becomes eligible again instead of stuck.

Net effect: after a redeploy the agents continue from the next open, unbuilt
issue and never rebuild finished ones.

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
| `GITHUB_TOKEN_MARCUS` | PAT (different account) | PM — **creates issues** and reviews PRs (GitHub blocks self-review, so this must differ from author tokens) |
| `GITHUB_REVIEWER` | your GitHub username | You're auto-requested as reviewer; final merge stays with you |
| `ISSUE_REQUIRE_READY_LABEL` | `false` | If `true`, only `agent-ready`-labelled issues are picked up |
| `AUTOBUILD_ENABLED` | `true` | Master switch (default) |
| `DATABASE_URL` | Railway Postgres URL | Activity/usage tracking (degrades gracefully if unset) |

A single shared `GITHUB_TOKEN` also works, but then Marcus can't review (same
account can't review its own PR) and all commits show one author.

## Getting started (seed the backlog)
The agents idle until there are issues. After setting the variables:

1. **Seed from the existing sprint:** `POST /api/pm/import-sprint` — turns the 11
   `FDA-*` tickets into labelled GitHub issues (with blocked-by links). Run once.
2. **Or have Marcus plan new work:** `POST /api/pm/create-issues {"goal": "..."}`.
3. The worker then picks the next eligible issue and opens a PR that closes it.

## Security — do before the next push
A real **Gmail app password** is committed in `backend/.env` and a NIM key in
`orchestrator/.env.example`. Rotate both, then `git rm --cached backend/.env`.
The root `.gitignore` prevents recommitting it.
