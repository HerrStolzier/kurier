"""Optional API key authentication for non-localhost access."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


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
            import hmac

            provided = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, self.api_key):
                raise HTTPException(status_code=401, detail="API key required")
        elif self.localhost_only:
            raise HTTPException(status_code=403, detail="Access restricted to localhost")

        return await call_next(request)
