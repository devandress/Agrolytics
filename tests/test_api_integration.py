"""End-to-end smoke tests hitting real HTTP endpoints (not just service internals)."""

import uuid


async def test_health_check(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_register_and_login(client):
    email = f"{uuid.uuid4()}@example.com"
    password = "correcthorse123"

    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert register.status_code == 201
    assert register.json()["email"] == email

    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


async def test_login_invalid_credentials(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "no-such-user@example.com", "password": "whatever123"},
    )
    assert response.status_code == 401


async def test_protected_endpoint_requires_token(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_protected_endpoint_with_valid_token(client):
    email = f"{uuid.uuid4()}@example.com"
    password = "correcthorse123"
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email
