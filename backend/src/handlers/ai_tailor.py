"""POST /ai/tailor — tailor a resume title + summary to a specific JD.

Scope is locked to **title and summary only** (see project_ai_tailoring
memory). Work experience, bullets, skills, dates, and company names are
off-limits — fabricating any of those is brutal at interview time.

Request body
------------
``{"master_title": "...", "master_summary": "...", "jd_text": "..."}``

Response
--------
``{"tailored_title": "...", "tailored_summary": "..."}``

The endpoint is **suggest-only** (slice 06 fork 2). It returns the
suggestion; the SPA populates the submission's tailored-fields inputs and
the user reviews + saves. This lets the user re-roll without polluting the
submission row.
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

MAX_MASTER_TITLE_CHARS = 300
MAX_MASTER_SUMMARY_CHARS = 4_000
MAX_JD_CHARS = 30_000
MAX_OUTPUT_TOKENS = 500

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


# Prompt design notes (project_ai_tailoring memory):
# - Output ONLY tailored title + summary. No bullets, skills, dates,
#   companies — fabricating any of those is interview-disqualifying.
# - Preserve the candidate's voice from the master summary. Match cadence,
#   sentence length, vocabulary level. Don't superimpose generic
#   resume-speak.
# - Avoid AI-detection signatures: no "Strategic [adjective] [noun] with
#   [N] years of experience…" openers, no "leveraged synergies" /
#   "spearheaded transformation" filler unless verbatim in master.
SYSTEM_PROMPT = (
    "You tailor exactly two fields of a resume to a specific job description: "
    "the title and the professional summary. You do not modify, generate, "
    "or output anything else from the resume.\n\n"
    "Hard constraints:\n"
    "1. Output ONLY a tailored title and a tailored summary. Never output "
    "work-experience bullets, skills, accomplishments, dates, employers, or "
    "education.\n"
    "2. Never invent facts. The tailored summary may reference experience or "
    "skills ONLY if they appear in the master summary the user provides. "
    "Treat the JD as positioning input, not as a source of new claims.\n"
    "3. Preserve the candidate's voice. Match the cadence, sentence length, "
    "and vocabulary register of the master summary. If the master summary "
    "is plain and direct, the tailored one is too.\n"
    "4. Avoid AI-detection signatures. Specifically: do NOT open with "
    "'Strategic <adjective> <noun> with <N> years of experience…' or any "
    "minor variation. Do not stuff buzzwords ('leveraged synergies', "
    "'spearheaded transformation', 'drove cross-functional alignment') "
    "unless they appear verbatim in the master.\n"
    "5. Title <= 80 chars, single line. Summary <= 3 sentences."
)

USER_PROMPT_TEMPLATE = """Master title (voice exemplar — keep tone/register):
{master_title}

Master summary (voice exemplar AND only allowed source of facts):
{master_summary}

Job description (positioning input only — do NOT pull facts from this):
---
{jd_text}
---

Return ONLY valid JSON with exactly two string fields, no prose, no markdown:
{{"tailored_title": "...", "tailored_summary": "..."}}"""


def _build_messages(
    master_title: str, master_summary: str, jd_text: str
) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(
                master_title=master_title,
                master_summary=master_summary,
                jd_text=jd_text,
            ),
        }
    ]


def _extract_json(raw: str) -> dict[str, Any]:
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


def _tailor(master_title: str, master_summary: str, jd_text: str) -> dict[str, str]:
    client = _get_client()
    logger.info(
        "ai_tailor: invoking bedrock",
        extra={
            "model_id": BEDROCK_MODEL_ID,
            "master_title_chars": len(master_title),
            "master_summary_chars": len(master_summary),
            "jd_chars": len(jd_text),
        },
    )
    resp = client.messages.create(
        model=BEDROCK_MODEL_ID,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=_build_messages(master_title, master_summary, jd_text),
    )
    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    logger.info(
        "ai_tailor: bedrock returned",
        extra={
            "stop_reason": resp.stop_reason,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "raw_chars": len(raw),
        },
    )

    parsed = _extract_json(raw)
    title = (parsed.get("tailored_title") or "").strip()
    summary = (parsed.get("tailored_summary") or "").strip()
    if not title or not summary:
        raise _AiServiceError(
            f"model returned empty tailored_title or tailored_summary: {parsed!r}"
        )
    return {"tailored_title": title, "tailored_summary": summary}


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    sub = user_sub(event)
    route_key = event.get("routeKey", "")
    logger.info("ai_tailor: enter", extra={"user_sub": sub, "route_key": route_key})
    try:
        body = json.loads(event.get("body") or "{}")
        master_title = (body.get("master_title") or "").strip()
        master_summary = (body.get("master_summary") or "").strip()
        jd_text = (body.get("jd_text") or "").strip()

        if not master_title:
            raise ValueError("master_title is required")
        if not master_summary:
            raise ValueError("master_summary is required")
        if not jd_text:
            raise ValueError("jd_text is required")
        if len(master_title) > MAX_MASTER_TITLE_CHARS:
            raise ValueError(
                f"master_title too long (max {MAX_MASTER_TITLE_CHARS} chars)"
            )
        if len(master_summary) > MAX_MASTER_SUMMARY_CHARS:
            raise ValueError(
                f"master_summary too long (max {MAX_MASTER_SUMMARY_CHARS} chars)"
            )
        if len(jd_text) > MAX_JD_CHARS:
            raise ValueError(f"jd_text too long (max {MAX_JD_CHARS} chars)")

        result = _tailor(master_title, master_summary, jd_text)
        logger.info(
            "ai_tailor: exit ok",
            extra={
                "user_sub": sub,
                "tailored_title_chars": len(result["tailored_title"]),
                "tailored_summary_chars": len(result["tailored_summary"]),
            },
        )
        return _response(200, result)
    except ValueError as e:
        logger.exception("ai_tailor: bad request", extra={"user_sub": sub})
        return _response(400, {"error": str(e)})
    except _AiServiceError:
        logger.exception(
            "ai_tailor: model reply unusable",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(502, {"error": "ai service failed"})
    except Exception:
        logger.exception(
            "ai_tailor: bedrock invocation failed",
            extra={"user_sub": sub, "route_key": route_key},
        )
        return _response(502, {"error": "ai service failed"})