import pytest
from products.financial-doc-analyzer.backend.pipeline import process_document

def test_process_document():
    pdf_path = 'path/to/test/pdf.pdf'
    output = process_document(pdf_path)
    assert 'summary' in output
    assert 'key_ratios' in output
    assert 'risk_flags' in output
    assert 'verdict' in output
    assert 'model_used' in output
    assert 'processing_ms' in output