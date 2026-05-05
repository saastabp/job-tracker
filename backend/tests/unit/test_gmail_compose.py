"""Unit tests for ``handlers/gmail_compose.py``.

The route is ``POST /messages/send``. Body carries ``submission_id?``
and ``contact_id?``; at least one is required (Fork 4 — slice-10 plan).

Three layers covered:

1. The MIME builder (``_build_mime``) — header construction, plaintext
   body, attachment, threading headers in reply mode.
2. The orchestration in ``_send`` — scope gate, anchor validation
   (submission-only / contact-only / both / neither), mode selection
   (compose vs reply), submission state coupling (must / must-not have
   ``gmail_thread_id``), thread_id persistence on compose, contact_outreach
   row insertion, mismatch detection on reply.
3. The handler-level dispatch — auth, validation, error mapping.

External calls mocked:
  * SSM (OAuth client) and S3 (resume PDF) singletons in the module
  * KMS via ``common.gmail_client._get_kms_client``
  * ``build_credentials`` / ``build_gmail_service`` (no google-auth dep)
  * The mocked Gmail service's send + messages.get chain
"""
from __future__ import annotations

import base64
import importlib
import json
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as _default_policy
from unittest.mock import MagicMock

import pytest


def message_from_bytes(raw: bytes) -> EmailMessage:
    """Parse RFC 822 bytes into the modern EmailMessage class.

    The stdlib's ``email.message_from_bytes`` defaults to the legacy
    Compat32 policy + ``Message`` class, which lacks
    ``iter_attachments()`` and ``get_body()``. ``BytesParser(policy=
    default)`` returns the modern API.
    """
    return BytesParser(policy=_default_policy).parsebytes(raw)


def _set_env(monkeypatch):
    monkeypatch.setenv("GMAIL_KMS_KEY_ID", "alias/jobtracker-gmail")
    monkeypatch.setenv(
        "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
    )
    monkeypatch.setenv("RESUME_BUCKET", "test-resume-bucket")


def _import_fresh():
    from handlers import gmail_compose

    importlib.reload(gmail_compose)
    return gmail_compose


def _auth_event(*, body: dict | None = None):
    e: dict = {
        "routeKey": "POST /messages/send",
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
            },
        },
    }
    if body is not None:
        e["body"] = json.dumps(body)
    return e


def _mock_ssm(mocker, gmail_compose):
    fake_ssm = MagicMock()
    fake_ssm.get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                {"client_id": "the-client-id", "client_secret": "the-secret"}
            )
        }
    }
    mocker.patch.object(gmail_compose, "_get_ssm", return_value=fake_ssm)


def _mock_kms(mocker, decrypted_token: str = "1//0gfake-refresh"):
    from common import gmail_client

    fake_kms = MagicMock()
    fake_kms.decrypt.return_value = {"Plaintext": decrypted_token.encode("utf-8")}
    mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)


def _mock_google_libs(
    mocker,
    gmail_compose,
    *,
    sent_thread_id: str = "thread-abc",
    sent_message_id: str = "sent-msg-1",
    in_reply_to_rfc: str = "<orig@mail.gmail.com>",
    in_reply_to_references: str = "",
):
    """Patch credential builder and a Gmail service mock that supports both
    ``users().messages().send`` and ``users().messages().get`` calls."""
    mocker.patch.object(gmail_compose, "build_credentials", return_value=MagicMock())

    send_execute = MagicMock(
        return_value={"id": sent_message_id, "threadId": sent_thread_id}
    )
    send_call = MagicMock(return_value=MagicMock(execute=send_execute))

    headers = []
    if in_reply_to_rfc:
        headers.append({"name": "Message-Id", "value": in_reply_to_rfc})
    if in_reply_to_references:
        headers.append({"name": "References", "value": in_reply_to_references})
    get_execute = MagicMock(return_value={"payload": {"headers": headers}})
    get_call = MagicMock(return_value=MagicMock(execute=get_execute))

    messages_resource = MagicMock(send=send_call, get=get_call)
    users_resource = MagicMock(
        messages=MagicMock(return_value=messages_resource)
    )
    service = MagicMock(users=MagicMock(return_value=users_resource))
    mocker.patch.object(gmail_compose, "build_gmail_service", return_value=service)
    return service, send_call


def _both_scopes() -> str:
    return (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/gmail.send"
    )


def _readonly_only() -> str:
    return "https://www.googleapis.com/auth/gmail.readonly"


# Catalog rows the contact_outreach insert path looks up. Each entry is a
# pair of (table-name-substring, fake-row) since `_catalog_id` issues a
# `SELECT id FROM <table>...`. Order follows the call order in `_send`:
# direction first, method second.
_CATALOG_DIRECTION_OUTBOUND = {"id": 71}
_CATALOG_METHOD_EMAIL = {"id": 81}


# ---------------------------------------------------------------------------
# MIME builder
# ---------------------------------------------------------------------------


class TestMimeBuilder:
    def test_to_only_plaintext(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()
        msg = gmail_compose._build_mime(
            to=["recruiter@acme.com"],
            cc=[],
            bcc=[],
            subject="Hello",
            body="Hi there.",
            in_reply_to_rfc="",
            references_chain="",
            attachment_pdf=None,
            attachment_filename=None,
        )
        raw = msg.as_bytes()
        parsed = message_from_bytes(raw)
        assert parsed["To"] == "recruiter@acme.com"
        assert parsed["Subject"] == "Hello"
        assert parsed["Cc"] is None
        assert parsed["In-Reply-To"] is None
        body = parsed.get_body(preferencelist=("plain",))
        assert body.get_content().strip() == "Hi there."

    def test_cc_and_bcc(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()
        msg = gmail_compose._build_mime(
            to=["a@x.com"],
            cc=["b@x.com", "c@x.com"],
            bcc=["d@x.com"],
            subject="S",
            body="B",
            in_reply_to_rfc="",
            references_chain="",
            attachment_pdf=None,
            attachment_filename=None,
        )
        parsed = message_from_bytes(msg.as_bytes())
        assert parsed["Cc"] == "b@x.com, c@x.com"
        assert parsed["Bcc"] == "d@x.com"

    def test_attachment_pdf(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()
        pdf_bytes = b"%PDF-1.4\nfake pdf content"
        msg = gmail_compose._build_mime(
            to=["a@x.com"],
            cc=[],
            bcc=[],
            subject="S",
            body="B",
            in_reply_to_rfc="",
            references_chain="",
            attachment_pdf=pdf_bytes,
            attachment_filename="my-resume.pdf",
        )
        parsed = message_from_bytes(msg.as_bytes())
        assert parsed.is_multipart()
        attachments = list(parsed.iter_attachments())
        assert len(attachments) == 1
        att = attachments[0]
        assert att.get_content_type() == "application/pdf"
        assert att.get_filename() == "my-resume.pdf"
        assert att.get_payload(decode=True) == pdf_bytes

    def test_reply_threading_headers(self, monkeypatch):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()
        msg = gmail_compose._build_mime(
            to=["a@x.com"],
            cc=[],
            bcc=[],
            subject="Re: S",
            body="B",
            in_reply_to_rfc="<orig@mail.gmail.com>",
            references_chain="<earlier@mail.gmail.com> <orig@mail.gmail.com>",
            attachment_pdf=None,
            attachment_filename=None,
        )
        parsed = message_from_bytes(msg.as_bytes())
        assert parsed["In-Reply-To"] == "<orig@mail.gmail.com>"
        assert parsed["References"] == (
            "<earlier@mail.gmail.com> <orig@mail.gmail.com>"
        )


# ---------------------------------------------------------------------------
# Submission-only path (slice-9 carry-over, new route)
# ---------------------------------------------------------------------------


class TestSubmissionOnly:
    """``submission_id`` set, ``contact_id`` null."""

    def _run(
        self,
        mocker,
        monkeypatch,
        lambda_ctx,
        mock_cursor,
        patched_conn,
        *,
        body: dict,
        submission_thread_id: str | None = None,
        scopes: str | None = None,
        sent_thread_id: str = "thread-new",
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()
        scopes = scopes if scopes is not None else _both_scopes()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {  # gmail_credentials
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": scopes,
            },
            {"id": 7, "gmail_thread_id": submission_thread_id},  # submission lookup
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        service, send_call = _mock_google_libs(
            mocker, gmail_compose, sent_thread_id=sent_thread_id
        )

        out = gmail_compose.handler(_auth_event(body=body), lambda_ctx)
        return out, mock_cursor, gmail_compose, send_call

    def test_happy_path_writes_thread_id(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, cursor, _gc, send_call = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["recruiter@acme.com"],
                "subject": "Application — Senior SRE",
                "body": "Hi! Excited about the role. Please find my resume.",
                "submission_id": 7,
            },
            submission_thread_id=None,
            sent_thread_id="thread-new",
        )
        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["thread_id"] == "thread-new"
        assert body["gmail_message_id"] == "sent-msg-1"

        send_kwargs = send_call.call_args.kwargs
        assert send_kwargs["userId"] == "me"
        assert "threadId" not in send_kwargs["body"]
        assert "raw" in send_kwargs["body"]

        update_calls = [
            c for c in cursor.execute.call_args_list
            if "UPDATE submissions SET gmail_thread_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1] == ("thread-new", 7, 42)

        # contact_id is null → no contact_outreach insert.
        co_inserts = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO contact_outreach" in c.args[0]
        ]
        assert len(co_inserts) == 0

    def test_already_linked_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _cursor, _gc, _send = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["a@x.com"],
                "subject": "S",
                "body": "B",
                "submission_id": 7,
            },
            submission_thread_id="existing-thread",
        )
        assert out["statusCode"] == 400
        assert "already linked" in json.loads(out["body"])["error"]

    def test_missing_send_scope_returns_403(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _cursor, _gc, _send = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["a@x.com"],
                "subject": "S",
                "body": "B",
                "submission_id": 7,
            },
            scopes=_readonly_only(),
        )
        assert out["statusCode"] == 403
        body = json.loads(out["body"])
        assert body["needs_reconsent"] is True

    def test_validation_missing_to(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _c, _gc, _s = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={"subject": "S", "body": "B", "submission_id": 7},
        )
        assert out["statusCode"] == 400
        assert "to is required" in json.loads(out["body"])["error"]

    def test_validation_missing_subject(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _c, _gc, _s = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={"to": ["a@x.com"], "body": "B", "submission_id": 7},
        )
        assert out["statusCode"] == 400
        assert "subject is required" in json.loads(out["body"])["error"]


# ---------------------------------------------------------------------------
# Contact-only path (new in slice 10)
# ---------------------------------------------------------------------------


class TestContactOnly:
    """``contact_id`` set, ``submission_id`` null."""

    def _run(
        self,
        mocker,
        monkeypatch,
        lambda_ctx,
        mock_cursor,
        patched_conn,
        *,
        body: dict,
        contact_found: bool = True,
        sent_thread_id: str = "thread-new",
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {  # gmail_credentials
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 11} if contact_found else None,  # contact lookup
            _CATALOG_DIRECTION_OUTBOUND,
            _CATALOG_METHOD_EMAIL,
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        _service, send_call = _mock_google_libs(
            mocker, gmail_compose, sent_thread_id=sent_thread_id
        )

        out = gmail_compose.handler(_auth_event(body=body), lambda_ctx)
        return out, mock_cursor, send_call

    def test_happy_path_inserts_contact_outreach(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, cursor, send_call = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["maria@coldoutreach.com"],
                "subject": "Quick intro",
                "body": "Hi Maria — saw your post about hiring SREs.",
                "contact_id": 11,
            },
            sent_thread_id="thread-cold-1",
        )
        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["thread_id"] == "thread-cold-1"
        assert body["gmail_message_id"] == "sent-msg-1"

        send_kwargs = send_call.call_args.kwargs
        assert "threadId" not in send_kwargs["body"]

        # No submission update (no submission_id).
        assert not any(
            "UPDATE submissions SET gmail_thread_id" in c.args[0]
            for c in cursor.execute.call_args_list
        )

        # contact_outreach insert with the right shape.
        co_inserts = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO contact_outreach" in c.args[0]
        ]
        assert len(co_inserts) == 1
        params = co_inserts[0].args[1]
        # (user_id, contact_id, method_id, direction_id,
        #  thread_id, gmail_message_id, subject, body_text, from_email)
        assert params[0] == 42
        assert params[1] == 11
        assert params[2] == _CATALOG_METHOD_EMAIL["id"]
        assert params[3] == _CATALOG_DIRECTION_OUTBOUND["id"]
        assert params[4] == "thread-cold-1"
        assert params[5] == "sent-msg-1"
        assert params[6] == "Quick intro"
        assert params[7] == "Hi Maria — saw your post about hiring SREs."
        assert params[8] == "user@gmail.com"

    def test_reply_with_contact_only_resolves_thread_from_contact_outreach(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Slice 10.5: reply with only contact_id resolves the thread from
        # the contact's most-recent contact_outreach row.
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {  # gmail_credentials
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 11},  # contact lookup
            {"gmail_thread_id": "thread-existing"},  # contact_outreach lookup
            _CATALOG_DIRECTION_OUTBOUND,
            _CATALOG_METHOD_EMAIL,
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        _service, send_call = _mock_google_libs(
            mocker, gmail_compose, sent_thread_id="thread-existing",
            in_reply_to_rfc="<orig@mail.gmail.com>",
        )

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["maria@coldoutreach.com"],
                    "subject": "Re: Quick intro",
                    "body": "Sounds great!",
                    "contact_id": 11,
                    "in_reply_to_message_id": "gmail-msg-id-orig",
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 200
        # send body carries the resolved thread_id
        send_kwargs = send_call.call_args.kwargs
        assert send_kwargs["body"]["threadId"] == "thread-existing"

        # Outbound contact_outreach row inserted with the same thread.
        co_inserts = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO contact_outreach" in c.args[0]
        ]
        assert len(co_inserts) == 1
        assert co_inserts[0].args[1][4] == "thread-existing"

    def test_reply_with_contact_with_no_thread_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Reply mode where the contact has no contact_outreach row carrying
        # a gmail_thread_id (e.g. only manually-logged outreach so far).
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 11},  # contact ownership
            None,        # contact_outreach lookup → no thread
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        _mock_google_libs(mocker, gmail_compose)

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["maria@coldoutreach.com"],
                    "subject": "Re: Quick intro",
                    "body": "Sounds great!",
                    "contact_id": 11,
                    "in_reply_to_message_id": "gmail-msg-id-orig",
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 400
        assert "no linked thread" in json.loads(out["body"])["error"]

    def test_contact_not_found_returns_404(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _cursor, _send = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["a@x.com"],
                "subject": "S",
                "body": "B",
                "contact_id": 999,
            },
            contact_found=False,
        )
        assert out["statusCode"] == 404
        assert "contact not found" in json.loads(out["body"])["error"]


# ---------------------------------------------------------------------------
# Both anchors (submission + contact)
# ---------------------------------------------------------------------------


class TestBothAnchors:
    def test_dual_write_in_compose_mode(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {  # gmail_credentials
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 7, "gmail_thread_id": None},  # submission
            {"id": 11},                           # contact
            _CATALOG_DIRECTION_OUTBOUND,
            _CATALOG_METHOD_EMAIL,
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        _service, send_call = _mock_google_libs(
            mocker, gmail_compose, sent_thread_id="thread-both"
        )

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["recruiter@acme.com"],
                    "subject": "Application via Maria",
                    "body": "Hi — Maria suggested I reach out for the SRE role.",
                    "submission_id": 7,
                    "contact_id": 11,
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["thread_id"] == "thread-both"

        # Both writes happened.
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET gmail_thread_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1] == ("thread-both", 7, 42)

        co_inserts = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO contact_outreach" in c.args[0]
        ]
        assert len(co_inserts) == 1
        assert co_inserts[0].args[1][1] == 11  # contact_id
        assert co_inserts[0].args[1][4] == "thread-both"

        # Both writes share the same connection; commit fired once after
        # both writes (atomicity proxy — same conn.commit call covers both).
        # We can sanity-check by ensuring the contact_outreach insert
        # happened in the call sequence AFTER the submission update.
        idx_update = next(
            i for i, c in enumerate(mock_cursor.execute.call_args_list)
            if "UPDATE submissions SET gmail_thread_id" in c.args[0]
        )
        idx_co = next(
            i for i, c in enumerate(mock_cursor.execute.call_args_list)
            if "INSERT INTO contact_outreach" in c.args[0]
        )
        assert idx_co > idx_update


# ---------------------------------------------------------------------------
# Reply mode (submission anchor required)
# ---------------------------------------------------------------------------


class TestHandlerReply:
    def _run(
        self,
        mocker,
        monkeypatch,
        lambda_ctx,
        mock_cursor,
        patched_conn,
        *,
        body: dict,
        submission_thread_id: str | None,
        sent_thread_id: str,
        in_reply_to_rfc: str = "<orig@mail.gmail.com>",
        in_reply_to_references: str = "",
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},
            {
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 7, "gmail_thread_id": submission_thread_id},
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        _service, send_call = _mock_google_libs(
            mocker,
            gmail_compose,
            sent_thread_id=sent_thread_id,
            in_reply_to_rfc=in_reply_to_rfc,
            in_reply_to_references=in_reply_to_references,
        )
        out = gmail_compose.handler(_auth_event(body=body), lambda_ctx)
        return out, send_call

    def test_happy_path_passes_thread_id_and_threading_headers(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, send_call = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["recruiter@acme.com"],
                "subject": "Re: Application",
                "body": "Thanks for getting back!",
                "in_reply_to_message_id": "gmail-msg-id-123",
                "submission_id": 7,
            },
            submission_thread_id="thread-abc",
            sent_thread_id="thread-abc",
        )
        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["thread_id"] == "thread-abc"

        send_kwargs = send_call.call_args.kwargs
        assert send_kwargs["body"]["threadId"] == "thread-abc"

        raw_b64 = send_kwargs["body"]["raw"]
        raw = base64.urlsafe_b64decode(raw_b64)
        parsed = message_from_bytes(raw)
        assert parsed["In-Reply-To"] == "<orig@mail.gmail.com>"
        assert parsed["References"] == "<orig@mail.gmail.com>"

    def test_references_chain_appended_when_original_has_references(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, send_call = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["a@x.com"],
                "subject": "S",
                "body": "B",
                "in_reply_to_message_id": "gmail-msg-id-123",
                "submission_id": 7,
            },
            submission_thread_id="thread-abc",
            sent_thread_id="thread-abc",
            in_reply_to_rfc="<orig@mail.gmail.com>",
            in_reply_to_references="<gp@mail.gmail.com> <p@mail.gmail.com>",
        )
        assert out["statusCode"] == 200
        raw = base64.urlsafe_b64decode(
            send_call.call_args.kwargs["body"]["raw"]
        )
        parsed = message_from_bytes(raw)
        assert parsed["References"] == (
            "<gp@mail.gmail.com> <p@mail.gmail.com> <orig@mail.gmail.com>"
        )

    def test_unlinked_submission_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _send = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={
                "to": ["a@x.com"],
                "subject": "S",
                "body": "B",
                "in_reply_to_message_id": "gmail-msg-id-123",
                "submission_id": 7,
            },
            submission_thread_id=None,
            sent_thread_id="thread-abc",
        )
        assert out["statusCode"] == 400
        assert "no linked thread" in json.loads(out["body"])["error"]

    def test_thread_id_mismatch_raises(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Reply mode where Gmail returns a different threadId than the
        # one stored on the submission. Should never happen in practice
        # (Gmail's threadId guard catches this), but if it does we
        # surface it as a 5xx via re-raise.
        with pytest.raises(RuntimeError, match="thread_id mismatch"):
            self._run(
                mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
                body={
                    "to": ["a@x.com"],
                    "subject": "S",
                    "body": "B",
                    "in_reply_to_message_id": "gmail-msg-id-123",
                    "submission_id": 7,
                },
                submission_thread_id="thread-abc",
                sent_thread_id="thread-different",
            )


# ---------------------------------------------------------------------------
# Anchor validation
# ---------------------------------------------------------------------------


class TestAnchorValidation:
    def test_neither_anchor_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {  # gmail_credentials
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
        ]
        patched_conn("handlers.gmail_compose")

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["a@x.com"],
                    "subject": "S",
                    "body": "B",
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 400
        assert "submission_id or contact_id" in json.loads(out["body"])["error"]

    def test_invalid_route_returns_404(
        self, monkeypatch, lambda_ctx, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()
        event = {
            "routeKey": "POST /something/else",
            "requestContext": {
                "authorizer": {
                    "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
                },
            },
        }
        out = gmail_compose.handler(event, lambda_ctx)
        assert out["statusCode"] == 404


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class TestHandlerMisc:
    def test_no_gmail_credentials_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            None,  # no gmail_credentials row
        ]
        patched_conn("handlers.gmail_compose")

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["a@x.com"],
                    "subject": "S",
                    "body": "B",
                    "submission_id": 7,
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 400
        assert "gmail not connected" in json.loads(out["body"])["error"]

    def test_resume_attachment_fetches_from_s3(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},
            {
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 7, "gmail_thread_id": None},
            {"file_s3_key": "users/user-sub-1/resumes/9.pdf",
             "original_filename": "Brian-Resume-2026.pdf"},
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)

        fake_s3 = MagicMock()
        fake_body = MagicMock()
        fake_body.read.return_value = b"%PDF-1.4 fake content"
        fake_s3.get_object.return_value = {"Body": fake_body}
        mocker.patch.object(gmail_compose, "_get_s3", return_value=fake_s3)

        _service, send_call = _mock_google_libs(
            mocker, gmail_compose, sent_thread_id="thread-new"
        )

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["recruiter@acme.com"],
                    "subject": "Application",
                    "body": "Hi.",
                    "resume_id": 9,
                    "submission_id": 7,
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 200

        fake_s3.get_object.assert_called_once_with(
            Bucket="test-resume-bucket",
            Key="users/user-sub-1/resumes/9.pdf",
        )

        raw_b64 = send_call.call_args.kwargs["body"]["raw"]
        raw = base64.urlsafe_b64decode(raw_b64)
        parsed = message_from_bytes(raw)
        attachments = list(parsed.iter_attachments())
        assert len(attachments) == 1
        att = attachments[0]
        assert att.get_content_type() == "application/pdf"
        assert att.get_filename() == "Brian-Resume-2026.pdf"
        assert att.get_payload(decode=True) == b"%PDF-1.4 fake content"

    def test_resume_not_owned_returns_404(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_compose = _import_fresh()

        mock_cursor.fetchone.side_effect = [
            {"id": 42},
            {
                "gmail_address": "user@gmail.com",
                "refresh_token_ciphertext": b"\x01\x02ciph",
                "scopes": _both_scopes(),
            },
            {"id": 7, "gmail_thread_id": None},
            None,  # resume not found / not owned
        ]
        patched_conn("handlers.gmail_compose")
        _mock_ssm(mocker, gmail_compose)
        _mock_kms(mocker)
        _mock_google_libs(mocker, gmail_compose)

        out = gmail_compose.handler(
            _auth_event(
                body={
                    "to": ["a@x.com"],
                    "subject": "S",
                    "body": "B",
                    "resume_id": 999,
                    "submission_id": 7,
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 404
        assert "resume not found" in json.loads(out["body"])["error"]