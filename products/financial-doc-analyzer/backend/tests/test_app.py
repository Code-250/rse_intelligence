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


def test_samples_catalogue_lists_demos():
    r = client.get("/api/samples")
    assert r.status_code == 200
    samples = r.json()["samples"]
    assert len(samples) >= 1
    assert {"id", "label", "blurb"} <= set(samples[0].keys())


def test_sample_detail_returns_analysis():
    r = client.get("/api/samples/growth-tech")
    assert r.status_code == 200
    body = r.json()
    assert "## Summary" in body["analysis_markdown"]
    assert body["filename"]


def test_unknown_sample_404s():
    r = client.get("/api/samples/does-not-exist")
    assert r.status_code == 404


def test_index_shows_samples_section():
    r = client.get("/")
    assert "/api/samples" in r.text


def test_ask_requires_question():
    r = client.post("/api/ask", json={"question": "  ", "context": "some doc text"})
    assert r.status_code == 400


def test_ask_rejects_overlong_question():
    r = client.post("/api/ask", json={"question": "x" * 600, "context": "doc"})
    assert r.status_code == 413


def test_robots_txt_allows_crawlers():
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "User-agent: *" in r.text
    assert "Allow: /" in r.text


def test_sitemap_is_valid_xml():
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "urlset" in r.text
    assert r.headers["content-type"].startswith("application/xml")


def test_index_has_structured_data():
    r = client.get("/")
    assert "application/ld+json" in r.text
    assert "WebApplication" in r.text
