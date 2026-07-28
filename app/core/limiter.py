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

``in_memory_fallback_enabled`` is load-bearing, not decorative: it's what
happened WITHOUT it that forced adding it. Render's managed Redis needed a
connection detail this didn't originally account for, and every route
decorated with ``@limiter.limit()`` — including login and register — started
hard-failing with 500s in production the moment a rate-limit check touched
Redis. A rate limiter must never be able to take down auth. With the fallback,
a Redis hiccup degrades silently to a looser per-process limit instead of
crashing the request.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    in_memory_fallback_enabled=True,
)
