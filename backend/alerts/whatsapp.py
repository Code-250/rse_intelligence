"""
Sends WhatsApp messages via CallMeBot free API.
"""
import os
import requests
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

PHONE = os.getenv("WHATSAPP_PHONE")
API_KEY = os.getenv("WHATSAPP_API_KEY")
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


def send_whatsapp(message: str) -> bool:
    if not PHONE or not API_KEY:
        print("WhatsApp credentials not set in .env")
        return False
    try:
        resp = requests.get(CALLMEBOT_URL, params={
            "phone": PHONE,
            "text": message,
            "apikey": API_KEY,
        }, timeout=10)
        if resp.status_code == 200:
            print(f"WhatsApp sent: {message[:60]}...")
            return True
        else:
            print(f"WhatsApp failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"WhatsApp error: {e}")
        return False


if __name__ == "__main__":
    send_whatsapp("RSE Intelligence: Test message. System is working!")
