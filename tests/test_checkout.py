"""Checkout in fake-payments mode: success fulfils and decrements stock, failure
marks the order failed and leaves stock untouched, and the oversell guard refuses
to drive stock negative when inventory disappeared before payment settled.
"""
from __future__ import annotations

from tests.helpers import (
    create_product,
    get_order,
    get_stock,
    order_id_from_pay_url,
    register,
    set_stock,
)


def _start_checkout(client, product_id: int, quantity: int) -> int:
    """Add to cart and POST /checkout; returns the new pending order id."""
    client.post("/cart/add", data={"product_id": product_id, "quantity": quantity})
    resp = client.post("/checkout")  # -> 303 to the fake pay page
    assert "/checkout/pay/" in resp.url.path
    return order_id_from_pay_url(resp)


def test_checkout_empty_cart_redirects_to_cart(client):
    register(client, "empty@example.com")
    resp = client.post("/checkout")
    assert resp.url.path == "/cart"


def test_fake_payment_success_fulfils_and_decrements_stock(client):
    product = create_product(stock=5, price_cents=2500)
    register(client, "buyer@example.com")
    order_id = _start_checkout(client, product["id"], 2)

    # Pending order does not hold inventory yet.
    assert get_order(order_id)["status"] == "pending"
    assert get_stock(product["id"]) == 5

    resp = client.post(f"/checkout/pay/{order_id}", data={"outcome": "success"})
    assert resp.status_code == 200  # followed redirect to the order receipt

    order = get_order(order_id)
    assert order["status"] == "paid"
    assert order["total_cents"] == 5000
    assert get_stock(product["id"]) == 3  # decremented by the purchased quantity


def test_fake_payment_failure_marks_failed_and_keeps_stock(client):
    product = create_product(stock=5, price_cents=2500)
    register(client, "declined@example.com")
    order_id = _start_checkout(client, product["id"], 2)

    client.post(f"/checkout/pay/{order_id}", data={"outcome": "fail"})

    assert get_order(order_id)["status"] == "failed"
    assert get_stock(product["id"]) == 5  # nothing charged, nothing reserved


def test_oversell_guard_fails_order_when_stock_gone(client):
    product = create_product(stock=1, price_cents=1000)
    register(client, "racer@example.com")
    order_id = _start_checkout(client, product["id"], 1)

    # Simulate the last unit selling elsewhere before this payment settles.
    set_stock(product["id"], 0)

    client.post(f"/checkout/pay/{order_id}", data={"outcome": "success"})

    # The guard refuses to oversell: the order fails and stock never goes negative.
    assert get_order(order_id)["status"] == "failed"
    assert get_stock(product["id"]) == 0
