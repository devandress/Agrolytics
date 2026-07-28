"""Authentication endpoints: register, login, refresh, logout."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, oauth2_scheme
from app.core.config import settings
from app.core.limiter import limiter
from app.core.redis_client import is_jti_revoked, revoke_jti
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.token import Token, TokenRefresh
from app.schemas.user import (
    ChangePassword,
    ForgotPassword,
    ResetPassword,
    UserCreate,
    UserLogin,
    UserOut,
    UserUpdate,
)
from app.services.email import send_password_reset_email

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def register(request: Request, payload: UserCreate, db: DBSession) -> UserOut:
    """Create a new user account.

    Raises 409 if the e-mail is already registered.
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user  # type: ignore[return-value]


@router.post("/login", response_model=Token)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def login(request: Request, payload: UserLogin, db: DBSession) -> Token:
    """Authenticate user and return access + refresh tokens."""
    if not payload.email or not payload.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password are required.",
        )

    result = await db.execute(select(User).where(User.email == payload.email))
    user: User | None = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    return Token(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        token_type="bearer",
    )


@router.post("/refresh", response_model=Token)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def refresh(request: Request, payload: TokenRefresh, db: DBSession) -> Token:
    """Issue a new access token given a valid refresh token."""
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise ValueError("Not a refresh token.")
        user_id: str = data["sub"]
    except (JWTError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido o expirado.",
        ) from exc

    return Token(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )


@router.get("/me", response_model=UserOut)
async def get_me(current_user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile."""
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(payload: UserUpdate, current_user: CurrentUser, db: DBSession) -> UserOut:
    """Update the user's own profile, preferences and notification settings."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()[:255]
    if payload.preferences is not None:
        current_user.preferences = payload.preferences
    if payload.notifications is not None:
        current_user.notifications = payload.notifications
    await db.commit()
    await db.refresh(current_user)
    return current_user  # type: ignore[return-value]


@router.post("/change-password")
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def change_password(
    request: Request, payload: ChangePassword, current_user: CurrentUser, db: DBSession
) -> dict:
    """Change the current user's password (requires the current password)."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="La contraseña actual es incorrecta.")
    current_user.hashed_password = hash_password(payload.new_password)
    await db.commit()
    return {"detail": "Contraseña actualizada."}


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def forgot_password(request: Request, payload: ForgotPassword, db: DBSession) -> dict:
    """Request a password reset link. Always returns the same generic message —
    never reveals whether the email is registered (enumeration protection)."""
    generic = {"detail": "Si el correo existe, te enviamos un enlace para restablecer tu contraseña."}
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user:
        return generic

    token = create_password_reset_token(str(user.id))
    reset_link = f"{settings.PUBLIC_BASE_URL}/?reset_token={token}"
    send_password_reset_email(user.email, reset_link)
    return generic


@router.post("/reset-password", status_code=status.HTTP_200_OK)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def reset_password(request: Request, payload: ResetPassword, db: DBSession) -> dict:
    """Consume a password-reset token (from the emailed link) and set a new password."""
    invalid = HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Enlace inválido o vencido. Solicita uno nuevo.")
    try:
        data = decode_token(payload.token)
    except JWTError:
        raise invalid from None
    if data.get("type") != "password_reset":
        raise invalid
    jti = data.get("jti")
    if jti and await is_jti_revoked(jti):
        raise invalid  # already used

    try:
        user_id = uuid.UUID(str(data.get("sub")))
    except ValueError:
        raise invalid from None
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise invalid

    user.hashed_password = hash_password(payload.new_password)
    await db.commit()

    if jti and data.get("exp"):
        ttl = int(data["exp"] - datetime.now(UTC).timestamp())
        await revoke_jti(jti, ttl)  # single-use: can't replay this link
    return {"detail": "Contraseña actualizada. Ya podés iniciar sesión."}


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(token: str = Depends(oauth2_scheme)) -> dict:
    """Revoke the current access token by blocklisting its JTI until it expires."""
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.") from None

    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        ttl = int(exp - datetime.now(UTC).timestamp())
        await revoke_jti(jti, ttl)
    return {"detail": "Logged out."}
