"""
Scheduled tasks — run autonomously on a timer.

Schedule:
  08:00 daily  — PM Agent writes daily stand-up → sent to WhatsApp
  09:00 Monday — Marketing Agent generates weekly report → sent to WhatsApp
  Every 30 min — Check for agent blockers and escalate if needed

Run this module as a background thread alongside the FastAPI app,
or as a separate Railway worker process.
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone

import schedule

from orchestrator.agents.runner import run_agent, resolve_agent
from orchestrator.channels.whatsapp import send_whatsapp, send_agent_response
from orchestrator.db.activity import log_activity, get_daily_summary

logger = logging.getLogger(__name__)

RICHARD_PHONE = os.getenv("WHATSAPP_PHONE", "")


# ── Daily stand-up (08:00 every day) ─────────────────────────────────────────

def daily_standup():
    """
    Each agent posts their own stand-up to the group chat.
    A summary digest is also sent to Richard's WhatsApp.
    """
    logger.info("[Scheduler] Running daily stand-up")
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    # Post to group via API (avoids circular imports)
    import requests as _req
    try:
        _req.post("http://localhost:8080/api/group/standup", timeout=120)
        logger.info("[Scheduler] Stand-up posted to group chat")
    except Exception as e:
        logger.warning("[Scheduler] Could not post to group API: %s", e)

    # Still send a WhatsApp digest summary via PM Agent
    summary_prompt = (
        f"Today is {today}. Write a one-paragraph WhatsApp digest for Richard summarising "
        "what the team is working on today and any critical items he needs to act on. "
        "Keep it under 100 words. Sign off as Marcus — PM."
    )
    summary = run_agent("project-manager", summary_prompt, history=[])
    log_activity("project-manager", "digest_sent", f"Daily stand-up digest ({today})")

    message = f"📋 *RSE Intelligence — Daily Stand-Up*\n_{today}_\n\n{summary}\n\n_Full updates in the Group Chat tab of your dashboard._"
    send_whatsapp(message)
    logger.info("[Scheduler] Stand-up digest sent to WhatsApp")


# ── Weekly marketing report (09:00 Monday) ───────────────────────────────────

def weekly_marketing_report():
    """Marketing Agent generates a weekly performance summary."""
    if datetime.now().weekday() != 0:  # 0 = Monday
        return

    logger.info("[Scheduler] Running weekly marketing report")
    today = datetime.now(timezone.utc).strftime("%d %B %Y")

    prompt = (
        f"Today is Monday {today}. Generate the weekly marketing report for Richard. "
        "We are in pre-launch phase for the Financial Document Analyzer. "
        "Report on: launch preparation status, recommended first advertising platform "
        "with projected CPI and 3-month revenue model, any competitor activity to note, "
        "one recommended action for this week with projected impact. "
        "Keep it under 300 words. Plain text only."
    )

    response = run_agent("sales-marketing", prompt, history=[])
    log_activity("sales-marketing", "digest_sent", f"Weekly marketing report sent ({today})")

    message = f"📣 *Weekly Marketing Report — {today}*\n\n{response}"
    send_whatsapp(message)
    logger.info("[Scheduler] Weekly marketing report sent")


# ── Blocker check (every 30 minutes) ─────────────────────────────────────────

def check_blockers():
    """Coordinator checks for any overdue tickets or agent blockers."""
    logger.info("[Scheduler] Running blocker check")

    prompt = (
        "Do a quick blocker check. Review the active sprint tickets. "
        "Are any tickets overdue? Are any agents blocked? "
        "If everything is on track, respond with 'All clear — no blockers.' "
        "If there are issues, list them concisely (one line each). "
        "Only send to WhatsApp if there are actual blockers — not if all is clear."
    )

    response = run_agent("coordinator", prompt, history=[])

    # Only WhatsApp Richard if something needs attention
    if "all clear" not in response.lower() and len(response.strip()) > 20:
        message = f"🧠 *Coordinator Alert*\n\n{response}"
        send_whatsapp(message)
        log_activity("coordinator", "blocker_alert", f"Blocker detected: {response[:100]}")
        logger.info("[Scheduler] Blocker alert sent to WhatsApp")
    else:
        log_activity("coordinator", "blocker_check", "No blockers found")
        logger.info("[Scheduler] No blockers — silent check complete")


# ── Activity digest (22:00 daily) ─────────────────────────────────────────────

def evening_digest():
    """Send a brief end-of-day summary of what the agents accomplished."""
    logger.info("[Scheduler] Running evening digest")
    stats = get_daily_summary()
    today = datetime.now(timezone.utc).strftime("%A, %d %B")

    if not stats["agent_stats"]:
        return  # Nothing happened today — don't spam Richard

    lines = [f"🌙 *Agent Activity — {today}*\n"]
    for stat in stats["agent_stats"]:
        agent = stat["agent_name"].replace("-", " ").title()
        lines.append(f"  • {agent}: {stat['action_count']} actions")

    lines.append(f"\nTotal conversations with Richard: {stats['total_messages']}")
    lines.append("\nSee full activity at your dashboard.")

    send_whatsapp("\n".join(lines))
    log_activity("coordinator", "digest_sent", f"Evening digest sent ({today})")


# ── Autonomous build worker (agents implement tickets 24/7) ───────────────────
#
# The agents work continuously — any time of day — as long as there is an
# eligible ticket AND budget remaining. They are not tied to office hours or
# night-time; the only real limit is spend. Two budget gates govern them:
#   • MONTHLY_BUDGET_USD        — hard monthly ceiling (also drives alerts)
#   • AUTOBUILD_DAILY_BUDGET_USD — paces spend across days so a backlog can't
#                                  drain the whole month in a few hours
# When over budget or out of work, the worker idles and re-checks later, so it
# resumes automatically when the day rolls over or new tickets become unblocked.


# Set when a build cycle hits a problem (over budget, misconfig, a ticket that
# couldn't complete). Stops the worker from re-running and re-spending until the
# app is redeployed (which clears it) or the issue is fixed.
_autobuild_halt_reason = ""


def _autobuild_ready() -> tuple[bool, str]:
    """Whether the builder may run right now. Returns (ok, reason_if_not)."""
    if _autobuild_halt_reason:
        return False, f"halted — {_autobuild_halt_reason}"
    if os.getenv("AUTOBUILD_ENABLED", "true").lower() != "true":
        return False, "AUTOBUILD_ENABLED is off"

    provider = os.getenv("CODE_PROVIDER", "nim").lower()
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY not set"
        # Budget only applies to the paid provider (NIM is free).
        from orchestrator.db.usage import get_monthly_spend, get_today_spend
        monthly_budget = float(os.getenv("MONTHLY_BUDGET_USD", "20.0"))
        daily_cap = float(os.getenv("AUTOBUILD_DAILY_BUDGET_USD", "3.0"))
        if get_monthly_spend() >= monthly_budget:
            return False, f"monthly budget reached (${monthly_budget:.2f})"
        if get_today_spend() >= daily_cap:
            return False, f"daily cap reached (${daily_cap:.2f})"
    else:  # nim (free)
        if not os.getenv("NVIDIA_NIM_API_KEY"):
            return False, "NVIDIA_NIM_API_KEY not set"
    return True, ""


def autonomous_build_step():
    """
    Build ONE eligible ticket if conditions allow.

    Returns the result dict when a ticket was built, or None when nothing ran
    (disabled, over budget, or no eligible ticket). Notifies Richard on WhatsApp
    for each ticket completed or paused.
    """
    global _autobuild_halt_reason

    ok, reason = _autobuild_ready()
    if not ok:
        logger.info("[AutoBuild] idle — %s", reason)
        return None

    from orchestrator.agents.executor import implement_next_ticket, revise_open_prs
    from orchestrator.db.usage import get_monthly_spend

    # ── 1. Address review feedback on existing PRs BEFORE starting new work ────
    # If a reviewer requested changes, left comments, or CI failed, the authoring
    # agent updates its own PR in place. Doing this first keeps open PRs moving
    # toward merge instead of piling up new ones. Cheap when there's no feedback.
    try:
        revisions = revise_open_prs()
    except Exception as e:
        logger.error("[AutoBuild] revision pass error: %s", e)
        revisions = []

    did_revise = False
    for rev in revisions:
        if rev.get("status") == "revised":
            did_revise = True
            log_activity("coordinator", "pr_revised",
                         f"{rev.get('ticket_id')} updated after review by {rev.get('agent_display_name', 'agent')}")
            pr_line = f"\n🔀 {rev['pr_url']}" if rev.get("pr_url") else ""
            send_whatsapp(
                f"♻️ *Agent updated a PR after review*\n\n"
                f"{rev.get('agent_display_name', 'Agent')} addressed feedback on "
                f"{rev.get('ticket_id')} — {len(rev.get('files_changed', []))} files updated."
                f"{pr_line}\n\nCI will re-run; re-review when ready."
            )
            logger.info("[AutoBuild] %s revised PR #%s", rev.get("ticket_id"), rev.get("pr_number"))
        elif rev.get("status") in ("revise_failed", "error"):
            logger.warning("[AutoBuild] revision issue on %s: %s",
                           rev.get("ticket_id", rev.get("branch")), rev.get("message"))

    # Did revision work this cycle → pace and re-check rather than also building.
    if did_revise:
        return {"revisions": revisions}

    # ── 2. Otherwise, build the next eligible ticket ───────────────────────────
    result = implement_next_ticket(None)  # auto-pick the next unblocked ticket
    status = result.get("status")

    if status == "no_ticket":
        logger.info("[AutoBuild] no eligible tickets right now — idling")
        return None

    if status == "completed":
        monthly = get_monthly_spend()
        monthly_budget = float(os.getenv("MONTHLY_BUDGET_USD", "20.0"))
        pr_line = f"\n🔀 {result['pr_url']}" if result.get("pr_url") else ""
        log_activity("coordinator", "deploy", f"AutoBuild: {result.get('ticket_id')} completed")
        send_whatsapp(
            f"✅ *Agent shipped a ticket*\n\n"
            f"{result.get('agent_display_name', 'Agent')} finished "
            f"{result.get('ticket_id')} — {result.get('ticket_title', '')}\n"
            f"{len(result.get('files_changed', []))} files · ${float(result.get('cost_usd', 0) or 0):.2f}"
            f"{pr_line}\n\n"
            f"Month to date: ${monthly:.2f} of ${monthly_budget:.2f}\n"
            f"Review and merge the PR on GitHub."
        )
        logger.info("[AutoBuild] %s completed ($%.2f)", result.get("ticket_id"), float(result.get("cost_usd", 0) or 0))
        return result

    # Any other outcome (budget reached, misconfig, error, stopped early, PR
    # failed) — HALT the worker so it can't re-run and re-spend. Alert Richard once.
    _autobuild_halt_reason = result.get("message") or result.get("pr_error") or status
    logger.warning("[AutoBuild] halting — %s", _autobuild_halt_reason)
    log_activity("coordinator", "blocker_alert", f"AutoBuild halted: {_autobuild_halt_reason[:120]}")
    send_whatsapp(
        f"⏸️ *Autonomous build paused*\n\n"
        f"Reason: {_autobuild_halt_reason}\n\n"
        f"No more credits will be spent until this is resolved. Fix it and redeploy "
        f"(or use the dashboard) to resume."
    )
    return None


def run_autobuild_worker():
    """
    Continuous worker thread: builds tickets back-to-back whenever there is
    eligible work and budget, one at a time (never overlapping). Idles and
    re-checks when disabled, over budget, or no ticket is ready.
    """
    pace_s = int(os.getenv("AUTOBUILD_PACE_SECONDS", "30"))    # gap after a successful build
    idle_s = int(os.getenv("AUTOBUILD_IDLE_SECONDS", "600"))   # wait when there's nothing to do

    def _loop():
        logger.info("[AutoBuild] Continuous worker started (pace %ds, idle %ds)", pace_s, idle_s)
        time.sleep(20)  # let the app finish booting and DB init
        # GitHub Issues are the source of truth, so completed (closed) and
        # in-progress (open PR) state already survive a redeploy. The only cleanup
        # needed is releasing any issue left labelled 'building' by a container that
        # died mid-build, so it becomes eligible again instead of stuck.
        try:
            from orchestrator.agents.executor import reconcile_building_labels
            cleared = reconcile_building_labels()
            logger.info("[AutoBuild] startup reconcile: cleared %d stale 'building' label(s)", cleared)
        except Exception as e:
            logger.warning("[AutoBuild] startup reconcile skipped: %s", e)
        while True:
            try:
                result = autonomous_build_step()
            except Exception as e:
                logger.error("[AutoBuild] worker error: %s", e)
                result = None
            time.sleep(pace_s if result else idle_s)

    thread = threading.Thread(target=_loop, daemon=True, name="autobuild")
    thread.start()
    logger.info("[AutoBuild] Worker thread launched")


# ── Scheduler runner ──────────────────────────────────────────────────────────

def setup_schedule():
    """Register the time-based scheduled tasks (digests, stand-ups, checks)."""
    schedule.every().day.at("08:00").do(daily_standup)
    schedule.every().day.at("09:00").do(weekly_marketing_report)  # Only runs on Monday internally
    schedule.every(30).minutes.do(check_blockers)
    schedule.every().day.at("22:00").do(evening_digest)
    logger.info("[Scheduler] Configured: stand-up 08:00, marketing 09:00 Mon, blocker check 30m, digest 22:00")


def run_scheduler():
    """Start the background scheduler thread and the continuous build worker. Call once on startup."""
    setup_schedule()

    def _loop():
        logger.info("[Scheduler] Background scheduler started")
        while True:
            schedule.run_pending()
            time.sleep(30)

    thread = threading.Thread(target=_loop, daemon=True, name="scheduler")
    thread.start()
    logger.info("[Scheduler] Background thread launched")

    # Agents build continuously (24/7) on their own thread, governed by budget.
    run_autobuild_worker()
