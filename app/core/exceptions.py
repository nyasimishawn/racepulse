from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from collections.abc import Mapping


logger = logging.getLogger(__name__)


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        },
        headers=headers,
    )


async def handle_http_exception(
    request: Request,
    error: StarletteHTTPException,
) -> ORJSONResponse:
    detail = error.detail

    message = (
        detail
        if isinstance(detail, str)
        else "The request could not be completed."
    )

    details = (
        detail
        if isinstance(detail, (dict, list))
        else None
    )

    return _error_response(
        status_code=error.status_code,
        code=f"HTTP_{error.status_code}",
        message=message,
        details=details,
        headers=error.headers,
    )


async def handle_validation_exception(
    request: Request,
    error: RequestValidationError,
) -> ORJSONResponse:
    details = [
        {
            "location": ".".join(
                str(item)
                for item in validation_error["loc"]
            ),
            "message": validation_error["msg"],
            "type": validation_error["type"],
        }
        for validation_error in error.errors()
    ]

    return _error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="REQUEST_VALIDATION_FAILED",
        message="One or more request values are invalid.",
        details=details,
    )


async def handle_database_exception(
    request: Request,
    error: SQLAlchemyError,
) -> ORJSONResponse:
    logger.error(
        "Database request failed path=%s error_type=%s",
        request.url.path,
        type(error).__name__,
    )

    return _error_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="DATABASE_UNAVAILABLE",
        message="The database is temporarily unavailable.",
    )


async def handle_unexpected_exception(
    request: Request,
    error: Exception,
) -> ORJSONResponse:
    logger.error(
        "Unexpected request failure path=%s error_type=%s",
        request.url.path,
        type(error).__name__,
    )

    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected server error occurred.",
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        StarletteHTTPException,
        handle_http_exception,
    )
    app.add_exception_handler(
        RequestValidationError,
        handle_validation_exception,
    )
    app.add_exception_handler(
        SQLAlchemyError,
        handle_database_exception,
    )
    app.add_exception_handler(
        Exception,
        handle_unexpected_exception,
    )
