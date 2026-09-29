"""Admin authorization and management flows.

Authorization is the headline: anonymous visitors are redirected to login and
authenticated non-admins get a 403. Admins can manage the catalog and orders,
and deleting a product preserves past receipts (order-item snapshots survive).
"""
from __future__ import annotations

from tests.helpers import (
    completed_event,
    create_product,
    get_order,
    get_order_items,
    make_admin,
    order_id_from_pay_url,
    register,
)


def test_anonymous_redirected_from_admin(client):
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_non_admin_forbidden(client):
    register(client, "plainuser@example.com")
    resp = client.get("/admin")
    assert resp.status_code == 403


def test_non_admin_cannot_create_product(client):
    register(client, "sneaky@example.com")
    resp = client.post(
        "/admin/products",
        data={"name": "Contraband", "price": "9.99", "stock": "3", "is_active": "true"},
    )
    assert resp.status_code == 403


def test_admin_dashboard_and_product_list(client):
    register(client, "boss@example.com")
    make_admin("boss@example.com")
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/products").status_code == 200
    assert client.get("/admin/orders").status_code == 200


def test_admin_can_create_product(client):
    register(client, "boss@example.com")
    make_admin("boss@example.com")
    resp = client.post(
        "/admin/products",
        data={
            "name": "Admin Special",
            "price": "12.34",
            "stock": "7",
            "description": "Made in the admin panel.",
            "is_active": "true",
        },
    )
    assert resp.status_code == 200  # followed 303 -> /admin/products
    assert resp.url.path == "/admin/products"
    assert "Admin Special" in resp.text


def test_admin_rejects_negative_price(client):
    register(client, "boss@example.com")
    make_admin("boss@example.com")
    resp = client.post(
        "/admin/products",
        data={"name": "Bad Price", "price": "-5.00", "stock": "1", "is_active": "true"},
    )
    assert resp.status_code == 400


def test_admin_can_fulfil_pending_order(client):
    product = create_product(stock=3, price_cents=1000)
    register(client, "boss@example.com")
    make_admin("boss@example.com")
    # The admin is also a shopper here: build a pending order.
    client.post("/cart/add", data={"product_id": product["id"], "quantity": 1})
    resp = client.post("/checkout")
    order_id = order_id_from_pay_url(resp)

    resp = client.post(f"/admin/orders/{order_id}/status", data={"action": "fulfill"})
    assert resp.status_code == 200
    assert get_order(order_id)["status"] == "paid"


def test_deleting_product_preserves_order_history(client):
    product = create_product(stock=5, price_cents=2000)
    register(client, "boss@example.com")
    make_admin("boss@example.com")

    client.post("/cart/add", data={"product_id": product["id"], "quantity": 2})
    resp = client.post("/checkout")
    order_id = order_id_from_pay_url(resp)
    client.post("/webhooks/stripe", json=completed_event(order_id))
    assert get_order(order_id)["status"] == "paid"

    # Delete the product from the catalog.
    resp = client.post(f"/admin/products/{product['id']}/delete")
    assert resp.status_code == 200

    # The receipt survives: the line item keeps its name/price snapshot, and the
    # product reference is nulled rather than cascading a delete onto the order.
    items = get_order_items(order_id)
    assert len(items) == 1
    assert items[0]["product_id"] is None
    assert items[0]["product_name"] == "Test Widget"
    assert items[0]["unit_price_cents"] == 2000

    # The customer-facing receipt still renders.
    assert client.get(f"/orders/{order_id}").status_code == 200
