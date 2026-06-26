"""
Pre-computed sample analyses.

The single biggest drop-off in a no-login tool is the visitor who lands without a
PDF in hand. These canned examples let anyone click once and immediately see what
ClariFi produces — turning "looks interesting" into "I get it" before they ever
upload anything. The output format matches a real /api/analyze response so the UI
renders samples through the exact same code path.
"""
from __future__ import annotations

SAMPLES: dict[str, dict] = {
    "growth-tech": {
        "id": "growth-tech",
        "label": "High-growth software company",
        "blurb": "Annual report — fast revenue growth, turning profitable",
        "filename": "NorthwindCloud_FY2025_Annual_Report.pdf",
        "pages": 84,
        "method": "text",
        "model_used": "nvidia/nemotron-3-ultra-550b-a55b",
        "processing_ms": 14200,
        "analysis_markdown": (
            "## Summary\n"
            "Northwind Cloud's FY2025 annual report covers a SaaS business that grew revenue "
            "39% year-over-year to $412M and crossed into operating profitability for the first "
            "time. The headline takeaway: growth is still strong while the company has finally "
            "stopped burning cash.\n\n"
            "## Key Figures\n"
            "- Revenue: $412M (up 39% from $296M)\n"
            "- Gross margin: 78%\n"
            "- Operating income: $11M (vs. -$24M loss last year)\n"
            "- Net retention rate: 117%\n"
            "- Cash & equivalents: $268M\n"
            "- Total debt: $40M (convertible notes)\n"
            "- Free cash flow: $33M positive\n\n"
            "## Risks & Red Flags\n"
            "- Revenue growth is decelerating (was 54% the prior year) — watch the trend.\n"
            "- Customer concentration: top 10 accounts are 22% of revenue.\n"
            "- Stock-based compensation is high at 21% of revenue, diluting shareholders.\n"
            "- No going-concern or liquidity concerns evident.\n\n"
            "## Bottom Line\n"
            "A healthy, scaling SaaS business that just proved it can grow and make money at the "
            "same time; the main thing to monitor is whether growth keeps slowing.\n\n"
            "_This is an automated draft to assist your own review — not financial advice._"
        ),
    },
    "distressed-retail": {
        "id": "distressed-retail",
        "label": "Struggling retailer (red flags)",
        "blurb": "Earnings release — falling sales, rising debt",
        "filename": "ValueMart_Q4_Earnings_Release.pdf",
        "pages": 12,
        "method": "text",
        "model_used": "nvidia/nemotron-3-ultra-550b-a55b",
        "processing_ms": 9800,
        "analysis_markdown": (
            "## Summary\n"
            "ValueMart's Q4 earnings release describes a brick-and-mortar retailer in decline: "
            "same-store sales fell for the sixth straight quarter and the company swung to a net "
            "loss. The headline takeaway is deteriorating fundamentals with mounting balance-sheet "
            "pressure.\n\n"
            "## Key Figures\n"
            "- Revenue: $1.8B (down 9% year-over-year)\n"
            "- Same-store sales: -6.4%\n"
            "- Net loss: -$72M (vs. +$31M profit last year)\n"
            "- Gross margin: 24% (down from 28%)\n"
            "- Total debt: $940M (up from $610M)\n"
            "- Cash: $85M\n"
            "- Inventory: $640M (up 14% while sales fell)\n\n"
            "## Risks & Red Flags\n"
            "- Inventory rising while sales fall — a classic sign of unsold stock and possible "
            "future write-downs.\n"
            "- Debt jumped 54% in one year; interest coverage is thinning.\n"
            "- Margins compressing alongside falling revenue.\n"
            "- Management language references 'evaluating financing options' — watch for dilution "
            "or covenant stress.\n\n"
            "## Bottom Line\n"
            "A business under real financial strain on multiple fronts; the combination of falling "
            "sales, rising debt, and bloated inventory warrants caution.\n\n"
            "_This is an automated draft to assist your own review — not financial advice._"
        ),
    },
    "bank-statement": {
        "id": "bank-statement",
        "label": "Personal bank statement",
        "blurb": "Scanned PDF (OCR) — cash flow at a glance",
        "filename": "Personal_Bank_Statement_March.pdf",
        "pages": 4,
        "method": "ocr",
        "model_used": "meta/llama-3.3-70b-instruct",
        "processing_ms": 18600,
        "analysis_markdown": (
            "## Summary\n"
            "This is a personal checking-account statement for March, read from a scanned PDF via "
            "OCR. The headline takeaway: the account ended the month higher than it started, but "
            "spending is close to income with little buffer.\n\n"
            "## Key Figures\n"
            "- Opening balance: $2,140\n"
            "- Total deposits: $4,820\n"
            "- Total withdrawals: $4,360\n"
            "- Closing balance: $2,600\n"
            "- Largest recurring outflow: $1,450 (rent)\n"
            "- Estimated discretionary spend: ~$900\n\n"
            "## Risks & Red Flags\n"
            "- Net monthly surplus is thin (~$460); an unexpected bill could push the account "
            "negative.\n"
            "- Two overdraft-style fee lines appear (~$35 each) — worth eliminating.\n"
            "- One figure was partly obscured in the scan and may be approximate.\n\n"
            "## Bottom Line\n"
            "Finances are stable but tight; building a small cash cushion and cutting the fees "
            "would meaningfully improve the picture.\n\n"
            "_This is an automated draft to assist your own review — not financial advice._"
        ),
    },
}


def list_samples() -> list[dict]:
    """Lightweight catalogue for the UI's sample buttons (no heavy markdown)."""
    return [
        {"id": s["id"], "label": s["label"], "blurb": s["blurb"]}
        for s in SAMPLES.values()
    ]


def get_sample(sample_id: str) -> dict | None:
    """Return the full canned analysis for a sample id, or None if unknown."""
    return SAMPLES.get(sample_id)
