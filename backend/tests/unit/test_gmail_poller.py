"""Unit tests for ``handlers/gmail_poller.py``.

Slice 10 reshaped this module: the poller now enumerates the union of
submission- and contact-linked threads, and per non-self-sent message
dual-writes a ``responses`` row (per matching submission) AND a
``contact_outreach`` row (most-recent contact_id, Fork 1).

Layers covered:

1. Pure helpers (`classify`, `_extract_from_email`, `_extract_body_text`).
2. ``_insert_response_row`` and ``_insert_contact_outreach_row`` —
   the per-table write helpers, including INSERT IGNORE idempotency
   and status-bump matrix.
3. ``_process_thread`` — the dual-write orchestration: self-sent filter,
   per-submission response insert, most-recent-contact contact_outreach
   insert, single S3 archive shared across writes.
4. ``handler`` — top-level user → thread enumeration, per-user isolation.

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


# Fixed catalog ids the contact_outreach helpers expect.
EMAIL_METHOD_ID = 81
INBOUND_DIRECTION_ID = 72


def _service_with_thread(messages: list[dict], raw_b64: str = "cmF3IGJ5dGVz"):
    """Build a Gmail service mock that supports the chains the poller uses."""
    threads_get_execute = MagicMock(return_value={"messages": messages})
    threads_get_call = MagicMock(return_value=MagicMock(execute=threads_get_execute))
    threads = MagicMock(get=threads_get_call)

    raw_get_execute = MagicMock(return_value={"raw": raw_b64})
    raw_get_call = MagicMock(return_value=MagicMock(execute=raw_get_execute))
    messages_resource = MagicMock(get=raw_get_call)

    users = MagicMock(
        threads=MagicMock(return_value=threads),
        messages=MagicMock(return_value=messages_resource),
    )
    return MagicMock(users=MagicMock(return_value=users))


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
        result = gmail_poller._extract_body_text(payload)
        assert "hello" in result
        assert "recruiter" in result
        assert "<" not in result
        assert ">" not in result

    def test_body_extraction_html_strips_style_block(self, monkeypatch):
        """ATS-template regression: <style> contents must not leak into body_text.

        A naive ``<[^>]+>`` tag-strip leaves CSS bodies in place because
        the inner text contains no angle brackets. The fix drops the
        whole ``<style>...</style>`` block before tag-stripping.
        """
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        raw = (
            b"<html><head><style>"
            b"@import url('https://fonts.googleapis.com/css?family=Open+Sans');"
            b".atsEmail{max-width:100%;padding:10px;}"
            b"</style></head><body>"
            b"<p>Role: Solutions Architect</p>"
            b"<p>Location: REMOTE</p>"
            b"</body></html>"
        )
        payload = {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(raw).decode("ascii")},
        }
        result = gmail_poller._extract_body_text(payload)
        assert "Role: Solutions Architect" in result
        assert "Location: REMOTE" in result
        # CSS body is gone.
        assert "atsEmail" not in result
        assert "@import" not in result
        assert "googleapis.com" not in result
        # And the paragraph break survived the conversion.
        assert "Solutions Architect" in result.split("\n")[0]
        assert any("Location" in line for line in result.splitlines()[1:])

    def test_body_extraction_html_decodes_entities(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        raw = b"<p>caf&eacute; &amp; tea&nbsp;party</p>"
        payload = {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(raw).decode("ascii")},
        }
        result = gmail_poller._extract_body_text(payload)
        assert "café" in result
        assert "&amp;" not in result
        assert "&nbsp;" not in result
        assert "& tea" in result


# ---------------------------------------------------------------------------
# _insert_response_row
# ---------------------------------------------------------------------------


class TestInsertResponseRow:
    def _call(
        self, gmail_poller, *, conn, msg_id="msg-1",
        classification_short="rejection",
    ):
        from datetime import datetime, timezone

        return gmail_poller._insert_response_row(
            conn=conn,
            user_id=42,
            submission_id=7,
            msg_id=msg_id,
            received_at=datetime(2024, 5, 2, tzinfo=timezone.utc),
            from_email="recruiter@acme.com",
            subject="S",
            body_text="B",
            s3_key="42/msg-1.eml",
            classification_short=classification_short,
            classifications=_classifications_map(),
            statuses=_statuses_map(),
        )

    def test_insert_with_status_bump_on_rejection(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        mock_cursor.rowcount = 1

        outcome = self._call(gmail_poller, conn=mock_conn)
        assert outcome == "inserted"

        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1][0] == _statuses_map()["rejected"]

    def test_insert_with_status_bump_on_interview_invite(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        mock_cursor.rowcount = 1

        outcome = self._call(
            gmail_poller, conn=mock_conn,
            classification_short="interview_invite",
        )
        assert outcome == "inserted"
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1][0] == _statuses_map()["interviewing"]

    def test_insert_no_bump_on_other(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        mock_cursor.rowcount = 1

        self._call(gmail_poller, conn=mock_conn, classification_short="other")
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 0

    def test_skipped_exists_when_unique_key_matches(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        mock_cursor.rowcount = 0  # INSERT IGNORE matched the unique key

        outcome = self._call(gmail_poller, conn=mock_conn)
        assert outcome == "skipped:exists"
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET submission_status_id" in c.args[0]
        ]
        assert len(update_calls) == 0


# ---------------------------------------------------------------------------
# _insert_contact_outreach_row
# ---------------------------------------------------------------------------


class TestInsertContactOutreachRow:
    def _call(
        self, gmail_poller, *, conn, msg_id="msg-1",
    ):
        from datetime import datetime, timezone

        return gmail_poller._insert_contact_outreach_row(
            conn=conn,
            user_id=42,
            contact_id=11,
            msg_id=msg_id,
            thread_id="thread-abc",
            received_at=datetime(2024, 5, 2, tzinfo=timezone.utc),
            from_email="maria@coldoutreach.com",
            subject="Re: Quick intro",
            body_text="Sounds great!",
            email_method_id=EMAIL_METHOD_ID,
            inbound_direction_id=INBOUND_DIRECTION_ID,
        )

    def test_inserts_row_with_expected_params(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        mock_cursor.rowcount = 1

        outcome = self._call(gmail_poller, conn=mock_conn)
        assert outcome == "inserted"

        inserts = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT IGNORE INTO contact_outreach" in c.args[0]
        ]
        assert len(inserts) == 1
        params = inserts[0].args[1]
        # (user_id, contact_id, received_at, method_id, direction_id,
        #  thread_id, gmail_message_id, subject, body_text, from_email)
        assert params[0] == 42
        assert params[1] == 11
        assert params[3] == EMAIL_METHOD_ID
        assert params[4] == INBOUND_DIRECTION_ID
        assert params[5] == "thread-abc"
        assert params[6] == "msg-1"
        assert params[7] == "Re: Quick intro"
        assert params[8] == "Sounds great!"
        assert params[9] == "maria@coldoutreach.com"

    def test_skipped_exists_when_unique_key_matches(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        mock_cursor.rowcount = 0
        outcome = self._call(gmail_poller, conn=mock_conn)
        assert outcome == "skipped:exists"


# ---------------------------------------------------------------------------
# _archive_raw — fan-out shared between both write paths
# ---------------------------------------------------------------------------


class TestArchive:
    def test_returns_s3_key_on_success(self, mocker, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        fake_s3 = MagicMock()
        mocker.patch.object(gmail_poller, "_get_s3", return_value=fake_s3)

        execute = MagicMock(return_value={"raw": "cmF3IGJ5dGVz"})
        get_call = MagicMock(return_value=MagicMock(execute=execute))
        messages = MagicMock(get=get_call)
        users = MagicMock(messages=MagicMock(return_value=messages))
        service = MagicMock(users=MagicMock(return_value=users))

        s3_key = gmail_poller._archive_raw(
            service=service, user_id=42, msg_id="msg-1",
            archive_bucket="bucket",
        )
        assert s3_key == "42/msg-1.eml"
        fake_s3.put_object.assert_called_once()

    def test_returns_none_on_failure(self, mocker, monkeypatch):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()
        fake_s3 = MagicMock()
        fake_s3.put_object.side_effect = RuntimeError("boom")
        mocker.patch.object(gmail_poller, "_get_s3", return_value=fake_s3)

        execute = MagicMock(return_value={"raw": "cmF3IGJ5dGVz"})
        get_call = MagicMock(return_value=MagicMock(execute=execute))
        messages = MagicMock(get=get_call)
        users = MagicMock(messages=MagicMock(return_value=messages))
        service = MagicMock(users=MagicMock(return_value=users))

        assert gmail_poller._archive_raw(
            service=service, user_id=42, msg_id="msg-1",
            archive_bucket="bucket",
        ) is None


# ---------------------------------------------------------------------------
# _process_thread — dual-write orchestration
# ---------------------------------------------------------------------------


class TestProcessThread:
    """Exercises the dual-write per-message logic.

    The thread anchors are resolved via two SELECTs (submissions list +
    most-recent contact_id). Tests configure ``mock_cursor.fetchall`` /
    ``fetchone`` via ``side_effect`` to simulate each anchor scenario.
    """

    def _common_kwargs(self, gmail_poller, *, mock_conn, mocker, messages):
        # S3 archive succeeds and returns a key.
        mocker.patch.object(
            gmail_poller, "_archive_raw",
            return_value="42/msg-id.eml",
        )
        return {
            "conn": mock_conn,
            "service": _service_with_thread(messages),
            "user_id": 42,
            "thread_id": "thread-abc",
            "gmail_address": "user@gmail.com",
            "classifications": _classifications_map(),
            "statuses": _statuses_map(),
            "email_method_id": EMAIL_METHOD_ID,
            "inbound_direction_id": INBOUND_DIRECTION_ID,
            "archive_bucket": "bucket",
        }

    def test_thread_with_no_anchors_skipped(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        # No submissions, no contact_outreach rows.
        mock_cursor.fetchall.side_effect = [[]]
        mock_cursor.fetchone.return_value = None

        msg = _make_message()
        kwargs = self._common_kwargs(
            gmail_poller, mock_conn=mock_conn, mocker=mocker, messages=[msg]
        )
        counters = gmail_poller._process_thread(**kwargs)
        # Nothing inserted because there are no anchors.
        assert counters.get("responses_inserted", 0) == 0
        assert counters.get("contact_outreach_inserted", 0) == 0

    def test_submission_only_thread(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        # _resolve_thread_anchors: fetchall → submissions, fetchone → contact.
        mock_cursor.fetchall.side_effect = [[{"id": 7}]]
        mock_cursor.fetchone.return_value = None
        mock_cursor.rowcount = 1  # All inserts succeed

        msg = _make_message(
            subject="Update", body_text="Unfortunately we won't be moving forward."
        )
        kwargs = self._common_kwargs(
            gmail_poller, mock_conn=mock_conn, mocker=mocker, messages=[msg]
        )
        counters = gmail_poller._process_thread(**kwargs)

        assert counters["responses_inserted"] == 1
        assert counters.get("contact_outreach_inserted", 0) == 0

        co_inserts = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT IGNORE INTO contact_outreach" in c.args[0]
        ]
        assert len(co_inserts) == 0

    def test_contact_only_thread(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        # No submissions; contact 11 is the most-recent.
        mock_cursor.fetchall.side_effect = [[]]
        mock_cursor.fetchone.return_value = {"contact_id": 11}
        mock_cursor.rowcount = 1

        msg = _make_message(
            subject="Re: intro", body_text="Sounds great, let's chat."
        )
        kwargs = self._common_kwargs(
            gmail_poller, mock_conn=mock_conn, mocker=mocker, messages=[msg]
        )
        counters = gmail_poller._process_thread(**kwargs)

        assert counters.get("responses_inserted", 0) == 0
        assert counters["contact_outreach_inserted"] == 1

        co_inserts = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT IGNORE INTO contact_outreach" in c.args[0]
        ]
        assert len(co_inserts) == 1
        assert co_inserts[0].args[1][1] == 11  # contact_id

        # No status-bump UPDATE in the contact-only path.
        assert not any(
            "UPDATE submissions SET submission_status_id" in c.args[0]
            for c in mock_cursor.execute.call_args_list
        )

    def test_thread_mapped_to_both(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        # Submission 7 + most-recent contact 11.
        mock_cursor.fetchall.side_effect = [[{"id": 7}]]
        mock_cursor.fetchone.return_value = {"contact_id": 11}
        mock_cursor.rowcount = 1

        msg = _make_message(
            subject="Re: Application", body_text="Let's schedule a call."
        )
        kwargs = self._common_kwargs(
            gmail_poller, mock_conn=mock_conn, mocker=mocker, messages=[msg]
        )
        counters = gmail_poller._process_thread(**kwargs)

        assert counters["responses_inserted"] == 1
        assert counters["contact_outreach_inserted"] == 1

    def test_self_sent_message_skipped(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        mock_cursor.fetchall.side_effect = [[{"id": 7}]]
        mock_cursor.fetchone.return_value = {"contact_id": 11}

        msg = _make_message(from_addr='"Me" <USER@gmail.com>')
        kwargs = self._common_kwargs(
            gmail_poller, mock_conn=mock_conn, mocker=mocker, messages=[msg]
        )
        counters = gmail_poller._process_thread(**kwargs)

        assert counters["skipped:self_sent"] == 1
        assert counters.get("responses_inserted", 0) == 0
        assert counters.get("contact_outreach_inserted", 0) == 0

    def test_idempotent_rerun_skips_existing_rows(
        self, mocker, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        mock_cursor.fetchall.side_effect = [[{"id": 7}]]
        mock_cursor.fetchone.return_value = {"contact_id": 11}
        mock_cursor.rowcount = 0  # INSERT IGNORE matched on every write

        msg = _make_message()
        kwargs = self._common_kwargs(
            gmail_poller, mock_conn=mock_conn, mocker=mocker, messages=[msg]
        )
        counters = gmail_poller._process_thread(**kwargs)

        assert counters.get("responses_inserted", 0) == 0
        assert counters["responses_skipped:exists"] == 1
        assert counters.get("contact_outreach_inserted", 0) == 0
        assert counters["contact_outreach_skipped:exists"] == 1


# ---------------------------------------------------------------------------
# _resolve_thread_anchors — multi-contact most-recent (Fork 1)
# ---------------------------------------------------------------------------


class TestResolveThreadAnchors:
    def test_returns_most_recent_contact(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        # The SQL is `ORDER BY outreach_at DESC LIMIT 1`, so the test
        # only needs to assert that the cursor returns whatever the
        # query yields. The "multi-contact" semantics live in the SQL
        # itself; here we verify the function plumbs it through.
        mock_cursor.fetchall.return_value = [{"id": 7}, {"id": 12}]
        mock_cursor.fetchone.return_value = {"contact_id": 11}

        sub_ids, contact_id = gmail_poller._resolve_thread_anchors(
            mock_conn, user_id=42, thread_id="thread-abc"
        )
        assert sub_ids == [7, 12]
        assert contact_id == 11

    def test_no_anchors(self, monkeypatch, mock_cursor, mock_conn):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None

        sub_ids, contact_id = gmail_poller._resolve_thread_anchors(
            mock_conn, user_id=42, thread_id="thread-abc"
        )
        assert sub_ids == []
        assert contact_id is None


# ---------------------------------------------------------------------------
# _enumerate_thread_ids — UNION of submissions ∪ contact_outreach
# ---------------------------------------------------------------------------


class TestEnumerateThreadIds:
    def test_returns_distinct_thread_ids_from_both_sources(
        self, monkeypatch, mock_cursor, mock_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        mock_cursor.fetchall.return_value = [
            {"thread_id": "thread-sub-only"},
            {"thread_id": "thread-contact-only"},
            {"thread_id": "thread-both"},
        ]

        thread_ids = gmail_poller._enumerate_thread_ids(mock_conn, user_id=42)
        assert thread_ids == [
            "thread-sub-only",
            "thread-contact-only",
            "thread-both",
        ]

        sql = mock_cursor.execute.call_args.args[0]
        assert "FROM submissions" in sql
        assert "FROM contact_outreach" in sql
        assert "UNION" in sql


# ---------------------------------------------------------------------------
# Top-level handler
# ---------------------------------------------------------------------------


class TestHandler:
    def test_no_active_users_exits_clean(
        self, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_poller = _import_fresh()

        mock_cursor.fetchall.return_value = []
        patched_conn("handlers.gmail_poller")

        out = gmail_poller.handler({}, lambda_ctx)

        assert out["users_processed"] == 0
        assert out["users_failed"] == 0

    def test_misconfigured_env_returns_error(self, monkeypatch, lambda_ctx):
        # Don't set the env vars — trigger the early misconfigured check.
        monkeypatch.delenv("GMAIL_KMS_KEY_ID", raising=False)
        monkeypatch.delenv("GMAIL_ARCHIVE_BUCKET", raising=False)
        monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SSM_PATH", "x")
        gmail_poller = _import_fresh()

        out = gmail_poller.handler({}, lambda_ctx)

        assert out["users_processed"] == 0
        assert out.get("error") == "misconfigured"