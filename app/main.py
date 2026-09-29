"""Cartify application: lifespan (schema + optional seed), static files, routers,
and HTML-friendly auth/authorization error handling."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import init_db
from app.dependencies import Forbidden, NotAuthenticated
from app.routers import admin, auth, cart, catalog, checkout, orders, webhooks, wishlist
from app.seed import maybe_seed
from app.web import static_files, templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.auto_seed:
        await maybe_seed()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", static_files, name="static")

app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(orders.router)
app.include_router(admin.router)
app.include_router(webhooks.router)
app.include_router(wishlist.router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness probe for platform health checks — no auth, no DB touch."""
    return {"status": "ok", "payments_mode": settings.payments_mode}


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> Response:
    """Anonymous users hitting a protected route are sent to the login page."""
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = "/login"
        return response
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"code": 403, "message": "You don't have access to that.", "user": None},
        status_code=403,
    )


@app.exception_handler(StarletteHTTPException)
async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 404, "message": "Page not found.", "user": None},
            status_code=404,
        )
    return JSONResponse(
        {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
    )
