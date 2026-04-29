"""Cognito post-confirmation trigger — creates a ``users`` row for new sign-ups.

Cognito invokes this after a user verifies their email or completes federation.
The trigger MUST return the event unchanged or Cognito treats the sign-up as
failed and the user is left in an unverified state.

Notes
-----
The insert is idempotent (``ON DUPLICATE KEY UPDATE``) so re-runs (e.g. a user
who deletes and re-creates their account with the same Cognito sub, or a
Cognito retry) do not error.
"""
from __future__ import annotations

from typing import Any

from common.db import get_connection
from common.logger import logger


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    user_attrs = event.get("request", {}).get("userAttributes", {})
    sub = user_attrs.get("sub") or event.get("userName")
    email = user_attrs.get("email")
    logger.info("post_confirmation: enter", extra={"user_sub": sub, "email": email})

    if not sub or not email:
        logger.error(
            "post_confirmation: missing sub or email in event",
            extra={"user_attrs_keys": list(user_attrs.keys())},
        )
        raise ValueError("Cognito event missing sub or email")

    try:
        with get_connection() as conn, conn.cursor() as cur:
            logger.info("post_confirmation: upserting users row", extra={"user_sub": sub})
            cur.execute(
                "INSERT INTO users (cognito_sub, email) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE email = VALUES(email), updated_at = CURRENT_TIMESTAMP",
                (sub, email),
            )
            conn.commit()
        logger.info("post_confirmation: exit ok", extra={"user_sub": sub})
    except Exception:
        logger.exception("post_confirmation: db write failed", extra={"user_sub": sub})
        raise

    return event