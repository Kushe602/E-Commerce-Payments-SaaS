"""Business logic for carts, orders, and stock — kept out of the routers so it is
independently unit-testable and reused by both the checkout flow and the webhook.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SUPPORTS_ROW_LOCKING
from app.models import Cart, CartItem, Order, OrderItem, OrderStatus, Product, User

# --- Pure money math (no I/O, trivially unit-testable) -----------------------

def line_total_cents(unit_price_cents: int, quantity: int) -> int:
    return unit_price_cents * quantity


def cart_total_cents(lines: Iterable[tuple[int, int]]) -> int:
    """Sum ``(unit_price_cents, quantity)`` pairs. Integer cents throughout."""
    return sum(line_total_cents(price, qty) for price, qty in lines)


# --- Errors ------------------------------------------------------------------

class EmptyCartError(Exception):
    """Raised when trying to check out an empty cart."""


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


# --- Shared view context -----------------------------------------------------

async def nav_context(db: AsyncSession, user: User | None) -> dict:
    """Common template context for the nav bar (current user + cart badge)."""
    return {"user": user, "cart_count": await cart_count(db, user)}
