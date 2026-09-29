"""Payment webhooks.

In Stripe mode the signature is verified with ``STRIPE_WEBHOOK_SECRET`` before the
event is trusted; a bad/missing signature returns 400 and changes nothing. In fake
mode an unsigned, Stripe-shaped JSON event is accepted so the fulfilment path is
exercisable locally and in CI. Either way, a ``checkout.session.completed`` event
fulfils the referenced order (marks it paid + decrements stock), idempotently.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.payments import WebhookVerificationError, get_payment_provider
from app.services import fulfill_order

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    provider = get_payment_provider()
    try:
        event = provider.parse_webhook(payload, signature)
    except WebhookVerificationError:
        return JSONResponse({"error": "invalid signature"}, status_code=400)

    if event.type == "checkout.session.completed" and event.order_id is not None:
        await fulfill_order(db, event.order_id)

    return JSONResponse({"received": True})
