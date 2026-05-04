"""GET /integrations/gmail/oauth/start  +  GET /integrations/gmail/oauth/callback.

Two routes, one Lambda. The dispatch happens on ``routeKey``; both routes
share env vars, IAM, and the SSM lookup for the Google OAuth client.

Routes
------
``GET /integrations/gmail/oauth/start`` — **Cognito-authenticated.**
    Generates a 32-byte CSRF state token, persists it on the requesting
    user's row with a 10-minute expiry, and returns Google's consent URL
    with the state embedded as the ``state`` query param. The SPA
    redirects the browser to that URL.

``GET /integrations/gmail/oauth/callback`` — **UNAUTHENTICATED.**
    Google's 302 redirect carries no Authorization header, so this route
    is configured with ``Auth: { Authorizer: NONE }`` in the api stack
    template. The user is identified by looking up the ``state`` token
    in ``users.gmail_oauth_state``; that's the CSRF defense plus the
    user-attribution mechanism. The handler exchanges the authorization
    code for tokens, fetches the gmail address via the Gmail profile
    API, encrypts the refresh token via KMS, and upserts the
    ``gmail_credentials`` row. Always returns a 302 to the SPA — either
    ``?gmail_connected=1`` on success or ``?gmail_error=<reason>`` on
    failure (callback-specific exception handling: re-raising would
    yield a 5xx HTML page mid-browser-redirect, which is a worse UX
    than redirecting to the SPA with an error flag).

The OAuth redirect URI is constructed at request time from
``event.requestContext.domainName`` so it matches whatever the API
Gateway is actually serving — no need to track the URI as an env var.
The same domain is registered in GCP per ``infra/gmail/GCP-SETUP.md``.
"""
from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import urllib.request
from typing import Any

import boto3

from common.auth import user_sub
from common.db import get_connection
from common.gmail_client import (
    build_credentials,
    build_gmail_service,
    encrypt_refresh_token,
)
from common.logger import logger
from common.users import get_user_id

GMAIL_KMS_KEY_ID = os.environ.get("GMAIL_KMS_KEY_ID", "")
GMAIL_OAUTH_CLIENT_SSM_PATH = os.environ.get(
    "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
)
SPA_REDIRECT_URL = os.environ.get("SPA_REDIRECT_URL", "http://localhost:5173")

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
STATE_TTL_MINUTES = 10

_ssm: Any = None


def _get_ssm() -> Any:
    """Memoized boto3 SSM client. Reused across Lambda invocations."""
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _redirect(location: str) -> dict[str, Any]:
    return {
        "statusCode": 302,
        "headers": {"location": location},
        "body": "",
    }


def _build_redirect_uri(event: dict[str, Any]) -> str:
    """Construct the OAuth callback URL from the inbound API Gateway event.

    Matches the value registered in GCP without us tracking it as an env
    var: ``domainName`` is the actual hostname Google will redirect to, and
    the path is fixed.
    """
    domain = event.get("requestContext", {}).get("domainName", "")
    return f"https://{domain}/integrations/gmail/oauth/callback"


def _get_oauth_client_credentials() -> tuple[str, str]:
    """Fetch the GCP OAuth client_id + client_secret from the SSM SecureString."""
    response = _get_ssm().get_parameter(
        Name=GMAIL_OAUTH_CLIENT_SSM_PATH,
        WithDecryption=True,
    )
    payload = json.loads(response["Parameter"]["Value"])
    return payload["client_id"], payload["client_secret"]


def _exchange_code_for_tokens(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict[str, Any]:
    """POST to Google's token endpoint and return the JSON response."""
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _handle_start(event: dict[str, Any]) -> dict[str, Any]:
    sub = user_sub(event)
    if not sub:
        return _response(401, {"error": "unauthenticated"})

    state = secrets.token_urlsafe(32)
    redirect_uri = _build_redirect_uri(event)
    client_id, _client_secret = _get_oauth_client_credentials()

    logger.info(
        "gmail_oauth: start enter",
        extra={"user_sub": sub, "redirect_uri": redirect_uri},
    )

    with get_connection() as conn:
        user_id = get_user_id(conn, sub)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users "
                "SET gmail_oauth_state = %s, "
                "    gmail_oauth_state_expires_at = "
                "        DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s MINUTE) "
                "WHERE id = %s",
                (state, STATE_TTL_MINUTES, user_id),
            )
            conn.commit()
        logger.info(
            "gmail_oauth: state stored",
            extra={"user_id": user_id, "ttl_minutes": STATE_TTL_MINUTES},
        )

    consent_url = (
        GOOGLE_AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(OAUTH_SCOPES),
                "state": state,
                "access_type": "offline",
                # `prompt=consent` forces Google to re-issue a refresh_token
                # even if the user has previously consented to this client.
                # Without it, Google may only return an access_token on
                # subsequent grants, leaving us unable to refresh.
                "prompt": "consent",
            }
        )
    )

    logger.info("gmail_oauth: start exit ok", extra={"user_sub": sub})
    return _response(200, {"consent_url": consent_url})


def _handle_callback(event: dict[str, Any]) -> dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code")
    state = qs.get("state")
    google_error = qs.get("error")

    logger.info(
        "gmail_oauth: callback enter",
        extra={
            "has_code": bool(code),
            "has_state": bool(state),
            "google_error": google_error,
        },
    )

    if google_error:
        # User denied consent (or Google returned an error). Send the
        # browser back to the SPA with an error flag.
        return _redirect(
            f"{SPA_REDIRECT_URL}/settings?gmail_error={urllib.parse.quote(google_error)}"
        )

    if not code or not state:
        return _response(400, {"error": "missing code or state"})

    # Callback-specific exception handling: the request is a browser
    # mid-OAuth-redirect, not an API client. Re-raising would yield a
    # 5xx HTML page that strands the user. Redirect to the SPA with an
    # error flag so the SPA's onboarding flow can surface a friendly
    # error and offer retry. Pattern-matched on existing
    # `submissions._read_jd_text` (S3 swallow) and `common/scheduler.py`
    # (scheduler-API swallow).
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, cognito_sub FROM users "
                    "WHERE gmail_oauth_state = %s "
                    "  AND gmail_oauth_state_expires_at > CURRENT_TIMESTAMP "
                    "  AND deleted_at IS NULL",
                    (state,),
                )
                row = cur.fetchone()
            if not row:
                logger.warning(
                    "gmail_oauth: invalid or expired state",
                    extra={"state_prefix": state[:8]},
                )
                return _redirect(
                    f"{SPA_REDIRECT_URL}/settings?gmail_error=invalid_state"
                )

            user_id = int(row["id"])
            user_sub_value = row["cognito_sub"]

            # One-time-use: clear immediately so a replay of the same
            # callback URL doesn't grant a second token.
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users "
                    "SET gmail_oauth_state = NULL, "
                    "    gmail_oauth_state_expires_at = NULL "
                    "WHERE id = %s",
                    (user_id,),
                )
                conn.commit()
            logger.info(
                "gmail_oauth: state validated, exchanging code",
                extra={"user_id": user_id, "user_sub": user_sub_value},
            )

            redirect_uri = _build_redirect_uri(event)
            client_id, client_secret = _get_oauth_client_credentials()
            token_response = _exchange_code_for_tokens(
                code=code,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )

            refresh_token = token_response.get("refresh_token")
            if not refresh_token:
                # No refresh_token in the response. Usually means
                # access_type=offline was missing from the start, OR the
                # user has previously consented and Google chose not to
                # re-issue. We pass `prompt=consent` precisely to force
                # re-issuance, so this branch is mostly defensive.
                logger.error(
                    "gmail_oauth: no refresh_token in token response",
                    extra={
                        "user_id": user_id,
                        "response_keys": sorted(token_response.keys()),
                    },
                )
                return _redirect(
                    f"{SPA_REDIRECT_URL}/settings?gmail_error=no_refresh_token"
                )

            granted_scopes = token_response.get("scope", "")
            credentials = build_credentials(
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                scopes=granted_scopes.split(),
            )
            service = build_gmail_service(credentials)
            profile = service.users().getProfile(userId="me").execute()
            gmail_address = profile["emailAddress"]

            logger.info(
                "gmail_oauth: tokens received",
                extra={
                    "user_id": user_id,
                    "gmail_address": gmail_address,
                    "scopes": granted_scopes,
                },
            )

            ciphertext = encrypt_refresh_token(refresh_token, GMAIL_KMS_KEY_ID)

            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO gmail_credentials "
                    "(user_id, gmail_address, refresh_token_ciphertext, scopes, deleted_at) "
                    "VALUES (%s, %s, %s, %s, NULL) "
                    "ON DUPLICATE KEY UPDATE "
                    "  gmail_address = VALUES(gmail_address), "
                    "  refresh_token_ciphertext = VALUES(refresh_token_ciphertext), "
                    "  scopes = VALUES(scopes), "
                    "  deleted_at = NULL, "
                    "  updated_at = CURRENT_TIMESTAMP",
                    (user_id, gmail_address, ciphertext, granted_scopes),
                )
                conn.commit()

        logger.info(
            "gmail_oauth: credentials persisted",
            extra={"user_id": user_id, "gmail_address": gmail_address},
        )
        return _redirect(f"{SPA_REDIRECT_URL}/settings?gmail_connected=1")

    except Exception:
        logger.exception(
            "gmail_oauth: callback failed",
            extra={"state_prefix": state[:8]},
        )
        return _redirect(f"{SPA_REDIRECT_URL}/settings?gmail_error=callback_failed")


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    route_key = event.get("routeKey", "")
    logger.info("gmail_oauth: enter", extra={"route_key": route_key})

    try:
        if route_key == "GET /integrations/gmail/oauth/start":
            return _handle_start(event)
        if route_key == "GET /integrations/gmail/oauth/callback":
            return _handle_callback(event)
        logger.warning("gmail_oauth: unknown route", extra={"route_key": route_key})
        return _response(404, {"error": "not found"})
    except Exception:
        logger.exception(
            "gmail_oauth: unhandled error", extra={"route_key": route_key}
        )
        raise