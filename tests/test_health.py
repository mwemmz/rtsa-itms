"""Health and root endpoint tests."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_liveness():
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness():
    response = client.get("/v1/health/ready")
    # Will be 503 without a real DB — that's expected in CI without DATABASE_URL
    assert response.status_code in (200, 503)


def test_openapi_has_all_key_paths():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    required = [
        "/v1/auth/login",
        "/v1/auth/register",
        "/v1/citizens/me",
        "/v1/applications",
        "/v1/payments/intents",
        "/v1/payments/transactions",
        "/v1/notifications/send",
        "/v1/admin/users",
        "/v1/admin/settings",
        "/v1/admin/thresholds",
        "/v1/admin/audit-logs",
        "/v1/reports/dashboard",
        "/v1/integrations/police/incidents",
        "/v1/integrations/monitoring/status",
        "/v1/rbac/roles",
    ]
    missing = [p for p in required if p not in paths]
    assert not missing, f"Missing paths in OpenAPI spec: {missing}"
