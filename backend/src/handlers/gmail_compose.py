"""POST /messages/send — unified compose / reply via Gmail.

One route, one Lambda. Three anchor combinations land here:

* **Submission-only** (``submission_id`` set, ``contact_id`` null): the
  slice-9 path. Email links a submission to a Gmail thread.

* **Contact-only** (``contact_id`` set, ``submission_id`` null): outreach
  to a contact who isn't (yet) tied to a specific role. The outbound
  message is logged as a ``contact_outreach`` row (direction=outbound,
  method=email) carrying subject/body/gmail_thread_id/gmail_message_id
  so the contact-detail timeline can render the conversation.

* **Both** (``submission_id`` and ``contact_id`` set): "applying for
  this role via this recruiter." Two writes happen — the submission's
  thread_id is set, AND a contact_outreach row is inserted — within a
  single DB transaction.

At least one anchor is required; rejecting an unanchored send is the
locked decision (Fork 4) per the slice-10 plan: if you compose from
inside the app, you want the interaction tracked. The handler returns
``{thread_id, gmail_message_id}`` regardless of which anchors fired.

Two modes (orthogonal to the anchor question):

* **Compose mode** (``in_reply_to_message_id`` absent in body)
    Submission (when present) must NOT yet have a ``gmail_thread_id``.
    The handler sends a fresh message; Gmail creates a new thread and
    returns its ``threadId``, which is written onto the submission and
    carried into the contact_outreach insert.

* **Reply mode** (``in_reply_to_message_id`` present)
    A thread anchor must resolve from whichever side is set:
    ``submissions.gmail_thread_id`` (slice 9) or — for contact-only
    replies — the contact's most-recent ``contact_outreach.gmail_thread_id``
    (slice 10.5). If the user posts a reply with neither submission nor
    contact carrying a thread, the handler 400s. The handler fetches the
    original message's RFC 822 ``Message-ID`` and ``References`` headers,
    builds proper threading headers (``In-Reply-To``, ``References``),
    and passes ``threadId`` in the send request as a belt-and-suspenders
    check alongside the headers.

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


def _coerce_optional_int(value: Any, name: str) -> int | None:
    """Accept None, an int, or a numeric string; return int or None.

    Reject anything else with ``ValueError``.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            raise ValueError(f"{name} must be an integer")
    raise ValueError(f"{name} must be an integer")


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

    submission_id = _coerce_optional_int(body.get("submission_id"), "submission_id")
    contact_id = _coerce_optional_int(body.get("contact_id"), "contact_id")
    if submission_id is None and contact_id is None:
        raise ValueError("submission_id or contact_id is required")

    return {
        "to": to,
        "cc": _normalize_recipients(body.get("cc") or []),
        "bcc": _normalize_recipients(body.get("bcc") or []),
        "subject": subject,
        "body": body_text,
        "resume_id": body.get("resume_id"),
        "in_reply_to_message_id": body.get("in_reply_to_message_id"),
        "submission_id": submission_id,
        "contact_id": contact_id,
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


def _catalog_id(conn: Any, table: str, short_name: str) -> int:
    """Look up a catalog row's id by short_name. Raises if missing."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"{table}.short_name='{short_name}' not found")
    return int(row["id"])


def _send(
    *,
    conn: Any,
    user_id: int,
    gmail_address: str,
    refresh_token_ciphertext: bytes,
    scopes: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Orchestrate validation → send → persist. Returns ``{thread_id, gmail_message_id}``.

    Raises
    ------
    ValueError
        Bad input (missing fields, mode mismatch, no PDF on the resume).
    LookupError
        Submission, contact, or resume not found / not owned.
    _ScopeError
        ``gmail.send`` missing from credential scopes.
    """
    validated = _validate_body(body)
    submission_id = validated["submission_id"]
    contact_id = validated["contact_id"]

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

    # Anchor lookups: ownership-check whichever of submission_id /
    # contact_id were provided. Both are optional; at least one was
    # validated above.
    current_thread_id: str | None = None
    if submission_id is not None:
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

    if contact_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM contacts "
                "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
                (contact_id, user_id),
            )
            con_row = cur.fetchone()
        if not con_row:
            raise LookupError("contact not found")

    in_reply_to_msg_id = validated["in_reply_to_message_id"]
    in_reply_to_rfc = ""
    references_chain = ""

    if in_reply_to_msg_id:
        # Reply mode resolves a thread anchor from whichever side has one:
        # submissions.gmail_thread_id (slice 9) or, when the user is
        # replying from ContactDetail without a submission link, the
        # most-recent contact_outreach.gmail_thread_id for this contact
        # (slice 10.5).
        if not current_thread_id and contact_id is not None:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT gmail_thread_id FROM contact_outreach "
                    "WHERE user_id = %s AND contact_id = %s "
                    "  AND deleted_at IS NULL "
                    "  AND gmail_thread_id IS NOT NULL "
                    "ORDER BY outreach_at DESC, id DESC LIMIT 1",
                    (user_id, contact_id),
                )
                co_row = cur.fetchone()
            if co_row:
                current_thread_id = co_row["gmail_thread_id"]
        if not current_thread_id:
            raise ValueError(
                "cannot reply: no linked thread on submission or contact"
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
        # Compose mode: submission (if anchored) must not already be linked.
        if submission_id is not None and current_thread_id:
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
            "contact_id": contact_id,
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

    if current_thread_id and current_thread_id != returned_thread_id:
        # Reply mode mismatch — Gmail's threadId guard should have rejected
        # the send before we got here. If not, something is very wrong.
        raise RuntimeError(
            f"thread_id mismatch: stored={current_thread_id} "
            f"returned={returned_thread_id}"
        )

    # Persistence — both writes (when both anchors are present) happen in
    # the same transaction so the row + thread_id update either both land
    # or neither does.
    with conn.cursor() as cur:
        if submission_id is not None and not current_thread_id:
            cur.execute(
                "UPDATE submissions SET gmail_thread_id = %s "
                "WHERE id = %s AND user_id = %s",
                (returned_thread_id, submission_id, user_id),
            )
            logger.info(
                "gmail_compose: thread linked from send",
                extra={
                    "submission_id": submission_id,
                    "thread_id": returned_thread_id,
                },
            )

        if contact_id is not None:
            outbound_id = _catalog_id(conn, "outreach_directions", "outbound")
            email_method_id = _catalog_id(conn, "outreach_methods", "email")
            cur.execute(
                """
                INSERT INTO contact_outreach
                    (user_id, contact_id, outreach_at,
                     outreach_method_id, outreach_direction_id,
                     gmail_thread_id, gmail_message_id,
                     subject, body_text, from_email)
                VALUES (%s, %s, CURRENT_TIMESTAMP,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s)
                """,
                (
                    user_id, contact_id,
                    email_method_id, outbound_id,
                    returned_thread_id, sent_id,
                    validated["subject"][:998],
                    validated["body"],
                    gmail_address,
                ),
            )
            logger.info(
                "gmail_compose: contact_outreach row inserted",
                extra={
                    "contact_id": contact_id,
                    "thread_id": returned_thread_id,
                    "gmail_message_id": sent_id,
                },
            )
    conn.commit()

    logger.info(
        "gmail_compose: send ok",
        extra={
            "user_id": user_id,
            "submission_id": submission_id,
            "contact_id": contact_id,
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

    if route_key != "POST /messages/send":
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
                body=body,
            )

        logger.info(
            "gmail_compose: exit ok", extra={"user_sub": sub}
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