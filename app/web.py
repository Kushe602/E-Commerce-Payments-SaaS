"""Shared web plumbing: the Jinja2 template environment and static-files mount."""
from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def money(cents: int | None) -> str:
    """Format integer cents as a dollar string using integer math only.

    ``1999 -> "$19.99"``, ``500000 -> "$5,000.00"``. No floats touch the amount.
    """
    if cents is None:
        cents = 0
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["money"] = money
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["payments_mode"] = settings.payments_mode

static_files = StaticFiles(directory=str(STATIC_DIR))
