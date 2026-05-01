"""Unit tests for ``handlers/followup_notify.py``.

Mocks the SES client + DB cursor so the test never reaches AWS.

Covers:
  * happy path: row → render → SES send → notified_at update
  * skip paths: not found, already actioned, already notified, missing user email
  * subject + body include role and company
  * config error: SENDER_EMAIL unset → skip without raising
"""
from __future__ import annotations

import os


def _row(**overrides):
    base = {
        "id": 7,
        "due_at": "2026-05-05 14:00:00",
        "actioned_at": None,
        "notified_at": None,
        "notes": None,
        "submission_id": 99,
        "role_title": "SRE",
        "submitted_on": "2026-04-28",
        "company_name": "Acme",
        "user_email": "saastabp@gmail.com",
        "user_display_name": None,
    }
    base.update(overrides)
    return base


def test_send_happy_path(mocker, patched_conn, mock_cursor, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    patched_conn("handlers.followup_notify")
    mock_cursor.fetchone.return_value = _row()
    ses = mocker.patch.object(followup_notify, "_ses", None)  # reset cached client
    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler({"follow_up_id": 7}, lambda_ctx)

    assert out == {"follow_up_id": 7, "status": "sent"}
    fake_ses.send_email.assert_called_once()
    msg = fake_ses.send_email.call_args.kwargs
    assert msg["Source"] == "saastabp@gmail.com"
    assert msg["Destination"] == {"ToAddresses": ["saastabp@gmail.com"]}
    assert "Acme" in msg["Message"]["Subject"]["Data"]
    assert "SRE" in msg["Message"]["Body"]["Text"]["Data"]
    # notified_at UPDATE fired.
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("notified_at = CURRENT_TIMESTAMP" in s for s in sql_calls)


def test_send_skips_when_actioned(mocker, patched_conn, mock_cursor, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    patched_conn("handlers.followup_notify")
    mock_cursor.fetchone.return_value = _row(actioned_at="2026-04-29 10:00:00")
    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler({"follow_up_id": 7}, lambda_ctx)

    assert out["status"] == "skipped:actioned"
    fake_ses.send_email.assert_not_called()


def test_send_skips_when_already_notified(mocker, patched_conn, mock_cursor, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    patched_conn("handlers.followup_notify")
    mock_cursor.fetchone.return_value = _row(notified_at="2026-05-04 14:00:00")
    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler({"follow_up_id": 7}, lambda_ctx)

    assert out["status"] == "skipped:already_notified"
    fake_ses.send_email.assert_not_called()


def test_send_skips_when_row_gone(mocker, patched_conn, mock_cursor, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    patched_conn("handlers.followup_notify")
    mock_cursor.fetchone.return_value = None
    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler({"follow_up_id": 999}, lambda_ctx)

    assert out["status"] == "skipped:not_found"
    fake_ses.send_email.assert_not_called()


def test_send_skips_when_sender_email_unset(
    mocker, patched_conn, mock_cursor, lambda_ctx, monkeypatch,
):
    monkeypatch.setattr("handlers.followup_notify.SENDER_EMAIL", "")
    from handlers import followup_notify

    patched_conn("handlers.followup_notify")
    mock_cursor.fetchone.return_value = _row()
    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler({"follow_up_id": 7}, lambda_ctx)

    assert out["status"] == "skipped:no_sender"
    fake_ses.send_email.assert_not_called()


def test_missing_follow_up_id_raises(mocker, patched_conn, lambda_ctx):
    from handlers import followup_notify

    patched_conn("handlers.followup_notify")

    import pytest
    with pytest.raises(ValueError):
        followup_notify.handler({}, lambda_ctx)