"""Unit tests for ``common/gmail_client.py``.

Three concerns:
  * ``parse_message_id`` — exhaustive matrix of input forms (Gmail web header
    line, Thunderbird ``mid:`` URI, multi-header blob, bare/bracketed/UUID
    variants, garbage)
  * ``encrypt_refresh_token`` / ``decrypt_refresh_token`` — boto3 KMS mocked;
    asserts the calls go through with the right key id and plaintext bytes
  * ``resolve_message_id_to_thread`` — the Gmail API client is mocked; checks
    the search-query construction and threadId extraction

The credential / service factory functions (:func:`build_credentials` /
:func:`build_gmail_service`) are not exercised here — they're trivial
constructors that only fail if google-auth / google-api-python-client are
absent at runtime, which a Lambda integration test catches better than a
unit test.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from common import gmail_client


# ---------------------------------------------------------------------------
# parse_message_id
# ---------------------------------------------------------------------------


class TestParseMessageId:
    def test_gmail_web_show_original_line_with_brackets(self):
        # The most common path: triple-click the Message-ID line in Gmail web
        # "Show original" header table.
        result = gmail_client.parse_message_id(
            "Message-ID: <77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com>"
        )
        assert result == "77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com"

    def test_gmail_web_line_no_brackets(self):
        result = gmail_client.parse_message_id(
            "Message-ID: 77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com"
        )
        assert result == "77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com"

    def test_header_name_case_insensitive_lower(self):
        result = gmail_client.parse_message_id(
            "message-id: <abc@example.com>"
        )
        assert result == "abc@example.com"

    def test_header_name_case_insensitive_upper(self):
        result = gmail_client.parse_message_id(
            "MESSAGE-ID: <abc@example.com>"
        )
        assert result == "abc@example.com"

    def test_header_name_case_insensitive_mixed(self):
        result = gmail_client.parse_message_id(
            "Message-Id: <abc@example.com>"
        )
        assert result == "abc@example.com"

    def test_thunderbird_mid_uri(self):
        # Right-click → Organize → Copy Message Link in Thunderbird.
        result = gmail_client.parse_message_id(
            "mid:CAFus6uRsoh0ymLiiGb_uz1kN5w1QL8P9DoRv39W-UmRvr+nF1A@mail.gmail.com"
        )
        assert (
            result
            == "CAFus6uRsoh0ymLiiGb_uz1kN5w1QL8P9DoRv39W-UmRvr+nF1A@mail.gmail.com"
        )

    def test_bare_message_id(self):
        result = gmail_client.parse_message_id(
            "CAFus6uR-test+chars@mail.gmail.com"
        )
        assert result == "CAFus6uR-test+chars@mail.gmail.com"

    def test_angle_bracketed_no_prefix(self):
        result = gmail_client.parse_message_id("<abc@example.com>")
        assert result == "abc@example.com"

    def test_uuid_style_gmail_id(self):
        # Different shape from the CA*@mail.gmail.com form.
        result = gmail_client.parse_message_id(
            "mid:eb7d3002-ca80-4c68-984d-74e0cb802de0@gmail.com"
        )
        assert result == "eb7d3002-ca80-4c68-984d-74e0cb802de0@gmail.com"

    def test_plus_characters_preserved(self):
        # Critical: rfc822msgid: search depends on exact characters.
        # `+` must NOT be URL-decoded to space.
        result = gmail_client.parse_message_id(
            "<CAFus6uR-something+nF1A@mail.gmail.com>"
        )
        assert result == "CAFus6uR-something+nF1A@mail.gmail.com"
        assert "+" in result

    def test_leading_trailing_whitespace_stripped(self):
        result = gmail_client.parse_message_id(
            "   <abc@example.com>   \n"
        )
        assert result == "abc@example.com"

    def test_multi_header_blob_picks_message_id(self):
        # Real-world paste: line breaks collapsed by the browser, multiple
        # headers run together. Parser must pick `Message-ID:` and ignore
        # the adjacent `References:` (which is also a valid Message-ID but
        # for a different message).
        blob = (
            'May 2026 13:27:41 -0700 (PDT)Content-Type: multipart/alternative; '
            'boundary="------------W9DX6KWCv3NgYD7ftYPJfVfG"'
            "Message-ID: <77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com>"
            "Date: Sat, 2 May 2026 10:27:39 -1000MIME-Version: 1.0"
            "User-Agent: Mozilla Thunderbird"
            "Subject: =?UTF-8?Q?Fwd=3A_Find_your_next_favorite_playlist_=F0=9F=8E=A7?="
            "References: <FCOjk_3KQOS9m_kpolPp5w@geopod-ismtpd-100>"
            "Content-Language: en-USTo: dmk4914@gmail.com"
            "From: Brian Saastad <saastabp@gmail.com>"
        )
        result = gmail_client.parse_message_id(blob)
        assert result == "77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com"
        # And critically NOT the References value:
        assert "FCOjk_3KQOS9m_kpolPp5w" not in (result or "")

    def test_in_reply_to_not_picked_up(self):
        # If only In-Reply-To is present (no Message-ID), the regex should
        # NOT match it. Falls through to step-2 fallback, which produces a
        # non-Message-ID value because of the "In-Reply-To: " prefix that
        # the regex won't strip — the `: ` whitespace breaks the candidate.
        result = gmail_client.parse_message_id(
            "In-Reply-To: <abc@example.com>"
        )
        # The parser doesn't recognize In-Reply-To as Message-ID; the fallback
        # path produces a string with internal whitespace, which gets rejected.
        assert result is None

    def test_garbage_no_at_sign(self):
        result = gmail_client.parse_message_id("not a real message id")
        assert result is None

    def test_garbage_empty_string(self):
        result = gmail_client.parse_message_id("")
        assert result is None

    def test_garbage_only_brackets(self):
        result = gmail_client.parse_message_id("<>")
        assert result is None

    def test_garbage_just_at_sign(self):
        result = gmail_client.parse_message_id("@")
        # No local-part, no domain: "@" alone doesn't have anything either
        # side, so it shouldn't be accepted. The regex requires `[^\s<>]+@
        # [^\s<>]+` (one or more non-space chars on each side).
        assert result is None


# ---------------------------------------------------------------------------
# encrypt_refresh_token / decrypt_refresh_token
# ---------------------------------------------------------------------------


class TestEncryptDecryptRefreshToken:
    def test_encrypt_calls_kms_with_utf8_bytes(self, mocker):
        fake_kms = MagicMock()
        fake_kms.encrypt.return_value = {
            "CiphertextBlob": b"\x01\x02\x03cipher"
        }
        mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)

        result = gmail_client.encrypt_refresh_token(
            "1//0gabc-secret-token", "alias/jobtracker-gmail"
        )

        assert result == b"\x01\x02\x03cipher"
        fake_kms.encrypt.assert_called_once_with(
            KeyId="alias/jobtracker-gmail",
            Plaintext=b"1//0gabc-secret-token",
        )

    def test_decrypt_calls_kms_with_explicit_key_id(self, mocker):
        # Passing KeyId on decrypt is the belt-and-suspenders check that
        # protects against ciphertext-under-wrong-key confusion.
        fake_kms = MagicMock()
        fake_kms.decrypt.return_value = {
            "Plaintext": b"1//0gabc-secret-token"
        }
        mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)

        result = gmail_client.decrypt_refresh_token(
            b"\x01\x02\x03cipher", "alias/jobtracker-gmail"
        )

        assert result == "1//0gabc-secret-token"
        fake_kms.decrypt.assert_called_once_with(
            CiphertextBlob=b"\x01\x02\x03cipher",
            KeyId="alias/jobtracker-gmail",
        )

    def test_round_trip_via_mocked_kms(self, mocker):
        # Simulate the full encrypt → store-as-blob → decrypt cycle the
        # OAuth callback + poller will exercise.
        plaintext = "1//0gXxX-very-secret"
        fake_blob = b"\xff\xee\xdd\xcccipher_payload"

        fake_kms = MagicMock()
        fake_kms.encrypt.return_value = {"CiphertextBlob": fake_blob}
        fake_kms.decrypt.return_value = {"Plaintext": plaintext.encode("utf-8")}
        mocker.patch.object(gmail_client, "_get_kms_client", return_value=fake_kms)

        ciphertext = gmail_client.encrypt_refresh_token(plaintext, "k")
        assert ciphertext == fake_blob

        recovered = gmail_client.decrypt_refresh_token(ciphertext, "k")
        assert recovered == plaintext


# ---------------------------------------------------------------------------
# resolve_message_id_to_thread
# ---------------------------------------------------------------------------


class TestResolveMessageIdToThread:
    def _build_service_mock(self, list_response: dict) -> MagicMock:
        # Mimics the chain: service.users().messages().list(...).execute()
        execute = MagicMock(return_value=list_response)
        list_call = MagicMock(return_value=MagicMock(execute=execute))
        messages_resource = MagicMock(list=list_call)
        users_resource = MagicMock(
            messages=MagicMock(return_value=messages_resource)
        )
        service = MagicMock(users=MagicMock(return_value=users_resource))
        return service

    def test_hit_returns_thread_id(self):
        service = self._build_service_mock(
            {"messages": [{"id": "msg-1", "threadId": "thread-abc"}]}
        )
        result = gmail_client.resolve_message_id_to_thread(
            service, "abc@example.com"
        )
        assert result == "thread-abc"

    def test_miss_returns_none(self):
        service = self._build_service_mock({})
        result = gmail_client.resolve_message_id_to_thread(
            service, "nonexistent@example.com"
        )
        assert result is None

    def test_empty_messages_list_returns_none(self):
        service = self._build_service_mock({"messages": []})
        result = gmail_client.resolve_message_id_to_thread(
            service, "nonexistent@example.com"
        )
        assert result is None

    def test_query_uses_rfc822msgid_operator(self):
        service = self._build_service_mock(
            {"messages": [{"id": "msg-1", "threadId": "t1"}]}
        )
        gmail_client.resolve_message_id_to_thread(
            service, "abc+plus@example.com"
        )
        # Walk back through the mock chain to inspect the list() call.
        list_kwargs = (
            service.users.return_value.messages.return_value.list.call_args.kwargs
        )
        assert list_kwargs["userId"] == "me"
        assert list_kwargs["q"] == "rfc822msgid:abc+plus@example.com"
        assert list_kwargs["maxResults"] == 1