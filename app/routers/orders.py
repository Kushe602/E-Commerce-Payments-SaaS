"""Order history and order detail (receipts)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Order, User
from app.services import get_order_items, nav_context
from app.web import templates

router = APIRouter()


@router.get("/orders", response_class=HTMLResponse)
async def order_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    orders = list(
        await db.scalars(
            select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())
        )
    )
    ctx = {"orders": orders, **await nav_context(db, user)}
    return templates.TemplateResponse(request, "orders/list.html", ctx)


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(
    order_id: int,
    request: Request,
    paid: int | None = None,
    canceled: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = await db.get(Order, order_id)
    # Owners see their own orders; admins can see any.
    if order is None or (order.user_id != user.id and not user.is_admin):
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "Order not found.", "user": user},
            status_code=404,
        )
    items = await get_order_items(db, order_id)
    ctx = {
        "order": order,
        "items": items,
        "canceled": bool(canceled),
        **await nav_context(db, user),
    }
    return templates.TemplateResponse(request, "orders/detail.html", ctx)
