"""Catalog routes: storefront listing (search + category filter + sort + price
range), product detail with reviews, and review submission."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_optional_user
from app.models import Category, Product, User
from app.services import (
    ReviewNotAllowedError,
    dollars_to_cents,
    get_review_summary,
    get_reviews,
    get_user_review,
    is_wishlisted,
    nav_context,
    upsert_review,
    user_has_purchased,
)
from app.web import templates

router = APIRouter()

# Sort options exposed in the UI -> ORDER BY clause. ``name`` is the default.
_SORTS = {
    "name": Product.name.asc(),
    "newest": Product.created_at.desc(),
    "price_asc": Product.price_cents.asc(),
    "price_desc": Product.price_cents.desc(),
}


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    sort: str = "name",
    min_price: str | None = None,
    max_price: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Storefront grid with search, category filter, sort, and a price range."""
    stmt = select(Product).where(Product.is_active.is_(True))
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q.strip()}%"))
    active_category = None
    if category:
        active_category = await db.scalar(select(Category).where(Category.slug == category))
        if active_category is not None:
            stmt = stmt.where(Product.category_id == active_category.id)

    # Price range: parsed from dollars into integer cents; blank/invalid → ignored.
    min_cents = dollars_to_cents(min_price) if min_price else None
    max_cents = dollars_to_cents(max_price) if max_price else None
    if min_cents is not None:
        stmt = stmt.where(Product.price_cents >= min_cents)
    if max_cents is not None:
        stmt = stmt.where(Product.price_cents <= max_cents)

    sort = sort if sort in _SORTS else "name"
    stmt = stmt.order_by(_SORTS[sort])

    products = list(await db.scalars(stmt))
    categories = list(await db.scalars(select(Category).order_by(Category.name)))

    ctx = {
        "products": products,
        "categories": categories,
        "active_category": active_category.slug if active_category else None,
        "q": q or "",
        "sort": sort,
        "min_price": min_price or "",
        "max_price": max_price or "",
        **await nav_context(db, user),
    }
    return templates.TemplateResponse(request, "index.html", ctx)


async def _reviews_context(db: AsyncSession, product: Product, user: User | None) -> dict:
    """Shared context for the reviews block (product detail + HTMX re-render)."""
    avg_rating, review_count = await get_review_summary(db, product.id)
    can_review = user is not None and await user_has_purchased(db, user, product.id)
    my_review = await get_user_review(db, user, product.id) if user is not None else None
    return {
        "product": product,
        "reviews": await get_reviews(db, product.id),
        "avg_rating": avg_rating,
        "review_count": review_count,
        "can_review": can_review,
        "my_review": my_review,
        "review_error": None,
        "user": user,
    }


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
    ctx = {
        "wishlisted": await is_wishlisted(db, user, product.id),
        **await _reviews_context(db, product, user),
        **await nav_context(db, user),
    }
    return templates.TemplateResponse(request, "product_detail.html", ctx)


@router.post("/products/{slug}/reviews")
async def submit_review(
    slug: str,
    request: Request,
    rating: int = Form(...),
    body: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Leave (or update) a review. Verified purchasers only; HTMX re-renders the block."""
    product = await db.scalar(select(Product).where(Product.slug == slug))
    if product is None:
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "Product not found.", "user": user},
            status_code=404,
        )
    error = None
    try:
        await upsert_review(db, user, product.id, rating, body)
    except ReviewNotAllowedError as exc:
        error = str(exc)

    if request.headers.get("HX-Request") == "true":
        ctx = await _reviews_context(db, product, user)
        ctx["review_error"] = error
        status = 200 if error is None else 400
        return templates.TemplateResponse(
            request, "partials/reviews.html", ctx, status_code=status
        )
    return RedirectResponse(f"/products/{slug}", status_code=303)
