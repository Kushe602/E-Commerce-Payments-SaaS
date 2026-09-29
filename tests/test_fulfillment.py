"""Order fulfilment lifecycle (paid → shipped → delivered) and refunds. Admin
transitions must follow the sequence; refunds restock every line and mark the
order refunded exactly once (idempotent), recording a provider refund ref. In
the keyless fake-payments mode the ref is ``fake_refund_{order_id}``.
"""
from __future__ import annotations

from tests.helpers import (
    buy_product,
    create_product,
    get_order,
    get_stock,
    make_admin,
    order_id_from_pay_url,
    register,
)


def _admin_buyer(client, email, product_id, quantity=1):
    """Register + promote to admin, then buy — the admin is also the shopper."""
    register(client, email)
    make_admin(email)
    return buy_product(client, product_id, quantity)


def _status(client, order_id, action):
    return client.post(f"/admin/orders/{order_id}/status", data={"action": action})


def test_ship_then_deliver(client):
    product = create_product(stock=5)
    order_id = _admin_buyer(client, "ops1@example.com", product["id"])
    assert get_order(order_id)["status"] == "paid"

    resp = _status(client, order_id, "ship")
    assert resp.status_code == 200
    assert get_order(order_id)["status"] == "shipped"

    _status(client, order_id, "deliver")
    assert get_order(order_id)["status"] == "delivered"


def test_cannot_deliver_before_ship(client):
    product = create_product(stock=5)
    order_id = _admin_buyer(client, "ops2@example.com", product["id"])
    _status(client, order_id, "deliver")
    assert get_order(order_id)["status"] == "paid"  # unchanged


def test_refund_restocks_and_marks_refunded(client):
    product = create_product(stock=5, price_cents=2000)
    order_id = _admin_buyer(client, "ops3@example.com", product["id"], 2)
    assert get_stock(product["id"]) == 3  # decremented on fulfilment

    _status(client, order_id, "refund")
    order = get_order(order_id)
    assert order["status"] == "refunded"
    assert order["refund_ref"] == f"fake_refund_{order_id}"
    assert get_stock(product["id"]) == 5  # restocked


def test_refund_is_idempotent(client):
    product = create_product(stock=5, price_cents=2000)
    order_id = _admin_buyer(client, "ops4@example.com", product["id"], 2)
    _status(client, order_id, "refund")
    _status(client, order_id, "refund")
    assert get_order(order_id)["status"] == "refunded"
    assert get_stock(product["id"]) == 5  # not restocked twice


def test_can_refund_shipped_order(client):
    product = create_product(stock=5, price_cents=2000)
    order_id = _admin_buyer(client, "ops5@example.com", product["id"], 2)
    _status(client, order_id, "ship")
    _status(client, order_id, "refund")
    order = get_order(order_id)
    assert order["status"] == "refunded"
    assert get_stock(product["id"]) == 5


def test_cannot_refund_unpaid_order(client):
    product = create_product(stock=5)
    register(client, "ops6@example.com")
    make_admin("ops6@example.com")
    client.post("/cart/add", data={"product_id": product["id"], "quantity": 1})
    resp = client.post("/checkout")
    order_id = order_id_from_pay_url(resp)  # pending, never paid

    _status(client, order_id, "refund")
    assert get_order(order_id)["status"] == "pending"
    assert get_stock(product["id"]) == 5
