"""Local API protections."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

CSRF_COOKIE_NAME = "kurier_csrf"
CSRF_HEADER_NAME = "x-kurier-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def generate_csrf_token() -> str:
    """Create a per-process CSRF token for browser/form requests."""
    return secrets.token_urlsafe(32)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Block or authenticate non-localhost requests.

    Behaviour:
    - Localhost (127.0.0.1 / ::1) is always allowed without a key.
    - Non-localhost with ``api_key`` set: require matching ``x-api-key`` header.
    - Non-localhost without ``api_key`` and ``localhost_only=True``: return 403.
    - Non-localhost without ``api_key`` and ``localhost_only=False``: allow through.
    """

    def __init__(
        self,
        app: object,
        api_key: str | None = None,
        localhost_only: bool = True,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.api_key = api_key
        self.localhost_only = localhost_only

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Localhost always allowed
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)

        # Non-localhost: check API key if configured
        if self.api_key:
            provided = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, self.api_key):
                raise HTTPException(status_code=401, detail="API key required")
        elif self.localhost_only:
            raise HTTPException(status_code=403, detail="Access restricted to localhost")

        return await call_next(request)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Require a CSRF header for local browser-style mutations.

    API-key clients stay simple: a valid ``x-api-key`` bypasses CSRF. Local
    dashboard/form clients use a same-site cookie plus ``x-kurier-csrf`` header.
    """

    def __init__(
        self,
        app: object,
        token: str,
        api_key: str | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.token = token
        self.api_key = api_key

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method not in SAFE_METHODS and not self._is_allowed(request):
            return JSONResponse(
                {"detail": "CSRF token required"},
                status_code=403,
            )

        response = await call_next(request)
        if request.method in SAFE_METHODS:
            response.set_cookie(
                CSRF_COOKIE_NAME,
                self.token,
                httponly=False,
                samesite="strict",
            )
        return response

    def _is_allowed(self, request: Request) -> bool:
        if self.api_key:
            provided_api_key = request.headers.get("x-api-key", "")
            if hmac.compare_digest(provided_api_key, self.api_key):
                return True

        provided_csrf = request.headers.get(CSRF_HEADER_NAME, "")
        cookie_csrf = request.cookies.get(CSRF_COOKIE_NAME, "")
        return (
            bool(provided_csrf)
            and bool(cookie_csrf)
            and hmac.compare_digest(provided_csrf, self.token)
            and hmac.compare_digest(cookie_csrf, self.token)
        )
