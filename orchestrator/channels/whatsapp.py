"""
WhatsApp integration — two layers:

  OUTBOUND (CallMeBot): send messages to Richard's WhatsApp.
    Used for: daily digest, PR alerts, P0 bug notifications.
    No setup needed — uses existing WHATSAPP_PHONE + WHATSAPP_API_KEY.

  INBOUND (Meta WhatsApp Cloud API): receive messages FROM Richard.
    Richard sends WhatsApp message → Meta webhook → this endpoint → agent responds.
    Setup required (see SETUP.md). Until configured, use the web dashboard.
"""
import logging
import os
import urllib.parse

import sys
from pathlib import Path
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agents.identities import format_whatsapp_message

load_dotenv()
logger = logging.getLogger(__name__)

# ── Outbound (CallMeBot) ──────────────────────────────────────────────────────
WHATSAPP_PHONE   = os.getenv("WHATSAPP_PHONE", "")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY", "")

# ── Inbound (Meta WhatsApp Cloud API) ────────────────────────────────────────
META_TOKEN         = os.getenv("WHATSAPP_META_TOKEN", "")
META_PHONE_ID      = os.getenv("WHATSAPP_META_PHONE_ID", "")
META_VERIFY_TOKEN  = os.getenv("WHATSAPP_META_VERIFY_TOKEN", "rse-intelligence-verify")
META_API_URL       = "https://graph.facebook.com/v19.0"


# ── Outbound ──────────────────────────────────────────────────────────────────

def send_whatsapp(message: str) -> bool:
    """
    Send a message to Richard via CallMeBot (outbound only).
    Returns True on success, False on failure.
    """
    if not WHATSAPP_PHONE or not WHATSAPP_API_KEY:
        logger.warning("[WhatsApp] WHATSAPP_PHONE or WHATSAPP_API_KEY not set — skipping send")
        return False

    encoded = urllib.parse.quote(message)
    url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={WHATSAPP_PHONE}"
        f"&text={encoded}"
        f"&apikey={WHATSAPP_API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=15)
        success = resp.status_code == 200
        if success:
            logger.info("[WhatsApp] Sent (%d chars)", len(message))
        else:
            logger.error("[WhatsApp] Send failed: %d %s", resp.status_code, resp.text[:100])
        return success
    except Exception as e:
        logger.error("[WhatsApp] Error: %s", e)
        return False


def send_agent_response(agent_name: str, response: str):
    """
    Format and send an agent response to WhatsApp using the agent's
    human identity (name, header, sign-off from identities.py).
    """
    message = format_whatsapp_message(agent_name, response)
    send_whatsapp(message)


# ── Inbound (Meta Cloud API) ──────────────────────────────────────────────────

def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """
    Handle Meta's webhook verification GET request.
    Returns the challenge string if valid, None if invalid.
    """
    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        logger.info("[WhatsApp] Webhook verified")
        return challenge
    logger.warning("[WhatsApp] Webhook verification failed")
    return None


def parse_inbound_message(payload: dict) -> tuple[str, str] | None:
    """
    Extract (sender_phone, message_text) from a Meta webhook payload.
    Returns None if the payload doesn't contain a text message.
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        msg = change["messages"][0]
        if msg.get("type") != "text":
            return None
        sender = msg["from"]
        text = msg["text"]["body"]
        return sender, text
    except (KeyError, IndexError):
        return None


def send_whatsapp_meta(to_phone: str, message: str) -> bool:
    """
    Send a message via Meta WhatsApp Cloud API (used for inbound reply).
    Requires WHATSAPP_META_TOKEN and WHATSAPP_META_PHONE_ID to be set.
    """
    if not META_TOKEN or not META_PHONE_ID:
        # Fall back to CallMeBot for outbound
        return send_whatsapp(message)

    url = f"{META_API_URL}/{META_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json",
    }
    # Truncate to WhatsApp limit
    text = message[:4096] if len(message) > 4096 else message
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        success = resp.status_code == 200
        if not success:
            logger.error("[WhatsApp Meta] Send failed: %d %s", resp.status_code, resp.text[:100])
        return success
    except Exception as e:
        logger.error("[WhatsApp Meta] Error: %s", e)
        return False
