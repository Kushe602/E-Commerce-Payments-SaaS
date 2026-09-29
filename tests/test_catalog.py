"""Catalog UX: sort options (name / newest / price) and a price-range filter on
the storefront listing. Anonymous browsing — no auth needed.
"""
from __future__ import annotations

from tests.helpers import create_product


def test_sort_price_low_to_high_orders_products(client):
    create_product(name="Cheap Thing", price_cents=500)
    create_product(name="Pricey Thing", price_cents=5000)
    resp = client.get("/?sort=price_asc")
    assert resp.status_code == 200
    assert resp.text.index("Cheap Thing") < resp.text.index("Pricey Thing")


def test_sort_price_high_to_low_orders_products(client):
    create_product(name="Cheap Thing", price_cents=500)
    create_product(name="Pricey Thing", price_cents=5000)
    resp = client.get("/?sort=price_desc")
    assert resp.text.index("Pricey Thing") < resp.text.index("Cheap Thing")


def test_price_range_filters_out_of_range_products(client):
    create_product(name="Budget Item", price_cents=500)     # $5
    create_product(name="Midrange Item", price_cents=1500)  # $15
    create_product(name="Premium Item", price_cents=2500)   # $25
    resp = client.get("/?min_price=10&max_price=20")
    assert "Midrange Item" in resp.text
    assert "Budget Item" not in resp.text
    assert "Premium Item" not in resp.text


def test_min_price_only(client):
    create_product(name="Budget Item", price_cents=500)
    create_product(name="Premium Item", price_cents=2500)
    resp = client.get("/?min_price=10")
    assert "Premium Item" in resp.text
    assert "Budget Item" not in resp.text


def test_max_price_only(client):
    create_product(name="Budget Item", price_cents=500)
    create_product(name="Premium Item", price_cents=2500)
    resp = client.get("/?max_price=10")
    assert "Budget Item" in resp.text
    assert "Premium Item" not in resp.text


def test_invalid_sort_falls_back_to_name(client):
    create_product(name="Alpha Product", price_cents=100)
    resp = client.get("/?sort=not-a-real-sort")
    assert resp.status_code == 200
    assert "Alpha Product" in resp.text


def test_invalid_price_input_is_ignored(client):
    create_product(name="Still Here", price_cents=1000)
    resp = client.get("/?min_price=abc&max_price=xyz")
    assert resp.status_code == 200
    assert "Still Here" in resp.text
