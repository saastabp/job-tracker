"""GET /dashboard/today — counts + targets for the Dashboard widgets.

Returns today's and this-week's counts for each target type, plus the user's
configured goals so the UI can render ``X of N`` directly.

Response shape::

    {
      "today": "2026-04-28",
      "week_start": "2026-04-27",
      "metrics": {
        "submissions":        {"today": 0, "week": 0, "daily": 5, "weekly": 25},
        "personal_outreach":  {"today": 0, "week": 0, "daily": null, "weekly": null,
                               "inbound_today": 0, "inbound_week": 0},
        "recruiter_outreach": {"today": 0, "week": 0, "daily": null, "weekly": null,
                               "inbound_today": 0, "inbound_week": 0},
        "follow_ups":         {"pending": 0,           "daily": null, "weekly": null}
      }
    }

The ``today`` / ``week`` counters on outreach metrics still measure
*outbound* events (what the goals track — "did I reach out enough this
week?"). ``inbound_today`` / ``inbound_week`` are a parallel counter
that surfaces incoming pings the user logged or the poller imported.
Surfaced as a separate field so the goal mechanic stays untouched but
the user can still see "the recruiters got back to me N times this
week" at a glance.

``follow_ups`` reports a pending count (rows with ``actioned_at IS NULL``)
rather than today/week, because that is what the user actions on.

Time semantics
--------------
This first cut uses the MySQL server's session timezone (UTC) for ``CURDATE()``
and ``YEARWEEK(_, 1)``. A user-configurable timezone is deferred to a later
slice; the cost of that deferral is "today" rolling over at UTC midnight rather
than the user's local midnight.
"""
from __future__ import annotations

import json
from typing import Any

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.users import get_user_id


def _zero_metric() -> dict[str, Any]:
    return {"today": 0, "week": 0, "daily": None, "weekly": None}


def _zero_outreach_metric() -> dict[str, Any]:
    return {
        "today": 0, "week": 0, "daily": None, "weekly": None,
        "inbound_today": 0, "inbound_week": 0,
    }


def _query_counts(conn: Any, user_id: int) -> dict[str, Any]:
    logger.info("dashboard: querying counts", extra={"user_id": user_id})
    metrics: dict[str, Any] = {
        "submissions": _zero_metric(),
        "personal_outreach": _zero_outreach_metric(),
        "recruiter_outreach": _zero_outreach_metric(),
        "follow_ups": {"pending": 0, "daily": None, "weekly": None},
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                SUM(CASE WHEN submitted_on = CURDATE() THEN 1 ELSE 0 END) AS today_count,
                SUM(CASE WHEN YEARWEEK(submitted_on, 1) = YEARWEEK(CURDATE(), 1) THEN 1 ELSE 0 END) AS week_count
            FROM submissions
            WHERE user_id = %s AND deleted_at IS NULL AND submitted_on IS NOT NULL
            """,
            (user_id,),
        )
        row = cur.fetchone() or {}
        metrics["submissions"]["today"] = int(row.get("today_count") or 0)
        metrics["submissions"]["week"] = int(row.get("week_count") or 0)

        # Outbound events still drive the goal counters — that's what the
        # widgets ask "did I reach out enough this week?" against. Inbound
        # events (the recruiter pinged me, or the poller imported a thread)
        # populate parallel `inbound_today` / `inbound_week` fields so the
        # user can see "they got back to me N times" without inflating the
        # goal numerator.
        cur.execute(
            """
            SELECT
                ck.short_name AS kind,
                od.short_name AS direction,
                SUM(CASE WHEN DATE(co.outreach_at) = CURDATE() THEN 1 ELSE 0 END) AS today_count,
                SUM(CASE WHEN YEARWEEK(co.outreach_at, 1) = YEARWEEK(CURDATE(), 1) THEN 1 ELSE 0 END) AS week_count
            FROM contact_outreach co
            JOIN contacts c             ON c.id = co.contact_id AND c.deleted_at IS NULL
            JOIN contact_kinds ck       ON ck.id = c.contact_kind_id
            JOIN outreach_directions od ON od.id = co.outreach_direction_id
            WHERE co.user_id = %s
              AND co.deleted_at IS NULL
            GROUP BY ck.short_name, od.short_name
            """,
            (user_id,),
        )
        for r in cur.fetchall():
            key = "personal_outreach" if r["kind"] == "personal" else "recruiter_outreach"
            today = int(r.get("today_count") or 0)
            week = int(r.get("week_count") or 0)
            if r["direction"] == "outbound":
                metrics[key]["today"] = today
                metrics[key]["week"] = week
            else:  # inbound
                metrics[key]["inbound_today"] = today
                metrics[key]["inbound_week"] = week

        cur.execute(
            """
            SELECT COUNT(*) AS pending
            FROM follow_ups f
            JOIN submissions s ON s.id = f.submission_id
            WHERE s.user_id = %s
              AND f.actioned_at IS NULL
              AND f.deleted_at IS NULL
              AND s.deleted_at IS NULL
            """,
            (user_id,),
        )
        row = cur.fetchone() or {}
        metrics["follow_ups"]["pending"] = int(row.get("pending") or 0)

        cur.execute(
            """
            SELECT tt.short_name AS short_name, t.cadence AS cadence, t.goal_count AS goal
            FROM targets t
            JOIN target_types tt ON tt.id = t.target_type_id
            WHERE t.user_id = %s AND t.deleted_at IS NULL
            """,
            (user_id,),
        )
        for r in cur.fetchall():
            name = r["short_name"]
            if name in metrics:
                metrics[name][r["cadence"]] = int(r["goal"])

        cur.execute(
            "SELECT CURDATE() AS today, "
            "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AS week_start"
        )
        date_row = cur.fetchone() or {}

        cur.execute(
            """
            SELECT
                s.id, s.role_title, s.submitted_on,
                c.name AS company_name,
                ss.short_name AS status,
                s.updated_at
            FROM submissions s
            JOIN submission_statuses ss ON ss.id = s.submission_status_id
            LEFT JOIN companies c ON c.id = s.company_id AND c.deleted_at IS NULL
            WHERE s.user_id = %s AND s.deleted_at IS NULL
            ORDER BY s.updated_at DESC
            LIMIT 10
            """,
            (user_id,),
        )
        recent = [
            {
                "id": int(r["id"]),
                "role_title": r["role_title"],
                "company_name": r["company_name"],
                "status": r["status"],
                "submitted_on": str(r["submitted_on"]) if r["submitted_on"] else None,
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
            }
            for r in cur.fetchall()
        ]

    return {
        "today": str(date_row.get("today")),
        "week_start": str(date_row.get("week_start")),
        "metrics": metrics,
        "recent_submissions": recent,
    }


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    logger.info("dashboard: enter", extra={"user_sub": sub})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            payload = _query_counts(conn, user_id)
        logger.info("dashboard: exit ok", extra={"user_sub": sub})
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(payload),
        }
    except Exception:
        logger.exception("dashboard: failed", extra={"user_sub": sub})
        raise