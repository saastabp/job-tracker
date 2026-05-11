"""GET /outreach/history?week_start=YYYY-MM-DD — outreach events for a week.

Returns the outreach events (both directions) that fall inside the
requested 7-day window, joined to contacts and — when the event came
from an imported email thread — to the submission carrying that
``gmail_thread_id`` so each row can surface the role and company it
relates to. Manually-logged rows have no ``gmail_thread_id`` and come
through with null role / company.

Response shape::

    {
      "week_start": "2026-04-13",
      "events": [
        {
          "id": 42,
          "outreach_at": "2026-04-15 14:32:00",
          "direction": "outbound",
          "method": "email",
          "subject": "Re: Senior SRE role",
          "body_text": "...",
          "notes": null,
          "gmail_message_id": "0x1a2b3c",
          "contact_id": 7,
          "contact_name": "Jane Recruiter",
          "contact_kind": "recruiter",
          "submission_id": 99,
          "role_title": "Senior SRE",
          "company_name": "Acme"
        },
        ...
      ]
    }

Time semantics
--------------
The window is ``co.outreach_at >= week_start AND
co.outreach_at < week_start + INTERVAL 7 DAY`` (exclusive upper bound)
so the query uses the ``ix_contact_outreach_user_at`` index instead of
wrapping ``DATE(co.outreach_at)`` in the filter, which would defeat the
index.

``week_start`` parsing is shared with the dashboard via
``common.week.parse_week_start`` so the two endpoints cannot drift on
Monday-rounding or empty-string handling.
"""
from __future__ import annotations

import json
from typing import Any

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.users import get_user_id
from common.week import parse_week_start


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _query_events(
    conn: Any, user_id: int, week_start: Any
) -> list[dict[str, Any]]:
    logger.info(
        "outreach_history: querying",
        extra={"user_id": user_id, "week_start": str(week_start)},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                co.id, co.outreach_at, co.subject, co.body_text,
                co.gmail_message_id, co.notes,
                od.short_name AS direction,
                om.short_name AS method,
                c.id AS contact_id, c.name AS contact_name,
                ck.short_name AS contact_kind,
                s.id AS submission_id, s.role_title,
                comp.name AS company_name
            FROM contact_outreach co
            JOIN contacts c             ON c.id = co.contact_id AND c.deleted_at IS NULL
            JOIN contact_kinds ck       ON ck.id = c.contact_kind_id
            JOIN outreach_directions od ON od.id = co.outreach_direction_id
            LEFT JOIN outreach_methods om ON om.id = co.outreach_method_id
            LEFT JOIN submissions s     ON s.gmail_thread_id = co.gmail_thread_id
                                        AND s.user_id = co.user_id
                                        AND s.deleted_at IS NULL
            LEFT JOIN companies comp    ON comp.id = s.company_id AND comp.deleted_at IS NULL
            WHERE co.user_id = %s
              AND co.deleted_at IS NULL
              AND co.outreach_at >= %s
              AND co.outreach_at <  %s + INTERVAL 7 DAY
            ORDER BY co.outreach_at DESC, co.id DESC
            """,
            (user_id, week_start, week_start),
        )
        rows = cur.fetchall()

    return [
        {
            "id": int(r["id"]),
            "outreach_at": str(r["outreach_at"]) if r["outreach_at"] else None,
            "direction": r["direction"],
            "method": r["method"],
            "subject": r["subject"],
            "body_text": r["body_text"],
            "notes": r["notes"],
            "gmail_message_id": r["gmail_message_id"],
            "contact_id": int(r["contact_id"]),
            "contact_name": r["contact_name"],
            "contact_kind": r["contact_kind"],
            "submission_id": (
                int(r["submission_id"]) if r["submission_id"] is not None else None
            ),
            "role_title": r["role_title"],
            "company_name": r["company_name"],
        }
        for r in rows
    ]


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info(
        "outreach_history: enter",
        extra={"user_sub": sub, "route_key": route_key},
    )
    try:
        qs = event.get("queryStringParameters") or {}
        try:
            week_start = parse_week_start(qs)
        except ValueError as e:
            logger.exception(
                "outreach_history: bad week_start",
                extra={"user_sub": sub, "route_key": route_key},
            )
            return _response(400, {"error": str(e)})

        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            events = _query_events(conn, user_id, week_start)

        logger.info(
            "outreach_history: exit ok",
            extra={
                "user_sub": sub,
                "week_start": str(week_start),
                "event_count": len(events),
            },
        )
        return _response(
            200, {"week_start": week_start.isoformat(), "events": events}
        )
    except Exception:
        logger.exception(
            "outreach_history: failed",
            extra={"user_sub": sub, "route_key": route_key},
        )
        raise