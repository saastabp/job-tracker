"""Scheduler-stack Lambda: send a follow-up reminder email via SES.

EventBridge Scheduler invokes this with payload ``{"follow_up_id": <int>}``.
Each schedule is one-shot and self-deleting (``ActionAfterCompletion: DELETE``)
so we don't worry about idempotency from re-fires; we DO worry about a
concurrent user-initiated cancel having raced ahead of us — hence the row
re-check at the top of ``_send``.

Behavior
--------
1. Load the follow-up + its submission + the user's email.
2. Skip silently if the row was actioned, soft-deleted, or already notified
   (re-invocation safety).
3. Render a plain-text reminder, send via SES, set ``follow_ups.notified_at``.

Errors raise so EventBridge Scheduler's retry/DLQ behavior takes over (the
scheduler stack is the right place to add a DLQ later if missed reminders
become a problem).
"""
from __future__ import annotations

import os
from typing import Any

import boto3

from common.db import get_connection
from common.logger import logger

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")

_ses: Any = None


def _get_ses() -> Any:
    global _ses
    if _ses is None:
        _ses = boto3.client("ses", region_name=AWS_REGION)
    return _ses


def _load(conn: Any, follow_up_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                f.id, f.due_at, f.actioned_at, f.notified_at, f.notes,
                s.id AS submission_id, s.role_title,
                s.submitted_on,
                c.name AS company_name,
                u.email AS user_email,
                u.display_name AS user_display_name
            FROM follow_ups f
            JOIN submissions s ON s.id = f.submission_id
            JOIN users u       ON u.id = s.user_id
            LEFT JOIN companies c ON c.id = s.company_id AND c.deleted_at IS NULL
            WHERE f.id = %s
              AND f.deleted_at IS NULL
              AND s.deleted_at IS NULL
              AND u.deleted_at IS NULL
            """,
            (follow_up_id,),
        )
        return cur.fetchone()


def _render(row: dict[str, Any]) -> tuple[str, str]:
    role = row.get("role_title") or "(role unset)"
    company = row.get("company_name") or "(company unset)"
    submitted = row.get("submitted_on")
    notes = row.get("notes")

    subject = f"Follow-up: {role} at {company}"
    lines = [
        "Time to follow up on this submission.",
        "",
        f"Role:       {role}",
        f"Company:    {company}",
    ]
    if submitted:
        lines.append(f"Submitted:  {submitted}")
    if notes:
        lines.extend(["", f"Your notes: {notes}"])
    lines.extend([
        "",
        "Open the tracker to record the follow-up or mark this one done.",
    ])
    return subject, "\n".join(lines)


def _send(conn: Any, follow_up_id: int) -> str:
    """Return a short status string for logging / test assertions."""
    row = _load(conn, follow_up_id)
    if row is None:
        logger.info(
            "followup_notify: row not found / deleted, skipping",
            extra={"follow_up_id": follow_up_id},
        )
        return "skipped:not_found"
    if row.get("actioned_at"):
        logger.info(
            "followup_notify: already actioned, skipping",
            extra={"follow_up_id": follow_up_id},
        )
        return "skipped:actioned"
    if row.get("notified_at"):
        logger.info(
            "followup_notify: already notified, skipping",
            extra={"follow_up_id": follow_up_id},
        )
        return "skipped:already_notified"

    user_email = row.get("user_email")
    if not user_email:
        logger.error(
            "followup_notify: user email missing, cannot send",
            extra={"follow_up_id": follow_up_id},
        )
        return "skipped:no_email"

    if not SENDER_EMAIL:
        logger.error(
            "followup_notify: SENDER_EMAIL env var unset, cannot send",
            extra={"follow_up_id": follow_up_id},
        )
        return "skipped:no_sender"

    subject, body = _render(row)
    logger.info(
        "followup_notify: sending email",
        extra={
            "follow_up_id": follow_up_id,
            "to": user_email,
            "subject": subject,
        },
    )
    _get_ses().send_email(
        Source=SENDER_EMAIL,
        Destination={"ToAddresses": [user_email]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_ups SET notified_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND notified_at IS NULL",
            (follow_up_id,),
        )
    conn.commit()
    return "sent"


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    follow_up_id = event.get("follow_up_id")
    logger.info("followup_notify: enter", extra={"follow_up_id": follow_up_id})
    if follow_up_id is None:
        logger.error("followup_notify: missing follow_up_id in event")
        raise ValueError("event missing follow_up_id")
    try:
        with get_connection() as conn:
            status = _send(conn, int(follow_up_id))
        logger.info("followup_notify: exit ok", extra={"follow_up_id": follow_up_id, "status": status})
        return {"follow_up_id": int(follow_up_id), "status": status}
    except Exception:
        logger.exception("followup_notify: failed", extra={"follow_up_id": follow_up_id})
        raise