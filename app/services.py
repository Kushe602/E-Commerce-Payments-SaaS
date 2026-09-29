"""Business logic for carts, orders, and stock — kept out of the routers so it is
independently unit-testable and reused by both the checkout flow and the webhook.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SUPPORTS_ROW_LOCKING
from app.models import (
    Cart,
    CartItem,
    Coupon,
    CouponKind,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Review,
    User,
    WishlistItem,
)

# --- Pure money math (no I/O, trivially unit-testable) -----------------------

def line_total_cents(unit_price_cents: int, quantity: int) -> int:
    return unit_price_cents * quantity


def cart_total_cents(lines: Iterable[tuple[int, int]]) -> int:
    """Sum ``(unit_price_cents, quantity)`` pairs. Integer cents throughout."""
    return sum(line_total_cents(price, qty) for price, qty in lines)


def dollars_to_cents(raw: str) -> int | None:
    """Parse a dollar string into non-negative integer **cents** via ``Decimal``.

    ``"19.99" -> 1999``. Returns ``None`` for blank/invalid/negative input, so no
    float ever touches a monetary amount.
    """
    try:
        value = Decimal(raw.strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        return None
    return int(value * 100) if value >= 0 else None


def _utcnow() -> datetime:
    """Naive UTC now, to compare against naive UTC timestamps read back from SQLite."""
    return datetime.now(UTC).replace(tzinfo=None)


# --- Errors ------------------------------------------------------------------

class EmptyCartError(Exception):
    """Raised when trying to check out an empty cart."""


class InvalidCouponError(Exception):
    """Raised when a coupon code is unknown, inactive, expired, or used up."""


class ReviewNotAllowedError(Exception):
    """Raised when a user may not review a product (didn't buy it, or bad rating)."""


# --- Cart --------------------------------------------------------------------

@dataclass
class CartLine:
    item: CartItem
    product: Product

    @property
    def total_cents(self) -> int:
        return line_total_cents(self.product.price_cents, self.item.quantity)


@dataclass
class CartView:
    lines: list[CartLine]

    @property
    def total_cents(self) -> int:
        return cart_total_cents(
            (line.product.price_cents, line.item.quantity) for line in self.lines
        )

    @property
    def count(self) -> int:
        return sum(line.item.quantity for line in self.lines)


async def get_or_create_cart(db: AsyncSession, user: User) -> Cart:
    cart = await db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart is None:
        cart = Cart(user_id=user.id)
        db.add(cart)
        await db.commit()
        await db.refresh(cart)
    return cart


async def get_cart_view(db: AsyncSession, user: User) -> CartView:
    """Load the user's cart lines joined to their products, ordered stably."""
    cart = await get_or_create_cart(db, user)
    items = list(
        await db.scalars(
            select(CartItem).where(CartItem.cart_id == cart.id).order_by(CartItem.id)
        )
    )
    if not items:
        return CartView(lines=[])
    product_ids = {item.product_id for item in items}
    products = {
        p.id: p
        for p in await db.scalars(select(Product).where(Product.id.in_(product_ids)))
    }
    lines = [
        CartLine(item=item, product=products[item.product_id])
        for item in items
        if item.product_id in products
    ]
    return CartView(lines=lines)


async def cart_count(db: AsyncSession, user: User | None) -> int:
    if user is None:
        return 0
    cart = await db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart is None:
        return 0
    quantities = await db.scalars(
        select(CartItem.quantity).where(CartItem.cart_id == cart.id)
    )
    return sum(quantities)


async def add_to_cart(db: AsyncSession, user: User, product_id: int, quantity: int) -> None:
    """Add ``quantity`` of a product, clamped to available stock (never oversell
    the cart itself). Silently ignores unknown/inactive products."""
    quantity = max(1, quantity)
    product = await db.get(Product, product_id)
    if product is None or not product.is_active:
        return
    cart = await get_or_create_cart(db, user)
    item = await db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.product_id == product_id
        )
    )
    current = item.quantity if item else 0
    new_qty = min(current + quantity, product.stock)
    if new_qty <= 0:
        return
    if item is None:
        db.add(CartItem(cart_id=cart.id, product_id=product_id, quantity=new_qty))
    else:
        item.quantity = new_qty
    await db.commit()


async def set_cart_item_quantity(
    db: AsyncSession, user: User, item_id: int, quantity: int
) -> None:
    """Set a line's quantity; ``<= 0`` removes it. Clamped to stock."""
    cart = await get_or_create_cart(db, user)
    item = await db.get(CartItem, item_id)
    if item is None or item.cart_id != cart.id:
        return
    if quantity <= 0:
        await db.delete(item)
        await db.commit()
        return
    product = await db.get(Product, item.product_id)
    item.quantity = min(quantity, product.stock) if product else quantity
    await db.commit()


async def remove_cart_item(db: AsyncSession, user: User, item_id: int) -> None:
    cart = await get_or_create_cart(db, user)
    item = await db.get(CartItem, item_id)
    if item is not None and item.cart_id == cart.id:
        await db.delete(item)
        await db.commit()


# --- Orders ------------------------------------------------------------------

async def create_order_from_cart(db: AsyncSession, user: User, provider: str) -> Order:
    """Snapshot the cart into a pending order and empty the cart, atomically.

    Stock is **not** decremented here — that happens on payment fulfilment, so a
    cart sitting in checkout never holds inventory hostage.
    """
    view = await get_cart_view(db, user)
    if not view.lines:
        raise EmptyCartError

    order = Order(
        user_id=user.id,
        status=OrderStatus.PENDING,
        total_cents=view.total_cents,
        payment_provider=provider,
    )
    db.add(order)
    await db.flush()  # assign order.id

    for line in view.lines:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=line.product.id,
                product_name=line.product.name,
                unit_price_cents=line.product.price_cents,
                quantity=line.item.quantity,
            )
        )
        await db.delete(line.item)  # empty the cart as we go

    await db.commit()
    await db.refresh(order)
    return order


async def get_order_items(db: AsyncSession, order_id: int) -> list[OrderItem]:
    return list(
        await db.scalars(
            select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
        )
    )


async def fulfill_order(db: AsyncSession, order_id: int) -> bool:
    """Mark an order paid and decrement stock — atomically, with an oversell guard.

    Returns ``True`` when fulfilled, ``False`` when it can't be (insufficient
    stock, unknown order). Idempotent: a re-delivered webhook for an already-paid
    order returns ``True`` without decrementing again.
    """
    order = await db.get(Order, order_id)
    if order is None:
        return False
    if order.status == OrderStatus.PAID:
        return True  # idempotent — webhooks can arrive more than once
    if order.status != OrderStatus.PENDING:
        return False

    items = await get_order_items(db, order_id)

    # Load every product first (locking the rows on Postgres so two concurrent
    # fulfilments of the last unit can't both succeed), then verify all lines
    # before mutating any — so a partial failure decrements nothing.
    products: dict[int, Product | None] = {}
    for item in items:
        stmt = select(Product).where(Product.id == item.product_id)
        if SUPPORTS_ROW_LOCKING:
            stmt = stmt.with_for_update()
        products[item.product_id] = await db.scalar(stmt)

    for item in items:
        product = products.get(item.product_id)
        if product is None or product.stock < item.quantity:
            order.status = OrderStatus.FAILED
            await db.commit()
            return False

    for item in items:
        products[item.product_id].stock -= item.quantity  # type: ignore[union-attr]
    order.status = OrderStatus.PAID
    await db.commit()
    return True


async def fail_order(db: AsyncSession, order_id: int) -> None:
    """Mark a pending order failed (e.g. a simulated/declined payment)."""
    order = await db.get(Order, order_id)
    if order is not None and order.status == OrderStatus.PENDING:
        order.status = OrderStatus.FAILED
        await db.commit()


# --- Fulfilment lifecycle + refunds -----------------------------------------

# Allowed forward transitions of a fulfilled order: action -> (required, next).
_LIFECYCLE: dict[str, tuple[str, str]] = {
    "ship": (OrderStatus.PAID, OrderStatus.SHIPPED),
    "deliver": (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
}


async def advance_order_status(db: AsyncSession, order_id: int, action: str) -> bool:
    """Move a paid order along ``paid → shipped → delivered``.

    Returns ``True`` on a valid transition, ``False`` otherwise (unknown action,
    unknown order, or the order isn't in the required prior state).
    """
    if action not in _LIFECYCLE:
        return False
    order = await db.get(Order, order_id)
    if order is None:
        return False
    required, target = _LIFECYCLE[action]
    if order.status != required:
        return False
    order.status = target
    await db.commit()
    return True


async def refund_order(db: AsyncSession, order_id: int, provider) -> bool:
    """Refund a fulfilled order: restock every line and mark it refunded.

    Idempotent — an already-refunded order returns ``True`` without restocking
    again or calling the provider a second time. Only ``paid/shipped/delivered``
    orders can be refunded. The provider performs the actual refund (the fake
    provider just acknowledges it; Stripe issues a real refund), so this stays
    keyless-testable. Product rows are locked on Postgres, mirroring fulfilment.
    """
    order = await db.get(Order, order_id)
    if order is None:
        return False
    if order.status == OrderStatus.REFUNDED:
        return True  # idempotent
    if order.status not in OrderStatus.FULFILLED:
        return False

    result = await provider.refund(order)

    items = await get_order_items(db, order_id)
    for item in items:
        if item.product_id is None:
            continue  # product was deleted; nothing to restock
        stmt = select(Product).where(Product.id == item.product_id)
        if SUPPORTS_ROW_LOCKING:
            stmt = stmt.with_for_update()
        product = await db.scalar(stmt)
        if product is not None:
            product.stock += item.quantity
    order.status = OrderStatus.REFUNDED
    order.refund_ref = result.reference
    await db.commit()
    return True


# --- Coupons -----------------------------------------------------------------

def compute_discount_cents(subtotal_cents: int, coupon: Coupon) -> int:
    """Discount for a subtotal, in integer cents, clamped to ``[0, subtotal]``.

    Percentage discounts floor to the cent; a fixed discount never exceeds the
    subtotal, so an order total can never go negative.
    """
    if subtotal_cents <= 0:
        return 0
    if coupon.kind == CouponKind.PERCENT:
        discount = subtotal_cents * coupon.value // 100
    else:
        discount = coupon.value
    return max(0, min(discount, subtotal_cents))


async def find_valid_coupon(db: AsyncSession, code: str) -> Coupon | None:
    """Return an active, unexpired, not-used-up coupon for ``code``, else ``None``."""
    code = (code or "").strip().upper()
    if not code:
        return None
    coupon = await db.scalar(select(Coupon).where(Coupon.code == code))
    if coupon is None or not coupon.is_active:
        return None
    if coupon.expires_at is not None and coupon.expires_at < _utcnow():
        return None
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        return None
    return coupon


async def apply_coupon_to_order(db: AsyncSession, order: Order, coupon: Coupon) -> None:
    """Record the coupon on the order, adjust its total in cents, consume one use.

    The order is expected to have ``total_cents`` still equal to its subtotal (as
    created from the cart); the discount is subtracted from it here.
    """
    subtotal = order.total_cents
    discount = compute_discount_cents(subtotal, coupon)
    order.discount_cents = discount
    order.coupon_code = coupon.code
    order.total_cents = subtotal - discount
    coupon.used_count += 1
    await db.commit()


# --- Shared view context -----------------------------------------------------

async def nav_context(db: AsyncSession, user: User | None) -> dict:
    """Common template context for the nav bar (current user + cart badge)."""
    return {"user": user, "cart_count": await cart_count(db, user)}


# --- Reviews -----------------------------------------------------------------

@dataclass
class ReviewView:
    review: Review
    author_email: str


async def user_has_purchased(db: AsyncSession, user: User, product_id: int) -> bool:
    """True if the user has a fulfilled order containing this product."""
    stmt = (
        select(OrderItem.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.user_id == user.id,
            OrderItem.product_id == product_id,
            Order.status.in_(OrderStatus.FULFILLED),
        )
        .limit(1)
    )
    return await db.scalar(stmt) is not None


async def get_reviews(db: AsyncSession, product_id: int) -> list[ReviewView]:
    """Reviews for a product (newest first) with each author's email attached."""
    reviews = list(
        await db.scalars(
            select(Review)
            .where(Review.product_id == product_id)
            .order_by(Review.created_at.desc())
        )
    )
    if not reviews:
        return []
    user_ids = {r.user_id for r in reviews}
    emails = {
        u.id: u.email
        for u in await db.scalars(select(User).where(User.id.in_(user_ids)))
    }
    return [ReviewView(review=r, author_email=emails.get(r.user_id, "unknown")) for r in reviews]


async def get_review_summary(db: AsyncSession, product_id: int) -> tuple[float, int]:
    """Return ``(average_rating_rounded_1dp, count)``; ``(0.0, 0)`` when none."""
    avg, count = (
        await db.execute(
            select(func.avg(Review.rating), func.count(Review.id)).where(
                Review.product_id == product_id
            )
        )
    ).one()
    return (round(float(avg), 1) if avg is not None else 0.0, int(count))


async def get_user_review(db: AsyncSession, user: User, product_id: int) -> Review | None:
    return await db.scalar(
        select(Review).where(Review.product_id == product_id, Review.user_id == user.id)
    )


async def upsert_review(
    db: AsyncSession, user: User, product_id: int, rating: int, body: str
) -> Review:
    """Create or update the user's single review for a product they purchased."""
    if not 1 <= rating <= 5:
        raise ReviewNotAllowedError("Rating must be between 1 and 5 stars.")
    if not await user_has_purchased(db, user, product_id):
        raise ReviewNotAllowedError("Only verified purchasers can review this product.")
    review = await get_user_review(db, user, product_id)
    if review is None:
        review = Review(product_id=product_id, user_id=user.id, rating=rating, body=body.strip())
        db.add(review)
    else:
        review.rating = rating
        review.body = body.strip()
    await db.commit()
    await db.refresh(review)
    return review


# --- Wishlist ----------------------------------------------------------------

async def add_to_wishlist(db: AsyncSession, user: User, product_id: int) -> None:
    """Save a product to the user's wishlist (idempotent; ignores unknown ids)."""
    product = await db.get(Product, product_id)
    if product is None:
        return
    existing = await db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == user.id, WishlistItem.product_id == product_id
        )
    )
    if existing is None:
        db.add(WishlistItem(user_id=user.id, product_id=product_id))
        await db.commit()


async def remove_from_wishlist(db: AsyncSession, user: User, product_id: int) -> None:
    item = await db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == user.id, WishlistItem.product_id == product_id
        )
    )
    if item is not None:
        await db.delete(item)
        await db.commit()


async def get_wishlist_products(db: AsyncSession, user: User) -> list[Product]:
    """Active-or-not products on the user's wishlist, most recently added first."""
    product_ids = list(
        await db.scalars(
            select(WishlistItem.product_id)
            .where(WishlistItem.user_id == user.id)
            .order_by(WishlistItem.id.desc())
        )
    )
    if not product_ids:
        return []
    products = {
        p.id: p for p in await db.scalars(select(Product).where(Product.id.in_(product_ids)))
    }
    return [products[pid] for pid in product_ids if pid in products]


async def is_wishlisted(db: AsyncSession, user: User | None, product_id: int) -> bool:
    if user is None:
        return False
    return (
        await db.scalar(
            select(WishlistItem.id).where(
                WishlistItem.user_id == user.id, WishlistItem.product_id == product_id
            )
        )
    ) is not None
