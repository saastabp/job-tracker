"""POST /submissions/{id}/send — compose / send / reply via Gmail.

One route, one Lambda. Two modes:

* **Compose mode** (``in_reply_to_message_id`` absent in body)
    Submission must NOT yet have a ``gmail_thread_id``. The handler
    sends a fresh message; Gmail creates a new thread and returns its
    ``threadId``, which is written onto the submission. The user's
    send becomes the seed for the inbound poller.

* **Reply mode** (``in_reply_to_message_id`` present)
    Submission MUST have a ``gmail_thread_id`` (i.e. either previously
    sent via the app or manually linked via the admin handler). The
    handler fetches the original message's RFC 822 ``Message-ID`` and
    ``References`` headers, builds proper threading headers
    (``In-Reply-To``, ``References``), and passes ``threadId`` in the
    send request as a belt-and-suspenders check alongside the
    headers. Gmail will reject the send if the ``threadId`` doesn't
    match what ``In-Reply-To`` resolves to, catching cross-thread
    bugs.

Plaintext body only — slice 09 locked decision (no rich text, no
multipart/alternative). Optional resume attachment: ``resume_id``
resolves to a stored PDF in the resume bucket and is added as
``application/pdf`` with the resume's ``original_filename``.

Scope gate: refuses if ``gmail.send`` is missing from the user's
``gmail_credentials.scopes``. SPA reads scopes from
``GET /integrations/gmail/status`` and gates the Compose / Reply
buttons accordingly, so this is defense-in-depth.
"""
from __future__ import annotations

import base64
import json
import os
from email.message import EmailMessage
from typing import Any

import boto3

from common.auth import user_sub
from common.db import get_connection
from common.gmail_client import (
    build_credentials,
    build_gmail_service,
    decrypt_refresh_token,
)
from common.logger import logger
from common.users import get_user_id

GMAIL_KMS_KEY_ID = os.environ.get("GMAIL_KMS_KEY_ID", "")
GMAIL_OAUTH_CLIENT_SSM_PATH = os.environ.get(
    "GMAIL_OAUTH_CLIENT_SSM_PATH", "/jobtracker/gmail/oauth-client"
)
RESUME_BUCKET = os.environ.get("RESUME_BUCKET", "")

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

_ssm: Any = None
_s3: Any = None


def _get_ssm() -> Any:
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def _get_s3() -> Any:
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


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
    response = _get_ssm().get_parameter(
        Name=GMAIL_OAUTH_CLIENT_SSM_PATH, WithDecryption=True
    )
    payload = json.loads(response["Parameter"]["Value"])
    return payload["client_id"], payload["client_secret"]


class _ScopeError(PermissionError):
    """Raised when the user's gmail_credentials.scopes lacks gmail.send."""


def _normalize_recipients(value: Any) -> list[str]:
    """Accept a list of strings or a comma-separated string. Return stripped non-empty addrs."""
    if isinstance(value, list):
        return [str(s).strip() for s in value if s and str(s).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def _validate_body(body: dict[str, Any]) -> dict[str, Any]:
    to = _normalize_recipients(body.get("to"))
    if not to:
        raise ValueError("to is required")

    subject = (body.get("subject") or "").strip()
    if not subject:
        raise ValueError("subject is required")

    body_text = body.get("body") or ""
    if not body_text.strip():
        raise ValueError("body is required")

    return {
        "to": to,
        "cc": _normalize_recipients(body.get("cc") or []),
        "bcc": _normalize_recipients(body.get("bcc") or []),
        "subject": subject,
        "body": body_text,
        "resume_id": body.get("resume_id"),
        "in_reply_to_message_id": body.get("in_reply_to_message_id"),
    }


def _fetch_resume_pdf(
    conn: Any, user_id: int, resume_id: int
) -> tuple[bytes, str]:
    """Look up resume + ownership check, fetch PDF from S3.

    Returns
    -------
    (pdf_bytes, filename)

    Raises
    ------
    LookupError
        If no matching resume row exists (or it's soft-deleted).
    ValueError
        If the resume row has no ``file_s3_key`` (the user uploaded a
        record but never the PDF).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT file_s3_key, original_filename FROM resumes "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (resume_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError("resume not found")

    s3_key = row["file_s3_key"]
    if not s3_key:
        raise ValueError("resume has no PDF attached")

    obj = _get_s3().get_object(Bucket=RESUME_BUCKET, Key=s3_key)
    pdf_bytes = obj["Body"].read()
    filename = row["original_filename"] or "resume.pdf"
    return pdf_bytes, filename


def _fetch_in_reply_to_headers(
    service: Any, gmail_message_id: str
) -> tuple[str, str]:
    """Get RFC 822 ``Message-ID`` + ``References`` from the original message.

    Returns
    -------
    (rfc822_message_id, references_header)
        Either may be empty if the original message lacked the header.
    """
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=gmail_message_id,
            format="metadata",
            metadataHeaders=["Message-Id", "References"],
        )
        .execute()
    )
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
        if h.get("name") and h.get("value") is not None
    }
    return headers.get("message-id", ""), headers.get("references", "")


def _build_mime(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    in_reply_to_rfc: str,
    references_chain: str,
    attachment_pdf: bytes | None,
    attachment_filename: str | None,
) -> EmailMessage:
    """Build an RFC 822 message via stdlib's ``EmailMessage``.

    ``From`` is omitted intentionally — Gmail fills it from the OAuth
    identity at send time, which is also what the recipient sees.
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject

    if in_reply_to_rfc:
        msg["In-Reply-To"] = in_reply_to_rfc
    if references_chain:
        msg["References"] = references_chain

    msg.set_content(body, subtype="plain")

    if attachment_pdf:
        msg.add_attachment(
            attachment_pdf,
            maintype="application",
            subtype="pdf",
            filename=attachment_filename or "resume.pdf",
        )

    return msg


def _send(
    *,
    conn: Any,
    user_id: int,
    gmail_address: str,
    refresh_token_ciphertext: bytes,
    scopes: str,
    submission_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Orchestrate validation → send → persist. Returns ``{thread_id, gmail_message_id}``.

    Raises
    ------
    ValueError
        Bad input (missing fields, mode mismatch, no PDF on the resume).
    LookupError
        Submission or resume not found / not owned.
    _ScopeError
        ``gmail.send`` missing from credential scopes.
    """
    validated = _validate_body(body)

    scope_list = scopes.split() if scopes else []
    if GMAIL_SEND_SCOPE not in scope_list:
        raise _ScopeError("send scope required")

    refresh_token = decrypt_refresh_token(
        bytes(refresh_token_ciphertext), GMAIL_KMS_KEY_ID
    )
    client_id, client_secret = _get_oauth_client_credentials()
    credentials = build_credentials(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scope_list,
    )
    service = build_gmail_service(credentials)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, gmail_thread_id FROM submissions "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (submission_id, user_id),
        )
        sub_row = cur.fetchone()
    if not sub_row:
        raise LookupError("submission not found")
    current_thread_id = sub_row["gmail_thread_id"]

    in_reply_to_msg_id = validated["in_reply_to_message_id"]
    in_reply_to_rfc = ""
    references_chain = ""

    if in_reply_to_msg_id:
        # Reply mode
        if not current_thread_id:
            raise ValueError(
                "cannot reply: submission has no linked thread"
            )
        original_rfc, original_refs = _fetch_in_reply_to_headers(
            service, in_reply_to_msg_id
        )
        if not original_rfc:
            raise ValueError(
                "could not fetch original message's Message-ID header"
            )
        in_reply_to_rfc = original_rfc
        # References chain: original's References (if any) + original's Message-ID
        if original_refs.strip():
            references_chain = f"{original_refs.strip()} {original_rfc}"
        else:
            references_chain = original_rfc
    else:
        # Compose mode
        if current_thread_id:
            raise ValueError(
                "submission already linked to a thread; use reply mode"
            )

    pdf_bytes: bytes | None = None
    pdf_filename: str | None = None
    if validated["resume_id"] is not None:
        pdf_bytes, pdf_filename = _fetch_resume_pdf(
            conn, user_id, int(validated["resume_id"])
        )

    mime_msg = _build_mime(
        to=validated["to"],
        cc=validated["cc"],
        bcc=validated["bcc"],
        subject=validated["subject"],
        body=validated["body"],
        in_reply_to_rfc=in_reply_to_rfc,
        references_chain=references_chain,
        attachment_pdf=pdf_bytes,
        attachment_filename=pdf_filename,
    )
    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ascii")

    send_body: dict[str, Any] = {"raw": raw}
    if current_thread_id:
        send_body["threadId"] = current_thread_id

    logger.info(
        "gmail_compose: sending",
        extra={
            "user_id": user_id,
            "submission_id": submission_id,
            "mode": "reply" if in_reply_to_msg_id else "compose",
            "has_attachment": pdf_bytes is not None,
            "to_count": len(validated["to"]),
            "cc_count": len(validated["cc"]),
            "bcc_count": len(validated["bcc"]),
        },
    )
    result = (
        service.users()
        .messages()
        .send(userId="me", body=send_body)
        .execute()
    )
    sent_id = result["id"]
    returned_thread_id = result["threadId"]

    if not current_thread_id:
        # Compose mode: persist the thread_id Gmail just generated.
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE submissions SET gmail_thread_id = %s "
                "WHERE id = %s AND user_id = %s",
                (returned_thread_id, submission_id, user_id),
            )
            conn.commit()
        logger.info(
            "gmail_compose: thread linked from send",
            extra={
                "submission_id": submission_id,
                "thread_id": returned_thread_id,
            },
        )
    elif current_thread_id != returned_thread_id:
        # Reply mode mismatch — Gmail's threadId guard should have
        # rejected the send before we got here. If not, something is
        # very wrong.
        raise RuntimeError(
            f"thread_id mismatch: stored={current_thread_id} "
            f"returned={returned_thread_id}"
        )

    logger.info(
        "gmail_compose: send ok",
        extra={
            "user_id": user_id,
            "submission_id": submission_id,
            "thread_id": returned_thread_id,
            "gmail_message_id": sent_id,
        },
    )
    return {
        "thread_id": returned_thread_id,
        "gmail_message_id": sent_id,
    }


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info(
        "gmail_compose: enter", extra={"user_sub": sub, "route_key": route_key}
    )

    if route_key != "POST /submissions/{id}/send":
        return _response(404, {"error": "not found"})

    try:
        body = json.loads(event.get("body") or "{}")
        with get_connection() as conn:
            user_id = get_user_id(conn, sub)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT gmail_address, refresh_token_ciphertext, scopes "
                    "FROM gmail_credentials "
                    "WHERE user_id = %s AND deleted_at IS NULL",
                    (user_id,),
                )
                cred = cur.fetchone()
            if not cred:
                return _response(400, {"error": "gmail not connected"})

            result = _send(
                conn=conn,
                user_id=user_id,
                gmail_address=cred["gmail_address"],
                refresh_token_ciphertext=cred["refresh_token_ciphertext"],
                scopes=cred["scopes"],
                submission_id=_path_id(event),
                body=body,
            )

        logger.info(
            "gmail_compose: exit ok",
            extra={"user_sub": sub, "submission_id": _path_id(event)},
        )
        return _response(200, result)
    except _ScopeError:
        logger.warning(
            "gmail_compose: send scope missing",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(
            403, {"error": "send scope required", "needs_reconsent": True}
        )
    except LookupError as e:
        logger.warning(
            "gmail_compose: not found",
            extra={"user_sub": sub, "route_key": route_key, "reason": str(e)},
        )
        return _response(404, {"error": str(e)})
    except ValueError as e:
        logger.exception(
            "gmail_compose: bad request",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(400, {"error": str(e)})
    except json.JSONDecodeError as e:
        logger.exception(
            "gmail_compose: invalid JSON body",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(400, {"error": f"invalid JSON: {e}"})
    except Exception:
        logger.exception(
            "gmail_compose: failed",
            extra={"user_sub": sub, "route_key": route_key},
        )
        raise