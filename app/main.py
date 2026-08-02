"""Agrolytics FastAPI application factory."""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import setup_logging

# Ensure log directory exists before setting up loguru file sink
os.makedirs("logs", exist_ok=True)
setup_logging()

# ── Error tracking (Sentry) — only active when a DSN is configured ──────────────
if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        traces_sample_rate=0.1,
    )

# Hide interactive docs in production unless explicitly enabled.
_docs_on = settings.DOCS_ENABLED and not settings.is_production

app = FastAPI(
    title="Agrolytics API",
    description=(
        "Agricultural intelligence platform — satellite-derived spectral indices, "
        "management-zone mapping, stress alerts, prescriptions and yield estimates."
    ),
    version="1.0.0",
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
)

# ── Rate limiting (auth endpoints) ──────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Global exception handler ────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for uncaught errors.

    Logs the full traceback server-side (for Sentry/loguru) but returns an opaque
    500 to the client so internal details and stack traces never leak. Expected
    errors (HTTPException, RateLimitExceeded, validation) keep their own handlers.
    """
    logger.exception("Unhandled error on {} {}", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


# ── Security headers ────────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach standard hardening headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")

# ── Static files / frontend ───────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


# media_type explícito: sin él FastAPI anuncia `application/json` para estas
# páginas. Como además mandamos `X-Content-Type-Options: nosniff`, el navegador no
# puede corregirlo por su cuenta y termina decodificando el HTML con la codificación
# equivocada: los emoji del mensaje de WhatsApp llegaban como U+FFFD (�) y se
# enviaban rotos al productor. El archivo en disco siempre estuvo bien; lo que
# faltaba era declarar cómo leerlo.
_HTML = "text/html; charset=utf-8"


@app.get("/", include_in_schema=False)
async def serve_dashboard() -> FileResponse:
    return FileResponse("static/index.html", media_type=_HTML)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Liveness probe — returns 200 if the API is up."""
    return {"status": "ok", "env": settings.APP_ENV}


@app.get("/env.js", include_in_schema=False)
async def public_env() -> Response:
    """Expose ONLY public front-end config (no secrets) as a JS global.

    Lets the static frontend know the analytics keys without baking them in.
    """
    import json
    cfg = json.dumps({
        "posthog_key": settings.POSTHOG_KEY,
        "posthog_host": settings.POSTHOG_HOST,
        "env": settings.APP_ENV,
    })
    return Response(content=f"window.AGV_CONFIG={cfg};", media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


@app.get("/privacy", include_in_schema=False)
async def privacy_page() -> FileResponse:
    return FileResponse("static/privacy.html", media_type=_HTML)


@app.get("/terms", include_in_schema=False)
async def terms_page() -> FileResponse:
    return FileResponse("static/terms.html", media_type=_HTML)
