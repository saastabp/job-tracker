"""Builds the renderer-ready template dict from a validated resume body.

The renderer (``common.pdf_generator.PdfGenerator``) consumes a flat list
of content items. It does not support loops. The hierarchical structure
of a resume — jobs, accomplishments, bullets — gets flattened here at
template-build time.

Only ``tailored_title`` and ``tailored_summary`` remain as runtime
``{placeholder}`` tokens for the renderer's ``data`` dict. Everything
else (name, contact, jobs, etc.) is baked into the literal text of
content items. That matches the slice 13 design (``project_ai_tailoring``
constraint: title + summary are the only fields the AI can alter; the
rest of the body must round-trip from the master verbatim).

The bullet glyph used here is U+2022 ("•"); the master resume uses
OpenSymbol bullets, which we don't bundle. Long bullets wrap without a
hanging indent — acceptable for the typical resume bullet length, and
swappable later if it becomes ugly.
"""
from __future__ import annotations

from typing import Any

from common.resume_schema import ResumeContent, ResumeJob

BULLET_GLYPH = "•"
BODY_SIZE = 11
SECTION_SIZE = 14
ACCENT_COLOR = (31, 73, 125)


def _escape_markdown(text: str) -> str:
    """Defang fpdf2's markdown so user-supplied stars/underscores render literally.

    Parameters
    ----------
    text : str
        Raw user-supplied string from ``content_json``.

    Returns
    -------
    str
        A version safe to drop into a content-item ``text`` field that
        will be rendered with ``markdown=True``.
    """
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")


def _bullets(items: list[str]) -> list[dict[str, Any]]:
    """Render each entry prefixed with the bullet glyph."""
    return [
        {
            "type": "text",
            "text": f"{BULLET_GLYPH}  {_escape_markdown(item)}",
            "size": BODY_SIZE,
        }
        for item in items
    ]


def _job_block(job: ResumeJob) -> list[dict[str, Any]]:
    """Flatten one job into header row, role line, intro, and accomplishments."""
    block: list[dict[str, Any]] = []

    left_parts = [f"**{_escape_markdown(job.company)}**"]
    if job.location:
        left_parts.append(_escape_markdown(job.location))
    left = " — ".join(left_parts)

    block.append(
        {
            "type": "row",
            "left": left,
            "right": _escape_markdown(job.dates),
            "size": BODY_SIZE,
            "color": ACCENT_COLOR,
        }
    )

    if job.role:
        block.append(
            {
                "type": "text",
                "text": _escape_markdown(job.role),
                "size": BODY_SIZE,
                "style": "I",
                "color": ACCENT_COLOR,
                "after": 2,
            }
        )

    if job.intro:
        block.append(
            {
                "type": "text",
                "text": _escape_markdown(job.intro),
                "size": BODY_SIZE,
                "after": 4,
            }
        )

    for accomplishment in job.accomplishments:
        name = _escape_markdown(accomplishment.name)
        intro = _escape_markdown(accomplishment.intro)
        if intro:
            heading = f"**{name}** — {intro}"
        else:
            heading = f"**{name}**"
        block.append({"type": "text", "text": heading, "size": BODY_SIZE})
        block.extend(_bullets(accomplishment.bullets))
        block.append({"type": "text", "text": "", "size": 4, "after": 2})

    return block


def build_template(
    content: ResumeContent,
    tailored_title: str,
    tailored_summary: str,
) -> dict[str, Any]:
    """Assemble a renderer-ready template dict.

    Parameters
    ----------
    content : ResumeContent
        Validated resume body from ``resumes.content_json``.
    tailored_title : str
        AI-generated title to render under the name. Treated as a runtime
        placeholder so it lands in the ``data`` dict at render time.
    tailored_summary : str
        AI-generated summary, rendered under the "Professional Summary"
        header. Same placeholder treatment as the title.

    Returns
    -------
    dict
        Template dict ready for ``PdfGenerator(template).generate({...})``.
        The data dict at render time must supply ``tailored_title`` and
        ``tailored_summary``; the renderer escapes neither, so callers
        should pre-escape if their values may contain literal ``*`` or
        ``_`` characters.
    """
    items: list[dict[str, Any]] = []

    contact_lines: list[str] = []
    if content.header.contact_line:
        contact_lines.append(_escape_markdown(content.header.contact_line))
    for link in content.header.links:
        contact_lines.append(_escape_markdown(link))

    items.append(
        {
            "type": "banner",
            "name": _escape_markdown(content.header.name),
            "contact_lines": contact_lines,
            "name_size": 22,
            "line_size": 9,
            "after": 10,
        }
    )

    items.append(
        {
            "type": "text",
            "text": "{tailored_title}",
            "size": 13,
            "align": "C",
            "style": "B",
            "color": ACCENT_COLOR,
            "after": 10,
        }
    )

    items.append({"type": "section_header", "text": "Professional Summary", "size": SECTION_SIZE})
    items.append(
        {"type": "text", "text": "{tailored_summary}", "size": BODY_SIZE, "after": 6}
    )

    if content.areas_of_expertise:
        items.append(
            {"type": "section_header", "text": "Areas of Expertise", "size": SECTION_SIZE}
        )
        items.extend(_bullets(content.areas_of_expertise))
        items.append({"type": "text", "text": "", "size": 4, "after": 2})

    if content.technical_proficiencies:
        items.append(
            {"type": "section_header", "text": "Technical Proficiencies", "size": SECTION_SIZE}
        )
        items.extend(_bullets(content.technical_proficiencies))
        items.append({"type": "text", "text": "", "size": 4, "after": 2})

    if content.jobs:
        items.append(
            {"type": "section_header", "text": "Professional Experience", "size": SECTION_SIZE}
        )
        for job in content.jobs:
            items.extend(_job_block(job))

    if content.certifications:
        items.append(
            {"type": "section_header", "text": "Certifications", "size": SECTION_SIZE}
        )
        items.extend(_bullets(content.certifications))

    return {
        "page": {
            "format": "letter",
            "margin_left": 54,
            "margin_right": 54,
            "margin_top": 54,
            "margin_bottom": 54,
        },
        "font_family": "NotoSans",
        "fonts": {
            "regular": "NotoSans-Regular.ttf",
            "bold": "NotoSans-Bold.ttf",
            "italic": "NotoSans-Italic.ttf",
        },
        "default_font_size": BODY_SIZE,
        "default_line_height": 1.35,
        "content": items,
    }