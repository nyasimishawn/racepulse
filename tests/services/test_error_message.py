from app.services.error_message import safe_provider_error_message


def test_provider_error_messages_do_not_persist_secret_or_cache_details() -> None:
    error = RuntimeError(
        "GET https://provider.invalid?token=secret-value "
        "failed at data/fastf1-cache/private.sqlite"
    )

    message = safe_provider_error_message(
        error,
        operation="FastF1 session import",
    )

    assert message == "FastF1 session import failed (RuntimeError)."
    assert "secret-value" not in message
    assert "fastf1-cache" not in message
