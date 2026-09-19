"""Payment endpoint tests."""

import pytest


def _register_and_login(client, email, password="SecurePass1!"):
    client.post("/v1/auth/register", json={
        "email": email, "full_name": "Pay Test", "password": password,
    })
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    return r.json()["access_token"]


def test_create_payment_intent(client):
    token = _register_and_login(client, "payintent@example.com")
    r = client.post(
        "/v1/payments/intents",
        json={"payment_type": "FINE", "amount_ngwee": 50000, "reference_id": "CHALLAN-001"},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "test-idem-001"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["amount_ngwee"] == 50000
    assert data["currency"] == "ZMW"
    assert data["status"] == "PENDING"
    assert data["gateway_redirect_url"] is not None


def test_idempotent_payment_intent(client):
    token = _register_and_login(client, "payidem@example.com")
    payload = {"payment_type": "TOLL", "amount_ngwee": 5000}
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "idem-toll-999"}

    r1 = client.post("/v1/payments/intents", json=payload, headers=headers)
    r2 = client.post("/v1/payments/intents", json=payload, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code in (200, 201)


def test_gateway_webhook_settle(client):
    token = _register_and_login(client, "webhook@example.com")
    intent_r = client.post(
        "/v1/payments/intents",
        json={"payment_type": "LICENCE_FEE", "amount_ngwee": 15000},
        headers={"Authorization": f"Bearer {token}"},
    )
    intent_id = intent_r.json()["id"]

    r = client.post("/v1/payments/webhooks/gateway", json={
        "gateway_transaction_id": "GW-TX-001",
        "intent_id": intent_id,
        "status": "SUCCESS",
        "amount_ngwee": 15000,
        "currency": "ZMW",
        "signature": "sandbox",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "SETTLED"


def test_list_transactions(client):
    token = _register_and_login(client, "txlist@example.com")
    r = client.get("/v1/payments/transactions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "data" in r.json()
    assert "pagination" in r.json()


def test_revenue_summary_requires_staff(client):
    token = _register_and_login(client, "citizen_rev@example.com")
    r = client.get("/v1/revenue/summary", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
