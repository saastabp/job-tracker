"""EventBridge-scheduled poller — fetches new messages for each user's linked threads.

Invoked by an EventBridge rate(10 minutes) rule from the gmail stack
(``infra/gmail/template.yaml``). The event payload is the synthetic
"Scheduled Event" envelope; the handler doesn't read anything from it.

Per-user loop, one DB connection per user so one user's failure is
isolated from the rest. For each user with active ``gmail_credentials``,
the handler enumerates the union of the user's
``submissions.gmail_thread_id`` and ``contact_outreach.gmail_thread_id``
values (slice 10 — contact-only threads are now polled too), calls
``users.threads.get(format='full')`` once per unique thread, walks the
messages, and for each non-self-sent message:

  * archives raw RFC 822 bytes to the gmail-stack S3 bucket
    (best-effort; archive failure doesn't block row inserts)
  * for every submission whose ``gmail_thread_id`` matches the thread:
    inserts a ``responses`` row (INSERT IGNORE on
    ``uq_responses_gmail_message`` for idempotency); classifies via the
    heuristic patterns; bumps the submission's status on
    ``rejection`` / ``interview_invite``
  * if the thread also has ``contact_outreach`` rows: inserts an
    inbound ``contact_outreach`` row tagged to the thread's most-recent
    existing contact_id (Fork 1 — slice 10 plan), via INSERT IGNORE on
    ``uq_contact_outreach_gmail_message``

Self-sent messages are filtered out by ``from`` matching the user's
``gmail_address`` from credentials. The user's outbound — written by
the compose handler at send time — already lives in
``contact_outreach`` and/or via ``submissions.gmail_thread_id``; the
poller's fetch returns it but the self-sent filter drops it before any
insert is attempted.

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


def _load_catalog_ids(
    conn: Any,
) -> tuple[dict[str, int], dict[str, int], int, int]:
    """Return (classifications, statuses, email_method_id, inbound_direction_id).

    The poller writes to ``responses`` (needs response_classifications +
    submission_statuses for the status bump) AND to ``contact_outreach``
    (needs outreach_methods.email + outreach_directions.inbound).
    """
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

        cur.execute(
            "SELECT id FROM outreach_methods "
            "WHERE short_name = 'email' AND deleted_at IS NULL"
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("outreach_methods.short_name='email' not found")
        email_method_id = int(row["id"])

        cur.execute(
            "SELECT id FROM outreach_directions "
            "WHERE short_name = 'inbound' AND deleted_at IS NULL"
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(
                "outreach_directions.short_name='inbound' not found"
            )
        inbound_direction_id = int(row["id"])

    return classifications, statuses, email_method_id, inbound_direction_id


# ---------------------------------------------------------------------------
# Per-message processing
# ---------------------------------------------------------------------------


def _archive_raw(
    *,
    service: Any,
    user_id: int,
    msg_id: str,
    archive_bucket: str,
) -> str | None:
    """Fetch raw RFC 822 bytes and store in S3. Returns the S3 key, or None on failure.

    Best-effort: a 5xx from Gmail or a bucket-side failure returns None
    rather than raising. The caller falls through to writing rows with
    ``raw_email_s3_key = NULL``.
    """
    if not archive_bucket:
        return None
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
        return s3_key
    except Exception:
        logger.exception(
            "gmail_poller: S3 archive failed (continuing with row inserts)",
            extra={"user_id": user_id, "gmail_message_id": msg_id},
        )
        return None


def _insert_response_row(
    *,
    conn: Any,
    user_id: int,
    submission_id: int,
    msg_id: str,
    received_at: datetime,
    from_email: str,
    subject: str,
    body_text: str,
    s3_key: str | None,
    classification_short: str,
    classifications: dict[str, int],
    statuses: dict[str, int],
) -> str:
    """Insert into ``responses`` and bump submission status if applicable.

    Returns ``"inserted"`` on a successful new row, or ``"skipped:exists"``
    when ``INSERT IGNORE`` matched the unique key on ``gmail_message_id``.
    """
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


def _insert_contact_outreach_row(
    *,
    conn: Any,
    user_id: int,
    contact_id: int,
    msg_id: str,
    thread_id: str,
    received_at: datetime,
    from_email: str,
    subject: str,
    body_text: str,
    email_method_id: int,
    inbound_direction_id: int,
) -> str:
    """Insert an inbound ``contact_outreach`` row. ``INSERT IGNORE`` for idempotency."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT IGNORE INTO contact_outreach
                (user_id, contact_id, outreach_at,
                 outreach_method_id, outreach_direction_id,
                 gmail_thread_id, gmail_message_id,
                 subject, body_text, from_email)
            VALUES (%s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s)
            """,
            (
                user_id, contact_id, received_at,
                email_method_id, inbound_direction_id,
                thread_id, msg_id,
                subject[:998] if subject else None,
                body_text or None,
                from_email,
            ),
        )
        inserted = cur.rowcount > 0
        conn.commit()

    if not inserted:
        logger.info(
            "gmail_poller: contact_outreach row already present, skipping",
            extra={
                "contact_id": contact_id,
                "gmail_message_id": msg_id,
            },
        )
        return "skipped:exists"

    logger.info(
        "gmail_poller: contact_outreach row inserted",
        extra={
            "contact_id": contact_id,
            "gmail_message_id": msg_id,
            "from_email_domain": from_email.split("@")[-1] if "@" in from_email else None,
        },
    )
    return "inserted"


def _resolve_thread_anchors(
    conn: Any, user_id: int, thread_id: str
) -> tuple[list[int], int | None]:
    """Look up what a thread maps to: list of submission_ids + most-recent contact_id.

    Returns
    -------
    (submission_ids, most_recent_contact_id)
        Both can be empty / None — a thread might be linked only to
        submissions, only to contacts, both, or in pathological cases
        neither (e.g. the user unlinked everything between enumeration
        and per-thread fetch). The caller decides what to do.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM submissions "
            "WHERE user_id = %s AND gmail_thread_id = %s "
            "  AND deleted_at IS NULL",
            (user_id, thread_id),
        )
        submission_ids = [int(r["id"]) for r in cur.fetchall()]

        # Most-recent contact_id on this thread (Fork 1).
        cur.execute(
            "SELECT contact_id FROM contact_outreach "
            "WHERE user_id = %s AND gmail_thread_id = %s "
            "  AND deleted_at IS NULL "
            "ORDER BY outreach_at DESC, id DESC LIMIT 1",
            (user_id, thread_id),
        )
        row = cur.fetchone()
        contact_id = int(row["contact_id"]) if row else None

    return submission_ids, contact_id


def _process_thread(
    *,
    conn: Any,
    service: Any,
    user_id: int,
    thread_id: str,
    gmail_address: str,
    classifications: dict[str, int],
    statuses: dict[str, int],
    email_method_id: int,
    inbound_direction_id: int,
    archive_bucket: str,
) -> dict[str, int]:
    """Resolve anchors and dual-write each non-self-sent message. Returns counters.

    Used by the per-cycle poller. Callers that already know the anchors
    (e.g. the sync ``gmail_admin`` import path) should call
    ``process_thread_messages`` directly instead.
    """
    submission_ids, contact_id = _resolve_thread_anchors(conn, user_id, thread_id)
    if not submission_ids and contact_id is None:
        logger.info(
            "gmail_poller: thread has no live anchors, skipping",
            extra={"user_id": user_id, "thread_id": thread_id},
        )
        return {
            "responses_inserted": 0,
            "responses_skipped:exists": 0,
            "contact_outreach_inserted": 0,
            "contact_outreach_skipped:exists": 0,
            "skipped:self_sent": 0,
        }

    return process_thread_messages(
        conn=conn,
        service=service,
        user_id=user_id,
        thread_id=thread_id,
        gmail_address=gmail_address,
        submission_ids=submission_ids,
        contact_id=contact_id,
        classifications=classifications,
        statuses=statuses,
        email_method_id=email_method_id,
        inbound_direction_id=inbound_direction_id,
        archive_bucket=archive_bucket,
    )


def process_thread_messages(
    *,
    conn: Any,
    service: Any,
    user_id: int,
    thread_id: str,
    gmail_address: str,
    submission_ids: list[int],
    contact_id: int | None,
    classifications: dict[str, int],
    statuses: dict[str, int],
    email_method_id: int,
    inbound_direction_id: int,
    archive_bucket: str,
) -> dict[str, int]:
    """Fetch a thread and dual-write each non-self-sent message.

    The anchors (``submission_ids`` and ``contact_id``) are explicit —
    no DB lookup happens inside this function, so callers can override
    the "most-recent contact" heuristic the poller uses.

    Pass ``archive_bucket=""`` to skip the S3 raw-bytes archive (used
    by the sync gmail-link handler that has no S3 perms).
    """
    counters = {
        "responses_inserted": 0,
        "responses_skipped:exists": 0,
        "contact_outreach_inserted": 0,
        "contact_outreach_skipped:exists": 0,
        "skipped:self_sent": 0,
    }

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
            "thread_id": thread_id,
            "message_count": len(messages),
            "submission_count": len(submission_ids),
            "has_contact_anchor": contact_id is not None,
        },
    )

    for msg in messages:
        try:
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
                        "thread_id": thread_id,
                        "gmail_message_id": msg_id,
                    },
                )
                counters["skipped:self_sent"] += 1
                continue

            received_at = _internal_date_to_datetime(msg["internalDate"])
            body_text = _extract_body_text(payload)
            classification_short = classify(subject, body_text)

            s3_key = _archive_raw(
                service=service,
                user_id=user_id,
                msg_id=msg_id,
                archive_bucket=archive_bucket,
            )

            for sub_id in submission_ids:
                outcome = _insert_response_row(
                    conn=conn,
                    user_id=user_id,
                    submission_id=sub_id,
                    msg_id=msg_id,
                    received_at=received_at,
                    from_email=from_email,
                    subject=subject,
                    body_text=body_text,
                    s3_key=s3_key,
                    classification_short=classification_short,
                    classifications=classifications,
                    statuses=statuses,
                )
                key = f"responses_{outcome}"
                counters[key] = counters.get(key, 0) + 1

            if contact_id is not None:
                outcome = _insert_contact_outreach_row(
                    conn=conn,
                    user_id=user_id,
                    contact_id=contact_id,
                    msg_id=msg_id,
                    thread_id=thread_id,
                    received_at=received_at,
                    from_email=from_email,
                    subject=subject,
                    body_text=body_text,
                    email_method_id=email_method_id,
                    inbound_direction_id=inbound_direction_id,
                )
                key = f"contact_outreach_{outcome}"
                counters[key] = counters.get(key, 0) + 1
        except Exception:
            logger.exception(
                "gmail_poller: message processing failed (continuing)",
                extra={
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "gmail_message_id": msg.get("id"),
                },
            )
    return counters


def _enumerate_thread_ids(conn: Any, user_id: int) -> list[str]:
    """Return the union of submission- and contact-linked thread ids for a user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT thread_id FROM (
                SELECT gmail_thread_id AS thread_id FROM submissions
                 WHERE user_id = %s AND deleted_at IS NULL
                   AND gmail_thread_id IS NOT NULL
                UNION
                SELECT gmail_thread_id AS thread_id FROM contact_outreach
                 WHERE user_id = %s AND deleted_at IS NULL
                   AND gmail_thread_id IS NOT NULL
            ) t
            """,
            (user_id, user_id),
        )
        return [row["thread_id"] for row in cur.fetchall()]


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

    classifications, statuses, email_method_id, inbound_direction_id = (
        _load_catalog_ids(conn)
    )
    thread_ids = _enumerate_thread_ids(conn, user_id)

    logger.info(
        "gmail_poller: user threads enumerated",
        extra={
            "user_id": user_id,
            "linked_thread_count": len(thread_ids),
            "gmail_address": gmail_address,
        },
    )

    aggregate: dict[str, int] = {}
    for thread_id in thread_ids:
        try:
            counters = _process_thread(
                conn=conn,
                service=service,
                user_id=user_id,
                thread_id=thread_id,
                gmail_address=gmail_address,
                classifications=classifications,
                statuses=statuses,
                email_method_id=email_method_id,
                inbound_direction_id=inbound_direction_id,
                archive_bucket=archive_bucket,
            )
            for k, v in counters.items():
                aggregate[k] = aggregate.get(k, 0) + v
        except Exception:
            logger.exception(
                "gmail_poller: thread processing failed (continuing)",
                extra={"user_id": user_id, "thread_id": thread_id},
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

    aggregate: dict[str, int] = {}
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