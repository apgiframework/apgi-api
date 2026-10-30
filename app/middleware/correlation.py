"""
Correlation-ID middleware (S2-CAT-03).

Reads ``X-APGI-Correlation-Id`` (accepting only a conservative token so it
cannot be used for log injection) or mints one, exposes it on
``request.state.correlation_id`` and a context variable, and echoes it on the
response so a client can quote it when reporting a failure.
"""

import contextvars
import re
import uuid
from typing import Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADER = "X-APGI-Correlation-Id"
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

correlation_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> Optional[str]:
    return correlation_id_context.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get(HEADER, "")
        cid = supplied if _VALID.match(supplied) else str(uuid.uuid4())
        request.state.correlation_id = cid
        token = correlation_id_context.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_context.reset(token)
        response.headers[HEADER] = cid
        return response
