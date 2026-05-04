"""Unit tests for ``handlers/gmail_compose.py``.

Three layers covered:

1. The MIME builder (``_build_mime``) — header construction, plaintext
   body, attachment, threading headers in reply mode.
2. The orchestration in ``_send`` — scope gate, mode selection
   (compose vs reply), submission state coupling (must / must-not have
   ``gmail_thread_id``), thread_id persistence on compose, mismatch
   detection on reply.
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


def message_from_bytes(raw: bytes) -> EmailMessage:
    """Parse RFC 822 bytes into the modern EmailMessage class.

    The stdlib's ``email.message_from_bytes`` defaults to the legacy
    Compat32 policy + ``Message`` class, which lacks
    ``iter_attachments()`` and ``get_body()``. ``BytesParser(policy=
    default)`` returns the modern API.
    """
    return BytesParser(policy=_default_policy).parsebytes(raw)
from unittest.mock import MagicMock

import pytest


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


def _auth_event(*, body: dict | None = None, path_id: int | None = 7):
    e: dict = {
        "routeKey": "POST /submissions/{id}/send",
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
            },
        },
    }
    if body is not None:
        e["body"] = json.dumps(body)
    if path_id is not None:
        e["pathParameters"] = {"id": str(path_id)}
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

    # send() chain
    send_execute = MagicMock(
        return_value={"id": sent_message_id, "threadId": sent_thread_id}
    )
    send_call = MagicMock(return_value=MagicMock(execute=send_execute))

    # get() chain — used in reply mode to fetch the original message's headers
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
        # Body
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
# Orchestration (handler dispatch)
# ---------------------------------------------------------------------------


class TestHandlerCompose:
    """compose mode (no in_reply_to_message_id)"""

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
            },
            submission_thread_id=None,
            sent_thread_id="thread-new",
        )
        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["thread_id"] == "thread-new"
        assert body["gmail_message_id"] == "sent-msg-1"

        # send() called without threadId in the body (compose mode)
        send_kwargs = send_call.call_args.kwargs
        assert send_kwargs["userId"] == "me"
        assert "threadId" not in send_kwargs["body"]
        assert "raw" in send_kwargs["body"]

        # submissions.gmail_thread_id set from the response
        update_calls = [
            c for c in cursor.execute.call_args_list
            if "UPDATE submissions SET gmail_thread_id" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1] == ("thread-new", 7, 42)

    def test_already_linked_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Compose mode against a submission that already has a thread → error.
        out, _cursor, _gc, _send = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={"to": ["a@x.com"], "subject": "S", "body": "B"},
            submission_thread_id="existing-thread",
        )
        assert out["statusCode"] == 400
        assert "already linked" in json.loads(out["body"])["error"]

    def test_missing_send_scope_returns_403(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _cursor, _gc, _send = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={"to": ["a@x.com"], "subject": "S", "body": "B"},
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
            body={"subject": "S", "body": "B"},
        )
        assert out["statusCode"] == 400
        assert "to is required" in json.loads(out["body"])["error"]

    def test_validation_missing_subject(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        out, _c, _gc, _s = self._run(
            mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
            body={"to": ["a@x.com"], "body": "B"},
        )
        assert out["statusCode"] == 400
        assert "subject is required" in json.loads(out["body"])["error"]


class TestHandlerReply:
    """reply mode (in_reply_to_message_id present)"""

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
        service, send_call = _mock_google_libs(
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
            },
            submission_thread_id="thread-abc",
            sent_thread_id="thread-abc",
        )
        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["thread_id"] == "thread-abc"

        # send body should include threadId
        send_kwargs = send_call.call_args.kwargs
        assert send_kwargs["body"]["threadId"] == "thread-abc"

        # The raw RFC 822 should include In-Reply-To header
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
            },
            submission_thread_id=None,  # not linked
            sent_thread_id="thread-abc",
        )
        assert out["statusCode"] == 400
        assert "no linked thread" in json.loads(out["body"])["error"]

    def test_thread_id_mismatch_raises(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Reply mode where Gmail returns a different threadId than what we
        # have on the submission. Should never happen in practice (Gmail's
        # threadId guard catches this), but if it does we surface it as a
        # 5xx via re-raise.
        with pytest.raises(RuntimeError, match="thread_id mismatch"):
            self._run(
                mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn,
                body={
                    "to": ["a@x.com"],
                    "subject": "S",
                    "body": "B",
                    "in_reply_to_message_id": "gmail-msg-id-123",
                },
                submission_thread_id="thread-abc",
                sent_thread_id="thread-different",
            )


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

        # Mock S3 returning PDF bytes
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
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 200

        # S3 was hit with the right key
        fake_s3.get_object.assert_called_once_with(
            Bucket="test-resume-bucket",
            Key="users/user-sub-1/resumes/9.pdf",
        )

        # The raw email contains a PDF attachment with the right filename
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
                }
            ),
            lambda_ctx,
        )
        assert out["statusCode"] == 404
        assert "resume not found" in json.loads(out["body"])["error"]