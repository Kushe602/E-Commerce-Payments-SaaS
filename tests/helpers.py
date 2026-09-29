"""Test helpers: seed rows and read state through a synchronous engine, plus a
couple of API shortcuts. Direct DB access keeps the arrange/assert steps of a
test independent of the very endpoints under test.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import (
    Cart,
    CartItem,
    Coupon,
    Order,
    OrderItem,
    Product,
    Review,
    User,
    WishlistItem,
)

SYNC_DB_URL = "sqlite:///./test_cartify.db"


@contextmanager
def _session():
    engine = create_engine(SYNC_DB_URL)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


# --- Seeding -----------------------------------------------------------------

def create_product(
    name: str = "Test Widget",
    slug: str | None = None,
    price_cents: int = 1000,
    stock: int = 10,
    is_active: bool = True,
    description: str = "A product used in tests.",
) -> dict:
    slug = slug or name.lower().replace(" ", "-")
    with _session() as s:
        product = Product(
            name=name,
            slug=slug,
            description=description,
            price_cents=price_cents,
            stock=stock,
            is_active=is_active,
        )
        s.add(product)
        s.commit()
        s.refresh(product)
        return {"id": product.id, "slug": product.slug, "price_cents": product.price_cents}


def make_admin(email: str) -> None:
    with _session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is not None:
            user.is_admin = True
            s.commit()


def set_stock(product_id: int, stock: int) -> None:
    with _session() as s:
        product = s.get(Product, product_id)
        product.stock = stock
        s.commit()


def create_coupon(
    code: str,
    kind: str = "percent",
    value: int = 10,
    expires_at: datetime | None = None,
    max_uses: int | None = None,
    used_count: int = 0,
    is_active: bool = True,
) -> dict:
    """Insert a coupon directly. ``value`` is whole percent or integer cents."""
    with _session() as s:
        coupon = Coupon(
            code=code.upper(),
            kind=kind,
            value=value,
            expires_at=expires_at,
            max_uses=max_uses,
            used_count=used_count,
            is_active=is_active,
        )
        s.add(coupon)
        s.commit()
        s.refresh(coupon)
        return {"id": coupon.id, "code": coupon.code}


# --- Reading state -----------------------------------------------------------


def get_coupon(code: str) -> dict | None:
    with _session() as s:
        coupon = s.scalar(select(Coupon).where(Coupon.code == code.upper()))
        if coupon is None:
            return None
        return {
            "id": coupon.id,
            "code": coupon.code,
            "kind": coupon.kind,
            "value": coupon.value,
            "used_count": coupon.used_count,
            "max_uses": coupon.max_uses,
            "is_active": coupon.is_active,
        }


def product_reviews(product_id: int) -> list[dict]:
    with _session() as s:
        reviews = s.scalars(
            select(Review).where(Review.product_id == product_id).order_by(Review.id)
        ).all()
        return [{"user_id": r.user_id, "rating": r.rating, "body": r.body} for r in reviews]


def wishlist_product_ids(email: str) -> list[int]:
    with _session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            return []
        rows = s.scalars(
            select(WishlistItem.product_id)
            .where(WishlistItem.user_id == user.id)
            .order_by(WishlistItem.id)
        ).all()
        return list(rows)

def get_stock(product_id: int) -> int:
    with _session() as s:
        product = s.get(Product, product_id)
        return product.stock if product else -1


def get_order(order_id: int) -> dict:
    with _session() as s:
        order = s.get(Order, order_id)
        return {
            "id": order.id,
            "status": order.status,
            "total_cents": order.total_cents,
            "discount_cents": order.discount_cents,
            "coupon_code": order.coupon_code,
            "refund_ref": order.refund_ref,
            "payment_ref": order.payment_ref,
            "payment_provider": order.payment_provider,
        }


def count_orders() -> int:
    with _session() as s:
        return s.scalar(select(func.count()).select_from(Order))


def get_order_items(order_id: int) -> list[dict]:
    with _session() as s:
        items = s.scalars(
            select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
        ).all()
        return [
            {
                "product_id": i.product_id,
                "product_name": i.product_name,
                "unit_price_cents": i.unit_price_cents,
                "quantity": i.quantity,
            }
            for i in items
        ]


def cart_items(email: str) -> list[dict]:
    with _session() as s:
        user = s.scalar(select(User).where(User.email == email))
        cart = s.scalar(select(Cart).where(Cart.user_id == user.id)) if user else None
        if cart is None:
            return []
        items = s.scalars(
            select(CartItem).where(CartItem.cart_id == cart.id).order_by(CartItem.id)
        ).all()
        return [{"id": i.id, "product_id": i.product_id, "quantity": i.quantity} for i in items]


# --- API shortcuts -----------------------------------------------------------

def register(client, email: str = "shopper@example.com", password: str = "password123"):
    """Register (and, via the redirect, sign in) — cookies persist on the client."""
    return client.post("/register", data={"email": email, "password": password})


def order_id_from_pay_url(resp) -> int:
    """Extract the order id from a followed ``/checkout/pay/{id}`` redirect."""
    return int(resp.url.path.rstrip("/").rsplit("/", 1)[-1])


def buy_product(client, product_id: int, quantity: int = 1) -> int:
    """Add to cart, check out, and complete a successful fake payment.

    Returns the now-*paid* order id — the shortcut used to arrange a fulfilled
    purchase (so a shopper can review it, or an admin can ship/refund it).
    """
    client.post("/cart/add", data={"product_id": product_id, "quantity": quantity})
    resp = client.post("/checkout")
    order_id = order_id_from_pay_url(resp)
    client.post(f"/checkout/pay/{order_id}", data={"outcome": "success"})
    return order_id


def completed_event(order_id: int, session_id: str = "cs_test_123") -> dict:
    """A Stripe-shaped ``checkout.session.completed`` webhook body."""
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "metadata": {"order_id": str(order_id)}}},
    }
