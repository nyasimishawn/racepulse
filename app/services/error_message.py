from __future__ import annotations


def safe_provider_error_message(
    error: Exception,
    *,
    operation: str,
) -> str:
    """Return an operator-safe provider error without request/cache details."""
    return f"{operation} failed ({type(error).__name__})."
