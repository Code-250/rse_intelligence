"""
Gmail extractor — only downloads NEW emails from MO Capital.
Tracks every processed Message-ID in the database so nothing is downloaded twice.
"""
import imaplib
import email
import os
import re
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from db import get_conn

load_dotenv()

GMAIL_USER        = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
BASE_PDF_DIR      = os.path.join(os.path.dirname(__file__), "../../data/pdfs")


# ── helpers ──────────────────────────────────────────────────────────────────

def connect():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select('"[Gmail]/All Mail"')
    return mail


def safe_filename(name):
    return re.sub(r'[^\w\s\-.]', '_', name).strip()


def get_processed_ids(conn):
    """Return set of Message-IDs already in our DB."""
    cur = conn.cursor()
    cur.execute("SELECT message_id FROM processed_emails")
    ids = {row[0] for row in cur.fetchall()}
    cur.close()
    return ids


def mark_processed(conn, message_id, subject, sender, email_date, attachment_count):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO processed_emails (message_id, subject, sender, email_date, attachment_count)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO NOTHING
    """, (message_id, subject, sender, email_date, attachment_count))
    conn.commit()
    cur.close()


# ── search ────────────────────────────────────────────────────────────────────

def search_mocapital_emails(mail):
    """
    MO Capital sends from two addresses:
      - mocatradingdesk@gmail.com  (historical daily reports since 2024)
      - *@mocapital.co.rw          (contract notes, recent reports)
    """
    all_ids = set()
    search_terms = [
        'FROM "mocatradingdesk@gmail.com"',
        'FROM "mocapital.co.rw"',
        'SUBJECT "Contract Note"',
        'SUBJECT "Equities Purchase"',
    ]
    for term in search_terms:
        status, data = mail.search(None, term)
        if status == "OK" and data[0]:
            ids = data[0].split()
            all_ids.update(ids)

    print(f"  Found {len(all_ids)} total MO Capital emails in Gmail")
    return list(all_ids)


# ── download ──────────────────────────────────────────────────────────────────

def download_new_attachments(mail, email_ids, conn):
    """
    For each email ID:
    - Fetch Message-ID header
    - Skip if already in processed_emails
    - Download PDF attachments
    - Mark as processed
    """
    already_done = get_processed_ids(conn)
    results = []
    skipped = 0
    new_emails = 0

    for eid in email_ids:
        # Fetch headers only first (cheap)
        status, data = mail.fetch(eid, "(RFC822.HEADER)")
        if status != "OK":
            continue

        headers = email.message_from_bytes(data[0][1])
        message_id = headers.get("Message-ID", "").strip()
        subject    = headers.get("Subject", "no subject")
        sender     = headers.get("From", "")
        email_date = headers.get("Date", "")

        if not message_id:
            # Fallback: use UID as message_id
            message_id = f"uid_{eid.decode()}"

        if message_id in already_done:
            skipped += 1
            continue

        # New email — fetch full body
        status, data = mail.fetch(eid, "(RFC822)")
        if status != "OK":
            continue

        msg = email.message_from_bytes(data[0][1])
        attachment_count = 0

        for part in msg.walk():
            if part.get_content_type() != "application/pdf":
                continue

            filename = part.get_filename() or f"attachment_{eid.decode()}.pdf"
            safe_name = safe_filename(filename)

            inbox_dir = os.path.join(BASE_PDF_DIR, "inbox")
            os.makedirs(inbox_dir, exist_ok=True)
            filepath = os.path.join(inbox_dir, safe_name)

            # If file exists on disk but wasn't in DB (e.g. DB was reset), skip download
            if not os.path.exists(filepath):
                payload = part.get_payload(decode=True)
                with open(filepath, "wb") as f:
                    f.write(payload)
                print(f"    Downloaded: {safe_name}")
            else:
                print(f"    File exists, re-queuing: {safe_name}")

            attachment_count += 1
            results.append({
                "path": filepath,
                "filename": safe_name,
                "email_subject": subject,
                "email_date": email_date,
            })

        mark_processed(conn, message_id, subject, sender, email_date, attachment_count)
        new_emails += 1

    print(f"  New emails processed: {new_emails} | Already done (skipped): {skipped}")
    return results


# ── main ──────────────────────────────────────────────────────────────────────

def run(conn=None):
    """
    Pull only new MO Capital emails. Pass an existing DB connection or one will be created.
    Returns list of new attachment dicts.
    """
    close_conn = False
    if conn is None:
        conn = get_conn()
        close_conn = True

    print("Connecting to Gmail...")
    mail = connect()
    ids = search_mocapital_emails(mail)
    results = download_new_attachments(mail, ids, conn)
    mail.logout()

    if close_conn:
        conn.close()

    return results


if __name__ == "__main__":
    files = run()
    print(f"\nTotal new files: {len(files)}")
