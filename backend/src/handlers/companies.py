"""Companies CRUD endpoints.

Routes
------
GET  /companies          — list the caller's companies (with submission counts)
POST /companies          — create a new company for the caller
GET  /companies/{id}     — single company + its submissions
PUT  /companies/{id}     — update name / notes

A single Lambda dispatches on ``event["routeKey"]``. All queries are
row-level scoped to the caller's ``users.id``.
"""
from __future__ import annotations

import json
from typing import Any

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.users import get_user_id


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _row_to_company(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "notes": row.get("notes"),
        "submission_count": int(row.get("submission_count") or 0),
    }


def _list(conn: Any, user_id: int) -> list[dict[str, Any]]:
    logger.info("companies.list: querying", extra={"user_id": user_id})
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id, c.name, c.notes,
                COUNT(s.id) AS submission_count
            FROM companies c
            LEFT JOIN submissions s
                ON s.company_id = c.id AND s.deleted_at IS NULL
            WHERE c.user_id = %s AND c.deleted_at IS NULL
            GROUP BY c.id, c.name, c.notes
            ORDER BY c.name
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    return [_row_to_company(r) for r in rows]


def _create(conn: Any, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    notes = body.get("notes")
    logger.info(
        "companies.create: inserting",
        extra={"user_id": user_id, "company_name": name},
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO companies (user_id, name, notes) VALUES (%s, %s, %s)",
            (user_id, name, notes),
        )
        new_id = cur.lastrowid
    conn.commit()
    return _detail(conn, user_id, int(new_id))


def _detail(conn: Any, user_id: int, company_id: int) -> dict[str, Any]:
    logger.info(
        "companies.detail: querying",
        extra={"user_id": user_id, "company_id": company_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.notes,
                   COUNT(s.id) AS submission_count
            FROM companies c
            LEFT JOIN submissions s
                ON s.company_id = c.id AND s.deleted_at IS NULL
            WHERE c.id = %s AND c.user_id = %s AND c.deleted_at IS NULL
            GROUP BY c.id, c.name, c.notes
            """,
            (company_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("company not found")

        cur.execute(
            """
            SELECT
                s.id, s.role_title, s.submitted_on, s.notes,
                ss.short_name AS status
            FROM submissions s
            JOIN submission_statuses ss ON ss.id = s.submission_status_id
            WHERE s.company_id = %s AND s.user_id = %s AND s.deleted_at IS NULL
            ORDER BY s.submitted_on DESC, s.id DESC
            """,
            (company_id, user_id),
        )
        submissions = [
            {
                "id": int(r["id"]),
                "role_title": r["role_title"],
                "submitted_on": str(r["submitted_on"]) if r["submitted_on"] else None,
                "status": r["status"],
                "notes": r["notes"],
            }
            for r in cur.fetchall()
        ]

    company = _row_to_company(row)
    company["submissions"] = submissions
    return company


def _update(conn: Any, user_id: int, company_id: int, body: dict[str, Any]) -> dict[str, Any]:
    name = body.get("name")
    notes = body.get("notes")
    logger.info(
        "companies.update: updating",
        extra={"user_id": user_id, "company_id": company_id},
    )
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        n = name.strip()
        if not n:
            raise ValueError("name cannot be blank")
        sets.append("name = %s")
        params.append(n)
    if notes is not None:
        sets.append("notes = %s")
        params.append(notes)
    if not sets:
        return _detail(conn, user_id, company_id)
    params.extend([company_id, user_id])
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE companies SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
            tuple(params),
        )
        if cur.rowcount == 0:
            raise LookupError("company not found")
    conn.commit()
    return _detail(conn, user_id, company_id)


def _path_id(event: dict[str, Any]) -> int:
    raw = (event.get("pathParameters") or {}).get("id")
    if raw is None:
        raise ValueError("missing path parameter: id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid id: {raw!r}")


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("companies: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if route_key == "GET /companies":
                result: Any = _list(conn, user_id)
            elif route_key == "POST /companies":
                body = json.loads(event.get("body") or "{}")
                result = _create(conn, user_id, body)
            elif route_key == "GET /companies/{id}":
                result = _detail(conn, user_id, _path_id(event))
            elif route_key == "PUT /companies/{id}":
                body = json.loads(event.get("body") or "{}")
                result = _update(conn, user_id, _path_id(event), body)
            else:
                logger.warning("companies: unknown route", extra={"route_key": route_key})
                return _response(404, {"error": f"no handler for {route_key}"})
        logger.info("companies: exit ok", extra={"user_sub": sub, "route_key": route_key})
        return _response(200, result)
    except LookupError as e:
        logger.warning(
            "companies: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except ValueError as e:
        logger.exception("companies: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception("companies: failed", extra={"user_sub": sub, "route_key": route_key})
        raise