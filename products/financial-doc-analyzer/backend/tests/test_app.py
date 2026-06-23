"""Smoke tests for the ClariFi MVP (no external services required)."""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "financial-doc-analyzer"}


def test_index_is_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "ClariFi" in r.text


def test_analyze_rejects_non_pdf():
    # A non-PDF must be rejected before any model call.
    r = client.post("/api/analyze", files={"file": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_waitlist_validates_email():
    r = client.post("/api/waitlist", json={"email": "not-an-email"})
    assert r.status_code == 422
