"""Unit tests for ``handlers/submissions.py``.

Mocks pymysql cursor, ``get_user_id``, and the boto3 S3 client so JD-snapshot
storage can be exercised without network access.

Covers:
  * route dispatch (list / create / detail / update)
  * filterable list (status / company_id / from / to passthrough)
  * find-or-create company on inline ``company_name``
  * JD archival to S3 + ``jd_snapshots`` insert
  * status short_name → submission_status_id catalog resolution
  * error paths: unknown status (400), missing submission (404), bad path id (400)
  * regression: LookupError returns 404 (no logger collision → 500)
"""
from __future__ import annotations

import json


def _status_row():
    return {"id": 1}


def _user_row():
    return {"id": 42}


def test_list_submissions_with_filters(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {
            "id": 1, "role_title": "SRE",
            "submitted_on": "2026-04-28", "notes": None,
            "company_id": 7, "company_name": "Acme",
            "status": "applied",
            "tailored_title": None, "tailored_summary": None,
            "jd_url": None,
            "created_at": None, "updated_at": None,
        },
    ]

    resp = submissions.handler(
        auth_event(
            "GET /submissions",
            qs={"status": "applied", "company_id": "7", "from": "2026-04-01", "to": "2026-04-30"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body) == 1
    assert body[0]["company_name"] == "Acme"
    # The query should have been parameterized with the four filter values.
    sql_call = mock_cursor.execute.call_args
    params = sql_call.args[1]
    assert 42 in params  # user_id
    assert "applied" in params
    assert 7 in params
    assert "2026-04-01" in params
    assert "2026-04-30" in params


def test_create_submission_with_company_name_and_jd(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """End-to-end happy path: inline company_name + jd_text → S3 + DB rows."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    sched = mocker.patch("handlers.submissions.schedule_followup", return_value=True)
    s3 = mocker.patch.object(submissions, "_s3")

    # In order, the handler does:
    #   1. SELECT submission_statuses        → fetchone → {"id": 1}    (status_id)
    #   2. SELECT companies WHERE name=...   → fetchone → None         (no existing)
    #   3. INSERT companies (lastrowid=88)
    #   4. INSERT submissions (lastrowid=99)
    #   5. INSERT jd_snapshots
    #   6. _auto_queue_followup: SELECT users.follow_up_days → {"follow_up_days": 7}
    #      INSERT follow_ups (lastrowid=123)
    #   7. _detail: SELECT submissions ...   → fetchone → submission row
    #      SELECT jd_snapshots               → fetchone → snapshot row
    #      SELECT follow_ups                 → fetchall → [auto-created row]
    #      SELECT responses                  → fetchall → []
    submission_row = {
        "id": 99, "role_title": "SRE",
        "submitted_on": "2026-04-28", "notes": None,
        "company_id": 88, "company_name": "Acme",
        "resume_id": None, "resume_title": None,
        "status": "applied",
        "tailored_title": None, "tailored_summary": None,
        "jd_url": "https://example.com/jd",
        "created_at": None, "updated_at": None,
    }
    snapshot_row = {
        "id": 5,
        "s3_key": "users/user-sub-1/submissions/99/jd.txt",
        "source_url": "https://example.com/jd",
        "captured_at": "2026-04-28 10:00:00",
    }
    mock_cursor.fetchone.side_effect = [
        _status_row(),                # status_id lookup
        None,                         # company name lookup miss
        {"follow_up_days": 7},        # auto-followup users lookup
        submission_row,               # _detail submission
        snapshot_row,                 # _detail jd_snapshot
    ]
    mock_cursor.fetchall.side_effect = [
        [],  # follow_ups (none surfaced here in this fetchall stub)
        [],  # responses
    ]
    # lastrowid is read three times: company INSERT, submission INSERT, follow_ups INSERT.
    type(mock_cursor).lastrowid = mocker.PropertyMock(side_effect=[88, 99, 123])
    s3.get_object.return_value = {
        "Body": mocker.MagicMock(read=mocker.MagicMock(return_value=b"the jd")),
    }

    resp = submissions.handler(
        auth_event(
            "POST /submissions",
            body={
                "company_name": "Acme",
                "role_title": "SRE",
                "status": "applied",
                "submitted_on": "2026-04-28",
                "jd_url": "https://example.com/jd",
                "jd_text": "the jd body",
            },
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["id"] == 99
    assert body["company_name"] == "Acme"
    assert body["jd_snapshot"]["s3_key"] == "users/user-sub-1/submissions/99/jd.txt"
    assert body["jd_text"] == "the jd"

    # JD body was uploaded to the resume bucket under the user-scoped key.
    s3.put_object.assert_called_once()
    put_kwargs = s3.put_object.call_args.kwargs
    assert put_kwargs["Bucket"] == "test-bucket"
    assert put_kwargs["Key"] == "users/user-sub-1/submissions/99/jd.txt"
    assert put_kwargs["Body"] == b"the jd body"

    # Auto-created follow-up landed and got scheduled — submitted_on is
    # 2026-04-28, follow_up_days is 7 → 2026-05-05 14:00 UTC.
    sched.assert_called_once()
    kwargs = sched.call_args.kwargs
    assert kwargs["follow_up_id"] == 123
    assert kwargs["due_at"].year == 2026
    assert kwargs["due_at"].month == 5
    assert kwargs["due_at"].day == 5


def test_create_submission_without_submitted_on_skips_auto_followup(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Without submitted_on we don't guess a due date — auto-create is skipped."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    sched = mocker.patch("handlers.submissions.schedule_followup")

    submission_row = {
        "id": 99, "role_title": "SRE",
        "submitted_on": None, "notes": None,
        "company_id": None, "company_name": None,
        "resume_id": None, "resume_title": None,
        "status": "applied",
        "tailored_title": None, "tailored_summary": None,
        "jd_url": None,
        "created_at": None, "updated_at": None,
    }
    mock_cursor.fetchone.side_effect = [
        _status_row(),     # status_id lookup
        submission_row,    # _detail submission
        None,              # _detail jd_snapshot
    ]
    type(mock_cursor).lastrowid = mocker.PropertyMock(return_value=99)

    resp = submissions.handler(
        auth_event(
            "POST /submissions",
            body={"role_title": "SRE", "status": "applied"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sched.assert_not_called()


def test_create_submission_unknown_status_returns_400(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # status catalog miss

    resp = submissions.handler(
        auth_event("POST /submissions", body={"status": "not-a-status"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "unknown submission status" in json.loads(resp["body"])["error"]


def test_get_submission_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Regression: slice-03 logger.extra collision had this returning 500."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # _detail submission miss

    resp = submissions.handler(
        auth_event("GET /submissions/{id}", path_id="999"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "submission not found"


def test_invalid_path_id_returns_400(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

    resp = submissions.handler(
        auth_event("GET /submissions/{id}", path_id="abc"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_update_submission(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

    submission_row = {
        "id": 99, "role_title": "SRE",
        "submitted_on": "2026-04-28", "notes": "updated",
        "company_id": None, "company_name": None,
        "resume_id": None, "resume_title": None,
        "status": "applied",
        "tailored_title": None, "tailored_summary": None,
        "jd_url": None,
        "created_at": None, "updated_at": None,
    }
    # status lookup (status_id), then UPDATE rowcount, then _detail's fetchones.
    mock_cursor.fetchone.side_effect = [
        _status_row(),    # status_id lookup
        submission_row,   # _detail submission
        None,             # _detail jd_snapshot
    ]
    mock_cursor.rowcount = 1

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}",
            path_id="99",
            body={"status": "applied", "notes": "updated"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["notes"] == "updated"


def test_update_submission_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    mock_cursor.rowcount = 0  # UPDATE matched nothing
    mock_cursor.fetchone.return_value = None

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}", path_id="999", body={"notes": "x"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_update_submission_jd_text_upserts_snapshot(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """PUT with jd_text writes to S3 and upserts jd_snapshots, even when no scalar fields change."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    s3 = mocker.patch.object(submissions, "_s3")

    submission_row = {
        "id": 99, "role_title": "SRE",
        "submitted_on": "2026-04-28", "notes": None,
        "company_id": None, "company_name": None,
        "resume_id": None, "resume_title": None,
        "status": "applied",
        "tailored_title": None, "tailored_summary": None,
        "jd_url": "https://example.com/jd",
        "created_at": None, "updated_at": None,
    }
    snapshot_row = {
        "id": 5,
        "s3_key": "users/user-sub-1/submissions/99/jd.txt",
        "source_url": "https://example.com/jd",
        "captured_at": "2026-04-28 10:00:00",
    }
    # _update_jd reads existing jd_url, then _detail runs.
    mock_cursor.fetchone.side_effect = [
        {"jd_url": "https://example.com/jd"},  # _update_jd source_url lookup
        submission_row,                         # _detail submission
        snapshot_row,                           # _detail jd_snapshot
    ]
    s3.get_object.return_value = {
        "Body": mocker.MagicMock(read=mocker.MagicMock(return_value=b"new jd body")),
    }

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}",
            path_id="99",
            body={"jd_text": "new jd body"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    s3.put_object.assert_called_once()
    put_kwargs = s3.put_object.call_args.kwargs
    assert put_kwargs["Body"] == b"new jd body"
    # An upsert query against jd_snapshots fired.
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("INSERT INTO jd_snapshots" in s for s in sql_calls)


def test_update_submission_empty_jd_text_clears_snapshot(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    mocker.patch.object(submissions, "_s3")

    submission_row = {
        "id": 99, "role_title": "SRE",
        "submitted_on": "2026-04-28", "notes": None,
        "company_id": None, "company_name": None,
        "resume_id": None, "resume_title": None,
        "status": "applied",
        "tailored_title": None, "tailored_summary": None,
        "jd_url": None,
        "created_at": None, "updated_at": None,
    }
    mock_cursor.fetchone.side_effect = [
        {"jd_url": None},  # _update_jd source_url lookup
        submission_row,    # _detail submission
        None,              # _detail jd_snapshot (none, because we just cleared)
    ]

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}",
            path_id="99",
            body={"jd_text": ""},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(
        "UPDATE jd_snapshots SET deleted_at = CURRENT_TIMESTAMP" in s
        for s in sql_calls
    )


def test_unknown_route_returns_404(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

    resp = submissions.handler(
        auth_event("DELETE /submissions/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404