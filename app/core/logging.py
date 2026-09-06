from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import settings


request_logger = logging.getLogger("racepulse.request")


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log safe request metadata without touching bodies or auth headers."""

    async def dispatch(
        self,
        request: Request,
        call_next,
    ) -> Response:
        request_id = _request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        started = time.perf_counter()
        response: Response | None = None

        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            request_logger.info(
                "request_completed %s",
                json.dumps(
                    {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": (
                            response.status_code
                            if response is not None
                            else 500
                        ),
                        "duration_ms": duration_ms,
                        "client_ip": (
                            _client_ip(request)
                        ),
                    },
                    separators=(",", ":"),
                ),
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id


def _request_id(value: str | None) -> str:
    if value is not None:
        normalized = value.strip()
        if normalized and len(normalized) <= 128 and normalized.isascii():
            return normalized
    return str(uuid4())


def _client_ip(request: Request) -> str | None:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            value = forwarded.split(",", maxsplit=1)[0].strip()
            if value:
                return value[:128]

    if request.client is not None:
        return request.client.host
    return None
