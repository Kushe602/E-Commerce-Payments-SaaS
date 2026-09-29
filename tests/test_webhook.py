"""Payment webhook: a ``checkout.session.completed`` event fulfils the referenced
order (paid + stock decrement) and is idempotent across re-deliveries.
"""
from __future__ import annotations

from tests.helpers import (
    completed_event,
    create_product,
    get_order,
    get_stock,
    order_id_from_pay_url,
    register,
)


def _pending_order(client, product_id: int, quantity: int) -> int:
    client.post("/cart/add", data={"product_id": product_id, "quantity": quantity})
    resp = client.post("/checkout")
    return order_id_from_pay_url(resp)


def test_webhook_completed_fulfils_and_decrements(client):
    product = create_product(stock=4, price_cents=1500)
    register(client, "hooked@example.com")
    order_id = _pending_order(client, product["id"], 3)

    assert get_order(order_id)["status"] == "pending"
    assert get_stock(product["id"]) == 4

    resp = client.post("/webhooks/stripe", json=completed_event(order_id))
    assert resp.status_code == 200
    assert resp.json() == {"received": True}

    assert get_order(order_id)["status"] == "paid"
    assert get_stock(product["id"]) == 1


def test_webhook_is_idempotent(client):
    product = create_product(stock=4, price_cents=1500)
    register(client, "retry@example.com")
    order_id = _pending_order(client, product["id"], 2)

    first = client.post("/webhooks/stripe", json=completed_event(order_id))
    second = client.post("/webhooks/stripe", json=completed_event(order_id))
    assert first.status_code == 200
    assert second.status_code == 200

    # Re-delivery must not decrement stock twice.
    assert get_order(order_id)["status"] == "paid"
    assert get_stock(product["id"]) == 2


def test_webhook_ignores_unrelated_event_type(client):
    resp = client.post(
        "/webhooks/stripe",
        json={"type": "payment_intent.created", "data": {"object": {}}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"received": True}


def test_webhook_unknown_order_is_noop(client):
    resp = client.post("/webhooks/stripe", json=completed_event(999999))
    assert resp.status_code == 200
    assert resp.json() == {"received": True}
