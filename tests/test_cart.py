"""Cart: pure money math plus the add/update/remove endpoints (with stock clamp)."""
from __future__ import annotations

from app.services import cart_total_cents, line_total_cents
from tests.helpers import cart_items, create_product, register

# --- Pure math (no I/O) ------------------------------------------------------

def test_line_total_cents():
    assert line_total_cents(1999, 3) == 5997


def test_cart_total_cents_sums_pairs():
    assert cart_total_cents([(1000, 2), (250, 4)]) == 3000


def test_cart_total_cents_empty_is_zero():
    assert cart_total_cents([]) == 0


# --- Endpoints ---------------------------------------------------------------

def test_add_to_cart_reflects_quantity_and_total(client):
    product = create_product(stock=10, price_cents=500)
    register(client, "cartuser@example.com")
    resp = client.post(
        "/cart/add", data={"product_id": product["id"], "quantity": 3}
    )
    assert resp.status_code == 200  # non-HTMX add follows redirect to /cart
    body = client.get("/cart").text
    assert "$15.00" in body  # 3 x $5.00


def test_add_to_cart_is_clamped_to_stock(client):
    product = create_product(stock=2, price_cents=500)
    register(client, "clamp@example.com")
    client.post("/cart/add", data={"product_id": product["id"], "quantity": 5})
    items = cart_items("clamp@example.com")
    assert len(items) == 1
    assert items[0]["quantity"] == 2  # never more than stock


def test_update_and_remove_cart_item(client):
    product = create_product(stock=10, price_cents=1000)
    register(client, "editor@example.com")
    client.post("/cart/add", data={"product_id": product["id"], "quantity": 2})
    item_id = cart_items("editor@example.com")[0]["id"]

    client.post("/cart/update", data={"item_id": item_id, "quantity": 5})
    assert cart_items("editor@example.com")[0]["quantity"] == 5

    client.post("/cart/remove", data={"item_id": item_id})
    assert cart_items("editor@example.com") == []


def test_add_htmx_returns_badge_partial(client):
    product = create_product(stock=10, price_cents=1000)
    register(client, "htmx@example.com")
    resp = client.post(
        "/cart/add",
        data={"product_id": product["id"], "quantity": 1},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert 'id="cart-badge"' in resp.text
