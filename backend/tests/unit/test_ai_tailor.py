"""Unit tests for ``handlers/ai_tailor.py``.

Mirrors test_ai_mine in shape: patch the AnthropicBedrock client, verify
prompt carries the master fields verbatim (the model evaluation for
voice-preservation can only happen against a real Bedrock endpoint at
deploy time — see slice 06 doc test plan).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


def _fake_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=200, output_tokens=80),
    )


def _patch_client(mocker, *, returns: SimpleNamespace) -> MagicMock:
    from handlers import ai_tailor

    fake = MagicMock()
    fake.messages.create.return_value = returns
    mocker.patch.object(ai_tailor, "_client", fake)
    return fake


def _event(body: dict | None) -> dict:
    return {
        "routeKey": "POST /ai/tailor",
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
            }
        },
        "body": json.dumps(body) if body is not None else "",
    }


GOOD_BODY = {
    "master_title": "Senior SRE — Distributed Systems",
    "master_summary": (
        "I build observable, boring infrastructure. Ten years across "
        "ad-tech, fintech, and one regrettable crypto stint. I prefer "
        "Postgres to Mongo and YAML to nothing."
    ),
    "jd_text": "We are looking for a senior platform engineer...",
}


def test_tailor_returns_title_and_summary(mocker, lambda_ctx):
    from handlers import ai_tailor

    fake = _patch_client(
        mocker,
        returns=_fake_response(
            '{"tailored_title": "Senior Platform Engineer", '
            '"tailored_summary": "Builds boring infra. Ten years deep, '
            'Postgres-leaning."}'
        ),
    )

    resp = ai_tailor.handler(_event(GOOD_BODY), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["tailored_title"] == "Senior Platform Engineer"
    assert "boring infra" in body["tailored_summary"]


def test_tailor_prompt_carries_master_fields_verbatim(mocker, lambda_ctx):
    """Voice-preservation evals require the master fields appear verbatim
    in the prompt — that's what the model uses as a style exemplar.
    """
    from handlers import ai_tailor

    fake = _patch_client(
        mocker,
        returns=_fake_response(
            '{"tailored_title": "T", "tailored_summary": "S."}'
        ),
    )

    ai_tailor.handler(_event(GOOD_BODY), lambda_ctx)

    call = fake.messages.create.call_args
    prompt_text = call.kwargs["messages"][0]["content"]
    assert GOOD_BODY["master_title"] in prompt_text
    assert GOOD_BODY["master_summary"] in prompt_text
    assert GOOD_BODY["jd_text"] in prompt_text
    # System prompt explicitly forbids fabrication.
    assert "Never invent facts" in call.kwargs["system"]


def test_tailor_strips_fenced_json(mocker, lambda_ctx):
    from handlers import ai_tailor

    _patch_client(
        mocker,
        returns=_fake_response(
            '```json\n{"tailored_title": "X", "tailored_summary": "Y."}\n```'
        ),
    )

    resp = ai_tailor.handler(_event(GOOD_BODY), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["tailored_title"] == "X"


def test_tailor_missing_master_title_is_400(mocker, lambda_ctx):
    from handlers import ai_tailor  # noqa: F401

    resp = ai_tailor.handler(
        _event({**GOOD_BODY, "master_title": ""}), lambda_ctx
    )

    assert resp["statusCode"] == 400
    assert "master_title is required" in json.loads(resp["body"])["error"]


def test_tailor_missing_master_summary_is_400(mocker, lambda_ctx):
    from handlers import ai_tailor  # noqa: F401

    resp = ai_tailor.handler(
        _event({**GOOD_BODY, "master_summary": ""}), lambda_ctx
    )

    assert resp["statusCode"] == 400


def test_tailor_missing_jd_is_400(mocker, lambda_ctx):
    from handlers import ai_tailor  # noqa: F401

    resp = ai_tailor.handler(_event({**GOOD_BODY, "jd_text": ""}), lambda_ctx)

    assert resp["statusCode"] == 400


def test_tailor_jd_too_long_is_400(mocker, lambda_ctx):
    from handlers import ai_tailor

    body = {**GOOD_BODY, "jd_text": "x" * (ai_tailor.MAX_JD_CHARS + 1)}
    resp = ai_tailor.handler(_event(body), lambda_ctx)

    assert resp["statusCode"] == 400
    assert "too long" in json.loads(resp["body"])["error"]


def test_tailor_parse_failure_is_502(mocker, lambda_ctx):
    from handlers import ai_tailor  # noqa: F401

    _patch_client(mocker, returns=_fake_response("Here you go!"))

    resp = ai_tailor.handler(_event(GOOD_BODY), lambda_ctx)

    assert resp["statusCode"] == 502


def test_tailor_empty_field_is_502(mocker, lambda_ctx):
    from handlers import ai_tailor  # noqa: F401

    _patch_client(
        mocker,
        returns=_fake_response(
            '{"tailored_title": "T", "tailored_summary": ""}'
        ),
    )

    resp = ai_tailor.handler(_event(GOOD_BODY), lambda_ctx)

    assert resp["statusCode"] == 502


def test_tailor_bedrock_exception_is_502(mocker, lambda_ctx):
    from handlers import ai_tailor

    fake = MagicMock()
    fake.messages.create.side_effect = RuntimeError("ServiceUnavailable")
    mocker.patch.object(ai_tailor, "_client", fake)

    resp = ai_tailor.handler(_event(GOOD_BODY), lambda_ctx)

    assert resp["statusCode"] == 502