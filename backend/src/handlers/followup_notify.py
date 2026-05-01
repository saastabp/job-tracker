"""Scheduler-stack Lambda: send a follow-up reminder email via SES.

EventBridge Scheduler invokes this with a payload like::

    {
      "follow_up_id":  17,
      "user_email":    "you@example.com",
      "role_title":    "Senior SRE",
      "company_name":  "Acme",
      "submitted_on":  "2026-04-28",
      "notes":         "ping recruiter directly"
    }

Every field is populated by the api-stack handler that created the schedule
(submissions / followups). This Lambda runs OUTSIDE the VPC and never touches
the DB — keeping it DB-less is what avoids the SES VPC interface endpoint
(~$14/mo) and matches the slice 07 architecture flatten.

Schedules are one-shot with ``ActionAfterCompletion: DELETE``, so re-fire
isn't a concern. Errors raise so EventBridge Scheduler's retry/DLQ behavior
takes over (the scheduler stack is the right place to add a DLQ later if
missed reminders become a problem).
"""
from __future__ import annotations

import os
from typing import Any

import boto3

from common.logger import logger

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")

_ses: Any = None


def _get_ses() -> Any:
    global _ses
    if _ses is None:
        _ses = boto3.client("ses", region_name=AWS_REGION)
    return _ses


def _render(event: dict[str, Any]) -> tuple[str, str]:
    role = event.get("role_title") or "(role unset)"
    company = event.get("company_name") or "(company unset)"
    submitted = event.get("submitted_on")
    notes = event.get("notes")

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


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    follow_up_id = event.get("follow_up_id")
    logger.info("followup_notify: enter", extra={"follow_up_id": follow_up_id})

    if follow_up_id is None:
        logger.error("followup_notify: missing follow_up_id in event")
        raise ValueError("event missing follow_up_id")

    user_email = event.get("user_email")
    if not user_email:
        logger.error(
            "followup_notify: user_email missing from payload",
            extra={"follow_up_id": follow_up_id},
        )
        return {"follow_up_id": int(follow_up_id), "status": "skipped:no_email"}

    if not SENDER_EMAIL:
        logger.error(
            "followup_notify: SENDER_EMAIL env var unset, cannot send",
            extra={"follow_up_id": follow_up_id},
        )
        return {"follow_up_id": int(follow_up_id), "status": "skipped:no_sender"}

    subject, body = _render(event)
    logger.info(
        "followup_notify: sending email",
        extra={
            "follow_up_id": follow_up_id,
            "to": user_email,
            "subject": subject,
        },
    )
    try:
        _get_ses().send_email(
            Source=SENDER_EMAIL,
            Destination={"ToAddresses": [user_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
    except Exception:
        logger.exception(
            "followup_notify: SES send failed",
            extra={"follow_up_id": follow_up_id, "to": user_email},
        )
        raise

    logger.info(
        "followup_notify: exit ok",
        extra={"follow_up_id": follow_up_id, "status": "sent"},
    )
    return {"follow_up_id": int(follow_up_id), "status": "sent"}
