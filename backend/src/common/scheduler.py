"""EventBridge Scheduler helpers for follow-up reminders.

The api-stack handlers (submissions, followups) call ``schedule_followup`` to
register a one-shot future-dated trigger that invokes the notify Lambda owned
by the scheduler stack. ``cancel_followup`` removes the schedule when the
row is actioned, edited (the caller deletes-and-recreates), or soft-deleted.

The schedule's Input payload carries every field the notify Lambda needs to
render and send the email — ``user_email``, ``role_title``, ``company_name``,
``submitted_on``, ``notes``. The notify Lambda runs OUTSIDE the VPC and never
touches the DB, so all context must travel in the payload. Callers populate
the dict from local context at schedule-create time; if the user later edits
the submission's role_title, the schedule still carries the old value (fine
for personal use; the email is informational, not authoritative).

Stack segregation: if the scheduler stack isn't deployed, ``SCHEDULER_GROUP_NAME``
will be empty and these helpers no-op (logging a warning). The follow-up row
still persists, the dashboard still counts it, the user just doesn't get the
email reminder. This matches ``feedback_idempotent_deploys`` — tearing down
the scheduler stack must not break the api stack's create/update flow.

Schedule naming: ``followup-<follow_up_id>`` is deterministic, so create/update/
delete don't need to read state back. NotFound on cancel is swallowed because
the schedule may have already fired (one-shot) or may never have been created
(scheduler stack absent at the time of the row's creation).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.logger import logger

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
SCHEDULER_GROUP_NAME = os.environ.get("SCHEDULER_GROUP_NAME", "")
SCHEDULER_NOTIFY_ARN = os.environ.get("SCHEDULER_NOTIFY_ARN", "")
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = boto3.client("scheduler", region_name=AWS_REGION)
    return _client


def _schedule_name(follow_up_id: int) -> str:
    return f"followup-{follow_up_id}"


def _is_configured() -> bool:
    return bool(SCHEDULER_GROUP_NAME and SCHEDULER_NOTIFY_ARN and SCHEDULER_ROLE_ARN)


def _format_at(due_at: datetime) -> str:
    """EventBridge Scheduler at(...) wants 'YYYY-MM-DDTHH:MM:SS' in UTC, no tz suffix."""
    if due_at.tzinfo is not None:
        due_at = due_at.astimezone(timezone.utc).replace(tzinfo=None)
    return due_at.strftime("%Y-%m-%dT%H:%M:%S")


def schedule_followup(
    *,
    follow_up_id: int,
    due_at: datetime,
    payload: dict[str, Any],
) -> bool:
    """Create or replace a one-shot schedule for the given follow-up.

    Parameters
    ----------
    follow_up_id : int
        The local ``follow_ups.id``. Embedded in the schedule's Input so the
        notify Lambda can correlate logs back to a row, and used to derive
        the schedule name.
    due_at : datetime
        When the schedule should fire. Coerced to UTC.
    payload : dict
        Reminder context the notify Lambda renders into the email — must
        carry ``user_email``, ``role_title``, ``company_name``,
        ``submitted_on``, ``notes`` (all strings or None). Any extra keys
        are passed through harmlessly.

    Returns
    -------
    bool
        ``True`` if the schedule was created or updated. ``False`` if the
        scheduler stack isn't configured (env vars missing) or the API call
        failed — the caller should treat this as a soft warning and continue.
    """
    if not _is_configured():
        logger.warning(
            "scheduler: not configured, skipping schedule_followup",
            extra={"follow_up_id": follow_up_id},
        )
        return False

    name = _schedule_name(follow_up_id)
    at_expr = f"at({_format_at(due_at)})"
    input_payload = json.dumps({"follow_up_id": follow_up_id, **payload})
    target = {
        "Arn": SCHEDULER_NOTIFY_ARN,
        "RoleArn": SCHEDULER_ROLE_ARN,
        "Input": input_payload,
    }
    common: dict[str, Any] = {
        "Name": name,
        "GroupName": SCHEDULER_GROUP_NAME,
        "ScheduleExpression": at_expr,
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": target,
        "ActionAfterCompletion": "DELETE",
    }
    client = _get_client()
    try:
        logger.info(
            "scheduler: creating schedule",
            extra={"follow_up_id": follow_up_id, "schedule_name": name, "at": at_expr},
        )
        client.create_schedule(**common)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConflictException":
            logger.info(
                "scheduler: schedule exists, updating",
                extra={"follow_up_id": follow_up_id, "schedule_name": name},
            )
            try:
                client.update_schedule(**common)
                return True
            except ClientError:
                # Swallow + return False is deliberate per module docstring:
                # the follow-up row is the source of truth, the schedule is
                # a side-effect, and the caller logs a warning + continues.
                logger.exception(
                    "scheduler: update_schedule failed",
                    extra={"follow_up_id": follow_up_id, "schedule_name": name},
                )
                return False
        # See comment above — same graceful-degradation policy.
        logger.exception(
            "scheduler: create_schedule failed",
            extra={"follow_up_id": follow_up_id, "schedule_name": name},
        )
        return False


def cancel_followup(*, follow_up_id: int) -> bool:
    """Delete the schedule for the given follow-up.

    A NotFound is treated as success — the schedule may already have fired
    (one-shot schedules with ``ActionAfterCompletion: DELETE`` clean themselves
    up) or never have been created.

    Returns
    -------
    bool
        ``True`` if the delete succeeded or the schedule was already gone.
        ``False`` if the scheduler stack isn't configured or another error
        occurred.
    """
    if not _is_configured():
        logger.warning(
            "scheduler: not configured, skipping cancel_followup",
            extra={"follow_up_id": follow_up_id},
        )
        return False

    name = _schedule_name(follow_up_id)
    client = _get_client()
    try:
        logger.info(
            "scheduler: deleting schedule",
            extra={"follow_up_id": follow_up_id, "schedule_name": name},
        )
        client.delete_schedule(Name=name, GroupName=SCHEDULER_GROUP_NAME)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            logger.info(
                "scheduler: schedule already gone",
                extra={"follow_up_id": follow_up_id, "schedule_name": name},
            )
            return True
        # Swallow + return False per module docstring — same reasoning as
        # schedule_followup; cancel is best-effort and the caller doesn't
        # have a recovery path.
        logger.exception(
            "scheduler: delete_schedule failed",
            extra={"follow_up_id": follow_up_id, "schedule_name": name},
        )
        return False