"""Smoke test for the health endpoint (FDA-001 acceptance criterion 5)."""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_returns_ok():
    """GET /health returns 200 with the expected service payload."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "financial-doc-analyzer"}
