"""Health probe used by Render / compose healthchecks."""
from __future__ import annotations


def test_healthz_reports_ok_and_fake_mode(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # The suite pins the deterministic fake provider — no Stripe keys required.
    assert body["payments_mode"] == "fake"


def test_storefront_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Cartify" in resp.text
