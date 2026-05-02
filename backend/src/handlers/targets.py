"""Targets endpoints — catalog + per-user goal counts + follow-up cadence.

GET /targets
    Returns the user-tunable target_types catalog joined with the caller's
    targets, plus the caller's follow-up cadence. Shape::

        {
          "types": [
            {"id": 1, "short_name": "submissions", "description": "Submissions",
             "daily": 5, "weekly": 25},
            ...
          ],
          "follow_up_days": 7
        }

    ``daily`` / ``weekly`` are integers when the user has set a goal, ``null``
    otherwise. The ``follow_ups`` catalog row is excluded — follow-ups are
    reactive (queued by prior submissions hitting their due date), not a
    user-set daily/weekly goal.

PUT /targets
    Upserts the caller's goals and (optionally) follow-up cadence. Body::

        {
          "goals": [{"target_type_id": 1, "cadence": "daily", "goal_count": 5}, ...],
          "follow_up_days": 7
        }

    Each goals entry is upserted on ``(user_id, target_type_id, cadence)``.
    ``follow_up_days`` is optional; when present it updates ``users.follow_up_days``
    and only affects **new** submissions — already-scheduled follow-ups keep
    their original ``due_at``.
"""
from __future__ import annotations

import json
from typing import Any

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.users import get_user_id

ALLOWED_CADENCES = ("daily", "weekly")


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _get(conn: Any, user_id: int) -> dict[str, Any]:
    logger.info("targets.get: querying catalog + goals", extra={"user_id": user_id})
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                tt.id          AS id,
                tt.short_name  AS short_name,
                tt.description AS description,
                MAX(CASE WHEN t.cadence = 'daily'  THEN t.goal_count END) AS daily,
                MAX(CASE WHEN t.cadence = 'weekly' THEN t.goal_count END) AS weekly
            FROM target_types tt
            LEFT JOIN targets t
                ON t.target_type_id = tt.id
                AND t.user_id = %s
                AND t.deleted_at IS NULL
            WHERE tt.deleted_at IS NULL
              AND tt.short_name <> 'follow_ups'
            GROUP BY tt.id, tt.short_name, tt.description
            ORDER BY tt.id
            """,
            (user_id,),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT follow_up_days FROM users WHERE id = %s",
            (user_id,),
        )
        user_row = cur.fetchone()
    types = [
        {
            "id": int(r["id"]),
            "short_name": r["short_name"],
            "description": r["description"],
            "daily": int(r["daily"]) if r["daily"] is not None else None,
            "weekly": int(r["weekly"]) if r["weekly"] is not None else None,
        }
        for r in rows
    ]
    follow_up_days = int(user_row["follow_up_days"]) if user_row else 7
    logger.info(
        "targets.get: returning",
        extra={"type_count": len(types), "follow_up_days": follow_up_days},
    )
    return {"types": types, "follow_up_days": follow_up_days}


def _put(conn: Any, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    goals = body.get("goals", [])
    if not isinstance(goals, list):
        raise ValueError("body.goals must be a list")

    follow_up_days_raw = body.get("follow_up_days")
    follow_up_days: int | None = None
    if follow_up_days_raw is not None:
        try:
            follow_up_days = int(follow_up_days_raw)
        except (TypeError, ValueError) as e:
            raise ValueError("follow_up_days must be a non-negative integer") from e
        if follow_up_days < 0:
            raise ValueError("follow_up_days must be a non-negative integer")

    logger.info(
        "targets.put: upserting",
        extra={
            "user_id": user_id,
            "goal_count": len(goals),
            "follow_up_days": follow_up_days,
        },
    )

    with conn.cursor() as cur:
        for entry in goals:
            target_type_id = int(entry["target_type_id"])
            cadence = entry["cadence"]
            goal_count = int(entry["goal_count"])
            if cadence not in ALLOWED_CADENCES:
                raise ValueError(f"cadence must be one of {ALLOWED_CADENCES}")
            if goal_count < 0:
                raise ValueError("goal_count must be >= 0")
            cur.execute(
                """
                INSERT INTO targets (user_id, target_type_id, cadence, goal_count)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    goal_count = VALUES(goal_count),
                    updated_at = CURRENT_TIMESTAMP,
                    deleted_at = NULL
                """,
                (user_id, target_type_id, cadence, goal_count),
            )
        if follow_up_days is not None:
            cur.execute(
                "UPDATE users SET follow_up_days = %s WHERE id = %s",
                (follow_up_days, user_id),
            )
    conn.commit()
    logger.info("targets.put: committed", extra={"user_id": user_id})
    return _get(conn, user_id)


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    logger.info("targets: enter", extra={"user_sub": sub, "method": method})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if method == "GET":
                result = _get(conn, user_id)
            elif method == "PUT":
                raw = event.get("body") or "{}"
                body = json.loads(raw) if isinstance(raw, str) else raw
                result = _put(conn, user_id, body)
            else:
                logger.warning("targets: unsupported method", extra={"method": method})
                return _response(405, {"error": f"method {method} not allowed"})
        logger.info("targets: exit ok", extra={"user_sub": sub, "method": method})
        return _response(200, result)
    except ValueError as e:
        logger.exception("targets: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception("targets: failed", extra={"user_sub": sub, "method": method})
        raise