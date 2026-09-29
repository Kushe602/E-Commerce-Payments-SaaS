"""FastAPI dependencies: resolve the current user from the session cookie and
enforce authentication / admin authorization."""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.security import COOKIE_NAME, decode_token


class NotAuthenticated(Exception):
    """No valid session cookie. A global handler redirects to ``/login`` (or
    sends an ``HX-Redirect`` for HTMX requests)."""


class Forbidden(Exception):
    """Authenticated but not permitted (e.g. a non-admin hitting ``/admin``)."""


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the signed-in user, or ``None`` for anonymous visitors."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = decode_token(token)
    if user_id is None:
        return None
    return await db.get(User, user_id)


async def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    """Require an authenticated user, else raise :class:`NotAuthenticated`."""
    if user is None:
        raise NotAuthenticated
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require an authenticated **admin**, else raise :class:`Forbidden`."""
    if not user.is_admin:
        raise Forbidden
    return user
