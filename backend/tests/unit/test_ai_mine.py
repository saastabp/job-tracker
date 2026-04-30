"""Unit tests for ``handlers/ai_mine.py``.

The Bedrock Anthropic client is patched at the module level so prompt
construction, parsing, and error paths can be exercised without real AWS
or model calls.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


def _fake_response(text: str) -> SimpleNamespace:
    """Build a stand-in for ``client.messages.create`` return value."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


def _patch_client(mocker, *, returns: SimpleNamespace) -> MagicMock:
    from handlers import ai_mine

    fake = MagicMock()
    fake.messages.create.return_value = returns
    mocker.patch.object(ai_mine, "_client", fake)
    return fake


def _event(body: dict | None) -> dict:
    return {
        "routeKey": "POST /ai/mine-resume",
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
            }
        },
        "body": json.dumps(body) if body is not None else "",
    }


def test_mine_returns_title_and_summary(mocker, lambda_ctx):
    from handlers import ai_mine

    fake = _patch_client(
        mocker,
        returns=_fake_response(
            '{"title": "Senior SRE", "summary": "Hawaii-based platform engineer."}'
        ),
    )

    resp = ai_mine.handler(
        _event({"text": "JANE DOE\nSenior SRE\n10 years experience..."}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {
        "title": "Senior SRE",
        "summary": "Hawaii-based platform engineer.",
    }
    # Prompt carries the resume text verbatim and the right model is hit.
    call = fake.messages.create.call_args
    assert call.kwargs["model"] == ai_mine.BEDROCK_MODEL_ID
    assert "Senior SRE" in call.kwargs["messages"][0]["content"]
    assert "JANE DOE" in call.kwargs["messages"][0]["content"]


def test_mine_strips_fenced_json(mocker, lambda_ctx):
    """Defensive parser: model sometimes wraps JSON in ```json fences."""
    from handlers import ai_mine

    _patch_client(
        mocker,
        returns=_fake_response(
            '```json\n{"title": "Backend Engineer", "summary": "Distributed systems."}\n```'
        ),
    )

    resp = ai_mine.handler(_event({"text": "resume body"}), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["title"] == "Backend Engineer"


def test_mine_missing_text_is_400(mocker, lambda_ctx):
    from handlers import ai_mine  # noqa: F401  (ensure module loaded)

    resp = ai_mine.handler(_event({}), lambda_ctx)

    assert resp["statusCode"] == 400
    assert "text is required" in json.loads(resp["body"])["error"]


def test_mine_input_too_long_is_400(mocker, lambda_ctx):
    from handlers import ai_mine

    huge = "x" * (ai_mine.MAX_INPUT_CHARS + 1)
    resp = ai_mine.handler(_event({"text": huge}), lambda_ctx)

    assert resp["statusCode"] == 400
    assert "too long" in json.loads(resp["body"])["error"]


def test_mine_parse_failure_is_502(mocker, lambda_ctx):
    """Model returning prose instead of JSON → 502, not a confident lie."""
    from handlers import ai_mine  # noqa: F401

    _patch_client(mocker, returns=_fake_response("Sorry, I cannot help with that."))

    resp = ai_mine.handler(_event({"text": "resume"}), lambda_ctx)

    assert resp["statusCode"] == 502
    assert json.loads(resp["body"])["error"] == "ai service failed"


def test_mine_empty_fields_is_502(mocker, lambda_ctx):
    """Model returning JSON with blank title or summary → 502."""
    from handlers import ai_mine  # noqa: F401

    _patch_client(
        mocker,
        returns=_fake_response('{"title": "", "summary": "some text"}'),
    )

    resp = ai_mine.handler(_event({"text": "resume"}), lambda_ctx)

    assert resp["statusCode"] == 502


def test_mine_bedrock_exception_is_502(mocker, lambda_ctx):
    from handlers import ai_mine

    fake = MagicMock()
    fake.messages.create.side_effect = RuntimeError("ThrottlingException")
    mocker.patch.object(ai_mine, "_client", fake)

    resp = ai_mine.handler(_event({"text": "resume"}), lambda_ctx)

    assert resp["statusCode"] == 502