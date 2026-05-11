"""Resumes CRUD endpoints + presigned-URL upload/download flow.

Routes
------
GET    /resumes                      — list the caller's resumes (with submission counts)
POST   /resumes                      — create a new resume row (no file)
GET    /resumes/{id}                 — detail + transient ``download_url`` if a file is attached
PUT    /resumes/{id}                 — update title / summary / is_master
DELETE /resumes/{id}                 — soft-delete (sets ``deleted_at``)
POST   /resumes/{id}/upload-url      — request a fresh presigned PUT URL
PUT    /resumes/{id}/content         — set the structured resume body (maintenance)
POST   /resumes/from-tailor          — render a tailored PDF + insert resume row

Upload flow
-----------
The browser uploads files directly to S3 via a 5-minute presigned PUT URL.
The Lambda only signs the request — bytes never traverse API Gateway. The
resume bucket has CORS configured (see ``infra/data/template.yaml``) to
allow the PUT from the SPA origins.

S3 key layout::

    s3://<resume-bucket>/users/<cognito_sub>/resumes/<resume_id>/<original_filename>

Master-resume invariant
-----------------------
At most one ``resumes`` row per user has ``is_master=TRUE``. Enforced in the
handler: when a row is set master, the same transaction clears the flag on
the user's other rows. The first resume a user creates is auto-marked master.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import boto3
from pydantic import ValidationError

from common.auth import user_sub
from common.db import get_connection
from common.logger import logger
from common.pdf_generator import PdfGenerator
from common.resume_schema import ResumeContent
from common.resume_template import build_template
from common.users import get_user_id

RESUME_BUCKET = os.environ.get("RESUME_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

UPLOAD_URL_TTL_SECONDS = 300
DOWNLOAD_URL_TTL_SECONDS = 300
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB cap on PDF uploads.
ALLOWED_CONTENT_TYPES = {"application/pdf"}

MAX_TAILORED_TITLE_CHARS = 300
MAX_TAILORED_SUMMARY_CHARS = 4_000

FONTS_DIR = str(Path(__file__).resolve().parent.parent / "common" / "fonts")
TAILORED_FILENAME = "tailored.pdf"

_s3 = boto3.client("s3", region_name=AWS_REGION)


class _ConflictError(Exception):
    """Raised when the request is well-formed but conflicts with current state.

    Mapped to a 409 in the dispatcher. Used today for "base resume has no
    content_json yet" — the user can fix it by PUT-ing the maintenance
    endpoint, so 404 / 400 are both wrong.
    """


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


def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    deleted_at = row.get("deleted_at")
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "summary": row["summary"],
        "is_master": bool(row["is_master"]),
        "has_file": row.get("file_s3_key") is not None,
        "has_content_json": bool(row.get("has_content_json")),
        "original_filename": row.get("original_filename"),
        "submission_count": int(row.get("submission_count") or 0),
        "is_deleted": deleted_at is not None,
        "deleted_at": str(deleted_at) if deleted_at else None,
    }


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in ("1", "true", "yes", "on")


def _list(
    conn: Any, user_id: int, qs: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    qs = qs or {}
    include_deleted = _truthy(qs.get("include_deleted"))
    where = ["r.user_id = %s"]
    if not include_deleted:
        where.append("r.deleted_at IS NULL")

    logger.info(
        "resumes.list: querying",
        extra={"user_id": user_id, "include_deleted": include_deleted},
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                r.id, r.title, r.summary, r.is_master,
                r.file_s3_key, r.original_filename, r.deleted_at,
                (r.content_json IS NOT NULL) AS has_content_json,
                COUNT(s.id) AS submission_count
            FROM resumes r
            LEFT JOIN submissions s
                ON s.resume_id = r.id AND s.deleted_at IS NULL
            WHERE {' AND '.join(where)}
            GROUP BY r.id, r.title, r.summary, r.is_master,
                     r.file_s3_key, r.original_filename, r.deleted_at,
                     r.content_json, r.updated_at
            ORDER BY (r.deleted_at IS NOT NULL) ASC,
                     r.is_master DESC, r.updated_at DESC, r.id DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    return [_row_to_summary(r) for r in rows]


def _has_any_resume(conn: Any, user_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM resumes WHERE user_id = %s AND deleted_at IS NULL LIMIT 1",
            (user_id,),
        )
        return cur.fetchone() is not None


def _clear_other_masters(conn: Any, user_id: int, except_id: int | None) -> None:
    """Unset ``is_master`` on every other live resume for the user.

    Called inside the same transaction as the row that *will* be master, so
    the invariant "at most one master per user" holds at COMMIT time.
    """
    with conn.cursor() as cur:
        if except_id is None:
            cur.execute(
                "UPDATE resumes SET is_master = FALSE "
                "WHERE user_id = %s AND deleted_at IS NULL AND is_master = TRUE",
                (user_id,),
            )
        else:
            cur.execute(
                "UPDATE resumes SET is_master = FALSE "
                "WHERE user_id = %s AND id != %s AND deleted_at IS NULL AND is_master = TRUE",
                (user_id, except_id),
            )


def _create(conn: Any, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    summary = body.get("summary")
    is_master_in = body.get("is_master")

    # First resume → auto-master, regardless of what the caller passed.
    auto_master = not _has_any_resume(conn, user_id)
    is_master = bool(is_master_in) if is_master_in is not None else auto_master
    if auto_master:
        is_master = True

    logger.info(
        "resumes.create: inserting",
        extra={"user_id": user_id, "title_len": len(title), "is_master": is_master},
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO resumes (user_id, title, summary, is_master) VALUES (%s, %s, %s, %s)",
            (user_id, title, summary, is_master),
        )
        new_id = int(cur.lastrowid)

    if is_master:
        _clear_other_masters(conn, user_id, except_id=new_id)

    conn.commit()
    return _detail(conn, user_id, new_id)


def _detail(conn: Any, user_id: int, resume_id: int) -> dict[str, Any]:
    logger.info(
        "resumes.detail: querying",
        extra={"user_id": user_id, "resume_id": resume_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.id, r.title, r.summary, r.is_master,
                r.file_s3_key, r.original_filename, r.deleted_at,
                r.created_at, r.updated_at,
                (r.content_json IS NOT NULL) AS has_content_json,
                COUNT(s.id) AS submission_count
            FROM resumes r
            LEFT JOIN submissions s
                ON s.resume_id = r.id AND s.deleted_at IS NULL
            WHERE r.id = %s AND r.user_id = %s
            GROUP BY r.id, r.title, r.summary, r.is_master,
                     r.file_s3_key, r.original_filename, r.deleted_at,
                     r.created_at, r.updated_at, r.content_json
            """,
            (resume_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("resume not found")

        cur.execute(
            """
            SELECT
                s.id, s.role_title, s.submitted_on,
                ss.short_name AS status,
                c.name AS company_name
            FROM submissions s
            JOIN submission_statuses ss ON ss.id = s.submission_status_id
            LEFT JOIN companies c ON c.id = s.company_id AND c.deleted_at IS NULL
            WHERE s.resume_id = %s AND s.user_id = %s AND s.deleted_at IS NULL
            ORDER BY s.submitted_on DESC, s.id DESC
            """,
            (resume_id, user_id),
        )
        submissions = [
            {
                "id": int(r["id"]),
                "role_title": r["role_title"],
                "submitted_on": str(r["submitted_on"]) if r["submitted_on"] else None,
                "status": r["status"],
                "company_name": r["company_name"],
            }
            for r in cur.fetchall()
        ]

    detail = _row_to_summary(row)
    detail["submissions"] = submissions
    detail["download_url"] = (
        _presign_get(row["file_s3_key"], row.get("original_filename"))
        if row.get("file_s3_key")
        else None
    )
    return detail


_MUTABLE_FIELDS = {
    "title": "title = %s",
    "summary": "summary = %s",
}


def _update(
    conn: Any, user_id: int, resume_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    sets: list[str] = []
    params: list[Any] = []

    for field, fragment in _MUTABLE_FIELDS.items():
        if field in body:
            value = body[field]
            if field == "title":
                value = (value or "").strip()
                if not value:
                    raise ValueError("title cannot be blank")
            sets.append(fragment)
            params.append(value)

    is_master_change = "is_master" in body
    new_master_state = bool(body.get("is_master")) if is_master_change else None
    if is_master_change:
        sets.append("is_master = %s")
        params.append(new_master_state)

    if not sets:
        return _detail(conn, user_id, resume_id)

    params.extend([resume_id, user_id])
    logger.info(
        "resumes.update: updating",
        extra={
            "user_id": user_id,
            "resume_id": resume_id,
            "fields": list(body.keys()),
        },
    )
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE resumes SET {', '.join(sets)} "
            f"WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            tuple(params),
        )
        if cur.rowcount == 0:
            raise LookupError("resume not found")

    if is_master_change and new_master_state:
        _clear_other_masters(conn, user_id, except_id=resume_id)

    conn.commit()
    return _detail(conn, user_id, resume_id)


def _delete(conn: Any, user_id: int, resume_id: int) -> dict[str, Any]:
    logger.info(
        "resumes.delete: soft-deleting",
        extra={"user_id": user_id, "resume_id": resume_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE resumes SET deleted_at = CURRENT_TIMESTAMP, is_master = FALSE "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (resume_id, user_id),
        )
        if cur.rowcount == 0:
            raise LookupError("resume not found")
    conn.commit()
    return {"id": resume_id, "deleted": True}


def _restore(conn: Any, user_id: int, resume_id: int) -> dict[str, Any]:
    """Undo a soft-delete. ``is_master`` is left FALSE — caller re-elects masters explicitly."""
    logger.info(
        "resumes.restore: clearing deleted_at",
        extra={"user_id": user_id, "resume_id": resume_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE resumes SET deleted_at = NULL "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NOT NULL",
            (resume_id, user_id),
        )
        if cur.rowcount == 0:
            raise LookupError("resume not found or not deleted")
    conn.commit()
    return _detail(conn, user_id, resume_id)


def _purge(conn: Any, user_id: int, resume_id: int) -> dict[str, Any]:
    """Hard-delete a resume row. Only allowed on already soft-deleted rows.

    Drops the S3 file (best-effort — a missing object is logged but not fatal)
    then deletes the database row. Submissions referencing this resume are
    NULLed out via the FK ``ON DELETE SET NULL`` on ``submissions.resume_id``.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT file_s3_key FROM resumes "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NOT NULL",
            (resume_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError("resume not found or not soft-deleted")

    s3_key = row.get("file_s3_key")
    if s3_key and RESUME_BUCKET:
        logger.info(
            "resumes.purge: deleting S3 object",
            extra={"user_id": user_id, "resume_id": resume_id, "s3_key": s3_key},
        )
        try:
            _s3.delete_object(Bucket=RESUME_BUCKET, Key=s3_key)
        except Exception:
            logger.exception(
                "resumes.purge: S3 delete failed (continuing)",
                extra={"s3_key": s3_key},
            )

    logger.info(
        "resumes.purge: deleting row",
        extra={"user_id": user_id, "resume_id": resume_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM resumes WHERE id = %s AND user_id = %s",
            (resume_id, user_id),
        )
    conn.commit()
    return {"id": resume_id, "purged": True}


def _set_content(
    conn: Any, user_id: int, resume_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    """Validate ``body`` against ``ResumeContent`` and persist as JSON.

    Maintenance-only endpoint — no SPA UI calls it. Validation errors map
    to 400 via ``ValueError``; missing/not-owned rows map to 404.
    """
    logger.info(
        "resumes.set_content: validating",
        extra={"user_id": user_id, "resume_id": resume_id},
    )
    try:
        content = ResumeContent.model_validate(body)
    except ValidationError as e:
        raise ValueError(f"invalid content_json: {e.errors()}") from e

    serialized = json.dumps(content.model_dump(), ensure_ascii=False)
    logger.info(
        "resumes.set_content: persisting",
        extra={
            "user_id": user_id,
            "resume_id": resume_id,
            "content_bytes": len(serialized),
        },
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE resumes SET content_json = %s "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (serialized, resume_id, user_id),
        )
        if cur.rowcount == 0:
            raise LookupError("resume not found")
    conn.commit()
    return _detail(conn, user_id, resume_id)


def _read_base_content(
    conn: Any, user_id: int, base_resume_id: int
) -> ResumeContent:
    """Load and validate ``content_json`` for the user's base resume.

    Raises
    ------
    LookupError
        Base resume row does not exist or is not owned by this user.
    _ConflictError
        Row exists but ``content_json`` is NULL — user must PUT a body
        via the maintenance endpoint before tailoring is possible.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_json FROM resumes "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (base_resume_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError("base resume not found")
    raw = row.get("content_json")
    if raw is None:
        raise _ConflictError("base resume has no content_json")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        parsed = json.loads(raw)
    else:
        parsed = raw
    return ResumeContent.model_validate(parsed)


def _from_tailor(
    conn: Any,
    user_id: int,
    cognito_sub: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Render a tailored PDF from a base resume's ``content_json``.

    Inserts a new ``is_master=False`` resume row, uploads the rendered
    PDF to S3 with SSE-AES256, and returns the new row's detail JSON.
    """
    if not RESUME_BUCKET:
        raise RuntimeError("RESUME_BUCKET env var not configured")

    try:
        base_resume_id = int(body.get("base_resume_id"))
    except (TypeError, ValueError) as e:
        raise ValueError("base_resume_id is required and must be an integer") from e
    tailored_title = (body.get("tailored_title") or "").strip()
    tailored_summary = (body.get("tailored_summary") or "").strip()
    if not tailored_title:
        raise ValueError("tailored_title is required")
    if not tailored_summary:
        raise ValueError("tailored_summary is required")
    if len(tailored_title) > MAX_TAILORED_TITLE_CHARS:
        raise ValueError(
            f"tailored_title too long (max {MAX_TAILORED_TITLE_CHARS} chars)"
        )
    if len(tailored_summary) > MAX_TAILORED_SUMMARY_CHARS:
        raise ValueError(
            f"tailored_summary too long (max {MAX_TAILORED_SUMMARY_CHARS} chars)"
        )

    logger.info(
        "resumes.from_tailor: loading base",
        extra={"user_id": user_id, "base_resume_id": base_resume_id},
    )
    content = _read_base_content(conn, user_id, base_resume_id)

    logger.info(
        "resumes.from_tailor: rendering pdf",
        extra={"user_id": user_id, "base_resume_id": base_resume_id},
    )
    template = build_template(content, tailored_title, tailored_summary)
    generator = PdfGenerator(template, fonts_dir=FONTS_DIR)
    pdf_bytes = bytes(
        generator.generate(
            {"tailored_title": tailored_title, "tailored_summary": tailored_summary}
        )
    )
    logger.info(
        "resumes.from_tailor: pdf rendered",
        extra={
            "user_id": user_id,
            "base_resume_id": base_resume_id,
            "pdf_bytes": len(pdf_bytes),
        },
    )

    logger.info(
        "resumes.from_tailor: inserting row",
        extra={"user_id": user_id, "base_resume_id": base_resume_id},
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO resumes (user_id, title, summary, is_master) "
            "VALUES (%s, %s, %s, FALSE)",
            (user_id, tailored_title, tailored_summary),
        )
        new_id = int(cur.lastrowid)

    key = f"users/{cognito_sub}/resumes/{new_id}/{TAILORED_FILENAME}"
    logger.info(
        "resumes.from_tailor: putting to s3",
        extra={
            "user_id": user_id,
            "resume_id": new_id,
            "s3_key": key,
            "pdf_bytes": len(pdf_bytes),
        },
    )
    _s3.put_object(
        Bucket=RESUME_BUCKET,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE resumes SET file_s3_key = %s, original_filename = %s "
            "WHERE id = %s AND user_id = %s",
            (key, TAILORED_FILENAME, new_id, user_id),
        )
    conn.commit()

    return _detail(conn, user_id, new_id)


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    """Sanitize a user-supplied filename for use in an S3 key.

    Strips path separators and anything that isn't ``[A-Za-z0-9._-]``. Does
    not enforce an extension — that's the caller's job (PDF-only is checked
    against ``content_type``).
    """
    base = name.strip().split("/")[-1].split("\\")[-1]
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("._-")
    return cleaned or "resume.pdf"


def _upload_url(
    conn: Any, user_id: int, cognito_sub: str, resume_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    if not RESUME_BUCKET:
        raise RuntimeError("RESUME_BUCKET env var not configured")

    content_type = body.get("content_type") or "application/pdf"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(f"unsupported content_type: {content_type}")

    raw_filename = body.get("original_filename") or "resume.pdf"
    filename = _safe_filename(raw_filename)
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    content_length = body.get("content_length")
    if content_length is not None:
        try:
            content_length = int(content_length)
        except (TypeError, ValueError):
            raise ValueError("content_length must be an integer")
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"content_length out of range (1..{MAX_UPLOAD_BYTES} bytes)"
            )

    # Verify the resume row belongs to the caller before exposing a presigned URL.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM resumes "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (resume_id, user_id),
        )
        if not cur.fetchone():
            raise LookupError("resume not found")

    key = f"users/{cognito_sub}/resumes/{resume_id}/{filename}"
    logger.info(
        "resumes.upload-url: signing",
        extra={
            "user_id": user_id,
            "resume_id": resume_id,
            "s3_key": key,
            "content_type": content_type,
        },
    )
    url = _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": RESUME_BUCKET,
            "Key": key,
            "ContentType": content_type,
            "ServerSideEncryption": "AES256",
        },
        ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        HttpMethod="PUT",
    )

    # Optimistically record the key + filename now. The browser PUT may fail;
    # in that case the row points at a missing object, and the GET path
    # treats that as "no download available" (matches the JD-snapshot
    # graceful-miss pattern in submissions._read_jd_text).
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE resumes SET file_s3_key = %s, original_filename = %s "
            "WHERE id = %s AND user_id = %s",
            (key, filename, resume_id, user_id),
        )
    conn.commit()

    return {
        "url": url,
        "key": key,
        "content_type": content_type,
        "expires_in_seconds": UPLOAD_URL_TTL_SECONDS,
        "max_bytes": MAX_UPLOAD_BYTES,
    }


def _presign_get(s3_key: str, original_filename: str | None) -> str | None:
    """Return a 5-minute presigned GET URL for the resume file.

    A ``Content-Disposition`` header pinning the download filename is baked
    into the signature so the browser saves the file with the original name.
    Returns ``None`` if no resume bucket is configured (tests / local).
    """
    if not RESUME_BUCKET:
        return None
    params: dict[str, Any] = {"Bucket": RESUME_BUCKET, "Key": s3_key}
    if original_filename:
        params["ResponseContentDisposition"] = (
            f'inline; filename="{original_filename}"'
        )
    try:
        return _s3.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
            HttpMethod="GET",
        )
    except Exception:
        logger.exception("resumes.presign_get: failed", extra={"s3_key": s3_key})
        return None


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("resumes: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if route_key == "GET /resumes":
                qs = event.get("queryStringParameters") or {}
                result: Any = _list(conn, user_id, qs)
            elif route_key == "POST /resumes":
                body = json.loads(event.get("body") or "{}")
                result = _create(conn, user_id, body)
            elif route_key == "GET /resumes/{id}":
                result = _detail(conn, user_id, _path_id(event))
            elif route_key == "PUT /resumes/{id}":
                body = json.loads(event.get("body") or "{}")
                result = _update(conn, user_id, _path_id(event), body)
            elif route_key == "DELETE /resumes/{id}":
                result = _delete(conn, user_id, _path_id(event))
            elif route_key == "POST /resumes/{id}/restore":
                result = _restore(conn, user_id, _path_id(event))
            elif route_key == "POST /resumes/{id}/purge":
                result = _purge(conn, user_id, _path_id(event))
            elif route_key == "POST /resumes/{id}/upload-url":
                body = json.loads(event.get("body") or "{}")
                result = _upload_url(conn, user_id, sub, _path_id(event), body)
            elif route_key == "PUT /resumes/{id}/content":
                body = json.loads(event.get("body") or "{}")
                result = _set_content(conn, user_id, _path_id(event), body)
            elif route_key == "POST /resumes/from-tailor":
                body = json.loads(event.get("body") or "{}")
                result = _from_tailor(conn, user_id, sub, body)
            else:
                logger.warning("resumes: unknown route", extra={"route_key": route_key})
                return _response(404, {"error": f"no handler for {route_key}"})
        logger.info("resumes: exit ok", extra={"user_sub": sub, "route_key": route_key})
        return _response(200, result)
    except LookupError as e:
        logger.warning(
            "resumes: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except _ConflictError as e:
        logger.warning(
            "resumes: conflict",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(409, {"error": str(e)})
    except ValueError as e:
        logger.exception("resumes: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception("resumes: failed", extra={"user_sub": sub, "route_key": route_key})
        raise