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
    """PM Agent writes a stand-up and sends it to Richard via WhatsApp."""
    logger.info("[Scheduler] Running daily stand-up")
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    prompt = (
        f"Today is {today}. Write the daily stand-up summary for Richard Munyemana. "
        "Check the current sprint tickets in SPRINT-01.md and PR_QUEUE.md. "
        "Format: what's in progress (with agent names), what's completed since yesterday, "
        "any blockers, PRs awaiting Richard's review. "
        "Keep it under 250 words. Plain text only — no markdown headers, just short paragraphs."
    )

    response = run_agent("project-manager", prompt, history=[])
    log_activity("project-manager", "digest_sent", f"Daily stand-up sent to WhatsApp ({today})")

    message = f"📋 *Daily Stand-Up — {today}*\n\n{response}"
    send_whatsapp(message)
    logger.info("[Scheduler] Daily stand-up sent")


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


# ── Scheduler runner ──────────────────────────────────────────────────────────

def setup_schedule():
    """Register all scheduled tasks."""
    schedule.every().day.at("08:00").do(daily_standup)
    schedule.every().day.at("09:00").do(weekly_marketing_report)  # Only runs on Monday internally
    schedule.every(30).minutes.do(check_blockers)
    schedule.every().day.at("22:00").do(evening_digest)
    logger.info("[Scheduler] Schedule configured: stand-up 08:00, marketing 09:00 Mon, blocker check every 30m, digest 22:00")


def run_scheduler():
    """Run the scheduler in a background thread. Call once on app startup."""
    setup_schedule()

    def _loop():
        logger.info("[Scheduler] Background scheduler started")
        while True:
            schedule.run_pending()
            time.sleep(30)

    thread = threading.Thread(target=_loop, daemon=True, name="scheduler")
    thread.start()
    logger.info("[Scheduler] Background thread launched")
