"""User request / response schemas."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, field_validator


def _validate_password(v: str) -> str:
    """Shared password strength rule: 8+ chars and contains letters."""
    if len(v) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    if not re.search(r"[A-Za-z]", v):
        raise ValueError("La contraseña debe contener letras.")
    return v


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: Literal["farmer", "admin"] = "farmer"

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _validate_password(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not v or len(v) > 255:
            raise ValueError("Invalid email address.")
        return v.lower()


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ChangePassword(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def strong(cls, v: str) -> str:
        return _validate_password(v)


class ForgotPassword(BaseModel):
    email: EmailStr


class ResetPassword(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def strong(cls, v: str) -> str:
        return _validate_password(v)


class UserUpdate(BaseModel):
    """Self-service profile/preferences update."""
    full_name: str | None = None
    preferences: dict[str, Any] | None = None
    notifications: dict[str, Any] | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    full_name: str | None = None
    plan: str = "free"
    preferences: dict[str, Any] | None = None
    notifications: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

