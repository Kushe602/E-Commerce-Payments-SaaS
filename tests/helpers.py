"""Test helpers: seed rows and read state through a synchronous engine, plus a
couple of API shortcuts. Direct DB access keeps the arrange/assert steps of a
test independent of the very endpoints under test.
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Cart, CartItem, Order, OrderItem, Product, User

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


# --- Reading state -----------------------------------------------------------

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
            "payment_ref": order.payment_ref,
            "payment_provider": order.payment_provider,
        }


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


def completed_event(order_id: int, session_id: str = "cs_test_123") -> dict:
    """A Stripe-shaped ``checkout.session.completed`` webhook body."""
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "metadata": {"order_id": str(order_id)}}},
    }
