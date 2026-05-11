"""Pydantic schema for the ``resumes.content_json`` column.

The shape captures the structural distinctions the renderer cares about —
header (name + contact line + links), the four side-rail-style flat
sections (areas of expertise, technical proficiencies, certifications),
and the hierarchical experience section (jobs → accomplishments →
bullets).

The schema is the contract between three flows:

* The ``PUT /resumes/{id}/content`` maintenance endpoint validates
  inbound bodies against ``ResumeContent``.
* ``common.resume_template.build_template`` consumes a validated
  ``ResumeContent`` and emits a renderer-ready dict.
* The out-of-band one-time AI conversion of master PDF → JSON targets
  this shape so the result can be PUT directly.

Field bounds are protective ceilings (reject obviously-malformed
input), not editorial limits. The renderer trusts the model after
validation.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResumeHeader(BaseModel):
    """Name, contact line, and inline links shown at the top of the page."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    contact_line: str = Field(default="", max_length=300)
    links: list[str] = Field(default_factory=list, max_length=10)


class ResumeAccomplishment(BaseModel):
    """A named accomplishment under a job (project / initiative / system).

    ``bullets`` may be empty — some accomplishments are a single-paragraph
    intro with no detail bullets, and the renderer must not crash on that.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    intro: str = Field(default="", max_length=2000)
    bullets: list[str] = Field(default_factory=list, max_length=30)


class ResumeJob(BaseModel):
    """A single employment entry under "Professional Experience"."""

    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=100)
    dates: str = Field(default="", max_length=80)
    role: str = Field(default="", max_length=200)
    intro: str = Field(default="", max_length=3000)
    accomplishments: list[ResumeAccomplishment] = Field(default_factory=list)


class ResumeContent(BaseModel):
    """Top-level structured resume body stored in ``resumes.content_json``.

    Examples
    --------
    >>> ResumeContent.model_validate({
    ...     "header": {"name": "Jane Doe", "contact_line": "jane@example.com"},
    ...     "areas_of_expertise": ["Cloud Architecture"],
    ...     "jobs": [{
    ...         "company": "Acme", "role": "Engineer",
    ...         "dates": "2020 - Present", "accomplishments": [],
    ...     }],
    ... })
    """

    model_config = ConfigDict(extra="forbid")

    header: ResumeHeader
    areas_of_expertise: list[str] = Field(default_factory=list, max_length=40)
    technical_proficiencies: list[str] = Field(default_factory=list, max_length=40)
    jobs: list[ResumeJob] = Field(default_factory=list, max_length=30)
    certifications: list[str] = Field(default_factory=list, max_length=30)