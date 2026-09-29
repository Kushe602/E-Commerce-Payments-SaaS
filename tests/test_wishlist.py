"""Wishlist: signed-in shoppers save/remove products and view them on a
dedicated page. Adds are idempotent (one row per product), and the ``next``
redirect target is validated so it can't be used as an open redirect.
"""
from __future__ import annotations

from tests.helpers import create_product, register, wishlist_product_ids


def test_wishlist_requires_login(client):
    resp = client.get("/wishlist", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_add_and_view(client):
    product = create_product(name="Wishable Widget")
    register(client, "wisher1@example.com")
    resp = client.post("/wishlist/add", data={"product_id": product["id"], "next": "/wishlist"})
    assert resp.status_code == 200  # followed 303 -> /wishlist
    assert resp.url.path == "/wishlist"
    assert "Wishable Widget" in resp.text
    assert wishlist_product_ids("wisher1@example.com") == [product["id"]]


def test_add_is_idempotent(client):
    product = create_product()
    register(client, "wisher2@example.com")
    client.post("/wishlist/add", data={"product_id": product["id"]})
    client.post("/wishlist/add", data={"product_id": product["id"]})
    assert wishlist_product_ids("wisher2@example.com") == [product["id"]]


def test_remove(client):
    product = create_product()
    register(client, "wisher3@example.com")
    client.post("/wishlist/add", data={"product_id": product["id"]})
    client.post("/wishlist/remove", data={"product_id": product["id"]})
    assert wishlist_product_ids("wisher3@example.com") == []


def test_next_rejects_open_redirect(client):
    product = create_product()
    register(client, "wisher4@example.com")
    resp = client.post(
        "/wishlist/add",
        data={"product_id": product["id"], "next": "//evil.example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/wishlist"
