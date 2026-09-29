"""Checkout + payments orchestration.

``POST /checkout`` snapshots the cart into a pending order and hands off to the
active payment provider. In fake mode the shopper lands on a local pay page and
deterministically chooses success or failure; in Stripe mode they are redirected
to Stripe's hosted Checkout and fulfilment happens via the webhook.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Order, User
from app.payments import get_payment_provider
from app.services import (
    EmptyCartError,
    apply_coupon_to_order,
    create_order_from_cart,
    fail_order,
    find_valid_coupon,
    fulfill_order,
    get_order_items,
    nav_context,
)
from app.web import templates

router = APIRouter()


def _base_url(request: Request) -> str:
    return (settings.base_url or str(request.base_url)).rstrip("/")


async def _load_owned_order(db: AsyncSession, order_id: int, user: User) -> Order | None:
    order = await db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        return None
    return order


@router.post("/checkout")
async def checkout(
    request: Request,
    coupon_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = get_payment_provider()

    # Validate any coupon *before* creating the order, so a bad code creates
    # nothing and just bounces the shopper back to the cart with a message.
    coupon = None
    if coupon_code.strip():
        coupon = await find_valid_coupon(db, coupon_code)
        if coupon is None:
            return RedirectResponse("/cart?coupon_error=1", status_code=303)

    try:
        order = await create_order_from_cart(db, user, provider.mode)
    except EmptyCartError:
        return RedirectResponse("/cart", status_code=303)

    if coupon is not None:
        await apply_coupon_to_order(db, order, coupon)

    items = await get_order_items(db, order.id)
    base = _base_url(request)
    start = await provider.start_checkout(
        order,
        items,
        success_url=f"{base}/orders/{order.id}?paid=1",
        cancel_url=f"{base}/orders/{order.id}?canceled=1",
    )
    order.payment_ref = start.reference
    await db.commit()
    return RedirectResponse(start.redirect_url, status_code=303)


@router.get("/checkout/pay/{order_id}", response_class=HTMLResponse)
async def fake_pay_page(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deterministic local pay page — fake mode only."""
    if get_payment_provider().mode != "fake":
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "Not found.", "user": user},
            status_code=404,
        )
    order = await _load_owned_order(db, order_id, user)
    if order is None:
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "Order not found.", "user": user},
            status_code=404,
        )
    if order.status != "pending":
        return RedirectResponse(f"/orders/{order_id}", status_code=303)
    items = await get_order_items(db, order_id)
    ctx = {"order": order, "items": items, **await nav_context(db, user)}
    return templates.TemplateResponse(request, "checkout/pay.html", ctx)


@router.post("/checkout/pay/{order_id}")
async def fake_pay_complete(
    order_id: int,
    outcome: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Complete the simulated payment: ``success`` fulfils, anything else fails."""
    if get_payment_provider().mode != "fake":
        return RedirectResponse("/", status_code=303)
    order = await _load_owned_order(db, order_id, user)
    if order is None:
        return RedirectResponse("/orders", status_code=303)
    if order.status == "pending":
        if outcome == "success":
            await fulfill_order(db, order_id)
        else:
            await fail_order(db, order_id)
    return RedirectResponse(f"/orders/{order_id}", status_code=303)
