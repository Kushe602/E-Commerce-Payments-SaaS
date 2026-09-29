"""Wishlist routes: a per-user list of saved products, plus save/remove actions.

Add-to-cart from the wishlist reuses the existing ``/cart/add`` endpoint. Save and
remove are plain form posts that return to a safe local ``next`` path (the product
page or the wishlist), so the toggle works with or without JavaScript.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.services import (
    add_to_wishlist,
    get_wishlist_products,
    nav_context,
    remove_from_wishlist,
)
from app.web import templates

router = APIRouter()


def _safe_next(next_url: str | None) -> str:
    """Only allow local, single-slash paths as a redirect target (no open redirects)."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/wishlist"


@router.get("/wishlist", response_class=HTMLResponse)
async def wishlist_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    products = await get_wishlist_products(db, user)
    ctx = {"products": products, **await nav_context(db, user)}
    return templates.TemplateResponse(request, "wishlist.html", ctx)


@router.post("/wishlist/add")
async def wishlist_add(
    product_id: int = Form(...),
    next: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await add_to_wishlist(db, user, product_id)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/wishlist/remove")
async def wishlist_remove(
    product_id: int = Form(...),
    next: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await remove_from_wishlist(db, user, product_id)
    return RedirectResponse(_safe_next(next), status_code=303)
