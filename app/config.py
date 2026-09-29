"""Application settings, loaded from the environment / .env via pydantic-settings.

Payments default to a deterministic *fake* provider so the app (and CI, and a
free public demo) runs with zero third-party keys. Set ``STRIPE_SECRET_KEY`` to
switch to real Stripe test mode; set ``FORCE_FAKE_PAYMENTS=true`` to pin the fake
provider even when a key happens to be present (used by the hosted demo and the
test suite).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Cartify"

    # Signs the JWT session cookie. Override in every real deployment.
    secret_key: str = "dev-secret-change-me-to-a-long-random-value-32bytes"

    # Default is local SQLite; docker-compose / Postgres override via env.
    database_url: str = "sqlite+aiosqlite:///./cartify.db"

    # JWT / session-cookie lifetime.
    session_days: int = 7

    # Send the session cookie only over HTTPS. Leave False for plain-http local
    # dev; set COOKIE_SECURE=true behind TLS in production.
    cookie_secure: bool = False

    # Create the schema and load demo products on startup when the catalog is
    # empty. Handy for the one-click demo; tests disable it (AUTO_SEED=false).
    auto_seed: bool = True

    # --- Payments -------------------------------------------------------------
    # Unset STRIPE_SECRET_KEY (the default) → the fake provider is used.
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    # Verifies webhook signatures on the real Stripe path. Required in Stripe mode.
    stripe_webhook_secret: str | None = None
    # Pin the fake provider even if a Stripe key is configured.
    force_fake_payments: bool = False
    # Optional absolute base URL for building Stripe return URLs. When unset the
    # app derives it from the incoming request.
    base_url: str | None = None

    @property
    def payments_mode(self) -> str:
        """``"stripe"`` only when a secret key is set and fake mode isn't forced."""
        if self.force_fake_payments or not self.stripe_secret_key:
            return "fake"
        return "stripe"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
