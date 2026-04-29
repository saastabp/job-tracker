"""Unit tests for ``handlers/post_confirmation.py``.

Cognito invokes this trigger after email verification or federation. The
handler MUST return the event unchanged on success (Cognito treats any
exception as a sign-up failure and leaves the user unverified), and must
upsert a ``users`` row keyed by Cognito sub.
"""
from __future__ import annotations

import pytest


def _cognito_event(*, sub: str | None = "abc-123", email: str | None = "x@y.z") -> dict:
    user_attrs: dict = {}
    if sub is not None:
        user_attrs["sub"] = sub
    if email is not None:
        user_attrs["email"] = email
    return {
        "userName": sub or "",
        "request": {"userAttributes": user_attrs},
        "response": {},
    }


def test_post_confirmation_inserts_user(mocker, patched_conn, mock_cursor, lambda_ctx):
    from handlers import post_confirmation

    conn = patched_conn("handlers.post_confirmation")

    event = _cognito_event(sub="abc-123", email="x@y.z")
    result = post_confirmation.handler(event, lambda_ctx)

    # Cognito requires the event to be returned unchanged on success.
    assert result is event

    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args.args
    assert "INSERT INTO users" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params == ("abc-123", "x@y.z")
    conn.commit.assert_called_once()


def test_post_confirmation_falls_back_to_username(
    mocker, patched_conn, mock_cursor, lambda_ctx,
):
    """If userAttributes.sub is absent, the handler uses event.userName."""
    from handlers import post_confirmation

    patched_conn("handlers.post_confirmation")

    event = _cognito_event(sub=None, email="x@y.z")
    event["userName"] = "fallback-sub"

    result = post_confirmation.handler(event, lambda_ctx)

    assert result is event
    params = mock_cursor.execute.call_args.args[1]
    assert params == ("fallback-sub", "x@y.z")


def test_post_confirmation_missing_email_raises(mocker, patched_conn, lambda_ctx):
    """Email is required — the handler must raise so Cognito surfaces the failure."""
    from handlers import post_confirmation

    patched_conn("handlers.post_confirmation")

    event = _cognito_event(sub="abc-123", email=None)
    with pytest.raises(ValueError, match="missing sub or email"):
        post_confirmation.handler(event, lambda_ctx)


def test_post_confirmation_missing_sub_raises(mocker, patched_conn, lambda_ctx):
    from handlers import post_confirmation

    patched_conn("handlers.post_confirmation")

    event = _cognito_event(sub=None, email="x@y.z")
    event["userName"] = ""  # also no fallback
    with pytest.raises(ValueError, match="missing sub or email"):
        post_confirmation.handler(event, lambda_ctx)