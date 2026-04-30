"""Submissions CRUD endpoints + JD-snapshot capture on create.

Routes
------
GET  /submissions          — list, filterable by status/company_id/from/to
POST /submissions          — create; supports inline ``company_name`` create-or-find
                             and inline ``jd_text`` + ``jd_url`` (text written to S3,
                             jd_snapshots row inserted)
GET  /submissions/{id}     — detail with company, jd_snapshot meta, follow_ups, responses
PUT  /submissions/{id}     — update status/notes/role_title/resume_id/tailored_*

JD snapshots
------------
On create, if ``jd_text`` is provided, we write it to::

    s3://<resume-bucket>/users/<cognito_sub>/submissions/<submission_id>/jd.txt

…and insert a ``jd_snapshots`` row pointing to that key. Per
``project_jd_archival.md``, this is a day-one feature: every submission
captures the JD as posted.

Statuses are referenced by ``short_name`` (applied / responded / interviewing /
offer / rejected / ghosted) at the API boundary. The handler resolves to
``submission_status_id`` via the ``submission_statuses`` catalog so adding a
status later is a single seed insert (no API change).
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.users import get_user_id

RESUME_BUCKET = os.environ.get("RESUME_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

_s3 = boto3.client("s3", region_name=AWS_REGION)


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _path_id(event: dict[str, Any]) -> int:
    raw = (event.get("pathParameters") or {}).get("id")
    if raw is None:
        raise ValueError("missing path parameter: id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid id: {raw!r}")


def _status_id(conn: Any, short_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM submission_statuses WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown submission status: {short_name}")
    return int(row["id"])


def _find_or_create_company(
    conn: Any, user_id: int, *, company_id: int | None, company_name: str | None
) -> int | None:
    """Resolve a company reference for a submission.

    Either an explicit ``company_id`` (must belong to the caller) or a
    ``company_name`` (looked up case-insensitively, created on miss). Returns
    ``None`` if neither is provided (company is optional on a submission).
    """
    if company_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM companies WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
                (company_id, user_id),
            )
            row = cur.fetchone()
        if not row:
            raise ValueError(f"company_id {company_id} not found")
        return company_id

    if not company_name:
        return None
    name = company_name.strip()
    if not name:
        return None

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM companies "
            "WHERE user_id = %s AND deleted_at IS NULL AND LOWER(name) = LOWER(%s) "
            "LIMIT 1",
            (user_id, name),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])
        logger.info(
            "submissions: creating company on the fly",
            extra={"user_id": user_id, "company_name": name},
        )
        cur.execute(
            "INSERT INTO companies (user_id, name) VALUES (%s, %s)",
            (user_id, name),
        )
        return int(cur.lastrowid)


def _store_jd(
    conn: Any, *, cognito_sub: str, submission_id: int, jd_text: str, jd_url: str | None
) -> None:
    """Write the JD body to S3 and insert a jd_snapshots row.

    Idempotent on re-create: ``jd_snapshots.submission_id`` is unique, so a
    second call for the same submission updates the existing row.
    """
    if not RESUME_BUCKET:
        raise RuntimeError("RESUME_BUCKET env var not configured")
    key = f"users/{cognito_sub}/submissions/{submission_id}/jd.txt"
    logger.info(
        "submissions.jd: writing to S3",
        extra={"submission_id": submission_id, "s3_key": key, "bytes": len(jd_text)},
    )
    _s3.put_object(
        Bucket=RESUME_BUCKET,
        Key=key,
        Body=jd_text.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
        ServerSideEncryption="AES256",
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jd_snapshots (submission_id, s3_key, source_url)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                s3_key = VALUES(s3_key),
                source_url = VALUES(source_url),
                updated_at = CURRENT_TIMESTAMP,
                deleted_at = NULL
            """,
            (submission_id, key, jd_url),
        )


def _list(conn: Any, user_id: int, qs: dict[str, str]) -> list[dict[str, Any]]:
    where: list[str] = ["s.user_id = %s", "s.deleted_at IS NULL"]
    params: list[Any] = [user_id]

    status = qs.get("status")
    if status:
        where.append("ss.short_name = %s")
        params.append(status)

    company_id = qs.get("company_id")
    if company_id:
        where.append("s.company_id = %s")
        params.append(int(company_id))

    date_from = qs.get("from")
    if date_from:
        where.append("s.submitted_on >= %s")
        params.append(date_from)

    date_to = qs.get("to")
    if date_to:
        where.append("s.submitted_on <= %s")
        params.append(date_to)

    sql = f"""
        SELECT
            s.id, s.role_title, s.submitted_on, s.notes,
            s.company_id, c.name AS company_name,
            ss.short_name AS status,
            s.tailored_title, s.tailored_summary, s.jd_url,
            s.created_at, s.updated_at
        FROM submissions s
        JOIN submission_statuses ss ON ss.id = s.submission_status_id
        LEFT JOIN companies c ON c.id = s.company_id AND c.deleted_at IS NULL
        WHERE {' AND '.join(where)}
        ORDER BY s.submitted_on DESC, s.id DESC
        LIMIT 500
    """
    logger.info("submissions.list: querying", extra={"user_id": user_id, "filters": qs})
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [_row_to_summary(r) for r in rows]


def _row_to_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "role_title": r["role_title"],
        "submitted_on": str(r["submitted_on"]) if r["submitted_on"] else None,
        "notes": r["notes"],
        "company_id": int(r["company_id"]) if r["company_id"] is not None else None,
        "company_name": r["company_name"],
        "status": r["status"],
        "tailored_title": r["tailored_title"],
        "tailored_summary": r["tailored_summary"],
        "jd_url": r["jd_url"],
    }


def _create(
    conn: Any, user_id: int, cognito_sub: str, body: dict[str, Any]
) -> dict[str, Any]:
    role_title = (body.get("role_title") or "").strip() or None
    submitted_on = body.get("submitted_on")  # ISO date string or None
    status = body.get("status") or "applied"
    notes = body.get("notes")
    jd_url = (body.get("jd_url") or "").strip() or None
    jd_text = body.get("jd_text") or ""
    resume_id = body.get("resume_id")
    company_id_in = body.get("company_id")
    company_name_in = body.get("company_name")

    status_id = _status_id(conn, status)
    company_id = _find_or_create_company(
        conn, user_id, company_id=company_id_in, company_name=company_name_in
    )

    logger.info(
        "submissions.create: inserting",
        extra={
            "user_id": user_id,
            "company_id": company_id,
            "status": status,
            "has_jd_text": bool(jd_text),
        },
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO submissions
                (user_id, company_id, resume_id, submission_status_id,
                 role_title, submitted_on, notes, jd_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id, company_id, resume_id, status_id,
                role_title, submitted_on, notes, jd_url,
            ),
        )
        new_id = int(cur.lastrowid)

    if jd_text:
        _store_jd(
            conn,
            cognito_sub=cognito_sub,
            submission_id=new_id,
            jd_text=jd_text,
            jd_url=jd_url,
        )

    conn.commit()
    return _detail(conn, user_id, new_id)


def _detail(conn: Any, user_id: int, submission_id: int) -> dict[str, Any]:
    logger.info(
        "submissions.detail: querying",
        extra={"user_id": user_id, "submission_id": submission_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                s.id, s.role_title, s.submitted_on, s.notes,
                s.company_id, c.name AS company_name,
                s.resume_id, r.title AS resume_title,
                ss.short_name AS status,
                s.tailored_title, s.tailored_summary, s.jd_url,
                s.created_at, s.updated_at
            FROM submissions s
            JOIN submission_statuses ss ON ss.id = s.submission_status_id
            LEFT JOIN companies c ON c.id = s.company_id AND c.deleted_at IS NULL
            LEFT JOIN resumes r ON r.id = s.resume_id AND r.deleted_at IS NULL
            WHERE s.id = %s AND s.user_id = %s AND s.deleted_at IS NULL
            """,
            (submission_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("submission not found")

        cur.execute(
            "SELECT id, s3_key, source_url, captured_at "
            "FROM jd_snapshots WHERE submission_id = %s AND deleted_at IS NULL",
            (submission_id,),
        )
        jd_row = cur.fetchone()

        cur.execute(
            """
            SELECT id, due_at, actioned_at, notes
            FROM follow_ups
            WHERE submission_id = %s AND deleted_at IS NULL
            ORDER BY due_at
            """,
            (submission_id,),
        )
        follow_ups = [
            {
                "id": int(r["id"]),
                "due_at": str(r["due_at"]) if r["due_at"] else None,
                "actioned_at": str(r["actioned_at"]) if r["actioned_at"] else None,
                "notes": r["notes"],
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT
                r.id, r.received_at, r.from_email, r.subject,
                rc.short_name AS classification
            FROM responses r
            JOIN response_classifications rc ON rc.id = r.response_classification_id
            WHERE r.submission_id = %s AND r.deleted_at IS NULL
            ORDER BY r.received_at DESC
            """,
            (submission_id,),
        )
        responses = [
            {
                "id": int(r["id"]),
                "received_at": str(r["received_at"]) if r["received_at"] else None,
                "from_email": r["from_email"],
                "subject": r["subject"],
                "classification": r["classification"],
            }
            for r in cur.fetchall()
        ]

    detail = _row_to_summary(row)
    detail["resume_id"] = int(row["resume_id"]) if row["resume_id"] is not None else None
    detail["resume_title"] = row.get("resume_title")
    detail["jd_snapshot"] = (
        {
            "id": int(jd_row["id"]),
            "s3_key": jd_row["s3_key"],
            "source_url": jd_row["source_url"],
            "captured_at": str(jd_row["captured_at"]) if jd_row["captured_at"] else None,
        }
        if jd_row
        else None
    )
    detail["jd_text"] = _read_jd_text(jd_row["s3_key"]) if jd_row else None
    detail["follow_ups"] = follow_ups
    detail["responses"] = responses
    return detail


def _read_jd_text(s3_key: str) -> str | None:
    """Fetch the JD body from S3; return None on miss/error.

    A missing object is logged but not fatal — the snapshot row could exist
    with the file pruned (e.g. lifecycle policy), and the detail view should
    still render.
    """
    if not RESUME_BUCKET:
        return None
    try:
        logger.info("submissions.jd: reading from S3", extra={"s3_key": s3_key})
        obj = _s3.get_object(Bucket=RESUME_BUCKET, Key=s3_key)
        return obj["Body"].read().decode("utf-8")
    except Exception:
        logger.exception("submissions.jd: read failed", extra={"s3_key": s3_key})
        return None


_MUTABLE_FIELDS = {
    "role_title": "role_title = %s",
    "submitted_on": "submitted_on = %s",
    "notes": "notes = %s",
    "tailored_title": "tailored_title = %s",
    "tailored_summary": "tailored_summary = %s",
    "jd_url": "jd_url = %s",
    "resume_id": "resume_id = %s",
}


def _update(
    conn: Any,
    user_id: int,
    cognito_sub: str,
    submission_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    sets: list[str] = []
    params: list[Any] = []

    if "status" in body:
        sets.append("submission_status_id = %s")
        params.append(_status_id(conn, body["status"]))

    for field, fragment in _MUTABLE_FIELDS.items():
        if field in body:
            sets.append(fragment)
            params.append(body[field])

    if sets:
        params.extend([submission_id, user_id])
        logger.info(
            "submissions.update: updating",
            extra={
                "user_id": user_id,
                "submission_id": submission_id,
                "fields": list(body.keys()),
            },
        )
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE submissions SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
                tuple(params),
            )
            if cur.rowcount == 0:
                raise LookupError("submission not found")

    if "jd_text" in body:
        _update_jd(
            conn,
            user_id=user_id,
            cognito_sub=cognito_sub,
            submission_id=submission_id,
            body=body,
        )

    if not sets and "jd_text" not in body:
        return _detail(conn, user_id, submission_id)

    conn.commit()
    return _detail(conn, user_id, submission_id)


def _update_jd(
    conn: Any,
    *,
    user_id: int,
    cognito_sub: str,
    submission_id: int,
    body: dict[str, Any],
) -> None:
    """Apply a JD-text edit from a PUT body.

    Empty/None ``jd_text`` soft-deletes the snapshot. Non-empty upserts via
    ``_store_jd``. ``jd_url`` for ``source_url`` comes from the body if
    present, else is read from the submission row so an unrelated edit
    doesn't clobber the existing source URL.
    """
    jd_text = body.get("jd_text") or ""

    if "jd_url" in body:
        source_url = body.get("jd_url") or None
    else:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jd_url FROM submissions "
                "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
                (submission_id, user_id),
            )
            row = cur.fetchone()
        source_url = (row.get("jd_url") if row else None)

    if jd_text:
        _store_jd(
            conn,
            cognito_sub=cognito_sub,
            submission_id=submission_id,
            jd_text=jd_text,
            jd_url=source_url,
        )
        return

    logger.info(
        "submissions.jd: clearing snapshot",
        extra={"user_id": user_id, "submission_id": submission_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jd_snapshots SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE submission_id = %s AND deleted_at IS NULL",
            (submission_id,),
        )


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("submissions: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if route_key == "GET /submissions":
                qs = event.get("queryStringParameters") or {}
                result: Any = _list(conn, user_id, qs)
            elif route_key == "POST /submissions":
                body = json.loads(event.get("body") or "{}")
                result = _create(conn, user_id, sub, body)
            elif route_key == "GET /submissions/{id}":
                result = _detail(conn, user_id, _path_id(event))
            elif route_key == "PUT /submissions/{id}":
                body = json.loads(event.get("body") or "{}")
                result = _update(conn, user_id, sub, _path_id(event), body)
            else:
                logger.warning("submissions: unknown route", extra={"route_key": route_key})
                return _response(404, {"error": f"no handler for {route_key}"})
        logger.info("submissions: exit ok", extra={"user_sub": sub, "route_key": route_key})
        return _response(200, result)
    except LookupError as e:
        logger.warning(
            "submissions: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except ValueError as e:
        logger.exception("submissions: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception("submissions: failed", extra={"user_sub": sub, "route_key": route_key})
        raise