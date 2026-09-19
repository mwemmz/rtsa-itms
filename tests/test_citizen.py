"""Citizen portal and application tests."""


def _register_and_login(client, email, password="SecurePass1!"):
    client.post("/v1/auth/register", json={
        "email": email, "full_name": "Citizen Test", "password": password,
    })
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    return r.json()["access_token"]


def test_get_profile(client):
    token = _register_and_login(client, "profile@example.com")
    r = client.get("/v1/citizens/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "profile@example.com"


def test_update_profile(client):
    token = _register_and_login(client, "update_prof@example.com")
    r = client.put("/v1/citizens/me", json={"full_name": "Updated Name", "phone": "+260971000001"},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Updated Name"


def test_notification_preferences(client):
    token = _register_and_login(client, "notifpref@example.com")
    r = client.put("/v1/citizens/me/notification-preferences",
                   json={"notify_sms": False, "notify_email": True},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["notify_sms"] is False
    assert r.json()["notify_email"] is True


def test_submit_application(client):
    token = _register_and_login(client, "appsubmit@example.com")
    r = client.post("/v1/applications", json={
        "application_type": "LICENCE_RENEWAL",
        "details": {"current_licence": "DL-001234", "reason": "Expiry"},
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "SUBMITTED"
    assert data["application_type"] == "LICENCE_RENEWAL"
    assert data["reference_number"].startswith("APP-")


def test_list_applications(client):
    token = _register_and_login(client, "applist@example.com")
    client.post("/v1/applications", json={"application_type": "INSPECTION_BOOKING", "details": {}},
                headers={"Authorization": f"Bearer {token}"})
    r = client.get("/v1/applications", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1


def test_get_application_detail(client):
    token = _register_and_login(client, "appdetail@example.com")
    create_r = client.post("/v1/applications",
                           json={"application_type": "VEHICLE_TRANSFER", "details": {}},
                           headers={"Authorization": f"Bearer {token}"})
    app_id = create_r.json()["id"]

    r = client.get(f"/v1/applications/{app_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["id"] == app_id
    assert "status_history" in r.json()


def test_application_status_endpoint(client):
    token = _register_and_login(client, "appstatus@example.com")
    create_r = client.post("/v1/applications",
                           json={"application_type": "PSV_PERMIT", "details": {}},
                           headers={"Authorization": f"Bearer {token}"})
    app_id = create_r.json()["id"]

    r = client.get(f"/v1/applications/{app_id}/status",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["status"] == "SUBMITTED"


def test_cancel_application(client):
    token = _register_and_login(client, "appcancel@example.com")
    create_r = client.post("/v1/applications",
                           json={"application_type": "LICENCE_RENEWAL", "details": {}},
                           headers={"Authorization": f"Bearer {token}"})
    app_id = create_r.json()["id"]

    r = client.post(f"/v1/applications/{app_id}/cancel",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_cancel_already_cancelled(client):
    token = _register_and_login(client, "appcancel2@example.com")
    create_r = client.post("/v1/applications",
                           json={"application_type": "LICENCE_RENEWAL", "details": {}},
                           headers={"Authorization": f"Bearer {token}"})
    app_id = create_r.json()["id"]
    client.post(f"/v1/applications/{app_id}/cancel", headers={"Authorization": f"Bearer {token}"})
    r = client.post(f"/v1/applications/{app_id}/cancel", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422


def test_cannot_see_another_users_application(client):
    token1 = _register_and_login(client, "app_user1@example.com")
    token2 = _register_and_login(client, "app_user2@example.com")
    create_r = client.post("/v1/applications",
                           json={"application_type": "LICENCE_RENEWAL", "details": {}},
                           headers={"Authorization": f"Bearer {token1}"})
    app_id = create_r.json()["id"]

    r = client.get(f"/v1/applications/{app_id}", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 404
