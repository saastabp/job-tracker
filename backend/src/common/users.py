"""Helpers for resolving the local ``users.id`` from a Cognito sub.

Every authenticated request carries the Cognito sub via the JWT claims, but
domain queries are keyed by the local ``users.id`` integer. The
post-confirmation trigger guarantees a row exists for any verified user, so a
missing row here is a hard error worth raising.
"""
from __future__ import annotations

from typing import Any

from common.logger import logger


class UserNotFoundError(LookupError):
    """No ``users`` row matches the given Cognito sub."""


def get_user_id(conn: Any, cognito_sub: str) -> int:
    """Resolve a Cognito sub to its local ``users.id``.

    Parameters
    ----------
    conn : pymysql connection
        An open DB connection.
    cognito_sub : str
        The Cognito user sub from the JWT claims.

    Returns
    -------
    int
        The local users.id.

    Raises
    ------
    UserNotFoundError
        If no users row exists for the given sub.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE cognito_sub = %s AND deleted_at IS NULL",
            (cognito_sub,),
        )
        row = cur.fetchone()
    if not row:
        logger.error("users: no row for cognito_sub", extra={"user_sub": cognito_sub})
        raise UserNotFoundError(f"no users row for cognito_sub={cognito_sub}")
    return int(row["id"])