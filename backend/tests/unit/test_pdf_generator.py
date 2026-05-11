"""Unit tests for the PDF rendering pipeline.

Three layers under test:

* ``common.resume_schema.ResumeContent`` — Pydantic validation rejects
  malformed payloads before the renderer ever sees them.
* ``common.resume_template.build_template`` — flattens validated
  hierarchical content into the engine's flat content-item list.
* ``common.pdf_generator.PdfGenerator`` — actually emits PDF bytes via
  fpdf2, using the bundled Noto Sans fonts.

These tests exercise the real fpdf2 + pypdf path so we catch font-
registration, markdown-escaping, and content-item-dispatch regressions.
The handler-level tests in ``test_resumes.py`` mock ``PdfGenerator``
out so they stay isolated and fast.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.pdf_generator import PdfGenerator
from common.resume_schema import ResumeContent
from common.resume_template import build_template


FONTS_DIR = str(Path(__file__).resolve().parents[2] / "src" / "common" / "fonts")


def _sample_content(*, accomplishments: list | None = None) -> ResumeContent:
    """Build a minimal but renderable ``ResumeContent`` for the happy paths."""
    return ResumeContent.model_validate(
        {
            "header": {
                "name": "Jane Tester",
                "contact_line": "jane@example.com • 555-0100",
                "links": ["https://www.linkedin.com/in/jane-tester/"],
            },
            "areas_of_expertise": ["Cloud Architecture", "Distributed Systems"],
            "technical_proficiencies": ["AWS (Lambda, S3, RDS)", "Python, Go"],
            "jobs": [
                {
                    "company": "Acme Cloud",
                    "location": "Remote",
                    "dates": "2022 – Present",
                    "role": "Principal Engineer",
                    "intro": "Led the platform team.",
                    "accomplishments": (
                        [
                            {
                                "name": "Eventing Platform",
                                "intro": "Replatformed the event bus.",
                                "bullets": [
                                    "Cut p99 latency by 40%.",
                                    "Migrated 200 producers with zero downtime.",
                                ],
                            }
                        ]
                        if accomplishments is None
                        else accomplishments
                    ),
                }
            ],
            "certifications": ["AWS Certified Solutions Architect"],
        }
    )


def _render(
    content: ResumeContent,
    title: str = "Senior Cloud Engineer",
    summary: str = "Builds resilient serverless platforms.",
) -> bytes:
    template = build_template(content, title, summary)
    generator = PdfGenerator(template, fonts_dir=FONTS_DIR)
    return bytes(generator.generate({"tailored_title": title, "tailored_summary": summary}))


def test_render_returns_pdf_bytes():
    """Smoke test: output begins with the PDF magic and is non-trivial in size."""
    pdf_bytes = _render(_sample_content())

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000


def test_tailored_title_and_summary_appear_in_extracted_text():
    """The tailored placeholders make it through fpdf2 → into the rendered text layer."""
    pypdf = pytest.importorskip("pypdf")

    title = "Distributed Systems Architect"
    summary = "Twenty years building globally distributed services."
    pdf_bytes = _render(_sample_content(), title=title, summary=summary)

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    extracted = "".join(page.extract_text() or "" for page in reader.pages)

    assert title in extracted
    assert summary in extracted


def test_schema_rejects_missing_header():
    """``header`` is required — empty body must not reach the renderer."""
    with pytest.raises(ValidationError):
        ResumeContent.model_validate({})


def test_schema_rejects_blank_name():
    """Header.name has ``min_length=1`` — blank values are 400-able input."""
    with pytest.raises(ValidationError):
        ResumeContent.model_validate(
            {"header": {"name": "", "contact_line": "x@y"}}
        )


def test_schema_rejects_unknown_top_level_key():
    """``extra='forbid'`` keeps typo'd payloads from silently being persisted."""
    with pytest.raises(ValidationError):
        ResumeContent.model_validate(
            {"header": {"name": "X"}, "exprience": []}  # typo on purpose
        )


def test_empty_accomplishments_renders_without_crash():
    """Jobs without accomplishments are legal — must render and emit PDF bytes."""
    content = _sample_content(accomplishments=[])
    pdf_bytes = _render(content)

    assert pdf_bytes.startswith(b"%PDF-")


def test_missing_fonts_dir_raises():
    """Renderer with a bogus fonts_dir should fail loudly at register time."""
    template = build_template(
        _sample_content(),
        "Title",
        "Summary",
    )
    generator = PdfGenerator(template, fonts_dir="/nonexistent/path/to/fonts")
    with pytest.raises(FileNotFoundError):
        generator.generate({"tailored_title": "Title", "tailored_summary": "Summary"})


def test_constructor_requires_fonts_dir():
    """An empty fonts_dir is a programmer error, not silent S3 fallback."""
    template = build_template(_sample_content(), "T", "S")
    with pytest.raises(ValueError):
        PdfGenerator(template, fonts_dir="")