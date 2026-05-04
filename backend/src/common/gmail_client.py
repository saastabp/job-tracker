"""Shared utilities for the Gmail integration (slice 09).

Used by ``handlers/gmail_oauth.py`` (encrypt refresh token at callback),
``handlers/gmail_admin.py`` (resolve pasted Message-IDs to thread IDs),
``handlers/gmail_poller.py`` (decrypt refresh token, fetch new history),
and ``handlers/gmail_compose.py`` (decrypt refresh token, send messages).

This module groups three concerns:

* **Message-ID parsing.** Pure-Python regex normalization of the various
  forms a user might paste — ``Message-ID:`` line copy from Gmail web,
  Thunderbird's ``mid:`` URI, bare or angle-bracketed RFC 822 IDs, or a
  whole header blob with line breaks collapsed. See ``parse_message_id``
  for the algorithm.
* **KMS encrypt / decrypt.** Refresh tokens are stored as ciphertext in
  ``gmail_credentials.refresh_token_ciphertext``. The KMS key id lives
  in the Lambda's environment (set by the gmail stack template).
* **Google API client construction.** The credential builder + service
  factory wrap ``google-auth`` and ``google-api-python-client``. Both
  libs are imported lazily inside the function bodies so unit tests for
  the parser and KMS helpers don't need them at import time.

No Lambda handler wiring lives here — every function returns plain values
or raises. Handlers compose these helpers into request/response flows.
"""
from __future__ import annotations

import re
from typing import Any

import boto3

# ---------------------------------------------------------------------------
# Message-ID parsing
# ---------------------------------------------------------------------------
#
# Two-step extraction per slice 09 plan:
#   Step 1 — header-anchored regex. Looks for `Message-ID:` (case-insensitive,
#       per RFC 822) anywhere in the input and captures the value. Anchoring
#       on `message-id` specifically is what keeps `References:` and
#       `In-Reply-To:` from matching when the user pastes a multi-header blob.
#   Step 2 — fallback for prefix-free pastes. Strips a leading `mid:`,
#       surrounding angle brackets, and whitespace. The result must look like
#       `local@domain` with no internal whitespace and no remaining brackets.
#
# `+` characters in Message-IDs are preserved verbatim — they're a Gmail
# auto-generated form and the rfc822msgid: search query needs them intact.
# Callers should pass JSON body values, NOT URL-decoded query strings.

_MESSAGE_ID_HEADER_REGEX = re.compile(
    r"(?i)message-id\s*:\s*<?([^\s<>]+@[^\s<>]+)>?"
)
_MID_PREFIX_REGEX = re.compile(r"^mid:", re.IGNORECASE)


def parse_message_id(input_text: str) -> str | None:
    """Extract an RFC 822 Message-ID from a tolerant set of input forms.

    Parameters
    ----------
    input_text : str
        User-pasted text. May be a bare Message-ID, an angle-bracketed
        Message-ID, a ``mid:`` URI, a single ``Message-ID: <...>`` line,
        or a whole pasted header blob with line breaks collapsed.

    Returns
    -------
    str or None
        The normalized Message-ID (no angle brackets, no ``mid:`` prefix,
        no surrounding whitespace) on success, or ``None`` if the input
        contains nothing that looks like a Message-ID.
    """
    if not input_text:
        return None

    match = _MESSAGE_ID_HEADER_REGEX.search(input_text)
    if match:
        return match.group(1).strip()

    candidate = input_text.strip()
    candidate = _MID_PREFIX_REGEX.sub("", candidate)
    candidate = candidate.strip()
    if candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1]
    candidate = candidate.strip()

    if not candidate:
        return None
    local, sep, domain = candidate.partition("@")
    if not sep or not local or not domain:
        return None
    if any(c.isspace() for c in candidate):
        return None
    if "<" in candidate or ">" in candidate:
        return None

    return candidate


# ---------------------------------------------------------------------------
# KMS encrypt / decrypt for refresh tokens
# ---------------------------------------------------------------------------

_kms_client: Any = None


def _get_kms_client() -> Any:
    """Memoized boto3 KMS client. Reused across Lambda invocations."""
    global _kms_client
    if _kms_client is None:
        _kms_client = boto3.client("kms")
    return _kms_client


def encrypt_refresh_token(plaintext: str, kms_key_id: str) -> bytes:
    """Encrypt a Gmail OAuth refresh token via KMS.

    Parameters
    ----------
    plaintext : str
        The refresh token as returned by Google's token endpoint.
    kms_key_id : str
        KMS key ID or ARN (the gmail stack's ``GmailRefreshTokenKey``).

    Returns
    -------
    bytes
        Raw ciphertext blob suitable for storage in a ``VARBINARY``
        column.
    """
    response = _get_kms_client().encrypt(
        KeyId=kms_key_id,
        Plaintext=plaintext.encode("utf-8"),
    )
    return response["CiphertextBlob"]


def decrypt_refresh_token(ciphertext: bytes, kms_key_id: str) -> str:
    """Decrypt a KMS-encrypted refresh token.

    Parameters
    ----------
    ciphertext : bytes
        The blob previously returned by :func:`encrypt_refresh_token`.
    kms_key_id : str
        KMS key ID or ARN. Passed explicitly (KMS does not strictly require
        it for decrypt, but supplying it hardens against ciphertext-under-
        wrong-key confusion if the stack is ever redeployed with a new key).

    Returns
    -------
    str
        The original plaintext refresh token.
    """
    response = _get_kms_client().decrypt(
        CiphertextBlob=ciphertext,
        KeyId=kms_key_id,
    )
    return response["Plaintext"].decode("utf-8")


# ---------------------------------------------------------------------------
# Google API credential and service factories
# ---------------------------------------------------------------------------
#
# Lazy imports so the parser and KMS helpers can be tested without the
# google-auth / google-api-python-client packages installed in the test
# environment. Lambda runtime images get them via requirements.txt.

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_credentials(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    scopes: list[str],
) -> Any:
    """Construct a ``google.oauth2.credentials.Credentials`` instance.

    The returned object holds no access token; the Google API client
    library obtains one transparently on the first request by exchanging
    the refresh token at ``GOOGLE_TOKEN_URI``.

    Parameters
    ----------
    refresh_token : str
        Decrypted refresh token from ``gmail_credentials``.
    client_id : str
        OAuth client id from the GCP console (stored in SSM).
    client_secret : str
        OAuth client secret from the GCP console (stored in SSM).
    scopes : list[str]
        Granted scopes as strings (e.g. ``["https://www.googleapis.com/
        auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]``).

    Returns
    -------
    google.oauth2.credentials.Credentials
    """
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=GOOGLE_TOKEN_URI,
        scopes=scopes,
    )


def build_gmail_service(credentials: Any) -> Any:
    """Construct the Gmail v1 API client.

    Parameters
    ----------
    credentials : google.oauth2.credentials.Credentials
        From :func:`build_credentials`.

    Returns
    -------
    googleapiclient.discovery.Resource
        Use as ``service.users().messages().list(...)`` etc.
    """
    from googleapiclient.discovery import build

    # cache_discovery=False suppresses an import-only warning in
    # environments without the optional cache backend installed.
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def resolve_message_id_to_thread(service: Any, message_id: str) -> str | None:
    """Look up a Gmail message by RFC 822 Message-ID and return its threadId.

    The lookup runs against the credentialed user's mailbox only — a
    Message-ID belonging to another user's account naturally returns
    empty, which is the ownership check the link-handler relies on.

    Parameters
    ----------
    service : googleapiclient.discovery.Resource
        From :func:`build_gmail_service`.
    message_id : str
        Normalized Message-ID (no brackets, no ``mid:`` prefix). Run the
        input through :func:`parse_message_id` first.

    Returns
    -------
    str or None
        The Gmail thread ID on hit, ``None`` if no message matched.
    """
    response = (
        service.users()
        .messages()
        .list(userId="me", q=f"rfc822msgid:{message_id}", maxResults=1)
        .execute()
    )
    messages = response.get("messages", [])
    if not messages:
        return None
    return messages[0].get("threadId")