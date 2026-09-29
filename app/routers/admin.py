"""Admin area: product CRUD and order management. Every route requires an admin."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import CartItem, Category, Order, OrderItem, Product, User
from app.services import fail_order, fulfill_order, nav_context
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
    """Parse a dollar string into integer cents via Decimal (never float)."""
    try:
        value = Decimal(raw.strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        return None
    return int(value * 100) if value >= 0 else None


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product_count = await db.scalar(select(func.count()).select_from(Product))
    order_count = await db.scalar(select(func.count()).select_from(Order))
    revenue = await db.scalar(
        select(func.coalesce(func.sum(Order.total_cents), 0)).where(Order.status == "paid")
    )
    ctx = {
        "product_count": product_count,
        "order_count": order_count,
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
    """Manually fulfil (pay + decrement stock) or fail a pending order."""
    if action == "fulfill":
        await fulfill_order(db, order_id)
    elif action == "fail":
        await fail_order(db, order_id)
    return RedirectResponse("/admin/orders", status_code=303)
