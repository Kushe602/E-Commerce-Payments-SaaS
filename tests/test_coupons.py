"""Discount coupons applied at checkout. Percentage or fixed-cents off, with
optional expiry and usage cap. All money stays in integer cents; a fixed
discount is clamped to the subtotal so totals never go negative. An invalid
code aborts checkout before any order is created.
"""
from __future__ import annotations

from datetime import datetime

from tests.helpers import (
    count_orders,
    create_coupon,
    create_product,
    get_coupon,
    get_order,
    order_id_from_pay_url,
    register,
)


def _add_to_cart(client, product_id, quantity=1):
    client.post("/cart/add", data={"product_id": product_id, "quantity": quantity})


def test_percentage_coupon_reduces_total(client):
    product = create_product(price_cents=1000, stock=5)
    create_coupon("PCT10", kind="percent", value=10)
    register(client, "coup1@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "PCT10"})
    order = get_order(order_id_from_pay_url(resp))
    assert order["discount_cents"] == 100
    assert order["total_cents"] == 900
    assert order["coupon_code"] == "PCT10"
    assert get_coupon("PCT10")["used_count"] == 1


def test_fixed_coupon_reduces_total(client):
    product = create_product(price_cents=1000, stock=5)
    create_coupon("FIX5", kind="fixed", value=500)
    register(client, "coup2@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "FIX5"})
    order = get_order(order_id_from_pay_url(resp))
    assert order["discount_cents"] == 500
    assert order["total_cents"] == 500


def test_fixed_coupon_is_clamped_to_subtotal(client):
    product = create_product(price_cents=1000, stock=5)
    create_coupon("HUGE", kind="fixed", value=999999)
    register(client, "coup3@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "HUGE"})
    order = get_order(order_id_from_pay_url(resp))
    assert order["discount_cents"] == 1000
    assert order["total_cents"] == 0


def test_lowercase_code_is_normalized(client):
    product = create_product(price_cents=1000, stock=5)
    create_coupon("HALF", kind="percent", value=50)
    register(client, "coup4@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "half"})
    order = get_order(order_id_from_pay_url(resp))
    assert order["total_cents"] == 500


def test_invalid_coupon_bounces_and_creates_no_order(client):
    product = create_product(price_cents=1000, stock=5)
    register(client, "coup5@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "NOPE"})
    assert resp.url.path == "/cart"
    assert "not placed" in resp.text
    assert count_orders() == 0


def test_expired_coupon_rejected(client):
    product = create_product(price_cents=1000, stock=5)
    create_coupon("OLD", kind="percent", value=10, expires_at=datetime(2000, 1, 1))
    register(client, "coup6@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "OLD"})
    assert resp.url.path == "/cart"
    assert count_orders() == 0
    assert get_coupon("OLD")["used_count"] == 0


def test_deactivated_coupon_rejected(client):
    product = create_product(price_cents=1000, stock=5)
    create_coupon("OFF", kind="percent", value=10, is_active=False)
    register(client, "coup7@example.com")
    _add_to_cart(client, product["id"])
    resp = client.post("/checkout", data={"coupon_code": "OFF"})
    assert resp.url.path == "/cart"
    assert count_orders() == 0


def test_usage_cap_is_enforced(client):
    product = create_product(price_cents=1000, stock=10)
    create_coupon("ONCE", kind="percent", value=10, max_uses=1)
    register(client, "coup8@example.com")

    _add_to_cart(client, product["id"])
    first = client.post("/checkout", data={"coupon_code": "ONCE"})
    assert "/checkout/pay/" in first.url.path
    assert get_coupon("ONCE")["used_count"] == 1

    _add_to_cart(client, product["id"])
    second = client.post("/checkout", data={"coupon_code": "ONCE"})
    assert second.url.path == "/cart"
    assert count_orders() == 1
