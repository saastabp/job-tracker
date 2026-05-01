"""Unit tests for ``handlers/followups.py``.

Mocks pymysql cursor, ``get_user_id``, and the ``common.scheduler`` helpers
so the EventBridge Scheduler API never gets called.

Covers:
  * route dispatch (list / create / update / delete)
  * GET /follow-ups with ``?pending=1`` filter
  * POST creates a row + calls schedule_followup with the parsed due_at
  * PUT due_at re-schedules; PUT actioned cancels the schedule
  * DELETE soft-deletes + cancels the schedule
  * error paths: missing parent submission (404), bad due_at (400),
    follow-up not found (404), unknown route (404)
"""
from __future__ import annotations

import json
from datetime import datetime


def test_list_pending_filter(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {
            "id": 1, "submission_id": 99,
            "due_at": "2026-05-05 09:00:00",
            "actioned_at": None, "notified_at": None,
            "notes": "ping recruiter",
            "auto_created": 0,
            "role_title": "SRE",
            "company_name": "Acme",
            "status": "applied",
        },
    ]

    resp = followups.handler(
        auth_event("GET /follow-ups", qs={"pending": "1"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body) == 1
    assert body[0]["company_name"] == "Acme"
    assert body[0]["auto_created"] is False
    sql = mock_cursor.execute.call_args.args[0]
    assert "f.actioned_at IS NULL" in sql


def test_create_followup_schedules_it(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    sched = mocker.patch("handlers.followups.schedule_followup", return_value=True)

    detail_row = {
        "id": 7, "submission_id": 99,
        "due_at": "2026-05-05 09:00:00",
        "actioned_at": None, "notified_at": None,
        "notes": "ping recruiter",
        "auto_created": 0,
        "role_title": "SRE", "company_name": "Acme", "status": "applied",
    }
    # _verify_submission → fetchone returns submission row, then INSERT, then _detail.
    mock_cursor.fetchone.side_effect = [
        {"id": 99},  # _verify_submission
        detail_row,  # _detail
    ]
    type(mock_cursor).lastrowid = mocker.PropertyMock(return_value=7)

    resp = followups.handler(
        auth_event(
            "POST /submissions/{id}/follow-ups",
            path_id="99",
            body={"due_at": "2026-05-05T09:00:00Z", "notes": "ping recruiter"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["id"] == 7
    assert body["submission_id"] == 99
    sched.assert_called_once()
    kwargs = sched.call_args.kwargs
    assert kwargs["follow_up_id"] == 7
    assert isinstance(kwargs["due_at"], datetime)


def test_create_followup_missing_submission_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    mocker.patch("handlers.followups.schedule_followup")
    mock_cursor.fetchone.return_value = None  # _verify_submission miss

    resp = followups.handler(
        auth_event(
            "POST /submissions/{id}/follow-ups",
            path_id="999",
            body={"due_at": "2026-05-05T09:00:00Z"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404
    assert "submission not found" in json.loads(resp["body"])["error"]


def test_create_followup_invalid_due_at_returns_400(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    mocker.patch("handlers.followups.schedule_followup")
    # _verify_submission passes; the next failure is the due_at parse.
    mock_cursor.fetchone.return_value = {"id": 99}

    resp = followups.handler(
        auth_event(
            "POST /submissions/{id}/follow-ups",
            path_id="99",
            body={"due_at": "not-a-date"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_update_due_at_reschedules(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    sched = mocker.patch("handlers.followups.schedule_followup", return_value=True)
    cancel = mocker.patch("handlers.followups.cancel_followup")

    existing = {
        "id": 7, "submission_id": 99,
        "due_at": datetime(2026, 5, 5, 9, 0),
        "actioned_at": None, "notified_at": None,
        "notes": None, "auto_created": 0,
    }
    detail_row = {**existing, "due_at": "2026-05-10 09:00:00",
                  "role_title": "SRE", "company_name": "Acme", "status": "applied"}
    mock_cursor.fetchone.side_effect = [existing, detail_row]
    mock_cursor.rowcount = 1

    resp = followups.handler(
        auth_event(
            "PUT /follow-ups/{id}",
            path_id="7",
            body={"due_at": "2026-05-10T09:00:00Z"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sched.assert_called_once()
    cancel.assert_not_called()


def test_update_actioned_cancels_schedule(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    sched = mocker.patch("handlers.followups.schedule_followup")
    cancel = mocker.patch("handlers.followups.cancel_followup", return_value=True)

    existing = {
        "id": 7, "submission_id": 99,
        "due_at": datetime(2026, 5, 5, 9, 0),
        "actioned_at": None, "notified_at": None,
        "notes": None, "auto_created": 0,
    }
    detail_row = {
        "id": 7, "submission_id": 99,
        "due_at": "2026-05-05 09:00:00",
        "actioned_at": "2026-04-30 12:00:00",
        "notified_at": None,
        "notes": None, "auto_created": 0,
        "role_title": "SRE", "company_name": "Acme", "status": "applied",
    }
    mock_cursor.fetchone.side_effect = [existing, detail_row]
    mock_cursor.rowcount = 1

    resp = followups.handler(
        auth_event(
            "PUT /follow-ups/{id}",
            path_id="7",
            body={"actioned": True},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    cancel.assert_called_once_with(follow_up_id=7)
    sched.assert_not_called()


def test_update_followup_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    mocker.patch("handlers.followups.schedule_followup")
    mocker.patch("handlers.followups.cancel_followup")
    mock_cursor.fetchone.return_value = None  # _load_owned miss

    resp = followups.handler(
        auth_event(
            "PUT /follow-ups/{id}",
            path_id="999",
            body={"notes": "x"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_delete_followup_cancels_schedule(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)
    cancel = mocker.patch("handlers.followups.cancel_followup", return_value=True)

    existing = {
        "id": 7, "submission_id": 99,
        "due_at": datetime(2026, 5, 5, 9, 0),
        "actioned_at": None, "notified_at": None,
        "notes": None, "auto_created": 0,
    }
    mock_cursor.fetchone.return_value = existing
    mock_cursor.rowcount = 1

    resp = followups.handler(
        auth_event("DELETE /follow-ups/{id}", path_id="7"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"id": 7, "deleted": True}
    cancel.assert_called_once_with(follow_up_id=7)


def test_unknown_route_returns_404(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import followups

    patched_conn("handlers.followups")
    mocker.patch("handlers.followups.get_user_id", return_value=42)

    resp = followups.handler(
        auth_event("PATCH /follow-ups/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404