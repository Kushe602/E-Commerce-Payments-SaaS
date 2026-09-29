"""Database models for Cartify.

Design notes:
* Money is stored as integer **cents** everywhere — never floats — so totals are
  exact and match what a payment processor charges.
* Relationships are queried explicitly (no ORM lazy ``relationship`` loads),
  which avoids implicit I/O on the async session (a common ``MissingGreenlet``
  foot-gun) and keeps queries obvious.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OrderStatus:
    """Allowed order states (plain string constants → portable across SQLite/PG).

    The fulfilment lifecycle runs ``paid → shipped → delivered``; ``refunded`` is a
    terminal state reachable from any fulfilled state (it restocks the order).
    """

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    REFUNDED = "refunded"

    ALL = ("pending", "paid", "failed", "shipped", "delivered", "refunded")
    # States that count as a completed purchase (stock has been decremented and
    # the money captured) — used to gate reviews and to allow refunds.
    FULFILLED = ("paid", "shipped", "delivered")


class CouponKind:
    """Discount kinds. ``value`` is whole percent (1–100) or integer cents off."""

    PERCENT = "percent"
    FIXED = "fixed"

    ALL = ("percent", "fixed")


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Price in integer cents (e.g. 1999 == $19.99).
    price_cents: Mapped[int] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "product_id", name="uq_cart_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING, index=True)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Coupon snapshot: the discount applied (integer cents) and the code used, so
    # a later edit/deletion of the coupon never rewrites what the customer paid.
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "fake" or "stripe" — which provider handled this order.
    payment_provider: Mapped[str] = mapped_column(String(20), default="fake")
    # Provider reference (Stripe Checkout Session id, or a fake session id).
    payment_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Provider refund reference, set when the order is refunded.
    refund_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class OrderItem(Base):
    """A line item, snapshotting name + unit price at purchase time.

    Snapshots mean later catalog edits (renames, re-pricing) never rewrite past
    orders — the receipt stays exactly what the customer paid.
    """

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    # Nullable so a product can be deleted without erasing past receipts (the
    # name + unit price are snapshotted below and remain the source of truth).
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True, index=True
    )
    product_name: Mapped[str] = mapped_column(String(200))
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)


class Review(Base):
    """A product rating (1–5) + text, one per (product, user).

    Only users who purchased the product may leave one (enforced in the service
    layer); the unique constraint lets a shopper edit their single review.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("product_id", "user_id", name="uq_review_product_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)  # constrained to 1..5 in services
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now, index=True)


class Coupon(Base):
    """A discount code: percentage or fixed cents off, with optional expiry + cap.

    ``expires_at`` is stored as a naive UTC datetime so comparisons never trip the
    aware/naive mismatch SQLite reads back (all timestamps here are UTC).
    """

    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # CouponKind.PERCENT / .FIXED
    # Whole percent (1..100) for PERCENT, or integer cents off for FIXED.
    value: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class WishlistItem(Base):
    """A saved product for a user. Unique per (user, product)."""

    __tablename__ = "wishlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_wishlist_user_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
