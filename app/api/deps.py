"""FastAPI dependency helpers: DB session, current user, admin guard."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import is_jti_revoked
from app.core.security import decode_token
from app.db.session import get_async_session
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

DBSession = Annotated[AsyncSession, Depends(get_async_session)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DBSession,
) -> User:
    """Validate access token and return the authenticated User row."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exc
        # Parse the subject as UUID here so a malformed/empty `sub` yields a clean
        # 401 instead of an unhandled ValueError (500) further down.
        user_uuid = uuid.UUID(str(payload.get("sub")))
    except (JWTError, ValueError):
        raise credentials_exc from None

    # Reject tokens that have been explicitly logged out (revoked).
    jti = payload.get("jti")
    if jti and await is_jti_revoked(jti):
        raise credentials_exc

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exc
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(current_user: CurrentUser) -> User:
    """Raise 403 if the current user is not an admin."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]

# HTTP 402 Payment Required — used to signal "upgrade your plan".
PLAN_REQUIRED = 402


def require_ai_plan(current_user: CurrentUser) -> User:
    """Gate AI features to plans that allow them (Free has no AI)."""
    from app.services.plans import plan_allows_ai
    if not plan_allows_ai(current_user.plan):
        raise HTTPException(
            status_code=PLAN_REQUIRED,
            detail="El asistente IA está disponible desde el plan Productor. Mejora tu plan para usarlo.",
        )
    return current_user


AiUser = Annotated[User, Depends(require_ai_plan)]
