"""Pluggable payments layer.

Two providers implement the same tiny interface:

* :class:`FakePaymentProvider` (default) — no network, no keys. ``start_checkout``
  points the browser at a local page where the shopper deterministically chooses
  "pay" or "simulate failure". Its ``parse_webhook`` accepts an unsigned,
  Stripe-shaped JSON event so the fulfilment path can be exercised in CI and demos.
* :class:`StripePaymentProvider` — real Stripe **test mode**. ``start_checkout``
  creates a hosted Checkout Session; ``parse_webhook`` verifies the signature with
  the webhook secret before trusting the event.

The router code never imports Stripe directly — it depends only on this module,
so the app boots and the whole suite runs with zero third-party dependencies
configured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import settings
from app.models import Order, OrderItem


@dataclass
class CheckoutStart:
    """Where to send the browser next, plus the provider reference to persist."""

    redirect_url: str
    reference: str


@dataclass
class WebhookEvent:
    """A normalised payment event, provider-agnostic."""

    type: str
    order_id: int | None
    reference: str | None


class WebhookVerificationError(Exception):
    """Raised when a webhook payload fails signature verification."""


class PaymentConfigError(Exception):
    """Raised when Stripe mode is selected but not fully configured."""


# --- Fake provider -----------------------------------------------------------

class FakePaymentProvider:
    mode = "fake"

    async def start_checkout(
        self, order: Order, items: list[OrderItem], success_url: str, cancel_url: str
    ) -> CheckoutStart:
        # Route to our own deterministic pay page. success/cancel URLs are unused
        # by the fake flow but kept in the signature to mirror the real provider.
        return CheckoutStart(
            redirect_url=f"/checkout/pay/{order.id}",
            reference=f"fake_sess_{order.id}",
        )

    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent:
        """Parse an unsigned, Stripe-shaped event. No signature to verify here."""
        try:
            event = json.loads(payload or b"{}")
        except json.JSONDecodeError as exc:
            raise WebhookVerificationError("malformed JSON") from exc
        obj = (event.get("data") or {}).get("object") or {}
        metadata = obj.get("metadata") or {}
        order_id = metadata.get("order_id")
        return WebhookEvent(
            type=event.get("type", ""),
            order_id=int(order_id) if order_id is not None else None,
            reference=obj.get("id"),
        )


# --- Stripe provider ---------------------------------------------------------

class StripePaymentProvider:
    mode = "stripe"

    def __init__(self) -> None:
        if not settings.stripe_secret_key:
            raise PaymentConfigError("STRIPE_SECRET_KEY is required for Stripe mode")

    def _client(self):
        import stripe  # imported lazily so fake mode needs no Stripe install

        stripe.api_key = settings.stripe_secret_key
        return stripe

    async def start_checkout(
        self, order: Order, items: list[OrderItem], success_url: str, cancel_url: str
    ) -> CheckoutStart:
        stripe = self._client()
        line_items = [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": item.product_name},
                    "unit_amount": item.unit_price_cents,  # integer cents
                },
                "quantity": item.quantity,
            }
            for item in items
        ]
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            success_url=success_url,
            cancel_url=cancel_url,
            # We reconcile the webhook back to our order via this metadata.
            metadata={"order_id": str(order.id)},
        )
        return CheckoutStart(redirect_url=session.url, reference=session.id)

    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent:
        stripe = self._client()
        if not settings.stripe_webhook_secret:
            raise PaymentConfigError("STRIPE_WEBHOOK_SECRET is required to verify webhooks")
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, settings.stripe_webhook_secret
            )
        except Exception as exc:  # stripe raises SignatureVerificationError/ValueError
            raise WebhookVerificationError(str(exc)) from exc
        obj = event["data"]["object"]
        metadata = obj.get("metadata") or {}
        order_id = metadata.get("order_id")
        return WebhookEvent(
            type=event["type"],
            order_id=int(order_id) if order_id is not None else None,
            reference=obj.get("id"),
        )


def get_payment_provider() -> FakePaymentProvider | StripePaymentProvider:
    """Return the active provider based on settings (fake unless Stripe is set)."""
    if settings.payments_mode == "stripe":
        return StripePaymentProvider()
    return FakePaymentProvider()
