"""Catalog routes: storefront listing (search + category filter) and product detail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_optional_user
from app.models import Category, Product, User
from app.services import nav_context
from app.web import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Storefront grid with optional full-text-ish search and category filter."""
    stmt = select(Product).where(Product.is_active.is_(True))
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q.strip()}%"))
    active_category = None
    if category:
        active_category = await db.scalar(select(Category).where(Category.slug == category))
        if active_category is not None:
            stmt = stmt.where(Product.category_id == active_category.id)
    stmt = stmt.order_by(Product.name)

    products = list(await db.scalars(stmt))
    categories = list(await db.scalars(select(Category).order_by(Category.name)))

    ctx = {
        "products": products,
        "categories": categories,
        "active_category": active_category.slug if active_category else None,
        "q": q or "",
        **await nav_context(db, user),
    }
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/products/{slug}", response_class=HTMLResponse)
async def product_detail(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    product = await db.scalar(select(Product).where(Product.slug == slug))
    if product is None or not product.is_active:
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "Product not found.", "user": user},
            status_code=404,
        )
    ctx = {"product": product, **await nav_context(db, user)}
    return templates.TemplateResponse(request, "product_detail.html", ctx)
