"""Adds X-APGI-Release-State and X-APGI-Platform-Release to every response (S5-CARD-04)."""

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app import __version__
from app.services.identifiers import load_identifiers


class ReleaseHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._release_state = str(load_identifiers()["release_state"]["current"])

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-APGI-Release-State"] = self._release_state
        response.headers["X-APGI-Platform-Release"] = __version__
        return response
