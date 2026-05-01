"""Follow-up CRUD endpoints + EventBridge Scheduler integration.

Routes
------
GET    /follow-ups                          — list all (filter ``?pending=1``)
POST   /submissions/{id}/follow-ups         — create a manual follow-up
PUT    /follow-ups/{id}                     — edit due_at / notes / actioned
DELETE /follow-ups/{id}                     — soft-delete

Scheduling side-effects
-----------------------
Every mutation that affects ``due_at`` or ``actioned_at`` reaches into the
scheduler stack to register or cancel a one-shot EventBridge schedule named
``followup-<id>``. Calls to ``common.scheduler`` swallow errors and log; the
DB row is the source of truth, the schedule is a side-effect that fails
softly (so a missing scheduler stack still allows manual follow-up CRUD).

Auto-creation on submission create lives in ``submissions.py``, not here —
the auto-create path runs inside the same transaction as the submission
INSERT, so co-locating it with that handler is simpler than a cross-handler
import.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.scheduler import cancel_followup, schedule_followup
from common.users import get_user_id


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _path_id(event: dict[str, Any], key: str = "id") -> int:
    raw = (event.get("pathParameters") or {}).get(key)
    if raw is None:
        raise ValueError(f"missing path parameter: {key}")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {key}: {raw!r}")


def _parse_due_at(raw: Any) -> datetime:
    """Accept ISO-8601 with or without trailing 'Z'; reject anything else."""
    if not raw or not isinstance(raw, str):
        raise ValueError("due_at is required")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"invalid due_at: {raw!r}")


def _verify_submission(conn: Any, user_id: int, submission_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM submissions "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (submission_id, user_id),
        )
        if not cur.fetchone():
            raise LookupError("submission not found")


def _load_owned(conn: Any, user_id: int, follow_up_id: int) -> dict[str, Any]:
    """Fetch a follow-up row, verifying it belongs to the caller via the parent submission."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                f.id, f.submission_id, f.due_at, f.actioned_at, f.notified_at,
                f.notes, f.auto_created
            FROM follow_ups f
            JOIN submissions s ON s.id = f.submission_id
            WHERE f.id = %s AND s.user_id = %s
              AND f.deleted_at IS NULL AND s.deleted_at IS NULL
            """,
            (follow_up_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError("follow-up not found")
    return row


def _row_to_followup(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "submission_id": int(row["submission_id"]),
        "due_at": str(row["due_at"]) if row["due_at"] else None,
        "actioned_at": str(row["actioned_at"]) if row["actioned_at"] else None,
        "notified_at": str(row["notified_at"]) if row.get("notified_at") else None,
        "notes": row.get("notes"),
        "auto_created": bool(row.get("auto_created")),
        "role_title": row.get("role_title"),
        "company_name": row.get("company_name"),
        "status": row.get("status"),
    }


def _list(conn: Any, user_id: int, qs: dict[str, str]) -> list[dict[str, Any]]:
    where = ["s.user_id = %s", "f.deleted_at IS NULL", "s.deleted_at IS NULL"]
    params: list[Any] = [user_id]

    if qs.get("pending") in ("1", "true", "yes"):
        where.append("f.actioned_at IS NULL")

    sql = f"""
        SELECT
            f.id, f.submission_id, f.due_at, f.actioned_at, f.notified_at,
            f.notes, f.auto_created,
            s.role_title,
            c.name AS company_name,
            ss.short_name AS status
        FROM follow_ups f
        JOIN submissions s          ON s.id = f.submission_id
        JOIN submission_statuses ss ON ss.id = s.submission_status_id
        LEFT JOIN companies c       ON c.id = s.company_id AND c.deleted_at IS NULL
        WHERE {' AND '.join(where)}
        ORDER BY f.due_at ASC, f.id ASC
        LIMIT 500
    """
    logger.info("followups.list: querying", extra={"user_id": user_id, "filters": qs})
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [_row_to_followup(r) for r in rows]


def _create(
    conn: Any, user_id: int, submission_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    _verify_submission(conn, user_id, submission_id)
    due_at = _parse_due_at(body.get("due_at"))
    notes = body.get("notes")

    logger.info(
        "followups.create: inserting",
        extra={"user_id": user_id, "submission_id": submission_id, "due_at": str(due_at)},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO follow_ups
                (submission_id, due_at, notes, auto_created)
            VALUES (%s, %s, %s, FALSE)
            """,
            (submission_id, due_at, notes),
        )
        new_id = int(cur.lastrowid)
    conn.commit()

    schedule_followup(follow_up_id=new_id, due_at=due_at)
    return _detail(conn, user_id, new_id)


def _detail(conn: Any, user_id: int, follow_up_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                f.id, f.submission_id, f.due_at, f.actioned_at, f.notified_at,
                f.notes, f.auto_created,
                s.role_title,
                c.name AS company_name,
                ss.short_name AS status
            FROM follow_ups f
            JOIN submissions s          ON s.id = f.submission_id
            JOIN submission_statuses ss ON ss.id = s.submission_status_id
            LEFT JOIN companies c       ON c.id = s.company_id AND c.deleted_at IS NULL
            WHERE f.id = %s AND s.user_id = %s
              AND f.deleted_at IS NULL AND s.deleted_at IS NULL
            """,
            (follow_up_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError("follow-up not found")
    return _row_to_followup(row)


def _update(
    conn: Any, user_id: int, follow_up_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    existing = _load_owned(conn, user_id, follow_up_id)

    sets: list[str] = []
    params: list[Any] = []
    new_due_at: datetime | None = None

    if "due_at" in body:
        new_due_at = _parse_due_at(body["due_at"])
        sets.append("due_at = %s")
        params.append(new_due_at)

    if "notes" in body:
        sets.append("notes = %s")
        params.append(body["notes"])

    actioned_change: bool | None = None
    if "actioned" in body:
        if body["actioned"]:
            sets.append("actioned_at = CURRENT_TIMESTAMP")
            actioned_change = True
        else:
            sets.append("actioned_at = NULL")
            actioned_change = False

    if not sets:
        return _row_to_followup(existing)

    params.extend([follow_up_id])
    logger.info(
        "followups.update: updating",
        extra={
            "user_id": user_id,
            "follow_up_id": follow_up_id,
            "fields": list(body.keys()),
        },
    )
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE follow_ups SET {', '.join(sets)} WHERE id = %s",
            tuple(params),
        )
        if cur.rowcount == 0:
            raise LookupError("follow-up not found")
    conn.commit()

    # Side effects on the schedule, in priority order.
    #   1. If the row just became actioned, drop any pending schedule — the
    #      user has handled it; no reminder needs to fire.
    #   2. Else if due_at changed, replace the schedule.
    #   3. Else if the row was un-actioned (rare), re-create against the
    #      current due_at so the reminder fires on the next cycle.
    if actioned_change is True:
        cancel_followup(follow_up_id=follow_up_id)
    elif new_due_at is not None:
        schedule_followup(follow_up_id=follow_up_id, due_at=new_due_at)
    elif actioned_change is False:
        existing_due = existing["due_at"]
        if isinstance(existing_due, datetime):
            schedule_followup(follow_up_id=follow_up_id, due_at=existing_due)

    return _detail(conn, user_id, follow_up_id)


def _delete(conn: Any, user_id: int, follow_up_id: int) -> dict[str, Any]:
    _load_owned(conn, user_id, follow_up_id)
    logger.info(
        "followups.delete: soft-deleting",
        extra={"user_id": user_id, "follow_up_id": follow_up_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_ups SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND deleted_at IS NULL",
            (follow_up_id,),
        )
        if cur.rowcount == 0:
            raise LookupError("follow-up not found")
    conn.commit()
    cancel_followup(follow_up_id=follow_up_id)
    return {"id": follow_up_id, "deleted": True}


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("followups: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if route_key == "GET /follow-ups":
                qs = event.get("queryStringParameters") or {}
                result: Any = _list(conn, user_id, qs)
            elif route_key == "POST /submissions/{id}/follow-ups":
                body = json.loads(event.get("body") or "{}")
                result = _create(conn, user_id, _path_id(event), body)
            elif route_key == "PUT /follow-ups/{id}":
                body = json.loads(event.get("body") or "{}")
                result = _update(conn, user_id, _path_id(event), body)
            elif route_key == "DELETE /follow-ups/{id}":
                result = _delete(conn, user_id, _path_id(event))
            else:
                logger.warning("followups: unknown route", extra={"route_key": route_key})
                return _response(404, {"error": f"no handler for {route_key}"})
        logger.info("followups: exit ok", extra={"user_sub": sub, "route_key": route_key})
        return _response(200, result)
    except LookupError as e:
        logger.warning(
            "followups: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except ValueError as e:
        logger.exception("followups: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception(
            "followups: failed", extra={"user_sub": sub, "route_key": route_key}
        )
        raise