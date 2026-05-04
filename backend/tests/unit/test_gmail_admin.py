"""Unit tests for ``handlers/gmail_admin.py``.

Covers all four routes: status, disconnect, link-thread, unlink-thread.

External calls mocked:
  * boto3 SSM (for OAuth client credentials)
  * boto3 KMS (via ``common.gmail_client._get_kms_client``)
  * ``urllib.request.urlopen`` (for the Google revoke endpoint)
  * ``common.gmail_client.build_credentials`` /
    ``common.gmail_client.build_gmail_service`` /
    ``common.gmail_client.resolve_message_id_to_thread`` (avoids the
    google-auth + google-api-python-client packages and lets each test
    control the resolved threadId).
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _auth_event(route_key: str, *, body: dict | None = None, path_id: int | None = None):
    e: dict = {
        "routeKey": route_key,
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}},
        },
    }
    if body is not None:
        e["body"] = json.dumps(body)
    if path_id is not None:
        e["pathParameters"] = {"id": str(path_id)}
    return e


def _set_env(monkeypatch):
    monkeypatch.setenv("GMAIL_KMS_KEY_ID", "alias/jobtracker-gmail")
    monkeypatch.setenv(
        "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
    )


def _import_fresh():
    """Re-import the handler module so env-var changes are picked up."""
    from handlers import gmail_admin

    importlib.reload(gmail_admin)
    return gmail_admin


def _mock_ssm(mocker, gmail_admin):
    fake_ssm = MagicMock()
    fake_ssm.get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                {"client_id": "the-client-id", "client_secret": "the-secret"}
            )
        }
    }
    mocker.patch.object(gmail_admin, "_get_ssm", return_value=fake_ssm)


def _mock_kms(mocker, decrypted_token: str = "1//0gfake-refresh-token"):
    """Patch the gmail_client KMS singleton."""
    from common import gmail_client

    fake_kms = MagicMock()
    fake_kms.decrypt.return_value = {"Plaintext": decrypted_token.encode("utf-8")}
    mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)
    return fake_kms


def _mock_google_libs(mocker, gmail_admin, thread_id: str | None = "thread-abc"):
    """Patch credential builder, service factory, and the Message-ID resolver.

    Setting ``thread_id=None`` simulates "Message-ID not in user's mailbox".
    """
    mocker.patch.object(gmail_admin, "build_credentials", return_value=MagicMock())
    mocker.patch.object(gmail_admin, "build_gmail_service", return_value=MagicMock())
    mocker.patch.object(
        gmail_admin, "resolve_message_id_to_thread", return_value=thread_id
    )


# ---------------------------------------------------------------------------
# GET /integrations/gmail/status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_connected_returns_full_state(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        # users.get_user_id query, then status query.
        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {
                "gmail_address": "user@gmail.com",
                "scopes": (
                    "https://www.googleapis.com/auth/gmail.readonly "
                    "https://www.googleapis.com/auth/gmail.send"
                ),
                "last_polled_at": datetime(2026, 5, 3, 12, 0, 0),
            },
        ]
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event("GET /integrations/gmail/status"), lambda_ctx
        )

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body["connected"] is True
        assert body["gmail_address"] == "user@gmail.com"
        assert body["scopes"] == [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ]
        assert body["last_polled_at"] == "2026-05-03T12:00:00"

    def test_not_connected_returns_empty_state(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            None,  # no gmail_credentials row
        ]
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event("GET /integrations/gmail/status"), lambda_ctx
        )

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body == {
            "connected": False,
            "gmail_address": None,
            "scopes": [],
            "last_polled_at": None,
        }


# ---------------------------------------------------------------------------
# DELETE /integrations/gmail
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_happy_path_revokes_and_soft_deletes(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},
            {"refresh_token_ciphertext": b"\x01\x02\x03cipher"},
        ]
        patched_conn("handlers.gmail_admin")

        _mock_kms(mocker)
        revoke_mock = mocker.patch.object(
            gmail_admin, "_revoke_at_google", return_value=200
        )

        out = gmail_admin.handler(
            _auth_event("DELETE /integrations/gmail"), lambda_ctx
        )

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body == {"connected": False, "disconnected": True}
        revoke_mock.assert_called_once_with("1//0gfake-refresh-token")
        # Soft-delete UPDATE should have run.
        soft_delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "deleted_at = CURRENT_TIMESTAMP" in c.args[0]
            and "gmail_credentials" in c.args[0]
        ]
        assert len(soft_delete_calls) == 1

    def test_revoke_failure_still_soft_deletes(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Best-effort revoke: if Google's endpoint is down, we still
        # disconnect locally so the user isn't stranded.
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},
            {"refresh_token_ciphertext": b"\x01\x02\x03cipher"},
        ]
        patched_conn("handlers.gmail_admin")

        _mock_kms(mocker)
        mocker.patch.object(
            gmail_admin, "_revoke_at_google", side_effect=RuntimeError("network down")
        )

        out = gmail_admin.handler(
            _auth_event("DELETE /integrations/gmail"), lambda_ctx
        )

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body == {"connected": False, "disconnected": True}
        soft_delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "deleted_at = CURRENT_TIMESTAMP" in c.args[0]
            and "gmail_credentials" in c.args[0]
        ]
        assert len(soft_delete_calls) == 1

    def test_no_credentials_returns_404(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},
            None,  # no gmail_credentials row
        ]
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event("DELETE /integrations/gmail"), lambda_ctx
        )

        assert out["statusCode"] == 404
        assert "gmail not connected" in json.loads(out["body"])["error"]


# ---------------------------------------------------------------------------
# PUT /submissions/{id}/gmail-link
# ---------------------------------------------------------------------------


class TestLinkThread:
    def test_happy_path_resolves_and_persists(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {"id": 7},  # submission ownership check
            {  # _build_service_for_user → gmail_credentials lookup
                "refresh_token_ciphertext": b"\x01\x02\x03cipher",
                "scopes": "https://www.googleapis.com/auth/gmail.readonly",
            },
        ]
        patched_conn("handlers.gmail_admin")

        _mock_ssm(mocker, gmail_admin)
        _mock_kms(mocker)
        _mock_google_libs(mocker, gmail_admin, thread_id="thread-abc")

        out = gmail_admin.handler(
            _auth_event(
                "PUT /submissions/{id}/gmail-link",
                body={"mid": "Message-ID: <77b734db-...@gmail.com>"},
                path_id=7,
            ),
            lambda_ctx,
        )

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body == {"submission_id": 7, "thread_id": "thread-abc"}
        # Resolution query was called with the parsed Message-ID
        gmail_admin.resolve_message_id_to_thread.assert_called_once()
        args = gmail_admin.resolve_message_id_to_thread.call_args.args
        assert args[1] == "77b734db-...@gmail.com"
        # Submission updated with the thread id
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET gmail_thread_id = %s" in c.args[0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1] == ("thread-abc", 7, 42)

    def test_garbage_input_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.return_value = {"id": 42}
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event(
                "PUT /submissions/{id}/gmail-link",
                body={"mid": "not a real message id"},
                path_id=7,
            ),
            lambda_ctx,
        )

        assert out["statusCode"] == 400
        assert "Message-ID" in json.loads(out["body"])["error"]

    def test_submission_not_owned_returns_404(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            None,  # submission not found / not owned
        ]
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event(
                "PUT /submissions/{id}/gmail-link",
                body={"mid": "<abc@example.com>"},
                path_id=999,
            ),
            lambda_ctx,
        )

        assert out["statusCode"] == 404

    def test_gmail_not_connected_returns_400(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {"id": 7},  # submission ownership ok
            None,  # gmail_credentials lookup → no row
        ]
        patched_conn("handlers.gmail_admin")

        _mock_ssm(mocker, gmail_admin)

        out = gmail_admin.handler(
            _auth_event(
                "PUT /submissions/{id}/gmail-link",
                body={"mid": "<abc@example.com>"},
                path_id=7,
            ),
            lambda_ctx,
        )

        assert out["statusCode"] == 400
        assert "gmail not connected" in json.loads(out["body"])["error"]

    def test_message_id_not_found_in_gmail_returns_404(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.side_effect = [
            {"id": 42},  # get_user_id
            {"id": 7},  # submission ownership ok
            {  # gmail_credentials lookup
                "refresh_token_ciphertext": b"\x01\x02\x03cipher",
                "scopes": "https://www.googleapis.com/auth/gmail.readonly",
            },
        ]
        patched_conn("handlers.gmail_admin")

        _mock_ssm(mocker, gmail_admin)
        _mock_kms(mocker)
        _mock_google_libs(mocker, gmail_admin, thread_id=None)  # no match

        out = gmail_admin.handler(
            _auth_event(
                "PUT /submissions/{id}/gmail-link",
                body={"mid": "<abc@example.com>"},
                path_id=7,
            ),
            lambda_ctx,
        )

        assert out["statusCode"] == 404
        assert "not found" in json.loads(out["body"])["error"].lower()


# ---------------------------------------------------------------------------
# DELETE /submissions/{id}/gmail-link
# ---------------------------------------------------------------------------


class TestUnlinkThread:
    def test_happy_path_clears_thread_id(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.return_value = {"id": 42}
        mock_cursor.rowcount = 1
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event("DELETE /submissions/{id}/gmail-link", path_id=7),
            lambda_ctx,
        )

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert body == {"submission_id": 7, "unlinked": True}
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE submissions SET gmail_thread_id = NULL" in c.args[0]
        ]
        assert len(update_calls) == 1

    def test_submission_not_found_returns_404(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        gmail_admin = _import_fresh()
        mock_cursor.fetchone.return_value = {"id": 42}
        mock_cursor.rowcount = 0
        patched_conn("handlers.gmail_admin")

        out = gmail_admin.handler(
            _auth_event("DELETE /submissions/{id}/gmail-link", path_id=999),
            lambda_ctx,
        )

        assert out["statusCode"] == 404