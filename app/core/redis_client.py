"""Async Redis client and JWT revocation (blocklist) helpers.

Reuses the existing ``REDIS_URL`` (already used as the Celery broker). Tokens are
revoked by storing their ``jti`` (minted in ``app.core.security``) with a TTL equal
to the token's remaining lifetime, so the blocklist self-expires.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from loguru import logger

from app.core.config import settings

_BLOCKLIST_PREFIX = "revoked_jti:"

# Lazily-created module-level client (connection pool is managed by redis-py).
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return a shared async Redis client."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def revoke_jti(jti: str, ttl_seconds: int) -> None:
    """Add *jti* to the blocklist for *ttl_seconds* (no-op if Redis is down)."""
    if ttl_seconds <= 0:
        return
    try:
        await get_redis().set(f"{_BLOCKLIST_PREFIX}{jti}", "1", ex=ttl_seconds)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Could not revoke token {jti}: {exc}")


async def is_jti_revoked(jti: str) -> bool:
    """Return True if *jti* is in the blocklist. Fails open if Redis is down."""
    try:
        return await get_redis().exists(f"{_BLOCKLIST_PREFIX}{jti}") == 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Blocklist check failed for {jti}: {exc}")
        return False
