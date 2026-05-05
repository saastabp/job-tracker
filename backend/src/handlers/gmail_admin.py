"""Gmail admin routes — connection status, disconnect, and per-submission thread linking.

Routes
------
``GET /integrations/gmail/status``
    Returns the current Gmail connection state for the requesting user
    (``connected``, ``gmail_address``, ``scopes`` as list, ``last_polled_at``).
    The SPA reads ``scopes`` to gate UI elements (e.g. Compose / Reply
    buttons require ``gmail.send`` to be present).

``DELETE /integrations/gmail``
    Disconnects the user's gmail. Best-effort revokes the refresh token
    at Google's revoke endpoint, then soft-deletes the
    ``gmail_credentials`` row. Existing ``submissions.gmail_thread_id``
    values are NOT cleared — they're harmless without credentials and
    resume polling on reconnect.

``PUT /submissions/{id}/gmail-link``
    Body: ``{ "mid": "..." }``. Resolves the pasted Message-ID (any
    tolerant form per ``common.gmail_client.parse_message_id``) to its
    Gmail thread via Gmail search and stores ``threadId`` on the
    submission.

``DELETE /submissions/{id}/gmail-link``
    Clears ``submissions.gmail_thread_id``.

``PUT /contacts/{id}/gmail-link``
    Body: ``{ "mid": "..." }``. Resolves the pasted Message-ID to its
    Gmail thread, then synchronously fetches every message in the
    thread and dual-writes ``contact_outreach`` rows tagged to this
    contact (and ``responses`` rows for any submission that already
    has the same thread linked). Reuses the per-message processor
    from ``handlers.gmail_poller``. Skips the S3 raw-bytes archive
    (this Lambda doesn't have the gmail-stack bucket policy; the
    parsed subject/body still land on the rows).

All routes are Cognito-authenticated by the api stack's default JWT
authorizer; no per-route auth overrides here.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

import boto3

from common.auth import user_sub
from common.db import get_connection
from common.gmail_client import (
    build_credentials,
    build_gmail_service,
    decrypt_refresh_token,
    parse_message_id,
    resolve_message_id_to_thread,
)
from common.logger import logger
from common.users import get_user_id

GMAIL_KMS_KEY_ID = os.environ.get("GMAIL_KMS_KEY_ID", "")
GMAIL_OAUTH_CLIENT_SSM_PATH = os.environ.get(
    "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
)

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

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


def _path_id(event: dict[str, Any], key: str = "id") -> int:
    raw = (event.get("pathParameters") or {}).get(key)
    if raw is None:
        raise ValueError(f"missing path parameter: {key}")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {key}: {raw!r}")


def _get_oauth_client_credentials() -> tuple[str, str]:
    """Fetch the GCP OAuth client_id + client_secret from the SSM SecureString."""
    response = _get_ssm().get_parameter(
        Name=GMAIL_OAUTH_CLIENT_SSM_PATH, WithDecryption=True
    )
    payload = json.loads(response["Parameter"]["Value"])
    return payload["client_id"], payload["client_secret"]


def _build_service_for_user(conn: Any, user_id: int) -> Any | None:
    """Decrypt the user's refresh token, build credentials, build Gmail service.

    Returns
    -------
    googleapiclient.discovery.Resource or None
        ``None`` if the user has no active ``gmail_credentials`` row.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT refresh_token_ciphertext, scopes "
            "FROM gmail_credentials "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None

    refresh_token = decrypt_refresh_token(
        bytes(row["refresh_token_ciphertext"]), GMAIL_KMS_KEY_ID
    )
    client_id, client_secret = _get_oauth_client_credentials()
    credentials = build_credentials(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        scopes=row["scopes"].split(),
    )
    return build_gmail_service(credentials)


def _revoke_at_google(refresh_token: str) -> int:
    """POST to Google's revoke endpoint. Returns the HTTP status."""
    body = urllib.parse.urlencode({"token": refresh_token}).encode("utf-8")
    request = urllib.request.Request(
        GOOGLE_REVOKE_URL,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request) as response:
        return response.status


def _status(conn: Any, user_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gmail_address, scopes, last_polled_at "
            "FROM gmail_credentials "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        row = cur.fetchone()

    if not row:
        return {
            "connected": False,
            "gmail_address": None,
            "scopes": [],
            "last_polled_at": None,
        }

    return {
        "connected": True,
        "gmail_address": row["gmail_address"],
        "scopes": row["scopes"].split() if row["scopes"] else [],
        "last_polled_at": (
            row["last_polled_at"].isoformat() if row["last_polled_at"] else None
        ),
    }


def _disconnect(conn: Any, user_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT refresh_token_ciphertext FROM gmail_credentials "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        row = cur.fetchone()

    if not row:
        raise LookupError("gmail not connected")

    # Best-effort revoke at Google. Wrapped in a broad try because
    # disconnect must always succeed locally even if Google is
    # unreachable — the user has signaled they want to disconnect.
    # Pattern-matched on the existing graceful-degradation cases
    # (`common/scheduler.py` swallows scheduler-API errors,
    # `submissions._read_jd_text` swallows S3 errors). Re-raising here
    # would strand the user with a half-disconnected state.
    try:
        refresh_token = decrypt_refresh_token(
            bytes(row["refresh_token_ciphertext"]), GMAIL_KMS_KEY_ID
        )
        _revoke_at_google(refresh_token)
        logger.info("gmail_admin: revoked at Google", extra={"user_id": user_id})
    except Exception:
        logger.exception(
            "gmail_admin: best-effort revoke failed (continuing soft-delete)",
            extra={"user_id": user_id},
        )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gmail_credentials "
            "SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
    conn.commit()
    logger.info("gmail_admin: credentials soft-deleted", extra={"user_id": user_id})
    return {"connected": False, "disconnected": True}


def _link_thread(
    conn: Any, user_id: int, submission_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    raw_input = body.get("mid", "") or ""
    message_id = parse_message_id(raw_input)
    if not message_id:
        raise ValueError("could not extract Message-ID from input")

    # Verify ownership of the submission first, before any Gmail API call.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM submissions "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (submission_id, user_id),
        )
        if not cur.fetchone():
            raise LookupError("submission not found")

    service = _build_service_for_user(conn, user_id)
    if service is None:
        raise ValueError("gmail not connected")

    logger.info(
        "gmail_admin: resolving Message-ID to thread",
        extra={
            "user_id": user_id,
            "submission_id": submission_id,
            "message_id_prefix": message_id[:16],
        },
    )
    thread_id = resolve_message_id_to_thread(service, message_id)
    if thread_id is None:
        # The lookup runs against the user's own mailbox, so a miss
        # also means "this Message-ID isn't yours" — same UX either way.
        raise LookupError("Message-ID not found in your gmail")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE submissions SET gmail_thread_id = %s "
            "WHERE id = %s AND user_id = %s",
            (thread_id, submission_id, user_id),
        )
    conn.commit()
    logger.info(
        "gmail_admin: thread linked",
        extra={
            "user_id": user_id,
            "submission_id": submission_id,
            "thread_id": thread_id,
        },
    )
    return {"submission_id": submission_id, "thread_id": thread_id}


def _link_contact_thread(
    conn: Any, user_id: int, contact_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    """Link a Gmail thread to a contact and synchronously import its messages.

    Returns ``{contact_id, thread_id, imported}`` where ``imported`` is the
    count of brand-new ``contact_outreach`` rows (existing rows from a
    prior import / poll cycle are skipped via the unique key).
    """
    raw_input = body.get("mid", "") or ""
    message_id = parse_message_id(raw_input)
    if not message_id:
        raise ValueError("could not extract Message-ID from input")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM contacts "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if not cur.fetchone():
            raise LookupError("contact not found")

    service = _build_service_for_user(conn, user_id)
    if service is None:
        raise ValueError("gmail not connected")

    logger.info(
        "gmail_admin: resolving Message-ID to thread (contact link)",
        extra={
            "user_id": user_id,
            "contact_id": contact_id,
            "message_id_prefix": message_id[:16],
        },
    )
    thread_id = resolve_message_id_to_thread(service, message_id)
    if thread_id is None:
        raise LookupError("Message-ID not found in your gmail")

    # Pull catalog ids + the user's gmail address (for self-sent filter).
    # Defer the import until here so test fixtures don't need to mock the
    # poller module at module-import time.
    from handlers import gmail_poller

    classifications, statuses, email_method_id, inbound_direction_id = (
        gmail_poller._load_catalog_ids(conn)
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gmail_address FROM gmail_credentials "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        cred = cur.fetchone()
    gmail_address = cred["gmail_address"] if cred else ""

    # Discover any submissions that ALSO have this thread linked — their
    # responses rows get filled in by the same loop.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM submissions "
            "WHERE user_id = %s AND gmail_thread_id = %s "
            "  AND deleted_at IS NULL",
            (user_id, thread_id),
        )
        submission_ids = [int(r["id"]) for r in cur.fetchall()]

    counters = gmail_poller.process_thread_messages(
        conn=conn,
        service=service,
        user_id=user_id,
        thread_id=thread_id,
        gmail_address=gmail_address,
        submission_ids=submission_ids,
        contact_id=contact_id,
        classifications=classifications,
        statuses=statuses,
        email_method_id=email_method_id,
        inbound_direction_id=inbound_direction_id,
        archive_bucket="",  # skip S3 archive; this Lambda lacks the policy
    )

    logger.info(
        "gmail_admin: contact thread linked",
        extra={
            "user_id": user_id,
            "contact_id": contact_id,
            "thread_id": thread_id,
            **counters,
        },
    )
    return {
        "contact_id": contact_id,
        "thread_id": thread_id,
        "imported": counters.get("contact_outreach_inserted", 0),
        "skipped:exists": counters.get("contact_outreach_skipped:exists", 0),
        "skipped:self_sent": counters.get("skipped:self_sent", 0),
    }


def _unlink_thread(conn: Any, user_id: int, submission_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE submissions SET gmail_thread_id = NULL "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (submission_id, user_id),
        )
        if cur.rowcount == 0:
            raise LookupError("submission not found")
    conn.commit()
    logger.info(
        "gmail_admin: thread unlinked",
        extra={"user_id": user_id, "submission_id": submission_id},
    )
    return {"submission_id": submission_id, "unlinked": True}


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info(
        "gmail_admin: enter", extra={"user_sub": sub, "route_key": route_key}
    )

    try:
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)
            if route_key == "GET /integrations/gmail/status":
                result: Any = _status(conn, user_id)
            elif route_key == "DELETE /integrations/gmail":
                result = _disconnect(conn, user_id)
            elif route_key == "PUT /submissions/{id}/gmail-link":
                body = json.loads(event.get("body") or "{}")
                result = _link_thread(conn, user_id, _path_id(event), body)
            elif route_key == "DELETE /submissions/{id}/gmail-link":
                result = _unlink_thread(conn, user_id, _path_id(event))
            elif route_key == "PUT /contacts/{id}/gmail-link":
                body = json.loads(event.get("body") or "{}")
                result = _link_contact_thread(
                    conn, user_id, _path_id(event), body
                )
            else:
                logger.warning(
                    "gmail_admin: unknown route", extra={"route_key": route_key}
                )
                return _response(404, {"error": "not found"})
        logger.info(
            "gmail_admin: exit ok",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(200, result)
    except LookupError as e:
        logger.warning(
            "gmail_admin: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except ValueError as e:
        logger.exception(
            "gmail_admin: bad request",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(400, {"error": str(e)})
    except Exception:
        logger.exception(
            "gmail_admin: failed",
            extra={"user_sub": sub, "route_key": route_key},
        )
        raise