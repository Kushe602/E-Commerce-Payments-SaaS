"""Product reviews + star ratings. Only shoppers who *purchased* a product (a
fulfilled order) may review it; ratings are 1–5. Reviews are one-per-user and
editing overwrites the prior one.
"""
from __future__ import annotations

from tests.helpers import buy_product, create_product, product_reviews, register


def test_purchaser_can_review_and_it_shows(client):
    product = create_product(name="Reviewable Gadget", price_cents=1000, stock=5)
    register(client, "rev1@example.com")
    buy_product(client, product["id"], 1)
    resp = client.post(
        f"/products/{product['slug']}/reviews",
        data={"rating": 5, "body": "Absolutely stellar."},
    )
    assert resp.status_code == 200  # followed 303 -> product page
    assert resp.url.path == f"/products/{product['slug']}"
    assert "Absolutely stellar." in resp.text
    reviews = product_reviews(product["id"])
    assert len(reviews) == 1
    assert reviews[0]["rating"] == 5


def test_review_average_is_rendered(client):
    product = create_product(name="Rated Gadget", stock=5)
    register(client, "rev2@example.com")
    buy_product(client, product["id"], 1)
    client.post(f"/products/{product['slug']}/reviews", data={"rating": 4, "body": "Good"})
    resp = client.get(f"/products/{product['slug']}")
    assert "4.0" in resp.text  # average rating rendered


def test_non_purchaser_cannot_review(client):
    product = create_product(name="Locked Gadget", stock=5)
    register(client, "rev3@example.com")  # never buys
    resp = client.post(
        f"/products/{product['slug']}/reviews",
        data={"rating": 5, "body": "Sneaky"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    assert product_reviews(product["id"]) == []


def test_review_requires_login(client):
    product = create_product(stock=5)
    resp = client.post(
        f"/products/{product['slug']}/reviews",
        data={"rating": 5, "body": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_rating_out_of_range_rejected(client):
    product = create_product(name="Range Gadget", stock=5)
    register(client, "rev4@example.com")
    buy_product(client, product["id"], 1)
    resp = client.post(
        f"/products/{product['slug']}/reviews",
        data={"rating": 9, "body": "too many stars"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    assert product_reviews(product["id"]) == []


def test_second_review_overwrites_first(client):
    product = create_product(name="Update Gadget", stock=5)
    register(client, "rev5@example.com")
    buy_product(client, product["id"], 1)
    client.post(f"/products/{product['slug']}/reviews", data={"rating": 4, "body": "First"})
    client.post(
        f"/products/{product['slug']}/reviews",
        data={"rating": 2, "body": "Changed my mind"},
    )
    reviews = product_reviews(product["id"])
    assert len(reviews) == 1
    assert reviews[0]["rating"] == 2
    assert reviews[0]["body"] == "Changed my mind"
