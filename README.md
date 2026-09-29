# Cartify

A single-store e-commerce application with a **real payments architecture** —
Stripe Checkout (test mode) plus a deterministic **fake** provider so the whole
app, its test suite, and a public demo run with **zero secrets and no network**.

Built with FastAPI, async SQLAlchemy 2.0, and server-rendered Jinja + HTMX.

[![CI](https://github.com/Kushe602/E-Commerce-Payments-SaaS/actions/workflows/ci.yml/badge.svg)](https://github.com/Kushe602/E-Commerce-Payments-SaaS/actions/workflows/ci.yml)

## Screenshot

<!-- Add a real screenshot here once deployed: it's the first thing recruiters look at. -->
<!-- ![Cartify storefront](docs/screenshot.png) -->

## Overview

Cartify is a compact but production-shaped online shop: shoppers browse a
catalog, manage a cart with live totals, check out, and pay; an admin manages
products and orders. Money is stored as integer **cents** end to end, orders are
created atomically from the cart, and stock is decremented only on confirmed
payment behind an oversell guard.

The interesting part is the **payments layer**. Two providers implement one tiny
interface, chosen at runtime from configuration:

- **Fake** (default) — no keys, no network. Deterministically simulates both a
  successful payment and a failed one. This is what CI and the hosted demo use.
- **Stripe** — real Stripe **test mode**: a hosted Checkout Session plus a
  signature-verified webhook that fulfils the order.

Because the routers depend only on this abstraction, the app boots and the full
suite passes with nothing configured.

## Features

- **Auth** — register / login / logout with bcrypt password hashing and a
  stateless JWT session in an httponly, SameSite cookie.
- **Catalog** — products (name, slug, description, price in cents, image, stock),
  categories, search, and category filtering.
- **Cart** — per-user, add / update / remove with live totals and an
  out-of-band nav badge via HTMX; quantities clamped to available stock.
- **Checkout + payments** — atomic order creation, provider hand-off, and
  fulfilment (mark paid + decrement stock) with an oversell guard.
- **Orders** — history list and detailed receipts with status (pending / paid /
  failed); receipts survive later catalog edits and deletions via snapshots.
- **Admin** — product CRUD and order management, gated by an `is_admin` check.

## Tech stack

| Concern        | Choice                                             |
| -------------- | -------------------------------------------------- |
| Web framework  | FastAPI + Uvicorn                                  |
| Data           | SQLAlchemy 2.0 (async) — SQLite locally, Postgres in Docker |
| Validation     | Pydantic v2 / pydantic-settings                    |
| Templates / UI | Jinja2 + HTMX + Tailwind (CDN)                      |
| Auth           | bcrypt + PyJWT (HS256), httponly session cookie    |
| Payments       | Stripe (test mode) with a fake provider fallback   |
| Tooling        | Ruff (lint), Pytest, Docker, GitHub Actions        |

## Payments architecture

The provider interface (`app/payments.py`) is two methods:

- `start_checkout(order, items, success_url, cancel_url) -> CheckoutStart`
- `parse_webhook(payload, signature) -> WebhookEvent`

`get_payment_provider()` returns the Stripe provider only when a
`STRIPE_SECRET_KEY` is set **and** fake mode isn't forced; otherwise the fake
provider. Nothing else in the app imports Stripe.

**Fake flow (default).** `POST /checkout` snapshots the cart into a *pending*
order and redirects to a local pay page (`/checkout/pay/{id}`) with two buttons:
pay, or simulate failure. Success fulfils the order; failure marks it failed.

**Stripe flow.** `start_checkout` creates a hosted Checkout Session (amounts in
integer cents) carrying `metadata.order_id`, and the browser is redirected there.
Stripe later calls `POST /webhooks/stripe`; the signature is verified with
`STRIPE_WEBHOOK_SECRET` before a `checkout.session.completed` event fulfils the
referenced order.

**Fulfilment is the source of truth for stock.** `fulfill_order` verifies every
line has sufficient stock *before* decrementing any (so a partial shortfall
changes nothing), is **idempotent** (a re-delivered webhook won't double-charge
stock), and takes `SELECT ... FOR UPDATE` row locks on Postgres to serialise
concurrent fulfilments. A pending order holds **no** inventory, so abandoned
checkouts never strand stock.

## Quickstart (fake mode, no keys)

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash);  use .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

python -m app.seed              # optional: load demo catalog + accounts
uvicorn app.main:app --reload
```

Open http://localhost:8000. The app auto-creates the schema and (by default)
seeds a demo catalog on first run.

### Demo accounts

Seeded by `python -m app.seed` (and on startup when `AUTO_SEED=true`):

| Role  | Email               | Password     |
| ----- | ------------------- | ------------ |
| Admin | `admin@cartify.dev` | `admin12345` |
| User  | `demo@cartify.dev`  | `demo12345`  |

## Real Stripe test mode (optional)

Set test keys and leave `FORCE_FAKE_PAYMENTS` off:

```bash
export STRIPE_SECRET_KEY=sk_test_...
export STRIPE_PUBLISHABLE_KEY=pk_test_...
export STRIPE_WEBHOOK_SECRET=whsec_...        # from `stripe listen`
```

Forward webhooks locally with the Stripe CLI:

```bash
stripe listen --forward-to localhost:8000/webhooks/stripe
```

Use Stripe's test cards (e.g. `4242 4242 4242 4242`). Only ever use **test**
keys with this project.

## Run with Docker Compose (web + Postgres)

```bash
docker compose up --build
```

This starts Postgres (with a healthcheck) and the app on
http://localhost:8000, wired to the async `asyncpg` driver and pinned to fake
payments — no secrets required.

## Testing & linting

```bash
ruff check .
pytest -q
```

The suite (Starlette `TestClient` over the async app, isolated SQLite schema per
test) covers auth, cart math, the fake-payment success **and** failure paths,
stock decrement, the oversell guard, webhook fulfilment (including idempotency),
and admin authorization (anonymous redirect + non-admin `403`).

## Deployment (Render)

`render.yaml` is a Blueprint: Docker runtime, free plan, health check at
`/healthz`, a generated `SECRET_KEY`, `COOKIE_SECURE=true` (Render provides TLS),
and `FORCE_FAKE_PAYMENTS=true`. Render injects `$PORT`, which the Dockerfile CMD
honours.

## Project structure

```
app/
  main.py          # app factory, lifespan (schema + seed), error handlers
  config.py        # pydantic-settings; payments_mode property
  database.py      # async engine, session factory, Base
  models.py        # User, Category, Product, Cart(+Item), Order(+Item)
  security.py      # bcrypt hashing + JWT session tokens
  dependencies.py  # current-user / admin guards
  payments.py      # Fake + Stripe providers behind one interface
  services.py      # cart & order logic, fulfilment + oversell guard
  routers/         # auth, catalog, cart, checkout, orders, admin, webhooks
  templates/       # Jinja2 (server-rendered) + HTMX partials
  seed.py          # idempotent demo data
tests/             # pytest suite (fake-payments mode, no secrets)
```

## Security notes

- **Card data never touches this app** — Stripe hosts the payment form; we store
  only an order id and a provider reference.
- **Webhooks are verified** on the real path via `STRIPE_WEBHOOK_SECRET`; an
  unsigned/invalid signature returns `400` and changes nothing. (The unsigned
  fake path exists only for local dev and CI, and is unreachable in Stripe mode.)
- **All mutating routes require authentication**; admin routes additionally
  require the `is_admin` role.
- **Passwords** are bcrypt-hashed; sessions are stateless JWTs in an httponly,
  SameSite=Lax cookie, with `Secure` enabled in production (`COOKIE_SECURE`).
- **No raw SQL** — all access goes through the SQLAlchemy ORM; input is parsed
  and validated (prices via `Decimal` into integer cents, never floats).
- **Secrets come from the environment** (`SECRET_KEY`, Stripe keys) and are never
  committed; the in-repo default `SECRET_KEY` is a dev placeholder — override it
  in every real deployment.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Kushe Jawadwala.
