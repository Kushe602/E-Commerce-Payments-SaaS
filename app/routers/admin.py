"""Admin area: product CRUD, coupon management, and order management. Every route
requires an admin."""
from __future__ import annotations

import re
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import (
    CartItem,
    Category,
    Coupon,
    CouponKind,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    User,
)
from app.payments import get_payment_provider
from app.services import (
    advance_order_status,
    dollars_to_cents,
    fail_order,
    fulfill_order,
    nav_context,
    refund_order,
)
from app.web import templates

router = APIRouter(prefix="/admin")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "item"


async def _unique_slug(db: AsyncSession, base: str, exclude_id: int | None = None) -> str:
    slug, n = base, 2
    while True:
        stmt = select(Product.id).where(Product.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(Product.id != exclude_id)
        if await db.scalar(stmt) is None:
            return slug
        slug, n = f"{base}-{n}", n + 1


def _parse_price_cents(raw: str) -> int | None:
    """Parse a dollar string into non-negative integer cents (never float)."""
    return dollars_to_cents(raw)


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product_count = await db.scalar(select(func.count()).select_from(Product))
    order_count = await db.scalar(select(func.count()).select_from(Order))
    coupon_count = await db.scalar(select(func.count()).select_from(Coupon))
    # Captured revenue = fulfilled orders (paid/shipped/delivered), net of refunds.
    revenue = await db.scalar(
        select(func.coalesce(func.sum(Order.total_cents), 0)).where(
            Order.status.in_(OrderStatus.FULFILLED)
        )
    )
    ctx = {
        "product_count": product_count,
        "order_count": order_count,
        "coupon_count": coupon_count,
        "revenue_cents": revenue,
        **await nav_context(db, admin),
    }
    return templates.TemplateResponse(request, "admin/dashboard.html", ctx)


@router.get("/products", response_class=HTMLResponse)
async def product_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    products = list(await db.scalars(select(Product).order_by(Product.created_at.desc())))
    ctx = {"products": products, **await nav_context(db, admin)}
    return templates.TemplateResponse(request, "admin/products.html", ctx)


async def _render_form(request, db, admin, product, error=None, status_code=200):
    categories = list(await db.scalars(select(Category).order_by(Category.name)))
    ctx = {
        "product": product,
        "categories": categories,
        "error": error,
        **await nav_context(db, admin),
    }
    return templates.TemplateResponse(
        request, "admin/product_form.html", ctx, status_code=status_code
    )


@router.get("/products/new", response_class=HTMLResponse)
async def product_new(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return await _render_form(request, db, admin, product=None)


@router.post("/products")
async def product_create(
    request: Request,
    name: str = Form(...),
    price: str = Form(...),
    stock: int = Form(0),
    description: str = Form(""),
    slug: str = Form(""),
    image_url: str = Form(""),
    category_id: str = Form(""),
    is_active: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    price_cents = _parse_price_cents(price)
    if not name.strip() or price_cents is None:
        return await _render_form(
            request, db, admin, product=None,
            error="A name and a valid, non-negative price are required.", status_code=400,
        )
    product = Product(
        name=name.strip(),
        slug=await _unique_slug(db, _slugify(slug or name)),
        description=description.strip(),
        price_cents=price_cents,
        stock=max(0, stock),
        image_url=image_url.strip() or None,
        category_id=int(category_id) if category_id.isdigit() else None,
        is_active=is_active,
    )
    db.add(product)
    await db.commit()
    return RedirectResponse("/admin/products", status_code=303)


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def product_edit(
    product_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product = await db.get(Product, product_id)
    if product is None:
        return RedirectResponse("/admin/products", status_code=303)
    return await _render_form(request, db, admin, product=product)


@router.post("/products/{product_id}")
async def product_update(
    product_id: int,
    request: Request,
    name: str = Form(...),
    price: str = Form(...),
    stock: int = Form(0),
    description: str = Form(""),
    slug: str = Form(""),
    image_url: str = Form(""),
    category_id: str = Form(""),
    is_active: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product = await db.get(Product, product_id)
    if product is None:
        return RedirectResponse("/admin/products", status_code=303)
    price_cents = _parse_price_cents(price)
    if not name.strip() or price_cents is None:
        return await _render_form(
            request, db, admin, product=product,
            error="A name and a valid, non-negative price are required.", status_code=400,
        )
    product.name = name.strip()
    product.slug = await _unique_slug(db, _slugify(slug or name), exclude_id=product.id)
    product.description = description.strip()
    product.price_cents = price_cents
    product.stock = max(0, stock)
    product.image_url = image_url.strip() or None
    product.category_id = int(category_id) if category_id.isdigit() else None
    product.is_active = is_active
    await db.commit()
    return RedirectResponse("/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
async def product_delete(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # Detach from past orders (keep their name/price snapshot) and drop from any
    # carts, then remove the product — safe on Postgres FKs, preserves receipts.
    await db.execute(
        update(OrderItem).where(OrderItem.product_id == product_id).values(product_id=None)
    )
    await db.execute(delete(CartItem).where(CartItem.product_id == product_id))
    product = await db.get(Product, product_id)
    if product is not None:
        await db.delete(product)
    await db.commit()
    return RedirectResponse("/admin/products", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
async def admin_orders(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    orders = list(await db.scalars(select(Order).order_by(Order.created_at.desc())))
    user_ids = {o.user_id for o in orders}
    emails: dict[int, str] = {}
    if user_ids:
        emails = {
            u.id: u.email
            for u in await db.scalars(select(User).where(User.id.in_(user_ids)))
        }
    ctx = {"orders": orders, "emails": emails, **await nav_context(db, admin)}
    return templates.TemplateResponse(request, "admin/orders.html", ctx)


@router.post("/orders/{order_id}/status")
async def admin_order_status(
    order_id: int,
    action: str = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Drive an order: fulfil/fail a pending one, advance its lifecycle, or refund it.

    ``fulfill``/``fail`` act on pending orders; ``ship``/``deliver`` move a paid
    order along its lifecycle; ``refund`` restocks a fulfilled order and marks it
    refunded (idempotently) via the active payment provider.
    """
    if action == "fulfill":
        await fulfill_order(db, order_id)
    elif action == "fail":
        await fail_order(db, order_id)
    elif action in ("ship", "deliver"):
        await advance_order_status(db, order_id, action)
    elif action == "refund":
        await refund_order(db, order_id, get_payment_provider())
    return RedirectResponse("/admin/orders", status_code=303)


# --- Coupons -----------------------------------------------------------------

_COUPON_CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9-]{1,63}")


def _parse_expiry(raw: str) -> tuple[datetime | None, bool]:
    """Parse an optional ``YYYY-MM-DD`` expiry into a naive end-of-day UTC datetime.

    Returns ``(value, ok)``: blank → ``(None, True)``; invalid → ``(None, False)``.
    """
    raw = raw.strip()
    if not raw:
        return None, True
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None, False
    return datetime.combine(parsed, time.max), True


@router.get("/coupons", response_class=HTMLResponse)
async def coupon_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    coupons = list(await db.scalars(select(Coupon).order_by(Coupon.created_at.desc())))
    ctx = {"coupons": coupons, **await nav_context(db, admin)}
    return templates.TemplateResponse(request, "admin/coupons.html", ctx)


@router.get("/coupons/new", response_class=HTMLResponse)
async def coupon_new(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ctx = {"error": None, **await nav_context(db, admin)}
    return templates.TemplateResponse(request, "admin/coupon_form.html", ctx)


@router.post("/coupons")
async def coupon_create(
    request: Request,
    code: str = Form(...),
    kind: str = Form(...),
    value: str = Form(...),
    expires_at: str = Form(""),
    max_uses: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    code = code.strip().upper()
    expiry, expiry_ok = _parse_expiry(expires_at)
    error = None

    if not _COUPON_CODE_RE.fullmatch(code):
        error = "Code must be 2–64 chars: letters, digits, and hyphens."
    elif kind not in CouponKind.ALL:
        error = "Choose a valid discount type."
    elif not expiry_ok:
        error = "Expiry must be a valid date (YYYY-MM-DD) or blank."

    # Value: whole percent (1–100) for percentage codes, else a dollar amount → cents.
    coupon_value = None
    if error is None:
        if kind == CouponKind.PERCENT:
            raw = value.strip()
            coupon_value = int(raw) if raw.isdigit() else None
            if coupon_value is None or not 1 <= coupon_value <= 100:
                error = "Percentage must be a whole number between 1 and 100."
        else:
            coupon_value = dollars_to_cents(value)
            if not coupon_value:  # None or 0
                error = "Fixed amount must be a positive dollar value."

    cap = None
    if error is None and max_uses.strip():
        cap = int(max_uses) if max_uses.strip().isdigit() else None
        if cap is None or cap < 1:
            error = "Usage cap must be a positive whole number, or blank."

    if error is None and await db.scalar(select(Coupon.id).where(Coupon.code == code)):
        error = "A coupon with that code already exists."

    if error is not None:
        ctx = {"error": error, **await nav_context(db, admin)}
        return templates.TemplateResponse(
            request, "admin/coupon_form.html", ctx, status_code=400
        )

    db.add(
        Coupon(
            code=code,
            kind=kind,
            value=coupon_value,
            expires_at=expiry,
            max_uses=cap,
            is_active=True,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/coupons", status_code=303)


@router.post("/coupons/{coupon_id}/toggle")
async def coupon_toggle(
    coupon_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Activate/deactivate a coupon (deactivated codes are refused at checkout)."""
    coupon = await db.get(Coupon, coupon_id)
    if coupon is not None:
        coupon.is_active = not coupon.is_active
        await db.commit()
    return RedirectResponse("/admin/coupons", status_code=303)
