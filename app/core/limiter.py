"""Shared slowapi rate limiter.

Defined in its own module so both the app factory (``app.main``) and the
endpoints that decorate routes (``app.api.v1.endpoints.auth``, ``chat``,
``analysis``) import the same instance. Keyed by client IP.

Backed by Redis (``REDIS_URL``, already used for the JWT blocklist) rather
than slowapi's in-memory default: every deployment here runs multiple uvicorn
workers (2 in production, 4 in local Compose), and an in-memory counter is
per-process — with N workers the real limit is silently N× looser, and with
enough traffic spread across workers a "5/minute" limit may never trigger at
all. Redis makes the counter shared and the limit actually mean what it says.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.REDIS_URL)
