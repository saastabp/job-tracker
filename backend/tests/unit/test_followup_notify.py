"""Unit tests for ``handlers/followup_notify.py``.

Notify Lambda runs OUTSIDE the VPC and never touches the DB; every field it
needs travels in the EventBridge schedule's Input payload. These tests mock
only the SES client.

Covers:
  * happy path: payload → render → SES send → 'sent' status
  * missing required field (no follow_up_id) → ValueError
  * graceful skip on missing user_email or unset SENDER_EMAIL
  * subject + body include role and company from the payload
  * SES exceptions propagate so EventBridge Scheduler retries / DLQ catches
"""
from __future__ import annotations

import os

import pytest


def _event(**overrides):
    base = {
        "follow_up_id": 7,
        "user_email": "saastabp@gmail.com",
        "role_title": "SRE",
        "company_name": "Acme",
        "submitted_on": "2026-04-28",
        "notes": None,
    }
    base.update(overrides)
    return base


def test_send_happy_path(mocker, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler(_event(), lambda_ctx)

    assert out == {"follow_up_id": 7, "status": "sent"}
    fake_ses.send_email.assert_called_once()
    msg = fake_ses.send_email.call_args.kwargs
    assert msg["Source"] == "saastabp@gmail.com"
    assert msg["Destination"] == {"ToAddresses": ["saastabp@gmail.com"]}
    assert "Acme" in msg["Message"]["Subject"]["Data"]
    assert "SRE" in msg["Message"]["Body"]["Text"]["Data"]
    assert "2026-04-28" in msg["Message"]["Body"]["Text"]["Data"]


def test_send_includes_notes_when_present(mocker, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler(
        _event(notes="ping the recruiter directly"), lambda_ctx
    )

    assert out["status"] == "sent"
    body = fake_ses.send_email.call_args.kwargs["Message"]["Body"]["Text"]["Data"]
    assert "ping the recruiter directly" in body


def test_send_skips_when_user_email_missing(mocker, lambda_ctx):
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler(_event(user_email=None), lambda_ctx)

    assert out == {"follow_up_id": 7, "status": "skipped:no_email"}
    fake_ses.send_email.assert_not_called()


def test_send_skips_when_sender_email_unset(mocker, lambda_ctx, monkeypatch):
    monkeypatch.setattr("handlers.followup_notify.SENDER_EMAIL", "")
    from handlers import followup_notify

    fake_ses = mocker.MagicMock()
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    out = followup_notify.handler(_event(), lambda_ctx)

    assert out == {"follow_up_id": 7, "status": "skipped:no_sender"}
    fake_ses.send_email.assert_not_called()


def test_missing_follow_up_id_raises(mocker, lambda_ctx):
    from handlers import followup_notify

    with pytest.raises(ValueError):
        followup_notify.handler({}, lambda_ctx)


def test_ses_failure_propagates(mocker, lambda_ctx):
    """SES errors raise so EventBridge Scheduler's retry/DLQ behavior catches them."""
    os.environ["SENDER_EMAIL"] = "saastabp@gmail.com"
    from handlers import followup_notify

    fake_ses = mocker.MagicMock()
    fake_ses.send_email.side_effect = RuntimeError("MessageRejected")
    mocker.patch.object(followup_notify, "_get_ses", return_value=fake_ses)

    with pytest.raises(RuntimeError, match="MessageRejected"):
        followup_notify.handler(_event(), lambda_ctx)
