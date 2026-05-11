"""Reusable PDF generation engine for tailored resumes.

Renders documents from a dict-shaped template definition using fpdf2.
The template is a flat list of content items; each item declares a type
(``text``, ``row``, ``section_header``) and the renderer dispatches on it.

Compared to the original ``looch`` version this module was vendored from,
two things changed and one was added:

* Fonts are loaded from a bundled directory, not S3. ``fonts_dir`` is
  required at construction time. Job-tracker ships
  ``backend/src/common/fonts/`` inside the Lambda zip; the handler passes
  that path in.
* Logging routes through ``common.logger`` (aws-lambda-powertools)
  instead of stdlib ``logging``.
* Two new content-item types — ``row`` (left + right text on the same
  baseline) and ``section_header`` (heading text with a horizontal rule
  underneath) — needed for the resume layout. The default ``text`` item
  behaves identically to the original engine.

Usage
-----
>>> from common.pdf_generator import PdfGenerator
>>> from common.resume_template import build_template
>>>
>>> template = build_template(content_json, tailored_title, tailored_summary)
>>> gen = PdfGenerator(template, fonts_dir="/var/task/common/fonts")
>>> pdf_bytes = gen.generate({})
"""
from __future__ import annotations

import os
from typing import Any

from fpdf import FPDF

from common.logger import logger


# Default accent color for section-header rules. Picked to roughly match the
# blue accent line in the current master resume. Templates can override per
# item via ``rule_color``.
DEFAULT_RULE_COLOR: tuple[int, int, int] = (31, 73, 125)
DEFAULT_RULE_WIDTH_PT: float = 0.75


class PdfGenerator:
    """Render a PDF document from a dict-shaped template definition.

    Parameters
    ----------
    template : dict
        Document template. See ``common.resume_template`` for the shape
        used by tailored-resume rendering. Top-level keys:
        ``page`` (format + margins), ``font_family`` (logical family name
        for fpdf2's font registry), ``fonts`` (style → filename map),
        ``default_font_size``, ``default_line_height``, ``content``
        (list of content items).
    fonts_dir : str
        Directory containing the ``.ttf`` files referenced by
        ``template["fonts"]``. Required — the renderer has no S3
        fallback (deliberate; see module docstring).

    Examples
    --------
    >>> gen = PdfGenerator(template, fonts_dir="/var/task/common/fonts")
    >>> pdf_bytes = gen.generate({"tailored_title": "...", "tailored_summary": "..."})
    """

    def __init__(self, template: dict[str, Any], fonts_dir: str) -> None:
        if not fonts_dir:
            raise ValueError("fonts_dir is required")
        self.template = template
        self.fonts_dir = fonts_dir

    def generate(self, data: dict[str, Any]) -> bytes:
        """Generate a PDF and return its bytes.

        Parameters
        ----------
        data : dict
            Field values substituted into ``{placeholder}`` tokens in
            content-item text. For tailored resumes this is typically
            ``{"tailored_title": "...", "tailored_summary": "..."}``;
            everything else is already baked into the template by
            ``resume_template.build_template``.

        Returns
        -------
        bytes
            The full PDF file contents.
        """
        return self._build_pdf(data)

    def _build_pdf(self, data: dict[str, Any]) -> bytes:
        page_cfg = self.template.get("page", {})

        pdf = FPDF(unit="pt", format=page_cfg.get("format", "letter"))
        pdf.set_auto_page_break(
            auto=True,
            margin=page_cfg.get("margin_bottom", 72),
        )
        pdf.set_margins(
            left=page_cfg.get("margin_left", 72),
            top=page_cfg.get("margin_top", 72),
            right=page_cfg.get("margin_right", 72),
        )

        self._register_fonts(pdf)
        pdf.add_page()

        font_family = self.template.get("font_family", "NotoSans")
        default_size = self.template.get("default_font_size", 10)
        default_line_height = self.template.get("default_line_height", 1.3)

        content = self.template.get("content", [])
        logger.debug(
            "pdf_generator: rendering %d content items, family=%s",
            len(content),
            font_family,
        )

        for item in content:
            item_type = item.get("type", "text")
            if item_type == "text":
                self._render_text(
                    pdf, item, data, font_family, default_size, default_line_height
                )
            elif item_type == "row":
                self._render_row(
                    pdf, item, data, font_family, default_size, default_line_height
                )
            elif item_type == "section_header":
                self._render_section_header(
                    pdf, item, data, font_family, default_size, default_line_height
                )
            elif item_type == "banner":
                self._render_banner(
                    pdf, item, data, font_family, default_size, default_line_height
                )
            else:
                raise ValueError(f"unknown content item type: {item_type!r}")

            after = item.get("after", 0)
            if after > 0:
                pdf.ln(after)

        return pdf.output()

    def _register_fonts(self, pdf: FPDF) -> None:
        """Register every font declared in ``template["fonts"]``.

        Parameters
        ----------
        pdf : FPDF
            The fpdf2 document being built.
        """
        font_family = self.template.get("font_family", "NotoSans")
        fonts = self.template.get("fonts", {})

        for style_key, font_filename in fonts.items():
            style = ""
            lowered = style_key.lower()
            if "bold" in lowered:
                style += "B"
            if "italic" in lowered:
                style += "I"

            font_path = os.path.join(self.fonts_dir, font_filename)
            if not os.path.exists(font_path):
                raise FileNotFoundError(f"font file not found: {font_path}")
            pdf.add_font(family=font_family, style=style, fname=font_path)

    def _render_text(
        self,
        pdf: FPDF,
        item: dict[str, Any],
        data: dict[str, Any],
        font_family: str,
        default_size: float,
        default_line_height: float,
    ) -> None:
        """Render a flowing paragraph of text via ``multi_cell``."""
        size = item.get("size", default_size)
        align = item.get("align", "L")
        line_height = size * item.get("line_height", default_line_height)
        style = item.get("style", "")
        color = item.get("color")

        text = item["text"].format(**data)

        if color is not None:
            pdf.set_text_color(*color)
        pdf.set_font(font_family, style=style, size=size)
        pdf.multi_cell(
            w=0,
            h=line_height,
            text=text,
            align=align,
            markdown=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        if color is not None:
            pdf.set_text_color(0, 0, 0)

    def _render_row(
        self,
        pdf: FPDF,
        item: dict[str, Any],
        data: dict[str, Any],
        font_family: str,
        default_size: float,
        default_line_height: float,
    ) -> None:
        """Render two strings on the same baseline: ``left`` left-aligned,
        ``right`` right-aligned. Both halves render through markdown so
        bolded segments (``**Company**``) work inline.

        Each half occupies half the page's content width. Long left text
        will get visually clipped against the right cell; templates should
        keep both halves short (a single line of metadata each).
        """
        size = item.get("size", default_size)
        line_height = size * item.get("line_height", default_line_height)
        style = item.get("style", "")
        color = item.get("color")

        left = item.get("left", "").format(**data)
        right = item.get("right", "").format(**data)

        usable = pdf.w - pdf.l_margin - pdf.r_margin
        half = usable / 2

        if color is not None:
            pdf.set_text_color(*color)
        pdf.set_font(font_family, style=style, size=size)
        pdf.cell(
            w=half,
            h=line_height,
            text=left,
            align="L",
            markdown=True,
            new_x="RIGHT",
            new_y="TOP",
        )
        pdf.cell(
            w=half,
            h=line_height,
            text=right,
            align="R",
            markdown=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        if color is not None:
            pdf.set_text_color(0, 0, 0)

    def _render_section_header(
        self,
        pdf: FPDF,
        item: dict[str, Any],
        data: dict[str, Any],
        font_family: str,
        default_size: float,
        default_line_height: float,
    ) -> None:
        """Render heading text in bold, then draw a horizontal rule under it.

        Honors per-item ``text_color``, ``rule_color`` (both ``(r, g, b)``)
        and ``rule_width`` (points); falls back to module-level defaults.
        Text color defaults to the same accent as the rule so headings and
        rules read as a single styled unit.
        """
        size = item.get("size", default_size)
        align = item.get("align", "L")
        line_height = size * item.get("line_height", default_line_height)
        style = item.get("style", "B")
        text_color = item.get("text_color", DEFAULT_RULE_COLOR)

        text = item["text"].format(**data)

        pdf.set_text_color(*text_color)
        pdf.set_font(font_family, style=style, size=size)
        pdf.multi_cell(
            w=0,
            h=line_height,
            text=text,
            align=align,
            markdown=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)

        rule_color = item.get("rule_color", DEFAULT_RULE_COLOR)
        rule_width = item.get("rule_width", DEFAULT_RULE_WIDTH_PT)
        rule_gap = item.get("rule_gap", 2)

        y = pdf.get_y() + rule_gap
        x1 = pdf.l_margin
        x2 = pdf.w - pdf.r_margin
        pdf.set_draw_color(*rule_color)
        pdf.set_line_width(rule_width)
        pdf.line(x1, y, x2, y)
        pdf.set_y(y + rule_width)

    def _render_banner(
        self,
        pdf: FPDF,
        item: dict[str, Any],
        data: dict[str, Any],
        font_family: str,
        default_size: float,
        default_line_height: float,
    ) -> None:
        """Render a full-width colored banner with name (left) and contact lines (right).

        Designed as the top-of-page header for a resume: the banner spans
        the page edge-to-edge, overriding the page's top margin. The name
        renders bold-left at ``name_size``; ``contact_lines`` render right-
        aligned, one line each, at ``line_size``. Both blocks are
        vertically centered against the taller column.

        After rendering, the cursor sits at ``y = banner_h`` with
        ``x = l_margin`` so the next content item flows below.
        """
        bg_color = item.get("bg_color", DEFAULT_RULE_COLOR)
        text_color = item.get("text_color", (255, 255, 255))
        name = item["name"].format(**data)
        contact_lines = [s.format(**data) for s in item.get("contact_lines", [])]
        name_size = item.get("name_size", 22)
        line_size = item.get("line_size", 9)
        padding_y = item.get("padding_y", 12)

        name_h = name_size * 1.15
        line_h = line_size * 1.25
        contact_block_h = len(contact_lines) * line_h
        banner_h = max(name_h, contact_block_h) + 2 * padding_y

        page_w = pdf.w

        pdf.set_fill_color(*bg_color)
        pdf.rect(x=0, y=0, w=page_w, h=banner_h, style="F")

        pdf.set_text_color(*text_color)

        block_center_y = banner_h / 2
        name_y = block_center_y - name_h / 2
        name_x = pdf.l_margin
        name_w = (page_w / 2) - name_x
        pdf.set_xy(name_x, name_y)
        pdf.set_font(font_family, style="B", size=name_size)
        pdf.cell(w=name_w, h=name_h, text=name, align="L")

        right_x = page_w / 2
        right_w = page_w - right_x - pdf.r_margin
        contact_y = block_center_y - contact_block_h / 2
        pdf.set_font(font_family, style="", size=line_size)
        for i, line in enumerate(contact_lines):
            pdf.set_xy(right_x, contact_y + i * line_h)
            pdf.cell(w=right_w, h=line_h, text=line, align="R")

        pdf.set_text_color(0, 0, 0)
        pdf.set_xy(pdf.l_margin, banner_h)