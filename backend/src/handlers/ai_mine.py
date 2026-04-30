"""POST /ai/mine-resume — extract a title + summary from raw resume text.

The browser parses the resume PDF with pdfjs-dist (slice 06 fork 1) and
POSTs the extracted plain text in the request body. This Lambda runs
**outside the VPC** — Bedrock is reached over AWS-managed networking, no
VPC endpoint and no DB access needed.

Request body
------------
``{"text": "<full resume plaintext>"}``

Response
--------
``{"title": "...", "summary": "..."}``

The model is asked to return JSON; parse failures bubble up as a 502 so the
caller knows the AI step misbehaved (vs. their input being bad, which is a
400). No heuristic regex fallback — see slice-04 doc for why that path was
abandoned.
"""
from __future__ import annotations

import json
import os
from typing import Any

from anthropic import AnthropicBedrock

from common.auth import user_sub
from common.logger import logger

BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

MAX_INPUT_CHARS = 30_000
MAX_OUTPUT_TOKENS = 400

_client: AnthropicBedrock | None = None


class _AiServiceError(Exception):
    """Raised when Bedrock returns something we can't trust (parse fail,
    empty fields). Distinct from ValueError so the handler can map it to
    502 instead of treating it as a 400 user-input error."""


def _get_client() -> AnthropicBedrock:
    global _client
    if _client is None:
        _client = AnthropicBedrock(aws_region=AWS_REGION)
    return _client


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


SYSTEM_PROMPT = (
    "You extract two short fields from a resume: a professional title and a "
    "professional summary. The title is a one-line role descriptor (e.g., "
    "'Senior Backend Engineer — Distributed Systems'). The summary is the "
    "candidate's own existing professional summary / objective / about "
    "section, copied as-is when present, or a faithful 2-3 sentence "
    "condensation of the candidate's most recent role and impact when "
    "absent. Do not invent skills, employers, dates, or accomplishments. "
    "Use the candidate's own words and phrasing wherever possible."
)

USER_PROMPT_TEMPLATE = """Extract the title and summary from this resume.

Return ONLY valid JSON with exactly two string fields, no prose, no markdown:
{{"title": "...", "summary": "..."}}

Constraints:
- title: <= 100 chars, single line
- summary: <= 600 chars, 2-3 sentences

Resume text:
---
{text}
---"""


def _build_messages(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)}]


def _extract_json(raw: str) -> dict[str, Any]:
    """Pull the first ``{...}`` block out of the model's reply and parse it.

    Haiku usually returns clean JSON when asked, but we strip ``)``-prose and
    fenced-code-block wrappers defensively before json.loads.
    """
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise _AiServiceError(f"no JSON object found in model reply: {raw!r}")
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError as e:
        raise _AiServiceError(f"model reply was not valid JSON: {e}") from e


def _mine(text: str) -> dict[str, str]:
    client = _get_client()
    logger.info(
        "ai_mine: invoking bedrock",
        extra={"model_id": BEDROCK_MODEL_ID, "input_chars": len(text)},
    )
    resp = client.messages.create(
        model=BEDROCK_MODEL_ID,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=_build_messages(text),
    )
    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    logger.info(
        "ai_mine: bedrock returned",
        extra={
            "stop_reason": resp.stop_reason,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "raw_chars": len(raw),
        },
    )

    parsed = _extract_json(raw)
    title = (parsed.get("title") or "").strip()
    summary = (parsed.get("summary") or "").strip()
    if not title or not summary:
        raise _AiServiceError(
            f"model returned empty title or summary: {parsed!r}"
        )
    return {"title": title, "summary": summary}


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("ai_mine: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        body = json.loads(event.get("body") or "{}")
        text = (body.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        if len(text) > MAX_INPUT_CHARS:
            raise ValueError(
                f"text too long ({len(text)} chars; max {MAX_INPUT_CHARS})"
            )

        result = _mine(text)
        logger.info(
            "ai_mine: exit ok",
            extra={
                "user_sub": sub,
                "title_chars": len(result["title"]),
                "summary_chars": len(result["summary"]),
            },
        )
        return _response(200, result)
    except ValueError as e:
        logger.exception("ai_mine: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except _AiServiceError:
        logger.exception(
            "ai_mine: model reply unusable",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(502, {"error": "ai service failed"})
    except Exception:
        logger.exception(
            "ai_mine: bedrock invocation failed",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(502, {"error": "ai service failed"})