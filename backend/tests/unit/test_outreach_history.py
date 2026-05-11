"""Unit tests for ``handlers/outreach_history.py``.

The handler issues a single SELECT with three LEFT JOINs (outreach_methods,
submissions via gmail_thread_id, companies via submissions). Tests stub
the cursor's ``fetchall`` to return canned row dicts mirroring that SQL's
shape.
"""
from __future__ import annotations

import datetime
import json


def test_history_returns_events_with_submission_context(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Email-sourced rows surface role_title + company_name through the
    submissions join on gmail_thread_id."""
    from handlers import outreach_history

    patched_conn("handlers.outreach_history")
    mocker.patch("handlers.outreach_history.get_user_id", return_value=42)

    mock_cursor.fetchall.return_value = [
        {
            "id": 7,
            "outreach_at": "2026-04-15 14:32:00",
            "subject": "Re: Senior SRE role",
            "body_text": "Hey Brian, ...",
            "gmail_message_id": "msg-1",
            "notes": None,
            "direction": "outbound",
            "method": "email",
            "contact_id": 3,
            "contact_name": "Jane Recruiter",
            "contact_kind": "recruiter",
            "submission_id": 99,
            "role_title": "Senior SRE",
            "company_name": "Acme",
        },
    ]

    resp = outreach_history.handler(
        auth_event("GET /outreach/history", qs={"week_start": "2026-04-13"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["week_start"] == "2026-04-13"
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["id"] == 7
    assert ev["contact_name"] == "Jane Recruiter"
    assert ev["role_title"] == "Senior SRE"
    assert ev["company_name"] == "Acme"
    assert ev["submission_id"] == 99


def test_history_manually_logged_rows_have_null_submission_fields(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Rows without ``gmail_thread_id`` come through with null
    submission_id / role_title / company_name from the LEFT JOIN."""
    from handlers import outreach_history

    patched_conn("handlers.outreach_history")
    mocker.patch("handlers.outreach_history.get_user_id", return_value=42)

    mock_cursor.fetchall.return_value = [
        {
            "id": 8,
            "outreach_at": "2026-04-15 09:00:00",
            "subject": None,
            "body_text": None,
            "gmail_message_id": None,
            "notes": "Met at meetup",
            "direction": "outbound",
            "method": "in_person",
            "contact_id": 5,
            "contact_name": "Bob",
            "contact_kind": "personal",
            "submission_id": None,
            "role_title": None,
            "company_name": None,
        },
    ]

    resp = outreach_history.handler(
        auth_event("GET /outreach/history", qs={"week_start": "2026-04-13"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    ev = body["events"][0]
    assert ev["submission_id"] is None
    assert ev["role_title"] is None
    assert ev["company_name"] is None
    assert ev["notes"] == "Met at meetup"


def test_history_empty_week_returns_empty_events_list(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import outreach_history

    patched_conn("handlers.outreach_history")
    mocker.patch("handlers.outreach_history.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = []

    resp = outreach_history.handler(
        auth_event("GET /outreach/history", qs={"week_start": "2025-01-06"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["week_start"] == "2025-01-06"
    assert body["events"] == []


def test_history_bad_week_start_returns_400(
    mocker, patched_conn, auth_event, lambda_ctx,
):
    """Malformed week_start values 400 before any DB work."""
    from handlers import outreach_history

    patched_conn("handlers.outreach_history")

    resp = outreach_history.handler(
        auth_event("GET /outreach/history", qs={"week_start": "garbage"}),
        lambda_ctx,
    )
    assert resp["statusCode"] == 400
    assert "week_start" in json.loads(resp["body"])["error"]


def test_history_defaults_to_current_monday_when_param_absent(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import outreach_history

    patched_conn("handlers.outreach_history")
    mocker.patch("handlers.outreach_history.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = []

    resp = outreach_history.handler(
        auth_event("GET /outreach/history"),
        lambda_ctx,
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    today = datetime.datetime.now(datetime.UTC).date()
    expected_monday = today - datetime.timedelta(days=today.weekday())
    assert body["week_start"] == expected_monday.isoformat()


def test_history_sql_uses_index_friendly_range(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """SQL filter must be ``co.outreach_at >= %s AND co.outreach_at < %s + INTERVAL 7 DAY``
    so the (user_id, outreach_at) index is usable. Guardrail against
    accidentally regressing to ``WHERE DATE(co.outreach_at) BETWEEN ...``
    which would force a full-table scan."""
    from handlers import outreach_history

    patched_conn("handlers.outreach_history")
    mocker.patch("handlers.outreach_history.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = []

    outreach_history.handler(
        auth_event("GET /outreach/history", qs={"week_start": "2026-04-13"}),
        lambda_ctx,
    )

    sql = mock_cursor.execute.call_args.args[0]
    assert "co.outreach_at >= %s" in sql
    assert "INTERVAL 7 DAY" in sql
    assert "DATE(co.outreach_at)" not in sql