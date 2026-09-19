"""Auth endpoint tests — registration, login, MFA, RBAC."""

import pytest


def test_register_new_citizen(client):
    r = client.post("/v1/auth/register", json={
        "email": "testcitizen@example.com",
        "full_name": "Test Citizen",
        "password": "SecurePass1!",
        "nrc_number": "987654/10/1",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["email"] == "testcitizen@example.com"
    assert data["role"] == "CITIZEN"
    assert "hashed_password" not in data


def test_register_duplicate_email(client):
    payload = {"email": "dup@example.com", "full_name": "Dup User", "password": "SecurePass1!"}
    client.post("/v1/auth/register", json=payload)
    r = client.post("/v1/auth/register", json=payload)
    assert r.status_code == 409


def test_login_success(client):
    client.post("/v1/auth/register", json={
        "email": "logintest@example.com",
        "full_name": "Login Test",
        "password": "SecurePass1!",
    })
    r = client.post("/v1/auth/login", json={
        "email": "logintest@example.com",
        "password": "SecurePass1!",
    })
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client):
    client.post("/v1/auth/register", json={
        "email": "badpass@example.com",
        "full_name": "Bad Pass",
        "password": "SecurePass1!",
    })
    r = client.post("/v1/auth/login", json={
        "email": "badpass@example.com",
        "password": "WrongPassword!",
    })
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"


def test_login_nonexistent_user(client):
    r = client.post("/v1/auth/login", json={
        "email": "nobody@example.com",
        "password": "SomePass123!",
    })
    assert r.status_code == 401


def test_protected_endpoint_no_token(client):
    r = client.get("/v1/citizens/me")
    assert r.status_code == 401


def test_protected_endpoint_with_token(client):
    client.post("/v1/auth/register", json={
        "email": "tokentest@example.com",
        "full_name": "Token Test",
        "password": "SecurePass1!",
    })
    login = client.post("/v1/auth/login", json={
        "email": "tokentest@example.com",
        "password": "SecurePass1!",
    })
    token = login.json()["access_token"]
    r = client.get("/v1/citizens/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "tokentest@example.com"


def test_refresh_token(client):
    client.post("/v1/auth/register", json={
        "email": "refreshtest@example.com",
        "full_name": "Refresh Test",
        "password": "SecurePass1!",
    })
    login = client.post("/v1/auth/login", json={
        "email": "refreshtest@example.com",
        "password": "SecurePass1!",
    })
    refresh_token = login.json()["refresh_token"]
    access_token = login.json()["access_token"]

    r = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token},
                    headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_logout(client):
    client.post("/v1/auth/register", json={
        "email": "logouttest@example.com",
        "full_name": "Logout Test",
        "password": "SecurePass1!",
    })
    login = client.post("/v1/auth/login", json={
        "email": "logouttest@example.com",
        "password": "SecurePass1!",
    })
    tokens = login.json()
    r = client.post(
        "/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 200


def test_password_reset_request(client):
    client.post("/v1/auth/register", json={
        "email": "resetreq@example.com",
        "full_name": "Reset Req",
        "password": "SecurePass1!",
    })
    r = client.post("/v1/auth/password/reset-request", json={"email": "resetreq@example.com"})
    assert r.status_code == 200  # always 200 to prevent enumeration


def test_password_reset_nonexistent_email(client):
    r = client.post("/v1/auth/password/reset-request", json={"email": "ghost@example.com"})
    assert r.status_code == 200  # still 200
