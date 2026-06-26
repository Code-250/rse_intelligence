"""
Financial document analysis.

Reads a PDF and produces a concise, plain-English financial analysis with an
NVIDIA NIM model. Two extraction paths, tried in order:

  1. Native text  — for normal text-based PDFs (pypdfium2; robust page parsing).
  2. OCR          — for SCANNED / image-only PDFs (very common for financial
                    filings): each page is rendered to an image and read with
                    Tesseract. This is what lets ClariFi handle scans.

Free NIM models keep cost near zero while we validate demand.

Public API:
    extract_text(pdf_bytes) -> (text, page_count, method)
    analyze_pdf_bytes(pdf_bytes, filename) -> dict
"""
from __future__ import annotations

import logging
import os
import time

import requests

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None
try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

logger = logging.getLogger(__name__)

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")

ANALYSIS_MODELS = [
    m.strip() for m in os.getenv(
        "FDA_ANALYSIS_MODEL",
        "nvidia/nemotron-3-ultra-550b-a55b,meta/llama-3.3-70b-instruct,meta/llama-3.1-70b-instruct",
    ).split(",") if m.strip()
]

# How much document text to send to the model (controls latency + cost).
MAX_CHARS = int(os.getenv("FDA_MAX_ANALYSIS_CHARS", "24000"))
# Below this many native-text chars we assume the PDF is scanned and run OCR.
TEXT_MIN_CHARS = int(os.getenv("FDA_TEXT_MIN_CHARS", "120"))
# Page caps (OCR is ~1-3s/page — bound the work so uploads stay responsive).
MAX_TEXT_PAGES = int(os.getenv("FDA_MAX_TEXT_PAGES", "40"))
MAX_OCR_PAGES = int(os.getenv("FDA_MAX_OCR_PAGES", "15"))
OCR_DPI = int(os.getenv("FDA_OCR_DPI", "200"))

SYSTEM_PROMPT = (
    "You are a senior financial analyst. You read a financial document (annual report, "
    "earnings release, financial statement, or similar) and produce a clear, plain-English "
    "analysis for a non-expert investor. Be accurate and concise. Never invent figures that "
    "are not in the document. If the text was produced by OCR it may contain small character "
    "errors — interpret sensibly and flag any figure you are unsure about. Write in markdown "
    "with EXACTLY these sections:\n\n"
    "## Summary\n(2-4 sentences on what this document is and the headline takeaway)\n\n"
    "## Key Figures\n(bullet list of the most important numbers: revenue, profit/loss, margins, "
    "cash, debt, growth — with the figure. Write 'Not stated' if a key figure is absent.)\n\n"
    "## Risks & Red Flags\n(bullet list of concerns: declining metrics, high debt, going-concern "
    "language, unusual items. If none are evident, say so.)\n\n"
    "## Bottom Line\n(1-2 sentence verdict)\n\n"
    "End with this exact line: '_This is an automated draft to assist your own review — not "
    "financial advice._'"
)


class AnalysisError(Exception):
    """Raised when a document cannot be analysed."""


def _native_text(doc) -> str:
    parts = []
    for i in range(min(len(doc), MAX_TEXT_PAGES)):
        try:
            tp = doc[i].get_textpage()
            parts.append(tp.get_text_range() or "")
        except Exception:  # noqa: BLE001
            parts.append("")
    return "\n\n".join(p for p in parts if p.strip()).strip()


def _ocr_text(doc) -> str:
    if pytesseract is None:
        raise AnalysisError("OCR engine unavailable on the server.")
    parts = []
    scale = OCR_DPI / 72.0
    for i in range(min(len(doc), MAX_OCR_PAGES)):
        try:
            pil = doc[i].render(scale=scale).to_pil()
            parts.append(pytesseract.image_to_string(pil) or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("OCR failed on page %d: %s", i + 1, e)
    return "\n\n".join(p for p in parts if p.strip()).strip()


def extract_text(pdf_bytes: bytes) -> tuple[str, int, str]:
    """Extract text from a PDF, OCR'ing scanned pages when needed.

    Returns (text, page_count, method) where method is "text" or "ocr".
    Raises AnalysisError if the PDF can't be opened or yields no readable text.
    """
    if pdfium is None:
        raise AnalysisError("PDF engine unavailable on the server.")
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        page_count = len(doc)
    except Exception as e:  # noqa: BLE001
        logger.error("PDF open failed: %s", e)
        raise AnalysisError("Could not open this PDF. It may be corrupted or password-protected.")

    if page_count == 0:
        raise AnalysisError("This PDF has no pages.")

    native = _native_text(doc)
    if len(native) >= TEXT_MIN_CHARS:
        return native, page_count, "text"

    # Looks scanned/image-only — OCR it.
    logger.info("Native text thin (%d chars) — running OCR on up to %d pages", len(native), MAX_OCR_PAGES)
    ocr = _ocr_text(doc)
    if len(ocr) < 40:
        raise AnalysisError(
            "Couldn't read any text from this document, even with OCR. "
            "If it's a photo of a page, try a clearer scan."
        )
    return ocr, page_count, "ocr"


def _run_chat(messages: list[dict], max_tokens: int = 1200) -> tuple[str, str]:
    """Call NIM with a message list, walking the model fallback chain.

    Returns (content, model_used). Shared by document analysis and follow-up Q&A.
    """
    if not NIM_API_KEY:
        raise AnalysisError("Analysis is not configured (missing NIM API key). Please try later.")

    headers = {"Authorization": f"Bearer {NIM_API_KEY}", "Content-Type": "application/json"}
    last_err = "no model responded"
    for model in ANALYSIS_MODELS:
        payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
        try:
            r = requests.post(f"{NIM_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=120)
        except requests.exceptions.Timeout:
            last_err = f"timeout on {model}"
            continue
        except Exception as e:  # noqa: BLE001
            last_err = f"request error on {model}: {e}"
            continue

        if r.status_code == 200:
            content = (r.json()["choices"][0]["message"]["content"] or "").strip()
            if content:
                return content, model
            last_err = f"empty response from {model}"
        elif r.status_code == 401:
            raise AnalysisError("Analysis service authentication failed. Please try later.")
        else:
            last_err = f"HTTP {r.status_code} on {model}: {r.text[:150]}"
            logger.warning("[NIM] %s", last_err)

    raise AnalysisError("The analysis service is busy right now. Please try again in a moment.")


def _call_nim(text: str) -> tuple[str, str]:
    """Run the full structured analysis over extracted document text."""
    user_content = (
        "Analyse the following financial document and respond using the required sections.\n\n"
        f"=== DOCUMENT START ===\n{text[:MAX_CHARS]}\n=== DOCUMENT END ==="
    )
    return _run_chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )


QA_SYSTEM_PROMPT = (
    "You are a financial analyst answering a follow-up question about a specific document. "
    "Use ONLY the provided document content to answer. If the answer isn't in the content, say "
    "so plainly rather than guessing. Be concise (1-4 sentences or a short bullet list). Never "
    "invent figures. End by reminding the reader this is not financial advice only if you gave a "
    "judgement or recommendation."
)


def answer_question(context: str, question: str) -> tuple[str, str]:
    """Answer a user follow-up question grounded in document text or a prior analysis.

    `context` is the extracted document text (for uploads) or the analysis markdown
    (for samples). Stateless — nothing is stored. Returns (answer_markdown, model_used).
    """
    q = (question or "").strip()
    if not q:
        raise AnalysisError("Please type a question.")
    user_content = (
        f"=== DOCUMENT CONTENT START ===\n{(context or '')[:MAX_CHARS]}\n=== DOCUMENT CONTENT END ===\n\n"
        f"Question: {q}"
    )
    return _run_chat(
        [
            {"role": "system", "content": QA_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=600,
    )


def analyze_pdf_bytes(pdf_bytes: bytes, filename: str = "document.pdf") -> dict:
    """Full pipeline: extract (text or OCR) → analyse → structured result.

    Returns {filename, pages, method, model_used, processing_ms, analysis_markdown,
    text_excerpt}. The excerpt is returned so the browser can hold context for
    follow-up questions without the server storing the document. Raises
    AnalysisError with a user-friendly message on failure.
    """
    start = time.time()
    text, pages, method = extract_text(pdf_bytes)
    analysis, model = _call_nim(text)
    return {
        "filename": filename,
        "pages": pages,
        "method": method,  # "text" or "ocr"
        "model_used": model,
        "processing_ms": int((time.time() - start) * 1000),
        "analysis_markdown": analysis,
        "text_excerpt": text[:MAX_CHARS],
    }
