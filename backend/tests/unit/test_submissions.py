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
        {                              # auto-followup user/submission/company JOIN
            "follow_up_days": 7,
            "user_email": "you@example.com",
            "role_title": "SRE",
            "company_name": "Acme",
        },
        submission_row,               # _detail submission
        snapshot_row,                 # _detail jd_snapshot
    ]
    mock_cursor.fetchall.side_effect = [
        [],  # follow_ups (none surfaced here in this fetchall stub)
        [],  # responses
        [],  # linked contacts (slice 08)
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
    payload = kwargs["payload"]
    assert payload["user_email"] == "you@example.com"
    assert payload["role_title"] == "SRE"
    assert payload["company_name"] == "Acme"
    assert payload["submitted_on"] == "2026-04-28"
    assert payload["notes"] is None


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


def test_create_submission_honors_follow_up_days_zero(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Regression: ``follow_up_days = 0`` must produce a same-day due_at.

    Earlier code did ``int(row.get("follow_up_days") or 7)`` which silently
    rewrote 0 → 7 because ``0`` is falsy in Python.
    """
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    sched = mocker.patch("handlers.submissions.schedule_followup", return_value=True)

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
        _status_row(),                # status_id lookup
        {                              # auto-followup user JOIN
            "follow_up_days": 0,
            "user_email": "you@example.com",
            "role_title": "SRE",
            "company_name": None,
        },
        submission_row,               # _detail submission
        None,                         # _detail jd_snapshot
    ]
    mock_cursor.fetchall.side_effect = [[], [], []]
    type(mock_cursor).lastrowid = mocker.PropertyMock(side_effect=[99, 123])

    resp = submissions.handler(
        auth_event(
            "POST /submissions",
            body={
                "role_title": "SRE",
                "status": "applied",
                "submitted_on": "2026-04-28",
            },
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sched.assert_called_once()
    due_at = sched.call_args.kwargs["due_at"]
    # 0-day window → same-day due_at, NOT the 7-day fallback.
    assert (due_at.year, due_at.month, due_at.day) == (2026, 4, 28)


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


def test_detail_includes_linked_contacts(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """_detail surfaces the contacts array via the submission_contacts join."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

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
        submission_row,   # _detail submission
        None,             # _detail jd_snapshot
    ]
    mock_cursor.fetchall.side_effect = [
        [],  # follow_ups
        [],  # responses
        [    # contacts
            {"id": 5, "name": "Alice", "email": "a@x.com", "kind": "personal"},
            {"id": 6, "name": "Bob", "email": None, "kind": "recruiter"},
        ],
    ]

    resp = submissions.handler(
        auth_event("GET /submissions/{id}", path_id="99"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert [c["name"] for c in body["contacts"]] == ["Alice", "Bob"]
    assert body["contacts"][0]["kind"] == "personal"
    assert body["contacts"][1]["email"] is None


def test_replace_contacts_diffs_against_existing(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """PUT /submissions/{id}/contacts inserts new links and deletes dropped ones."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

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
    # Sequence:
    #   1. _verify_submission         → {"id": 99}
    #   2. _detail submission row     → submission_row
    #   3. _detail jd_snapshot        → None
    mock_cursor.fetchone.side_effect = [{"id": 99}, submission_row, None]
    # Sequence of fetchall:
    #   1. ownership check on contact_ids        → all three present
    #   2. existing links                         → [3, 7]
    #   3. _detail follow_ups                     → []
    #   4. _detail responses                      → []
    #   5. _detail contacts                       → final state
    mock_cursor.fetchall.side_effect = [
        [{"id": 3}, {"id": 7}, {"id": 12}],          # ownership of new set
        [{"contact_id": 3}, {"contact_id": 7}],      # existing links
        [],                                           # follow_ups
        [],                                           # responses
        [                                             # final contacts
            {"id": 3, "name": "Alice", "email": None, "kind": "personal"},
            {"id": 7, "name": "Bob", "email": None, "kind": "recruiter"},
            {"id": 12, "name": "Carol", "email": None, "kind": "personal"},
        ],
    ]

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}/contacts",
            path_id="99",
            body={"contact_ids": [3, 7, 12]},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    many_sql = [c.args[0] for c in mock_cursor.executemany.call_args_list]
    # 3 was already linked, so no DELETE fires (existing == {3, 7}, new == {3, 7, 12}).
    assert not any("DELETE FROM submission_contacts" in s for s in sql_calls)
    # 12 is new → an INSERT runs (executemany batches the inserts).
    assert any("INSERT INTO submission_contacts" in s for s in many_sql)
    insert_rows = mock_cursor.executemany.call_args.args[1]
    assert insert_rows == [(99, 12)]


def test_replace_contacts_removes_dropped(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Dropping a contact from the set issues a DELETE and skips the INSERT."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

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
    mock_cursor.fetchone.side_effect = [{"id": 99}, submission_row, None]
    mock_cursor.fetchall.side_effect = [
        [{"id": 3}],                                  # ownership of new set [3]
        [{"contact_id": 3}, {"contact_id": 7}],      # existing links {3, 7}
        [],                                           # follow_ups
        [],                                           # responses
        [{"id": 3, "name": "Alice", "email": None, "kind": "personal"}],
    ]

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}/contacts",
            path_id="99",
            body={"contact_ids": [3]},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    many_sql = [c.args[0] for c in mock_cursor.executemany.call_args_list]
    # 7 was dropped → a DELETE fires.
    assert any("DELETE FROM submission_contacts" in s for s in sql_calls)
    # No new contacts → no INSERT (executemany not invoked).
    assert not any("INSERT INTO submission_contacts" in s for s in many_sql)


def test_replace_contacts_clear_to_empty_set(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """``contact_ids: []`` sweeps every existing link."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

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
    mock_cursor.fetchone.side_effect = [{"id": 99}, submission_row, None]
    # Empty set skips the ownership check (no IDs to verify), so the first
    # fetchall is the existing-links lookup.
    mock_cursor.fetchall.side_effect = [
        [{"contact_id": 3}, {"contact_id": 7}],  # existing links
        [], [], [],                                # follow_ups, responses, contacts
    ]

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}/contacts",
            path_id="99",
            body={"contact_ids": []},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("DELETE FROM submission_contacts" in s for s in sql_calls)


def test_replace_contacts_rejects_unowned_id_with_400(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Posting a contact_id that doesn't belong to the caller is a 400, not a silent drop."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

    # _verify_submission succeeds; ownership check returns only {3}, so 99 is missing.
    mock_cursor.fetchone.side_effect = [{"id": 77}]
    mock_cursor.fetchall.side_effect = [[{"id": 3}]]

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}/contacts",
            path_id="77",
            body={"contact_ids": [3, 99]},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "contact_ids not found" in json.loads(resp["body"])["error"]


def test_replace_contacts_submission_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # _verify_submission miss

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}/contacts",
            path_id="999",
            body={"contact_ids": [1]},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_replace_contacts_non_array_returns_400(
    mocker, patched_conn, auth_event, lambda_ctx,
):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

    resp = submissions.handler(
        auth_event(
            "PUT /submissions/{id}/contacts",
            path_id="1",
            body={"contact_ids": "not-an-array"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_unknown_route_returns_404(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)

    resp = submissions.handler(
        auth_event("PATCH /submissions/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_delete_submission_cascades_and_cancels_schedules(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """DELETE soft-deletes the submission + dependents and cancels schedules."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    cancel = mocker.patch("handlers.submissions.cancel_followup", return_value=True)

    # Sequence:
    #   1. _verify_submission           → fetchone → {"id": 99}
    #   2. SELECT follow_ups ids        → fetchall → [{"id": 7}, {"id": 8}]
    mock_cursor.fetchone.side_effect = [{"id": 99}]
    mock_cursor.fetchall.side_effect = [[{"id": 7}, {"id": 8}]]

    resp = submissions.handler(
        auth_event("DELETE /submissions/{id}", path_id="99"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"id": 99, "deleted": True}

    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    # Each dependent table got a soft-delete UPDATE.
    assert any("UPDATE follow_ups SET deleted_at" in s for s in sql_calls)
    assert any("UPDATE responses SET deleted_at" in s for s in sql_calls)
    assert any("UPDATE jd_snapshots SET deleted_at" in s for s in sql_calls)
    # Junction rows hard-deleted (no deleted_at on submission_contacts).
    assert any("DELETE FROM submission_contacts" in s for s in sql_calls)
    # The submission row itself soft-deleted with the ownership scope.
    assert any(
        "UPDATE submissions SET deleted_at" in s and "user_id" in s
        for s in sql_calls
    )

    # cancel_followup fired once per follow-up id we found.
    assert cancel.call_count == 2
    cancelled_ids = sorted(c.kwargs["follow_up_id"] for c in cancel.call_args_list)
    assert cancelled_ids == [7, 8]


def test_delete_submission_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    cancel = mocker.patch("handlers.submissions.cancel_followup")

    # _verify_submission misses → LookupError → 404. No subsequent SQL fires.
    mock_cursor.fetchone.return_value = None

    resp = submissions.handler(
        auth_event("DELETE /submissions/{id}", path_id="999"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404
    cancel.assert_not_called()
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert not any("UPDATE submissions SET deleted_at" in s for s in sql_calls)


def test_delete_submission_with_no_followups_skips_cancel(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """A submission with no follow-ups deletes cleanly without scheduler calls."""
    from handlers import submissions

    patched_conn("handlers.submissions")
    mocker.patch("handlers.submissions.get_user_id", return_value=42)
    cancel = mocker.patch("handlers.submissions.cancel_followup")

    mock_cursor.fetchone.side_effect = [{"id": 99}]
    mock_cursor.fetchall.side_effect = [[]]  # no follow-ups

    resp = submissions.handler(
        auth_event("DELETE /submissions/{id}", path_id="99"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    cancel.assert_not_called()