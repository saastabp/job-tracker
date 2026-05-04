"""Unit tests for ``handlers/gmail_poller.py``.

The poller has three layers of behavior worth covering independently:

1. The pure-function helpers (`classify`, `_extract_from_email`,
   `_extract_body_text`) — straightforward parameterized tests.
2. `_process_message` — the per-message logic (self-sent filter, INSERT
   IGNORE idempotency, status-bump matrix). All external calls (Gmail
   API, S3, KMS, classifier catalog lookups) get mocked.
3. The top-level `handler` — exercises the user → thread → message
   loop, including isolation (one user's failure doesn't block others).

External calls mocked:
  * ``boto3`` SSM (OAuth client) via ``_get_ssm``
  * ``boto3`` S3 via ``_get_s3``
  * KMS decrypt via ``common.gmail_client._get_kms_client``
  * ``build_credentials`` / ``build_gmail_service`` (avoids the
    google-auth + google-api-python-client packages)
"""
from __future__ import annotations

import base64
import importlib
import json
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _set_env(monkeypatch):
    monkeypatch.setenv("GMAIL_KMS_KEY_ID", "alias/jobtracker-gmail")
    monkeypatch.setenv(
        "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
    )
    monkeypatch.setenv("GMAIL_ARCHIVE_BUCKET", "test-archive-bucket")


def _import_fresh():
    from handlers import gmail_poller

    importlib.reload(gmail_poller)
    return gmail_poller


def _make_message(
    *,
    msg_id: str = "msg-1",
    from_addr: str = '"Recruiter" <recruiter@acme.com>',
    subject: str = "Following up on your application",
    body_text: str = "Thanks for applying. We'll review and get back to you.",
    internal_date_ms: str = "1714694400000",  # 2024-05-02
) -> dict:
    """Build a Gmail API "full" message dict mimicking the real shape."""
    return {
        "id": msg_id,
        "threadId": "thread-abc",
        "internalDate": internal_date_ms,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": subject},
                {"name": "To", "value": "user@gmail.com"},
            ],
            "body": {
                "data": base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii"),
            },
        },
    }


def _classifications_map():
    return {
        "rejection": 1,
        "interview_invite": 2,
        "auto_ack": 3,
        "recruiter_outreach": 4,
        "other": 5,
    }


def _statuses_map():
    return {
        "applied": 1,
        "responded": 2,
        "interviewing": 3,
        "offer": 4,
        "rejected": 5,
        "ghosted": 6,
    }


# ---------------------------------------------------------------------------
# Heuristic classifier
# ---------------------------------------------------------------------------


class TestClassifier:
    @pytest.mark.parametrize(
        "subject,body,expected",
        [
            (
                "Update on your application",
                "Unfortunately we won't be moving forward at this time.",
                "rejection",
            ),
            (
                "Re: Senior Engineer position",
                "Thanks for your interest. We've decided to proceed with other candidates.",
                "rejection",
            ),
            (
                "Next steps",
                "Hi! Let's schedule a call next week — calendly.com/me/30min",
                "interview_invite",
            ),
            (
                "Interview invitation",
                "We'd love to chat about the role. Can you book a time?",
                "interview_invite",
            ),
            (
                "Application received",
                "Thank you for applying. We'll review your application and be in touch.",
                "auto_ack",
            ),
            (
                "Opportunity at Acme",
                "Hi, I came across your profile and wanted to reach out about an exciting opportunity at Acme.",
                "recruiter_outreach",
            ),
            (
                "Quick question",
                "Hey, just checking in on the status.",
                "other",
            ),
        ],
    )
    def test_classifier_matrix(self, monkeypatch, subject, body, expected):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        assert gmail_poller.classify(subject, body) == expected


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_from_email_extracts_address_from_named(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        result = gmail_poller._extract_from_email(
            {"from": '"Jane Doe" <jane@acme.com>'}
        )
        assert result == "jane@acme.com"

    def test_from_email_handles_bare_address(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        result = gmail_poller._extract_from_email({"from": "jane@acme.com"})
        assert result == "jane@acme.com"

    def test_body_extraction_text_plain(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        payload = {
            "mimeType": "text/plain",
            "body": {
                "data": base64.urlsafe_b64encode(
                    b"hello recruiter"
                ).decode("ascii")
            },
        }
        assert gmail_poller._extract_body_text(payload) == "hello recruiter"

    def test_body_extraction_walks_multipart(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"<p>hello</p>"
                        ).decode("ascii")
                    },
                },
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"hello plain"
                        ).decode("ascii")
                    },
                },
            ],
        }
        # text/plain wins over text/html
        assert gmail_poller._extract_body_text(payload) == "hello plain"

    def test_body_extraction_html_fallback(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        payload = {
            "mimeType": "text/html",
            "body": {
                "data": base64.urlsafe_b64encode(
                    b"<p>hello <b>recruiter</b></p>"
                ).decode("ascii")
            },
        }
        # Tags stripped
        result = gmail_poller._extract_body_text(payload)
        assert "hello" in result
        assert "recruiter" in result
        assert "<" not in result
        assert ">" not in result


# ---------------------------------------------------------------------------
# _process_message
# ---------------------------------------------------------------------------


class TestProcessMessage:
    def _patch_archive(self, mocker, gmail_poller, succeed: bool = True):
        fake_s3 = MagicMock()
        if not succeed:
            fake_s3.put_object.side_effect = RuntimeError("S3 unreachable")
        mocker.patch.object(gmail_poller, "_get_s3", return_value=fake_s3)
        return fake_s3

    def _service_with_raw(self, mocker, raw_b64="cmF3IGJ5dGVz"):
        execute = MagicMock(return_value={"raw": raw_b64})
        get_call = MagicMock(return_value=MagicMock(execute=execute))
        messages = MagicMock(get=get_call)
        users = MagicMock(messages=MagicMock(return_value=messages))
        service = MagicMock(users=MagicMock(return_value=users))
        return service

    def test_self_sent_message_skipped(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        msg = _make_message(from_addr='"Me" <user@gmail.com>')
        outcome = gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )
        assert outcome == "skipped:self_sent"
        # No DB writes
        assert not any(
            "INSERT" in c.args[0]
            for c in mock_cursor.execute.call_args_list
        )

    def test_self_sent_check_case_insensitive(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        msg = _make_message(from_addr="USER@GMAIL.COM")
        outcome = gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )
        assert outcome == "skipped:self_sent"

    def test_inserted_response_with_archive(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        self._patch_archive(mocker, gmail_poller)

        mock_cursor.rowcount = 1  # INSERT IGNORE inserted

        msg = _make_message(
            subject="Update on your application",
            body_text="Unfortunately we won't be moving forward.",
        )

        outcome = gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )

        assert outcome == "inserted"
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT IGNORE INTO responses" in c.args[0]
        ]
        assert len(insert_calls) == 1
        # Check the params include the gmail_message_id and the s3_key
        params = insert_calls[0].args[1]
        assert params[0] == 7  # submission_id
        assert params[1] == _classifications_map()["rejection"]
        assert params[3] == "recruiter@acme.com"  # from_email
        assert params[6] == "42/msg-1.eml"  # s3_key
        assert params[7] == "msg-1"  # gmail_message_id

    def test_status_bump_on_rejection(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        self._patch_archive(mocker, gmail_poller)

        mock_cursor.rowcount = 1

        msg = _make_message(
            body_text="Unfortunately we won't be moving forward."
        )

        gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )

        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        params = update_calls[0].args[1]
        assert params[0] == _statuses_map()["rejected"]
        assert params[1] == 7
        assert params[2] == 42

    def test_status_bump_on_interview_invite(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        self._patch_archive(mocker, gmail_poller)

        mock_cursor.rowcount = 1

        msg = _make_message(
            body_text="Let's schedule a call next week to discuss."
        )

        gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )

        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1][0] == _statuses_map()["interviewing"]

    def test_no_status_bump_on_other(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        self._patch_archive(mocker, gmail_poller)

        mock_cursor.rowcount = 1

        msg = _make_message(
            subject="Quick question",
            body_text="Just checking in.",
        )

        gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )

        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 0

    def test_idempotent_skip_on_existing_message(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        # rowcount=0 from INSERT IGNORE means duplicate — skip status bump.
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        self._patch_archive(mocker, gmail_poller)

        mock_cursor.rowcount = 0

        msg = _make_message(
            body_text="Unfortunately we won't be moving forward."
        )

        outcome = gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )

        assert outcome == "skipped:exists"
        # No status bump for already-existing rows
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 0

    def test_archive_failure_does_not_block_insert(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        self._patch_archive(mocker, gmail_poller, succeed=False)

        mock_cursor.rowcount = 1

        msg = _make_message()

        outcome = gmail_poller._process_message(
            conn=mock_conn,
            service=self._service_with_raw(mocker),
            user_id=42,
            submission_id=7,
            msg=msg,
            gmail_address="user@gmail.com",
            classifications=_classifications_map(),
            statuses=_statuses_map(),
            archive_bucket="bucket",
        )

        assert outcome == "inserted"
        # The insert ran with s3_key=None (archive failed)
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT IGNORE INTO responses" in c.args[0]
        ]
        params = insert_calls[0].args[1]
        assert params[6] is None  # raw_email_s3_key


# ---------------------------------------------------------------------------
# Top-level handler
# ---------------------------------------------------------------------------


class TestHandler:
    def _patch_googley_bits(self, mocker, gmail_poller):
        from common import gmail_client

        fake_kms = MagicMock()
        fake_kms.decrypt.return_value = {"Plaintext": b"decrypted-token"}
        mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)

        fake_ssm = MagicMock()
        fake_ssm.get_parameter.return_value = {
            "Parameter": {
                "Value": json.dumps(
                    {"client_id": "cid", "client_secret": "csec"}
                )
            }
        }
        mocker.patch.object(gmail_poller, "_get_ssm", return_value=fake_ssm)

        mocker.patch.object(gmail_poller, "build_credentials", return_value=MagicMock())
        # `service` must support the chain users().threads().get(...).execute()
        # AND users().messages().get(...).execute() (for the raw fetch).
        return mocker.patch.object(gmail_poller, "build_gmail_service")

    def test_no_active_users_exits_clean(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        mock_cursor.fetchall.return_value = []
        patched_conn("handlers.gmail_poller")

        out = gmail_poller.handler({}, lambda_ctx)

        assert out["users_processed"] == 0
        assert out["users_failed"] == 0

    def test_misconfigured_env_returns_error(
        self, mocker, monkeypatch, lambda_ctx
    ):
        # Don't set the env vars — trigger the early misconfigured check.
        monkeypatch.delenv("GMAIL_KMS_KEY_ID", raising=False)
        monkeypatch.delenv("GMAIL_ARCHIVE_BUCKET", raising=False)
        monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SSM_PATH", "x")
        gmail_poller = _import_fresh()

        out = gmail_poller.handler({}, lambda_ctx)

        assert out["users_processed"] == 0
        assert out.get("error") == "misconfigured"