-- Slice 13: structured resume body for renderable tailored PDFs.
--
-- Stores a Pydantic-validated JSON object (header, areas_of_expertise,
-- technical_proficiencies, jobs[], certifications) on each resume row.
-- The /resumes/from-tailor endpoint reads this column, plugs the AI-
-- suggested title + summary into a template, and renders a PDF via fpdf2.
--
-- Deliberately separate from `parsed_text` (MEDIUMTEXT raw extraction for
-- AI prompting): one column is structured + render-ready, the other is
-- opaque text. Keeping them split keeps the schema honest.
--
-- Additive, NULL-tolerant. Rows without content_json simply don't expose
-- tailored-PDF generation in the SPA (banner shown on resume detail).

ALTER TABLE resumes
    ADD COLUMN content_json JSON NULL AFTER summary;