"""Cognito JWT claim accessors.

API Gateway's Cognito authorizer has already verified the token; the claims
are available on the event's request context. These helpers just pull them out.
"""
from typing import Any


def claims(event: dict[str, Any]) -> dict[str, Any]:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )


def user_sub(event: dict[str, Any]) -> str | None:
    return claims(event).get("sub")


def user_email(event: dict[str, Any]) -> str | None:
    return claims(event).get("email")