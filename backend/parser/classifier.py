"""
Classifies a MO Capital PDF into one of these types:

  daily_report       — Daily market summary (equity prices, FX, bonds)
  trade_confirmation — Contract note confirming a buy/sell you made
  account_statement  — Your portfolio / account balance statement
  announcement       — Corporate announcement (dividend, AGM, rights issue)
  unknown            — Could not determine type

Uses keyword matching on extracted text — no API calls needed.
"""
import pdfplumber
import re
import os
import shutil

DOCUMENT_TYPES = [
    "daily_report",
    "trade_confirmation",
    "account_statement",
    "announcement",
    "unknown",
]

# Keywords that strongly indicate each type
SIGNATURES = {
    "daily_report": [
        "today's trading summary",
        "today's exchange rate",
        "bond market",
        "outstanding bids",
        "outstanding offers",
        "equity turn over",
    ],
    "trade_confirmation": [
        "contract note",
        "trade confirmation",
        "we confirm",
        "bought",
        "sold",
        "settlement date",
        "consideration",
        "brokerage",
        "commission",
        "number of shares",
        "price per share",
    ],
    "account_statement": [
        "account statement",
        "portfolio",
        "portfolio value",
        "holdings",
        "opening balance",
        "closing balance",
        "cash balance",
        "your account",
    ],
    "announcement": [
        "annual general meeting",
        "agm",
        "rights issue",
        "notice to shareholders",
        "extraordinary general meeting",
        "egm",
        "prospectus",
        "offer for subscription",
    ],
}


def extract_text(filepath):
    try:
        with pdfplumber.open(filepath) as pdf:
            text = ""
            for page in pdf.pages[:3]:  # First 3 pages enough to classify
                text += (page.extract_text() or "") + "\n"
        return text.lower()
    except Exception as e:
        print(f"  Could not read {filepath}: {e}")
        return ""


def classify(filepath):
    """Returns (doc_type, confidence_score, extracted_text)"""
    text = extract_text(filepath)
    if not text.strip():
        return "unknown", 0, text

    scores = {}
    for doc_type, keywords in SIGNATURES.items():
        hits = sum(1 for kw in keywords if kw in text)
        scores[doc_type] = hits

    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]

    if best_score == 0:
        return "unknown", 0, text

    return best_type, best_score, text


def classify_and_move(filepath):
    """
    Classify the PDF and move it to data/pdfs/<doc_type>/filename.pdf
    Returns the new path and doc_type.
    """
    base_dir = os.path.join(os.path.dirname(__file__), "../../data/pdfs")
    doc_type, score, text = classify(filepath)

    dest_dir = os.path.join(base_dir, doc_type)
    os.makedirs(dest_dir, exist_ok=True)

    filename = os.path.basename(filepath)
    dest_path = os.path.join(dest_dir, filename)

    if filepath != dest_path:
        shutil.move(filepath, dest_path)

    print(f"  [{doc_type}] (score={score}) {filename}")
    return dest_path, doc_type, text


def classify_inbox():
    """
    Classify all PDFs sitting in data/pdfs/inbox/ and sort them into subfolders.
    Returns list of {path, doc_type}
    """
    inbox = os.path.join(os.path.dirname(__file__), "../../data/pdfs/inbox")
    if not os.path.exists(inbox):
        print("Inbox is empty.")
        return []

    pdfs = [f for f in os.listdir(inbox) if f.endswith(".pdf")]
    print(f"\nClassifying {len(pdfs)} PDFs from inbox...")

    results = []
    counts = {t: 0 for t in DOCUMENT_TYPES}

    for filename in sorted(pdfs):
        filepath = os.path.join(inbox, filename)
        new_path, doc_type, _ = classify_and_move(filepath)
        results.append({"path": new_path, "doc_type": doc_type})
        counts[doc_type] += 1

    print("\nClassification summary:")
    for t, c in counts.items():
        if c > 0:
            print(f"  {t}: {c}")

    return results


if __name__ == "__main__":
    results = classify_inbox()
