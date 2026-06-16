"""
Generates a professional daily investor advisory using the best available LLM.

Provider waterfall (handled transparently by llm.client):
  NVIDIA NIM (llama-3.1-nemotron-70b) → Ollama (local) → Groq → rule-based

The model is purely the writing engine.
All RSE market intelligence is assembled by context_builder.py and injected
into the prompt — the model never invents figures.
"""
import logging
import os

from dotenv import load_dotenv

from alerts.context_builder import build_rich_context
from llm.client import generate

load_dotenv()

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a senior investment analyst specialising exclusively \
in the Rwanda Stock Exchange (RSE).

Write a daily advisory note for a private investor. Follow these rules exactly:

FORMAT:
- Write in plain flowing paragraphs only. No bullet points, no numbered lists, \
no headers, no dashes, no markdown.
- Start directly with the market observation. No greeting.
- Maximum 200 words total.
- End with exactly: RSE Intelligence | [date]

CONTENT:
- Lead with the single most time-sensitive action if one exists.
- Follow with 1–2 supporting observations grounded in the data provided.
- Close with a one-sentence outlook.
- Do not invent figures. If nothing is actionable, say so in two sentences and close.

TONE: Direct, confident, concise. Written for someone who trusts your judgement \
but is not a finance expert."""


def _build_user_prompt(context_text: str, report_date) -> str:
    return (
        f"{context_text}\n\n"
        f"Write the advisory note now for {report_date}. "
        "Start writing immediately — do not explain what you are about to do, "
        "do not repeat the instructions, do not use any lists or headers. "
        "Write the advisory in plain paragraphs as a professional analyst would."
    )


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _rule_based_advisory(report_date, unique_signals: list, avg_bond_coupon: str) -> str:
    """
    Deterministic fallback when every LLM provider is unavailable.
    Applies RSE-specific rules to produce a structured note.
    """
    if not unique_signals:
        return (
            f"RSE Intelligence | {report_date}\n\n"
            "No actionable signals today. The market is quiet. "
            "Hold existing positions and review again tomorrow."
        )

    paragraphs = []

    urgent = [s for s in unique_signals if s["type"] == "dividend_capture"]
    if urgent:
        s = urgent[0]
        paragraphs.append(
            f"URGENT — {s['symbol']} dividend record date is within days. "
            f"{s['message']} Missing this date means missing the payment entirely."
        )

    squeezes = [s for s in unique_signals if s["type"] == "squeeze"]
    if squeezes:
        syms = ", ".join(s["symbol"] for s in squeezes)
        paragraphs.append(
            f"Order book pressure on {syms}: significant buy interest with no sellers "
            "in sight. Watch for entry when sellers appear — the imbalance signals "
            "upward price pressure."
        )

    if any(s["type"] == "bond_yield" for s in unique_signals):
        paragraphs.append(
            f"T-bonds are paying {avg_bond_coupon}% coupon — risk-free in RWF. "
            "Idle cash not deployed in equities should be rotated to government bonds."
        )

    drops = [s for s in unique_signals if s["type"] == "price_drop"]
    if drops:
        syms = ", ".join(s["symbol"] for s in drops)
        paragraphs.append(
            f"Notable price decline on {syms} today. "
            "RSE drops are rare — investigate before acting."
        )

    return f"RSE Intelligence | {report_date}\n\n" + "\n\n".join(paragraphs)


# ── RL signal filtering ───────────────────────────────────────────────────────

def _apply_rl_filter(conn, signals: list, report_date) -> list:
    """
    Run the RL agent to prioritise/suppress signals.
    Returns a filtered list with an 'emphasis' key added to each entry.
    Degrades gracefully if the rl module is unavailable.
    """
    try:
        from rl.environment import build_state
        from rl.q_agent import QAgent

        state = build_state(conn, report_date)
        agent = QAgent(conn)
        rl_actions = agent.choose_actions(state, signals)

        _record_rl_actions(conn, signals, rl_actions, report_date)

        filtered = []
        for s in signals:
            action = rl_actions.get(s["type"], 1)
            if action == 0:
                continue  # suppressed
            filtered.append({**s, "emphasis": "PRIMARY" if action == 2 else "normal"})
        return filtered

    except Exception as e:
        logger.warning("[advisory] RL filter unavailable (%s) — using all signals", e)
        return [{**s, "emphasis": "normal"} for s in signals]


def _record_rl_actions(conn, signals: list, rl_actions: dict, report_date) -> None:
    """Persist RL decisions for future training. Fire-and-forget."""
    seen: set = set()
    try:
        cur = conn.cursor()
        for s in signals:
            key = (s["type"], s.get("symbol"))
            if key in seen:
                continue
            seen.add(key)
            action = rl_actions.get(s["type"], 1)
            cur.execute(
                """
                UPDATE signal_outcomes SET action_taken = %s
                WHERE signal_date = %s AND signal_type = %s
                  AND symbol IS NOT DISTINCT FROM %s
                  AND outcome_checked = FALSE
                """,
                (action, report_date, s["type"], s.get("symbol")),
            )
        conn.commit()
        cur.close()
    except Exception as e:
        logger.warning("[advisory] Could not record RL actions: %s", e)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_advisory(report_date, signals: list, conn) -> str:
    """
    Build market context, run it through the best available LLM, and return
    the advisory text. Falls back to a rule-based note if all LLMs are down.
    """
    filtered_signals = _apply_rl_filter(conn, signals, report_date)
    context_text, unique_signals = build_rich_context(conn, report_date, filtered_signals)
    user_prompt = _build_user_prompt(context_text, report_date)

    text = generate(_SYSTEM_PROMPT, user_prompt)

    if not text:
        logger.warning("[advisory] All LLM providers failed — using rule-based fallback")
        avg_coupon = _fetch_avg_coupon(conn, report_date)
        text = _rule_based_advisory(report_date, unique_signals, avg_coupon)

    return text


def _fetch_avg_coupon(conn, report_date) -> str:
    """Helper for rule-based fallback — fetch average T-bond coupon."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT AVG(coupon_rate) FROM bond_trades WHERE report_date = %s",
            (report_date,),
        )
        row = cur.fetchone()
        cur.close()
        return f"{float(row[0]):.1f}" if row and row[0] else "13+"
    except Exception:
        return "13+"


def send_advisory(report_date, signals: list, conn) -> str:
    """Generate the advisory, send it via WhatsApp, and persist to DB."""
    from alerts.whatsapp import send_whatsapp

    logger.info("[advisory] Generating advisory for %s...", report_date)
    advisory_text = generate_advisory(report_date, signals, conn)

    print(f"\n--- ADVISORY PREVIEW ---\n{advisory_text}\n---\n")
    success = send_whatsapp(advisory_text)

    _persist_advisory(conn, report_date, advisory_text, success)
    return advisory_text


def _persist_advisory(conn, report_date, text: str, alerted: bool) -> None:
    """Upsert the advisory into the signals table for audit trail."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO signals (signal_date, signal_type, symbol, message, alerted)
            VALUES (%s, 'advisory', NULL, %s, %s)
            ON CONFLICT (signal_date, signal_type, symbol)
            DO UPDATE SET message = EXCLUDED.message, alerted = EXCLUDED.alerted
            """,
            (report_date, text, alerted),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error("[advisory] Failed to persist advisory: %s", e)
