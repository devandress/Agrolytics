"""Shared slowapi rate limiter.

Defined in its own module so both the app factory (``app.main``) and the
endpoints that decorate routes (``app.api.v1.endpoints.auth``) import the same
instance. Keyed by client IP.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
