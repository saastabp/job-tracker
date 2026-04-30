"""Contacts CRUD + outreach event logging.

Routes
------
GET    /contacts                                  — list (filter ``?kind=``, ``?company_id=``)
POST   /contacts                                  — create
GET    /contacts/{id}                             — detail + outreach timeline
PUT    /contacts/{id}                             — update mutable fields
DELETE /contacts/{id}                             — soft-delete
POST   /contacts/{id}/outreach                    — log an outreach event
DELETE /contacts/{contact_id}/outreach/{id}       — soft-delete an outreach event

Catalog references at the API boundary use ``short_name``:
``contact_kinds`` (``personal`` / ``recruiter``), ``outreach_methods``
(``email`` / ``linkedin`` / ``phone`` / ``in_person`` / ``other``),
``outreach_directions`` (``outbound`` / ``inbound``). The handler resolves
each to its catalog id so adding a new method later is a single seed
insert with no API change.
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


def _path_id(event: dict[str, Any], key: str = "id") -> int:
    raw = (event.get("pathParameters") or {}).get(key)
    if raw is None:
        raise ValueError(f"missing path parameter: {key}")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {key}: {raw!r}")


def _catalog_id(conn: Any, table: str, short_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown {table}.short_name: {short_name}")
    return int(row["id"])


def _verify_company(conn: Any, user_id: int, company_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM companies WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (company_id, user_id),
        )
        if not cur.fetchone():
            raise ValueError(f"company_id {company_id} not found")


def _row_to_contact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "name": row["name"],
        "email": row.get("email"),
        "phone": row.get("phone"),
        "linkedin_url": row.get("linkedin_url"),
        "primary_method": row.get("primary_method"),
        "company_id": int(row["company_id"]) if row.get("company_id") is not None else None,
        "company_name": row.get("company_name"),
        "notes": row.get("notes"),
        "outreach_count": int(row.get("outreach_count") or 0),
        "last_outreach_at": (
            str(row["last_outreach_at"]) if row.get("last_outreach_at") else None
        ),
    }


def _list(conn: Any, user_id: int, qs: dict[str, str]) -> list[dict[str, Any]]:
    where = ["c.user_id = %s", "c.deleted_at IS NULL"]
    params: list[Any] = [user_id]

    kind = qs.get("kind")
    if kind:
        where.append("ck.short_name = %s")
        params.append(kind)

    company_id = qs.get("company_id")
    if company_id:
        where.append("c.company_id = %s")
        params.append(int(company_id))

    sql = f"""
        SELECT
            c.id, c.name, c.email, c.phone, c.linkedin_url, c.notes,
            c.company_id, co_comp.name AS company_name,
            ck.short_name  AS kind,
            om.short_name  AS primary_method,
            COUNT(out_evt.id)        AS outreach_count,
            MAX(out_evt.outreach_at) AS last_outreach_at
        FROM contacts c
        JOIN contact_kinds ck    ON ck.id = c.contact_kind_id
        LEFT JOIN companies co_comp
            ON co_comp.id = c.company_id AND co_comp.deleted_at IS NULL
        LEFT JOIN outreach_methods om
            ON om.id = c.primary_method_id AND om.deleted_at IS NULL
        LEFT JOIN contact_outreach out_evt
            ON out_evt.contact_id = c.id AND out_evt.deleted_at IS NULL
        WHERE {' AND '.join(where)}
        GROUP BY c.id, c.name, c.email, c.phone, c.linkedin_url, c.notes,
                 c.company_id, co_comp.name, ck.short_name, om.short_name
        ORDER BY c.name
    """
    logger.info("contacts.list: querying", extra={"user_id": user_id, "filters": qs})
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [_row_to_contact(r) for r in rows]


def _create(conn: Any, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")

    kind = body.get("kind") or "personal"
    contact_kind_id = _catalog_id(conn, "contact_kinds", kind)

    primary_method_id: int | None = None
    if body.get("primary_method"):
        primary_method_id = _catalog_id(conn, "outreach_methods", body["primary_method"])

    company_id = body.get("company_id")
    if company_id is not None:
        _verify_company(conn, user_id, int(company_id))

    email = (body.get("email") or "").strip() or None
    phone = (body.get("phone") or "").strip() or None
    linkedin_url = (body.get("linkedin_url") or "").strip() or None
    notes = body.get("notes")

    logger.info(
        "contacts.create: inserting",
        extra={"user_id": user_id, "kind": kind, "name_len": len(name)},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contacts
                (user_id, contact_kind_id, name, email, phone, linkedin_url,
                 primary_method_id, company_id, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id, contact_kind_id, name, email, phone, linkedin_url,
                primary_method_id, company_id, notes,
            ),
        )
        new_id = int(cur.lastrowid)
    conn.commit()
    return _detail(conn, user_id, new_id)


def _detail(conn: Any, user_id: int, contact_id: int) -> dict[str, Any]:
    logger.info(
        "contacts.detail: querying",
        extra={"user_id": user_id, "contact_id": contact_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id, c.name, c.email, c.phone, c.linkedin_url, c.notes,
                c.company_id, co_comp.name AS company_name,
                ck.short_name AS kind,
                om.short_name AS primary_method,
                COUNT(out_evt.id)        AS outreach_count,
                MAX(out_evt.outreach_at) AS last_outreach_at
            FROM contacts c
            JOIN contact_kinds ck ON ck.id = c.contact_kind_id
            LEFT JOIN companies co_comp
                ON co_comp.id = c.company_id AND co_comp.deleted_at IS NULL
            LEFT JOIN outreach_methods om
                ON om.id = c.primary_method_id AND om.deleted_at IS NULL
            LEFT JOIN contact_outreach out_evt
                ON out_evt.contact_id = c.id AND out_evt.deleted_at IS NULL
            WHERE c.id = %s AND c.user_id = %s AND c.deleted_at IS NULL
            GROUP BY c.id, c.name, c.email, c.phone, c.linkedin_url, c.notes,
                     c.company_id, co_comp.name, ck.short_name, om.short_name
            """,
            (contact_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("contact not found")

        cur.execute(
            """
            SELECT
                co.id, co.outreach_at, co.notes,
                om.short_name AS method,
                od.short_name AS direction
            FROM contact_outreach co
            LEFT JOIN outreach_methods om
                ON om.id = co.outreach_method_id AND om.deleted_at IS NULL
            JOIN outreach_directions od
                ON od.id = co.outreach_direction_id
            WHERE co.contact_id = %s AND co.user_id = %s AND co.deleted_at IS NULL
            ORDER BY co.outreach_at DESC, co.id DESC
            """,
            (contact_id, user_id),
        )
        outreach = [
            {
                "id": int(r["id"]),
                "outreach_at": str(r["outreach_at"]) if r["outreach_at"] else None,
                "method": r["method"],
                "direction": r["direction"],
                "notes": r["notes"],
            }
            for r in cur.fetchall()
        ]

    detail = _row_to_contact(row)
    detail["outreach"] = outreach
    return detail


def _update(
    conn: Any, user_id: int, contact_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    sets: list[str] = []
    params: list[Any] = []

    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise ValueError("name cannot be blank")
        sets.append("name = %s")
        params.append(name)

    if "kind" in body:
        sets.append("contact_kind_id = %s")
        params.append(_catalog_id(conn, "contact_kinds", body["kind"]))

    if "primary_method" in body:
        if body["primary_method"]:
            sets.append("primary_method_id = %s")
            params.append(_catalog_id(conn, "outreach_methods", body["primary_method"]))
        else:
            sets.append("primary_method_id = NULL")

    if "email" in body:
        v = (body["email"] or "").strip() or None
        sets.append("email = %s")
        params.append(v)

    if "phone" in body:
        v = (body["phone"] or "").strip() or None
        sets.append("phone = %s")
        params.append(v)

    if "linkedin_url" in body:
        v = (body["linkedin_url"] or "").strip() or None
        sets.append("linkedin_url = %s")
        params.append(v)

    if "company_id" in body:
        cid = body["company_id"]
        if cid is None:
            sets.append("company_id = NULL")
        else:
            _verify_company(conn, user_id, int(cid))
            sets.append("company_id = %s")
            params.append(int(cid))

    if "notes" in body:
        sets.append("notes = %s")
        params.append(body["notes"])

    if not sets:
        return _detail(conn, user_id, contact_id)

    params.extend([contact_id, user_id])
    logger.info(
        "contacts.update: updating",
        extra={
            "user_id": user_id,
            "contact_id": contact_id,
            "fields": list(body.keys()),
        },
    )
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE contacts SET {', '.join(sets)} "
            f"WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            tuple(params),
        )
        if cur.rowcount == 0:
            raise LookupError("contact not found")
    conn.commit()
    return _detail(conn, user_id, contact_id)


def _delete(conn: Any, user_id: int, contact_id: int) -> dict[str, Any]:
    logger.info(
        "contacts.delete: soft-deleting",
        extra={"user_id": user_id, "contact_id": contact_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contacts SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if cur.rowcount == 0:
            raise LookupError("contact not found")
    conn.commit()
    return {"id": contact_id, "deleted": True}


def _verify_contact(conn: Any, user_id: int, contact_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM contacts "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if not cur.fetchone():
            raise LookupError("contact not found")


def _log_outreach(
    conn: Any, user_id: int, contact_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    _verify_contact(conn, user_id, contact_id)

    outreach_at = body.get("outreach_at")  # ISO timestamp or None
    if not outreach_at:
        # Default: now (server-side). Keep it explicit so the row survives any
        # later schema change that drops the DEFAULT.
        outreach_at = None  # let SQL NOW() handle it via CURRENT_TIMESTAMP

    direction = body.get("direction") or "outbound"
    direction_id = _catalog_id(conn, "outreach_directions", direction)

    method = body.get("method")
    method_id = _catalog_id(conn, "outreach_methods", method) if method else None

    notes = body.get("notes")

    logger.info(
        "contacts.outreach: inserting",
        extra={
            "user_id": user_id,
            "contact_id": contact_id,
            "direction": direction,
            "method": method,
        },
    )
    with conn.cursor() as cur:
        if outreach_at:
            cur.execute(
                """
                INSERT INTO contact_outreach
                    (user_id, contact_id, outreach_at,
                     outreach_method_id, outreach_direction_id, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, contact_id, outreach_at, method_id, direction_id, notes),
            )
        else:
            cur.execute(
                """
                INSERT INTO contact_outreach
                    (user_id, contact_id, outreach_at,
                     outreach_method_id, outreach_direction_id, notes)
                VALUES (%s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                """,
                (user_id, contact_id, method_id, direction_id, notes),
            )
    conn.commit()
    return _detail(conn, user_id, contact_id)


def _delete_outreach(
    conn: Any, user_id: int, contact_id: int, outreach_id: int
) -> dict[str, Any]:
    logger.info(
        "contacts.outreach.delete: soft-deleting",
        extra={
            "user_id": user_id,
            "contact_id": contact_id,
            "outreach_id": outreach_id,
        },
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contact_outreach SET deleted_at = CURRENT_TIMESTAMP
            WHERE id = %s AND contact_id = %s AND user_id = %s
              AND deleted_at IS NULL
            """,
            (outreach_id, contact_id, user_id),
        )
        if cur.rowcount == 0:
            raise LookupError("outreach event not found")
    conn.commit()
    return {"id": outreach_id, "deleted": True}


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("contacts: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if route_key == "GET /contacts":
                qs = event.get("queryStringParameters") or {}
                result: Any = _list(conn, user_id, qs)
            elif route_key == "POST /contacts":
                body = json.loads(event.get("body") or "{}")
                result = _create(conn, user_id, body)
            elif route_key == "GET /contacts/{id}":
                result = _detail(conn, user_id, _path_id(event))
            elif route_key == "PUT /contacts/{id}":
                body = json.loads(event.get("body") or "{}")
                result = _update(conn, user_id, _path_id(event), body)
            elif route_key == "DELETE /contacts/{id}":
                result = _delete(conn, user_id, _path_id(event))
            elif route_key == "POST /contacts/{id}/outreach":
                body = json.loads(event.get("body") or "{}")
                result = _log_outreach(conn, user_id, _path_id(event), body)
            elif route_key == "DELETE /contacts/{contact_id}/outreach/{id}":
                result = _delete_outreach(
                    conn,
                    user_id,
                    _path_id(event, "contact_id"),
                    _path_id(event, "id"),
                )
            else:
                logger.warning("contacts: unknown route", extra={"route_key": route_key})
                return _response(404, {"error": f"no handler for {route_key}"})
        logger.info("contacts: exit ok", extra={"user_sub": sub, "route_key": route_key})
        return _response(200, result)
    except LookupError as e:
        logger.warning(
            "contacts: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except ValueError as e:
        logger.exception("contacts: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception(
            "contacts: failed", extra={"user_sub": sub, "route_key": route_key}
        )
        raise