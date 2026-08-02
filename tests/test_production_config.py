"""El arranque en producción tiene que fallar ruidosamente ante una configuración
insegura o rota. Un fallo silencioso acá se descubre cuando un usuario no puede
recuperar su cuenta.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_BASE = {
    "APP_ENV": "production",
    "JWT_SECRET": "a" * 40,
    "DATABASE_URL": "postgresql+asyncpg://u:secreto@host/db",
    "DATABASE_URL_SYNC": "postgresql://u:secreto@host/db",
    "CORS_ORIGINS": "https://agrolytics.app",
    "PUBLIC_BASE_URL": "https://agrolytics.app",
}


def _settings(**over):
    return Settings(**{**_BASE, **over})


def test_a_sane_production_config_boots():
    assert _settings().is_production


def test_localhost_public_url_is_rejected():
    """Es el enlace que va dentro del correo de recuperación: con localhost, el
    usuario recibe un correo que se ve bien y no funciona."""
    with pytest.raises(ValidationError, match="localhost"):
        _settings(PUBLIC_BASE_URL="http://localhost:8001")


def test_public_url_must_be_https():
    with pytest.raises(ValidationError, match="https"):
        _settings(PUBLIC_BASE_URL="http://agrolytics.app")


def test_weak_jwt_secret_is_rejected():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(JWT_SECRET="change-me-in-production")


def test_default_database_password_is_rejected():
    with pytest.raises(ValidationError, match="postgres"):
        _settings(
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@db:5432/agrolytics",
            DATABASE_URL_SYNC="postgresql://postgres:postgres@db:5432/agrolytics",
        )


def test_wildcard_cors_is_rejected():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        _settings(CORS_ORIGINS="*")


def test_development_stays_permissive():
    """Estas reglas son sólo para producción: en desarrollo localhost es lo correcto."""
    dev = Settings(APP_ENV="development", PUBLIC_BASE_URL="http://localhost:8001")
    assert not dev.is_production


# ── Primer deploy: la plataforma sabe el dominio antes que nosotros ──

def test_render_url_is_adopted_when_unset(monkeypatch):
    """Huevo y gallina: no sabés el dominio hasta que el servicio existe, pero el
    servicio no arranca sin dominio. Render expone RENDER_EXTERNAL_URL."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://agrolytics-x7k2.onrender.com/")
    s = Settings(**{k: v for k, v in _BASE.items() if k != "PUBLIC_BASE_URL"})
    assert s.PUBLIC_BASE_URL == "https://agrolytics-x7k2.onrender.com"


def test_platform_url_is_added_to_cors(monkeypatch):
    """El frontend se sirve desde ese mismo origen: si no está permitido, la app
    responde pero no puede llamar a su propia API."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://agrolytics-x7k2.onrender.com")
    s = Settings(**{k: v for k, v in _BASE.items() if k != "PUBLIC_BASE_URL"})
    assert "https://agrolytics-x7k2.onrender.com" in s.cors_origins_list


def test_explicit_url_wins_over_the_platform(monkeypatch):
    """Con dominio propio configurado a mano, la plataforma no lo pisa."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://agrolytics-x7k2.onrender.com")
    s = _settings(PUBLIC_BASE_URL="https://agrolytics.app")
    assert s.PUBLIC_BASE_URL == "https://agrolytics.app"


def test_without_the_platform_var_localhost_still_fails(monkeypatch):
    """Fuera de Render el fail-fast sigue vigente: nadie despliega con localhost."""
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    with pytest.raises(ValidationError, match="localhost"):
        _settings(PUBLIC_BASE_URL="http://localhost:8001")
