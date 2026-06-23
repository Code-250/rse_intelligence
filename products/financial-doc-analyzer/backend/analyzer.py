"""
Financial document analysis.

Extracts text from an uploaded PDF (pdfplumber) and asks an NVIDIA NIM model to
produce a concise, plain-English financial analysis. Free NIM models keep cost
near zero while we validate demand.

Public API:
    extract_text(pdf_bytes) -> (text, page_count)
    analyze_pdf_bytes(pdf_bytes, filename) -> dict
"""
from __future__ import annotations

import io
import logging
import os
import time

import requests

try:
    import pdfplumber
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    pdfplumber = None

logger = logging.getLogger(__name__)

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")

# Models tried in order on 404 / tool-unsupported. Override with FDA_ANALYSIS_MODEL.
ANALYSIS_MODELS = [
    m.strip() for m in os.getenv(
        "FDA_ANALYSIS_MODEL",
        "nvidia/nemotron-3-ultra-550b-a55b,meta/llama-3.3-70b-instruct,meta/llama-3.1-70b-instruct",
    ).split(",") if m.strip()
]

# Cap how much document text we send (controls latency + cost). Most reports fit.
MAX_CHARS = int(os.getenv("FDA_MAX_ANALYSIS_CHARS", "24000"))

SYSTEM_PROMPT = (
    "You are a senior financial analyst. You read a financial document (annual report, "
    "earnings release, financial statement, or similar) and produce a clear, plain-English "
    "analysis for a non-expert investor. Be accurate and concise. Never invent figures that "
    "are not in the document. Write in markdown with EXACTLY these sections:\n\n"
    "## Summary\n(2-4 sentences on what this document is and the headline takeaway)\n\n"
    "## Key Figures\n(bullet list of the most important numbers you found: revenue, profit/loss, "
    "margins, cash, debt, growth — with the figure. Write 'Not stated' if a key figure is absent.)\n\n"
    "## Risks & Red Flags\n(bullet list of concerns: declining metrics, high debt, going-concern "
    "language, unusual items. If none are evident, say so.)\n\n"
    "## Bottom Line\n(1-2 sentence verdict)\n\n"
    "End with this exact line: '_This is an automated draft to assist your own review — not "
    "financial advice._'"
)


class AnalysisError(Exception):
    """Raised when a document cannot be analysed."""


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Extract concatenated text and page count from a PDF.

    Raises AnalysisError if the PDF can't be read or has no extractable text
    (e.g. a scanned image with no OCR layer).
    """
    if pdfplumber is None:
        raise AnalysisError("PDF engine unavailable on the server.")
    try:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
    except Exception as e:  # noqa: BLE001 - want a clean user-facing error
        logger.error("PDF parse failed: %s", e)
        raise AnalysisError("Could not read this PDF. It may be corrupted or password-protected.")

    text = "\n\n".join(p for p in parts if p.strip())
    if len(text.strip()) < 40:
        raise AnalysisError(
            "No readable text found. This looks like a scanned image — try a text-based PDF."
        )
    return text, page_count


def _call_nim(text: str) -> tuple[str, str]:
    """Call NIM, walking the model fallback chain. Returns (analysis, model_used)."""
    if not NIM_API_KEY:
        raise AnalysisError("Analysis is not configured (missing NIM API key). Please try later.")

    headers = {"Authorization": f"Bearer {NIM_API_KEY}", "Content-Type": "application/json"}
    user_content = (
        "Analyse the following financial document and respond using the required sections.\n\n"
        f"=== DOCUMENT START ===\n{text[:MAX_CHARS]}\n=== DOCUMENT END ==="
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    last_err = "no model responded"
    for model in ANALYSIS_MODELS:
        payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1200}
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


def analyze_pdf_bytes(pdf_bytes: bytes, filename: str = "document.pdf") -> dict:
    """Full pipeline: extract text → analyse → structured result.

    Returns {filename, pages, model_used, processing_ms, analysis_markdown}.
    Raises AnalysisError with a user-friendly message on failure.
    """
    start = time.time()
    text, pages = extract_text(pdf_bytes)
    analysis, model = _call_nim(text)
    return {
        "filename": filename,
        "pages": pages,
        "model_used": model,
        "processing_ms": int((time.time() - start) * 1000),
        "analysis_markdown": analysis,
    }
