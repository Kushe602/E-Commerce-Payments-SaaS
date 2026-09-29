"""Cart routes. Mutations return small HTMX partials so totals update live,
with the nav badge refreshed out-of-band."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.services import (
    add_to_cart,
    cart_count,
    get_cart_view,
    remove_cart_item,
    set_cart_item_quantity,
)
from app.web import templates

router = APIRouter()


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/cart", response_class=HTMLResponse)
async def cart_page(
    request: Request,
    coupon_error: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    view = await get_cart_view(db, user)
    return templates.TemplateResponse(
        request,
        "cart.html",
        {
            "view": view,
            "user": user,
            "cart_count": view.count,
            "coupon_error": bool(coupon_error),
        },
    )


async def _cart_body(request: Request, db: AsyncSession, user: User) -> HTMLResponse:
    """Render the cart body partial (line items + totals) with an OOB nav badge."""
    view = await get_cart_view(db, user)
    return templates.TemplateResponse(
        request,
        "partials/cart_body.html",
        {"view": view, "user": user, "cart_count": view.count, "partial": True},
    )


@router.post("/cart/add")
async def cart_add(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await add_to_cart(db, user, product_id, quantity)
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/cart_badge.html",
            {"cart_count": await cart_count(db, user), "oob": False},
        )
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/update")
async def cart_update(
    request: Request,
    item_id: int = Form(...),
    quantity: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await set_cart_item_quantity(db, user, item_id, quantity)
    if _is_htmx(request):
        return await _cart_body(request, db, user)
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/remove")
async def cart_remove(
    request: Request,
    item_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await remove_cart_item(db, user, item_id)
    if _is_htmx(request):
        return await _cart_body(request, db, user)
    return RedirectResponse("/cart", status_code=303)
