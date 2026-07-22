from typing import Any

from pydantic import BaseModel


class ApiErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ApiErrorResponse