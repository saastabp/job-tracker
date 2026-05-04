"""Unit tests for ``handlers/gmail_oauth.py``.

The OAuth start handler is straightforward (Cognito-authenticated, writes
state to users, returns consent URL). The callback handler is the meatier
one: state lookup, code exchange, gmail address fetch, KMS encrypt, upsert.

External calls mocked:
  * boto3 SSM (for OAuth client credentials)
  * boto3 KMS (via ``common.gmail_client._get_kms_client``)
  * ``urllib.request.urlopen`` (for Google's token exchange)
  * ``common.gmail_client.build_gmail_service`` (avoids importing
    google-api-python-client; the returned service mock provides
    ``users().getProfile()`` to satisfy the gmail-address fetch)

DB calls go through the standard ``patched_conn`` fixture from conftest.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest


def _start_event(sub: str = "user-sub-1") -> dict:
    return {
        "routeKey": "GET /integrations/gmail/oauth/start",
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"sub": sub, "email": "x@y.z"}}},
            "domainName": "abc123.execute-api.us-west-2.amazonaws.com",
        },
    }


def _callback_event(*, code: str = "auth-code-xyz", state: str = "state-token-abc") -> dict:
    qs: dict[str, str] = {}
    if code is not None:
        qs["code"] = code
    if state is not None:
        qs["state"] = state
    return {
        "routeKey": "GET /integrations/gmail/oauth/callback",
        "requestContext": {
            # No JWT claims — callback is unauthenticated.
            "domainName": "abc123.execute-api.us-west-2.amazonaws.com",
        },
        "queryStringParameters": qs,
    }


def _set_env(monkeypatch):
    """Set the env vars the handler reads at import time."""
    monkeypatch.setenv("GMAIL_KMS_KEY_ID", "alias/jobtracker-gmail")
    monkeypatch.setenv(
        "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
    )
    monkeypatch.setenv("SPA_REDIRECT_URL", "https://app.example.com")


def _mock_ssm_oauth_client(mocker, gmail_oauth):
    """Patch the SSM client to return canned OAuth client credentials."""
    fake_ssm = MagicMock()
    fake_ssm.get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                {"client_id": "the-client-id", "client_secret": "the-secret"}
            )
        }
    }
    mocker.patch.object(gmail_oauth, "_get_ssm", return_value=fake_ssm)
    return fake_ssm


# ---------------------------------------------------------------------------
# OAuth start
# ---------------------------------------------------------------------------


class TestOAuthStart:
    def test_happy_path_returns_consent_url(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        # Reload to ensure env vars are picked up.
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        _mock_ssm_oauth_client(mocker, gmail_oauth)
        # users.get_user_id query
        mock_cursor.fetchone.return_value = {"id": 42}
        patched_conn("handlers.gmail_oauth")

        out = gmail_oauth.handler(_start_event(), lambda_ctx)

        assert out["statusCode"] == 200
        body = json.loads(out["body"])
        assert "consent_url" in body
        url = body["consent_url"]

        assert url.startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"
        )
        # Required OAuth params present
        assert "client_id=the-client-id" in url
        assert "response_type=code" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url
        assert (
            "redirect_uri=https%3A%2F%2Fabc123.execute-api.us-west-2.amazonaws.com%2Fintegrations%2Fgmail%2Foauth%2Fcallback"
            in url
        )
        # Both scopes requested
        assert "gmail.readonly" in url
        assert "gmail.send" in url
        # State is a non-trivial random token in the URL
        assert "state=" in url

    def test_unauthenticated_returns_401(self, mocker, monkeypatch, lambda_ctx):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        event = _start_event()
        # Strip the JWT claims to simulate missing auth (this should not
        # actually happen at API Gateway level since the route is
        # protected by the Cognito authorizer, but defense-in-depth).
        event["requestContext"]["authorizer"] = {}

        out = gmail_oauth.handler(event, lambda_ctx)

        assert out["statusCode"] == 401

    def test_state_persisted_with_ttl(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        _mock_ssm_oauth_client(mocker, gmail_oauth)
        mock_cursor.fetchone.return_value = {"id": 42}
        patched_conn("handlers.gmail_oauth")

        gmail_oauth.handler(_start_event(), lambda_ctx)

        # The UPDATE call passes (state, ttl_minutes, user_id)
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "gmail_oauth_state = %s" in c.args[0]
            and "INTERVAL" in c.args[0]
        ]
        assert len(update_calls) == 1
        params = update_calls[0].args[1]
        assert isinstance(params[0], str) and len(params[0]) > 20  # state
        assert params[1] == gmail_oauth.STATE_TTL_MINUTES
        assert params[2] == 42  # user_id


# ---------------------------------------------------------------------------
# OAuth callback
# ---------------------------------------------------------------------------


class TestOAuthCallback:
    def _patch_token_exchange(self, mocker, gmail_oauth, **overrides):
        """Patch the urllib.request token-exchange function."""
        token_response = {
            "access_token": "ya29.fake-access",
            "refresh_token": "1//0gfake-refresh",
            "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send",
            "expires_in": 3599,
            "token_type": "Bearer",
        }
        token_response.update(overrides)
        return mocker.patch.object(
            gmail_oauth, "_exchange_code_for_tokens", return_value=token_response
        )

    def _patch_gmail_service(self, mocker, gmail_oauth, gmail_address="user@gmail.com"):
        """Patch build_credentials + build_gmail_service to avoid needing the
        google-auth / google-api-python-client packages installed in the test
        venv. The mocked service exposes users().getProfile() so the handler's
        gmail-address fetch resolves to the canned value."""
        mocker.patch.object(gmail_oauth, "build_credentials", return_value=MagicMock())
        execute = MagicMock(return_value={"emailAddress": gmail_address})
        get_profile = MagicMock(return_value=MagicMock(execute=execute))
        users_resource = MagicMock(getProfile=get_profile)
        service = MagicMock(users=MagicMock(return_value=users_resource))
        mocker.patch.object(gmail_oauth, "build_gmail_service", return_value=service)
        return service

    def _patch_kms(self, mocker, gmail_oauth):
        """Patch KMS encrypt to return canned ciphertext."""
        from common import gmail_client

        fake_kms = MagicMock()
        fake_kms.encrypt.return_value = {"CiphertextBlob": b"\xde\xad\xbe\xefcipher"}
        mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)
        return fake_kms

    def test_happy_path_redirects_to_spa_with_success_flag(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        _mock_ssm_oauth_client(mocker, gmail_oauth)
        # Sequence of fetchone() calls in the callback:
        #   1. SELECT user where state matches → {id: 42, cognito_sub: ...}
        mock_cursor.fetchone.side_effect = [
            {"id": 42, "cognito_sub": "user-sub-1"},
        ]
        patched_conn("handlers.gmail_oauth")

        self._patch_token_exchange(mocker, gmail_oauth)
        self._patch_gmail_service(mocker, gmail_oauth)
        self._patch_kms(mocker, gmail_oauth)

        out = gmail_oauth.handler(_callback_event(), lambda_ctx)

        assert out["statusCode"] == 302
        assert out["headers"]["location"] == (
            "https://app.example.com/settings?gmail_connected=1"
        )

        # The state was cleared (one-time use)
        clear_state_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "gmail_oauth_state = NULL" in c.args[0]
        ]
        assert len(clear_state_calls) == 1

        # Credentials were upserted with KMS ciphertext
        upsert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO gmail_credentials" in c.args[0]
        ]
        assert len(upsert_calls) == 1
        params = upsert_calls[0].args[1]
        assert params[0] == 42  # user_id
        assert params[1] == "user@gmail.com"
        assert params[2] == b"\xde\xad\xbe\xefcipher"
        assert "gmail.readonly" in params[3]
        assert "gmail.send" in params[3]

    def test_invalid_state_redirects_with_error(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        _mock_ssm_oauth_client(mocker, gmail_oauth)
        mock_cursor.fetchone.return_value = None  # no user matches state
        patched_conn("handlers.gmail_oauth")

        out = gmail_oauth.handler(_callback_event(state="bogus"), lambda_ctx)

        assert out["statusCode"] == 302
        assert "gmail_error=invalid_state" in out["headers"]["location"]

    def test_google_error_param_redirects_with_error(
        self, monkeypatch, lambda_ctx
    ):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        event = _callback_event()
        event["queryStringParameters"]["error"] = "access_denied"

        out = gmail_oauth.handler(event, lambda_ctx)

        assert out["statusCode"] == 302
        assert "gmail_error=access_denied" in out["headers"]["location"]

    def test_missing_code_returns_400(self, monkeypatch, lambda_ctx):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        event = _callback_event()
        del event["queryStringParameters"]["code"]

        out = gmail_oauth.handler(event, lambda_ctx)

        assert out["statusCode"] == 400

    def test_no_refresh_token_in_response_redirects_with_error(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        _mock_ssm_oauth_client(mocker, gmail_oauth)
        mock_cursor.fetchone.side_effect = [
            {"id": 42, "cognito_sub": "user-sub-1"},
        ]
        patched_conn("handlers.gmail_oauth")

        # Token response without refresh_token (Google chose not to re-issue)
        self._patch_token_exchange(mocker, gmail_oauth, refresh_token=None)
        self._patch_gmail_service(mocker, gmail_oauth)
        self._patch_kms(mocker, gmail_oauth)

        # Need to NOT include refresh_token; pop it from the canned response
        from handlers import gmail_oauth as _g

        mocker.patch.object(
            _g,
            "_exchange_code_for_tokens",
            return_value={
                "access_token": "ya29.fake",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
                "expires_in": 3599,
            },
        )

        out = gmail_oauth.handler(_callback_event(), lambda_ctx)

        assert out["statusCode"] == 302
        assert "gmail_error=no_refresh_token" in out["headers"]["location"]

    def test_actual_granted_scopes_persisted_not_hardcoded(
        self, mocker, monkeypatch, lambda_ctx, mock_cursor, patched_conn
    ):
        # Forward-compat item 1 from the slice plan: scopes column gets
        # populated from Google's actual token response, not hard-coded.
        _set_env(monkeypatch)
        from handlers import gmail_oauth

        importlib_reload(gmail_oauth)

        _mock_ssm_oauth_client(mocker, gmail_oauth)
        mock_cursor.fetchone.side_effect = [
            {"id": 42, "cognito_sub": "user-sub-1"},
        ]
        patched_conn("handlers.gmail_oauth")

        # Simulate user denying gmail.send on the consent screen — only
        # readonly comes back.
        self._patch_token_exchange(
            mocker,
            gmail_oauth,
            scope="https://www.googleapis.com/auth/gmail.readonly",
        )
        self._patch_gmail_service(mocker, gmail_oauth)
        self._patch_kms(mocker, gmail_oauth)

        gmail_oauth.handler(_callback_event(), lambda_ctx)

        upsert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO gmail_credentials" in c.args[0]
        ]
        assert len(upsert_calls) == 1
        scopes_persisted = upsert_calls[0].args[1][3]
        assert "gmail.readonly" in scopes_persisted
        assert "gmail.send" not in scopes_persisted  # NOT hard-coded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def importlib_reload(module):
    """Re-import a module so module-level env-var reads pick up monkeypatched values."""
    import importlib

    importlib.reload(module)