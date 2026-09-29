"""Seed the catalog with demo data so the storefront is never empty.

Idempotent: running it again (or auto-seed on startup) does nothing once products
exist. Run standalone with ``python -m app.seed``.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.database import SessionLocal, init_db
from app.models import Category, Coupon, CouponKind, Product, User
from app.security import hash_password

# Demo accounts (documented in the README). Passwords are >= 8 chars.
ADMIN_EMAIL, ADMIN_PASSWORD = "admin@cartify.dev", "admin12345"
DEMO_EMAIL, DEMO_PASSWORD = "demo@cartify.dev", "demo12345"

_CATEGORIES = [
    ("Electronics", "electronics"),
    ("Home & Kitchen", "home-kitchen"),
    ("Books", "books"),
    ("Apparel", "apparel"),
]

# (name, slug, category_slug, price_cents, stock, description)
_PRODUCTS = [
    ("Aurora Wireless Headphones", "aurora-wireless-headphones", "electronics",
     14999, 25, "Over-ear headphones with active noise cancellation and 30-hour battery."),
    ("Nimbus Mechanical Keyboard", "nimbus-mechanical-keyboard", "electronics",
     8999, 40, "Hot-swappable 75% mechanical keyboard with PBT keycaps."),
    ("Pulse Smartwatch", "pulse-smartwatch", "electronics",
     19999, 15, "Fitness-focused smartwatch with GPS and a 7-day battery."),
    ("Ember Pour-Over Kettle", "ember-pour-over-kettle", "home-kitchen",
     6499, 30, "Gooseneck electric kettle with 1-degree temperature control."),
    ("Terra Ceramic Mug Set", "terra-ceramic-mug-set", "home-kitchen",
     2999, 60, "Set of four stoneware mugs, microwave and dishwasher safe."),
    ("Clean Code Handbook", "clean-code-handbook", "books",
     3499, 100, "A pragmatic guide to writing code humans actually enjoy maintaining."),
    ("The Pragmatic Backend", "the-pragmatic-backend", "books",
     3999, 80, "Patterns for building reliable, observable web services."),
    ("Summit Merino Beanie", "summit-merino-beanie", "apparel",
     2499, 50, "Soft, breathable merino wool beanie for all-day warmth."),
    ("Trailhead Daypack", "trailhead-daypack", "apparel",
     5999, 20, "22L water-resistant daypack with a padded laptop sleeve."),
]


async def seed(db) -> bool:
    """Populate categories, products, and demo users. Returns True if it seeded."""
    existing = await db.scalar(select(func.count()).select_from(Product))
    if existing:
        return False

    categories = {}
    for name, slug in _CATEGORIES:
        category = Category(name=name, slug=slug)
        db.add(category)
        categories[slug] = category
    await db.flush()  # assign category ids

    for name, slug, cat_slug, price_cents, stock, description in _PRODUCTS:
        db.add(
            Product(
                name=name,
                slug=slug,
                description=description,
                price_cents=price_cents,
                stock=stock,
                image_url=f"https://picsum.photos/seed/{slug}/600/400",
                category_id=categories[cat_slug].id,
                is_active=True,
            )
        )

    if not await db.scalar(select(User).where(User.email == ADMIN_EMAIL)):
        db.add(
            User(
                email=ADMIN_EMAIL,
                hashed_password=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
        )
    if not await db.scalar(select(User).where(User.email == DEMO_EMAIL)):
        db.add(
            User(
                email=DEMO_EMAIL,
                hashed_password=hash_password(DEMO_PASSWORD),
                is_admin=False,
            )
        )

    # Demo coupons so the checkout coupon field has something to try out of the box.
    db.add(Coupon(code="WELCOME10", kind=CouponKind.PERCENT, value=10, is_active=True))
    db.add(Coupon(code="SAVE5", kind=CouponKind.FIXED, value=500, is_active=True))

    await db.commit()
    return True


async def maybe_seed() -> None:
    """Called on startup: seed only when the catalog is empty."""
    async with SessionLocal() as db:
        await seed(db)


async def _main() -> None:
    await init_db()
    async with SessionLocal() as db:
        seeded = await seed(db)
    print("Seeded demo catalog." if seeded else "Catalog already populated; nothing to do.")


if __name__ == "__main__":
    asyncio.run(_main())
