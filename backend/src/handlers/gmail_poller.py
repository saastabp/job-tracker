"""EventBridge-scheduled poller — fetches new responses for each user's linked threads.

Invoked by an EventBridge rate(10 minutes) rule from the gmail stack
(``infra/gmail/template.yaml``). The event payload is the synthetic
"Scheduled Event" envelope; the handler doesn't read anything from it.

Per-user loop, one DB connection per user so one user's failure is
isolated from the rest. For each user with active ``gmail_credentials``,
the handler enumerates every submission with a non-NULL
``gmail_thread_id``, calls ``users.threads.get(format='full')`` once per
thread, walks the messages, and for each:

  * filters out self-sent messages (``from`` matches the user's
    ``gmail_address`` from credentials — Decision per slice plan; the
    user's own outbound is implicit in the success of their compose UX)
  * skips messages already in ``responses`` (the
    ``uq_responses_gmail_message`` unique key makes ``INSERT IGNORE``
    the idempotency primitive)
  * classifies via heuristic-only patterns (slice 09 Decision 2 — AI
    fallback deferred until real miss patterns surface)
  * archives raw RFC 822 bytes to the gmail-stack S3 bucket
    (``responses.raw_email_s3_key`` points there). Best-effort: an
    archive failure doesn't block the row insert.
  * inserts a ``responses`` row, optionally bumps the submission's
    status on ``rejection`` / ``interview_invite``

After all threads for a user are processed, ``last_polled_at`` is
updated. ``last_history_id`` is left NULL for now — we don't use the
history.list endpoint in v1 (cheaper to re-fetch threads with INSERT
IGNORE deduping than to manage cursor state). The column is in the
schema for future use if quota becomes a concern.
"""
from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any

import boto3

from common.db import get_connection
from common.gmail_client import (
    build_credentials,
    build_gmail_service,
    decrypt_refresh_token,
)
from common.logger import logger

GMAIL_KMS_KEY_ID = os.environ.get("GMAIL_KMS_KEY_ID", "")
GMAIL_OAUTH_CLIENT_SSM_PATH = os.environ.get(
    "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
)
GMAIL_ARCHIVE_BUCKET = os.environ.get("GMAIL_ARCHIVE_BUCKET", "")

_ssm: Any = None
_s3: Any = None


def _get_ssm() -> Any:
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def _get_s3() -> Any:
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _get_oauth_client_credentials() -> tuple[str, str]:
    import json

    response = _get_ssm().get_parameter(
        Name=GMAIL_OAUTH_CLIENT_SSM_PATH, WithDecryption=True
    )
    payload = json.loads(response["Parameter"]["Value"])
    return payload["client_id"], payload["client_secret"]


# ---------------------------------------------------------------------------
# Heuristic classifier
# ---------------------------------------------------------------------------
#
# Each pattern is matched (case-insensitive, word-boundaried) against
# `subject\nbody`. First match wins; no match → `other`. Per slice 09
# Decision 2: heuristic-only for v1; AI fallback can be added later when
# observed misses justify the wiring cost.

_CLASSIFIER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "rejection",
        re.compile(
            r"\b(unfortunately|not moving forward|"
            r"will not be moving forward|not selected|not the right fit|"
            r"other candidates|decided to (move forward|proceed) with)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "interview_invite",
        re.compile(
            r"\b(schedule (an? )?(call|meeting|interview)|"
            r"calendly\.com|book a time|interview invitation|"
            r"would love to (chat|talk|meet)|next steps in (the|our) (process|interview))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "auto_ack",
        re.compile(
            r"\b(received your application|application has been received|"
            r"thank you for applying|will review|application is under review|"
            r"keep your (resume|application) on file)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "recruiter_outreach",
        re.compile(
            r"\b(came across your profile|reaching out about|"
            r"interested in your background|opportunity at|"
            r"role at|exciting opportunity|connect about)\b",
            re.IGNORECASE,
        ),
    ),
]

_STATUS_BUMP_MAP = {
    "rejection": "rejected",
    "interview_invite": "interviewing",
}


def classify(subject: str, body: str) -> str:
    """Return the short_name of the matching response_classification, or 'other'."""
    text = f"{subject}\n{body}"
    for short_name, pattern in _CLASSIFIER_PATTERNS:
        if pattern.search(text):
            return short_name
    return "other"


# ---------------------------------------------------------------------------
# Gmail message → fields
# ---------------------------------------------------------------------------


def _extract_headers(payload: dict[str, Any]) -> dict[str, str]:
    """Lower-case the header names so lookups are case-insensitive."""
    return {
        h["name"].lower(): h["value"]
        for h in payload.get("headers", [])
        if h.get("name") and h.get("value") is not None
    }


def _extract_from_email(headers: dict[str, str]) -> str:
    """Pull the bare email address out of a `From:` header.

    `parseaddr` handles ``"Name" <addr@dom>`` and bare ``addr@dom`` correctly.
    Falls back to the raw header on parse failure (caller handles empty-string
    by treating it as "unknown sender").
    """
    raw = headers.get("from", "")
    _name, email_addr = parseaddr(raw)
    return email_addr or raw


def _extract_body_text(payload: dict[str, Any]) -> str:
    """Walk the MIME tree and return the first text/plain part's text.

    Falls back to text/html (stripped of tags) only if no text/plain
    exists anywhere in the tree. Two-pass walk so a text/html part that
    happens to appear before its sibling text/plain doesn't win — the
    typical multipart/alternative email puts text/html first because
    it's the "richer" representation, but we want the cleaner
    classifier input.
    """
    plain = _find_part_text(payload, "text/plain")
    if plain:
        return plain
    html = _find_part_text(payload, "text/html")
    if html:
        # Crude tag strip. Classifier patterns are word-based, so HTML
        # entities and <tag> noise rarely trigger false matches.
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _find_part_text(payload: dict[str, Any], target_mime: str) -> str:
    """Recursively search the MIME tree for the first part of ``target_mime``."""
    if payload.get("mimeType") == target_mime:
        body_data = payload.get("body", {}).get("data")
        if body_data:
            return _decode_b64url(body_data)
    for part in payload.get("parts", []):
        text = _find_part_text(part, target_mime)
        if text:
            return text
    return ""


def _decode_b64url(data: str) -> str:
    """Gmail API returns body data as base64url. Decode and best-effort UTF-8."""
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _internal_date_to_datetime(internal_date_ms: str) -> datetime:
    """Gmail's `internalDate` is a string ms-since-epoch."""
    return datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Catalog lookups (cached per Lambda invocation)
# ---------------------------------------------------------------------------


def _load_catalog_ids(conn: Any) -> tuple[dict[str, int], dict[str, int]]:
    """Return (classification_short_name → id, status_short_name → id)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT short_name, id FROM response_classifications "
            "WHERE deleted_at IS NULL"
        )
        classifications = {row["short_name"]: int(row["id"]) for row in cur.fetchall()}

        cur.execute(
            "SELECT short_name, id FROM submission_statuses "
            "WHERE deleted_at IS NULL"
        )
        statuses = {row["short_name"]: int(row["id"]) for row in cur.fetchall()}
    return classifications, statuses


# ---------------------------------------------------------------------------
# Per-message processing
# ---------------------------------------------------------------------------


def _process_message(
    *,
    conn: Any,
    service: Any,
    user_id: int,
    submission_id: int,
    msg: dict[str, Any],
    gmail_address: str,
    classifications: dict[str, int],
    statuses: dict[str, int],
    archive_bucket: str,
) -> str:
    """Process one Gmail message. Returns one of: skipped:self_sent, skipped:exists, inserted."""
    msg_id = msg["id"]
    payload = msg.get("payload", {})
    headers = _extract_headers(payload)
    from_email = _extract_from_email(headers)
    subject = headers.get("subject", "")

    if from_email and from_email.lower() == gmail_address.lower():
        logger.info(
            "gmail_poller: skipping self-sent message",
            extra={
                "user_id": user_id,
                "submission_id": submission_id,
                "gmail_message_id": msg_id,
            },
        )
        return "skipped:self_sent"

    received_at = _internal_date_to_datetime(msg["internalDate"])
    body_text = _extract_body_text(payload)

    # Fetch raw bytes for S3 archive. Best-effort — a 5xx from Gmail or a
    # bucket-side failure shouldn't block the response row from landing.
    s3_key: str | None = None
    if archive_bucket:
        try:
            raw_msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="raw")
                .execute()
            )
            raw_bytes = base64.urlsafe_b64decode(raw_msg["raw"])
            s3_key = f"{user_id}/{msg_id}.eml"
            _get_s3().put_object(
                Bucket=archive_bucket,
                Key=s3_key,
                Body=raw_bytes,
                ContentType="message/rfc822",
            )
            logger.info(
                "gmail_poller: archived raw email",
                extra={
                    "user_id": user_id,
                    "gmail_message_id": msg_id,
                    "s3_key": s3_key,
                    "raw_bytes": len(raw_bytes),
                },
            )
        except Exception:
            logger.exception(
                "gmail_poller: S3 archive failed (continuing with row insert)",
                extra={"user_id": user_id, "gmail_message_id": msg_id},
            )
            s3_key = None

    classification_short = classify(subject, body_text)
    classification_id = classifications.get(classification_short)
    if classification_id is None:
        # `other` is seeded; if it's missing, the migration wasn't applied.
        # Hard fail rather than silently mis-categorize.
        raise RuntimeError(
            f"response_classifications.short_name='{classification_short}' not found"
        )

    with conn.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO responses "
            "(submission_id, response_classification_id, received_at, "
            " from_email, subject, body_text, raw_email_s3_key, "
            " gmail_message_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                submission_id,
                classification_id,
                received_at,
                from_email,
                subject[:998] if subject else None,  # respect column limit
                body_text or None,
                s3_key,
                msg_id,
            ),
        )
        inserted = cur.rowcount > 0
        if not inserted:
            logger.info(
                "gmail_poller: response already present, skipping",
                extra={"submission_id": submission_id, "gmail_message_id": msg_id},
            )
            conn.commit()
            return "skipped:exists"

        # Status bump (if applicable)
        target_status_short = _STATUS_BUMP_MAP.get(classification_short)
        if target_status_short:
            target_status_id = statuses.get(target_status_short)
            if target_status_id is None:
                raise RuntimeError(
                    f"submission_statuses.short_name='{target_status_short}' not found"
                )
            cur.execute(
                "UPDATE submissions SET submission_status_id = %s "
                "WHERE id = %s AND user_id = %s",
                (target_status_id, submission_id, user_id),
            )
            logger.info(
                "gmail_poller: status bumped",
                extra={
                    "submission_id": submission_id,
                    "from_classification": classification_short,
                    "to_status": target_status_short,
                },
            )
        conn.commit()

    logger.info(
        "gmail_poller: response inserted",
        extra={
            "submission_id": submission_id,
            "gmail_message_id": msg_id,
            "classification": classification_short,
            "from_email_domain": from_email.split("@")[-1] if "@" in from_email else None,
        },
    )
    return "inserted"


def _process_thread(
    *,
    conn: Any,
    service: Any,
    user_id: int,
    submission_id: int,
    thread_id: str,
    gmail_address: str,
    classifications: dict[str, int],
    statuses: dict[str, int],
    archive_bucket: str,
) -> dict[str, int]:
    """Fetch a thread, process each message. Returns counters by status."""
    counters = {"inserted": 0, "skipped:self_sent": 0, "skipped:exists": 0}
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    messages = thread.get("messages", [])
    logger.info(
        "gmail_poller: thread fetched",
        extra={
            "user_id": user_id,
            "submission_id": submission_id,
            "thread_id": thread_id,
            "message_count": len(messages),
        },
    )
    for msg in messages:
        try:
            outcome = _process_message(
                conn=conn,
                service=service,
                user_id=user_id,
                submission_id=submission_id,
                msg=msg,
                gmail_address=gmail_address,
                classifications=classifications,
                statuses=statuses,
                archive_bucket=archive_bucket,
            )
            counters[outcome] = counters.get(outcome, 0) + 1
        except Exception:
            logger.exception(
                "gmail_poller: message processing failed (continuing)",
                extra={
                    "user_id": user_id,
                    "submission_id": submission_id,
                    "gmail_message_id": msg.get("id"),
                },
            )
    return counters


def _process_user(
    conn: Any,
    user_id: int,
    gmail_address: str,
    refresh_token_ciphertext: bytes,
    scopes: str,
    archive_bucket: str,
) -> dict[str, Any]:
    """Process one user's linked threads. Returns aggregate counters."""
    refresh_token = decrypt_refresh_token(
        bytes(refresh_token_ciphertext), GMAIL_KMS_KEY_ID
    )
    client_id, client_secret = _get_oauth_client_credentials()
    credentials = build_credentials(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes.split(),
    )
    service = build_gmail_service(credentials)

    classifications, statuses = _load_catalog_ids(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, gmail_thread_id FROM submissions "
            "WHERE user_id = %s AND deleted_at IS NULL "
            "  AND gmail_thread_id IS NOT NULL",
            (user_id,),
        )
        linked = list(cur.fetchall())

    logger.info(
        "gmail_poller: user threads enumerated",
        extra={
            "user_id": user_id,
            "linked_thread_count": len(linked),
            "gmail_address": gmail_address,
        },
    )

    aggregate = {"inserted": 0, "skipped:self_sent": 0, "skipped:exists": 0}
    for sub_row in linked:
        try:
            counters = _process_thread(
                conn=conn,
                service=service,
                user_id=user_id,
                submission_id=int(sub_row["id"]),
                thread_id=sub_row["gmail_thread_id"],
                gmail_address=gmail_address,
                classifications=classifications,
                statuses=statuses,
                archive_bucket=archive_bucket,
            )
            for k, v in counters.items():
                aggregate[k] = aggregate.get(k, 0) + v
        except Exception:
            logger.exception(
                "gmail_poller: thread processing failed (continuing)",
                extra={
                    "user_id": user_id,
                    "submission_id": int(sub_row["id"]),
                    "thread_id": sub_row["gmail_thread_id"],
                },
            )

    # Mark the user as polled even if some threads failed.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gmail_credentials "
            "SET last_polled_at = CURRENT_TIMESTAMP "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        conn.commit()

    return aggregate


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    logger.info(
        "gmail_poller: enter",
        extra={"archive_bucket": GMAIL_ARCHIVE_BUCKET, "kms_set": bool(GMAIL_KMS_KEY_ID)},
    )

    if not GMAIL_KMS_KEY_ID or not GMAIL_ARCHIVE_BUCKET:
        logger.error(
            "gmail_poller: required env vars missing",
            extra={
                "kms_key_set": bool(GMAIL_KMS_KEY_ID),
                "archive_bucket_set": bool(GMAIL_ARCHIVE_BUCKET),
            },
        )
        return {"users_processed": 0, "error": "misconfigured"}

    # List of (user_id, gmail_address, refresh_token_ciphertext, scopes)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, gmail_address, refresh_token_ciphertext, scopes "
                "FROM gmail_credentials "
                "WHERE deleted_at IS NULL"
            )
            active = list(cur.fetchall())

    logger.info(
        "gmail_poller: active users", extra={"user_count": len(active)}
    )

    aggregate = {"inserted": 0, "skipped:self_sent": 0, "skipped:exists": 0}
    failures = 0
    for user in active:
        try:
            with get_connection() as conn:
                counters = _process_user(
                    conn=conn,
                    user_id=int(user["user_id"]),
                    gmail_address=user["gmail_address"],
                    refresh_token_ciphertext=user["refresh_token_ciphertext"],
                    scopes=user["scopes"],
                    archive_bucket=GMAIL_ARCHIVE_BUCKET,
                )
            for k, v in counters.items():
                aggregate[k] = aggregate.get(k, 0) + v
        except Exception:
            failures += 1
            logger.exception(
                "gmail_poller: user processing failed (continuing to next)",
                extra={"user_id": int(user["user_id"])},
            )

    logger.info(
        "gmail_poller: exit",
        extra={
            "users_processed": len(active),
            "users_failed": failures,
            **aggregate,
        },
    )
    return {
        "users_processed": len(active),
        "users_failed": failures,
        **aggregate,
    }