"""JWT token creation / validation and password hashing utilities."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# Bcrypt context for password hashing with automatic scheme upgrade
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return bcrypt hash of *plain* password. Uses cost factor 12 by default."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*; also upgrades hash if needed."""
    return pwd_context.verify(plain, hashed)


def _create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    """Create JWT token with expiration and unique JTI (JWT ID) for revocation."""
    payload = data.copy()
    payload["exp"] = datetime.now(UTC) + expires_delta
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str) -> str:
    """Create short-lived access token (default 30 minutes)."""
    return _create_token(
        {"sub": subject, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(subject: str) -> str:
    """Create long-lived refresh token for obtaining new access tokens (default 7 days)."""
    return _create_token(
        {"sub": subject, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def create_password_reset_token(subject: str) -> str:
    """Short-lived, single-purpose token for the forgot-password flow.

    Deliberately short (30 min) and its own ``type`` so it can never be reused
    as an access/refresh token even if leaked, and gets revoked (blocklisted by
    jti) the moment it's consumed — see auth.reset_password.
    """
    return _create_token({"sub": subject, "type": "password_reset"}, timedelta(minutes=30))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT token; raises JWTError if invalid or expired."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
