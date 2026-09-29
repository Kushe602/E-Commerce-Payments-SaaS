"""Authentication: registration validation, login, logout, and route protection."""
from __future__ import annotations

from tests.helpers import register


def test_register_then_logout_then_login(client):
    resp = register(client, "alice@example.com", "password123")
    assert resp.status_code == 200  # followed 303 -> "/"

    # Authenticated: the orders page is reachable (would redirect otherwise).
    assert client.get("/orders").status_code == 200

    client.post("/logout")
    # Cookie cleared: a protected route now redirects to the login page.
    resp = client.get("/orders", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    resp = client.post(
        "/login", data={"email": "alice@example.com", "password": "password123"}
    )
    assert resp.status_code == 200
    assert client.get("/orders").status_code == 200


def test_register_rejects_short_password(client):
    resp = register(client, "bob@example.com", "short")
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.text


def test_register_rejects_invalid_email(client):
    resp = register(client, "not-an-email", "password123")
    assert resp.status_code == 400


def test_register_rejects_duplicate_email(client):
    register(client, "carol@example.com", "password123")
    client.post("/logout")
    resp = register(client, "carol@example.com", "password123")
    assert resp.status_code == 400
    assert "already exists" in resp.text


def test_login_wrong_password_is_rejected(client):
    register(client, "dave@example.com", "password123")
    client.post("/logout")
    resp = client.post(
        "/login", data={"email": "dave@example.com", "password": "wrongpass1"}
    )
    assert resp.status_code == 400
    assert "Invalid email or password" in resp.text


def test_anonymous_cannot_reach_cart(client):
    resp = client.get("/cart", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
