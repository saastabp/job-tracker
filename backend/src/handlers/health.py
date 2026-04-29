"""GET /health — proves auth + IAM-authed RDS connectivity end-to-end."""
from __future__ import annotations

import json
from typing import Any

from common.auth import user_email, user_sub
from common.db import get_connection
from common.logger import logger


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    email = user_email(event)
    logger.info("health: enter", extra={"user_sub": sub})
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
        logger.info("health: exit ok", extra={"user_sub": sub, "db_row": row})
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(
                {"ok": True, "user_sub": sub, "user_email": email, "db": row}
            ),
        }
    except Exception:
        logger.exception("health: failed", extra={"user_sub": sub})
        raise