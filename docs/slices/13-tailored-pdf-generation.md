# Slice 13 — Tailored PDF generation (PLAN)

Status: planned, decisions locked 2026-05-10, not yet started.
Branch: `slice/13-tailored-pdf-generator` (already created).
Depends on slice 11 (`docs/slices/11-week-picker.md`) shipped.

Sequence note: slice 12 (AI email draft) is shelved indefinitely; this
is the next slice after 11.

## Why this slice

Today the AI-tailoring flow stops at suggesting a new title + summary
(slice 06 fork 2 — see `project_ai_tailoring` memory). The user then
pastes those two strings into the master .docx by hand, saves to PDF,
and uploads via the existing presign-PUT flow. The middle steps are
the friction this slice removes.

End-state: the user accepts the AI-suggested title/summary; the system
renders a new PDF from a structured JSON body that already lives in
the DB, inserts a new (non-master) `resumes` row, attaches it to the
submission, and shows a download link. No Word round-trip.

The hard problem here is **not** the render or the S3 plumbing — that
piggybacks on existing `resumes.py`. The hard problem is **where the
structured resume body comes from**, because the master uploaded today
is an opaque PDF that can't be programmatically edited.

## Locked decisions (decided 2026-05-10)

| Decision | Choice | Why |
|---|---|---|
| Body storage | **JSON column on `resumes`** (option B) | User wants resume content out of git; tailored variants reference one structured body. |
| Master upload UX | **Unchanged** | User keeps editing the master in Word; uploads PDF via existing flow. The JSON is a derived artifact, not the editing surface. |
| JSON ingestion | **One-time AI conversion, posted via maintenance endpoint** | User updates the master "a few times a year." Building a robust parse-on-upload pipeline is over-investment; a one-time AI-convert per master version is cheaper. |
| Renderer | **fpdf2 via the existing `pdf_generator.py` from `looch`** | Resume is single-column flow — WeasyPrint's CSS layout earns nothing here. Pure-Python, no system libs, fits in a normal zip Lambda. |
| Fonts | **Noto Sans Regular + Bold, bundled** | Identified from current master via `pdffonts` (`pdffonts "Brian Saastad-resume-eng.pdf"`). ~360 KB total. OpenSymbol bullets replaced with Unicode `•`; C059-Roman page footer rendered in Noto Sans Italic. |
| Tailoring scope | **Unchanged — title + summary only** | `project_ai_tailoring` memory still binds. Work-experience / bullets / skills / dates / employers off-limits to the model. |

## Architecture

```
┌────────────┐                                      ┌──────────────┐
│  master    │ presign-PUT (unchanged)              │  resumes row │
│  PDF in S3 │ ◀───────────────────────────         │  is_master=T │
└────────────┘                                      │  content_json│ ◀── one-time
                                                    └──────┬───────┘     PUT /content
                                                           │
                          ┌────────────────────────────────┘
                          │ read content_json
                          ▼
        ┌──────────────────────────────┐
        │  POST /resumes/from-tailor   │
        │  { base_resume_id,           │
        │    tailored_title,           │
        │    tailored_summary }        │
        └──────────────┬───────────────┘
                       │
                       │ render PDF
                       ▼
                ┌──────────────┐         ┌──────────────┐
                │ pdf_generator│ ──────▶ │ tailored PDF │
                │  (fpdf2)     │  bytes  │  in S3       │
                └──────────────┘         └──────┬───────┘
                                                │
                                                ▼
                                       ┌──────────────────┐
                                       │ new resumes row  │
                                       │ is_master=False  │
                                       │ file_s3_key set  │
                                       └──────────────────┘
```

## Proposed schema

Migration `0009_resume_content_json.sql`:

```sql
ALTER TABLE resumes
    ADD COLUMN content_json JSON NULL AFTER summary;
```

Do **not** repurpose `parsed_text` (MEDIUMTEXT, currently raw-text
extraction for AI prompting). Keeping the columns separate keeps the
schema honest.

`content_json` shape (validated by Pydantic on inbound `/content` calls
and assumed by the renderer):

```json
{
  "header": {
    "name": "Brian Saastad",
    "contact_line": "saastabp@gmail.com • 530-313-8982",
    "links": ["https://www.linkedin.com/in/brian-saastad/"]
  },
  "areas_of_expertise": ["Cloud & Solution Architecture", ...],
  "technical_proficiencies": [
    "AWS (Lambda, API Gateway, S3, ...)",
    "Microservices & REST APIs | Python, Java, C, Perl",
    ...
  ],
  "jobs": [
    {
      "company": "looch",
      "location": "Las Vegas, NV",
      "dates": "2024 – Present",
      "role": "Senior Solutions Architect",
      "intro": "Led the design and implementation of ...",
      "accomplishments": [
        {
          "name": "Financial Data Aggregation API Platform (Plaid/Open Banking)",
          "intro": "Architected an API platform ...",
          "bullets": [
            "Architected and implemented AWS Lambda-based APIs ...",
            ...
          ]
        },
        ...
      ]
    },
    ...
  ],
  "certifications": [
    "AWS Certified: Solutions Architect Associate, ...",
    ...
  ]
}
```

## Proposed routes

| Method | Path | Change |
|---|---|---|
| `PUT` | `/resumes/{id}/content` | **NEW.** Accepts a JSON body matching the Pydantic schema. Stores in `content_json`. 400 on schema fail, 404 on missing/not-owned resume, 200 on save. Maintenance endpoint — no SPA UI for it; user invokes via curl or out-of-band. |
| `POST` | `/resumes/from-tailor` | **NEW.** Body: `{base_resume_id, tailored_title, tailored_summary}`. Reads `content_json` from base resume; 409 if null. Renders PDF via `pdf_generator`. Server-side `s3.put_object` (SSE-AES256). Inserts new resume row with `is_master=False`, `file_s3_key`, `original_filename`. Returns the new resume's detail JSON (reuses `resumes._detail`). |

No changes to existing routes.

## Backend behavior

### `common/pdf_generator.py` (vendor from `looch`)

Copy from `/home/brians/looch/looch-backend-sls/internalServices/entity_tracking/lib/pdf_generator.py` and adapt:

- Drop the S3 font loader (`_download_font_from_s3`, `FONT_S3_PREFIX`). Use bundled `fonts_dir` only.
- Switch from stdlib `logging` to `aws_lambda_powertools.Logger` from `common/logger.py`.
- Convert Google-style docstrings → NumPy style per global preference.
- Extend the template engine with two new content-item types:
  - `row` — left text + right-aligned text on the same baseline (for "Company, City  ⋯  YYYY – YYYY" headers).
  - `section_header` — text + horizontal rule underneath (the blue accent line in the current master).

### `common/resume_template.py` (new)

Python dict template mirroring the current master's layout. Body
sections (jobs, certifications, etc.) are filled at render time from
`content_json`; only `{tailored_title}` and `{tailored_summary}` come
from request input.

### `common/resume_schema.py` (new)

Pydantic model defining `content_json` structure. Validates inbound
`/content` calls. The renderer trusts the model after validation and
assumes all required fields are present.

### `common/fonts/`

Bundle two `.ttf` files:
- `NotoSans-Regular.ttf` (~180 KB Latin-only subset is plenty)
- `NotoSans-Bold.ttf`

SIL OFL. Verify SAM packages non-`.py` files in the Lambda zip — by
default it does, but call it out in test plan.

### `handlers/resumes.py` — extend

Two new route branches in the dispatcher:

- `PUT /resumes/{id}/content` →  `_set_content` (Pydantic-validate body, store).
- `POST /resumes/from-tailor` → `_from_tailor` (read base content_json, render, put_object, insert row, return detail).

Both follow the entry/step/exit logging conventions from `CLAUDE.md`.
The `from-tailor` path's "render PDF" step gets its own log line with
`base_resume_id` and byte count.

### `infra/api/template.yaml`

`ResumesFunction` already exists; the two new routes attach as additional
`Events` entries on that function. The `s3:PutObject` permission for the
resume bucket should already be present from slice 04 — verify in the
slice work, add if missing.

## Frontend wiring

### Submission tailor flow

Current: AI returns title/summary diff → user accepts → SPA writes
tailored fields into the submission row.

New: after diff acceptance, a "Generate tailored PDF" button. On click:

1. `POST /resumes/from-tailor` with `{base_resume_id, tailored_title, tailored_summary}`.
2. `PUT /submissions/{id}` setting `resume_id` to the new row.
3. Show download link (existing presign-GET on the resume detail).

If the base resume's `content_json` is null, the button is disabled
with a "Master not yet parsed — see resume detail" tooltip.

### Resume detail page

Show a banner on master resumes where `content_json IS NULL`:

> Tailoring unavailable — this master hasn't been parsed yet.

(No editor UI for `content_json` — it's populated via the maintenance
endpoint.)

## One-time work (out of band, not in the slice)

For each master resume the user wants tailoring on:

1. Claude converts the master PDF → JSON matching the schema.
2. User `PUT`s it via `/resumes/{id}/content`.
3. Repeat when the master is redesigned (couple times a year).

Step 1 is done in a normal Claude Code session — load the PDF, ask for
JSON, save the output to a file.

## Test plan

### `test_pdf_generator.py` (new)

- Render the resume template with sample data. Assert bytes start with `%PDF-`.
- Parse the output with `pypdf` and grep the tailored title + summary in extracted text.
- Render with a JSON missing required fields — Pydantic validation should fail before render.
- Render with a job whose `accomplishments` list is empty (job-only entry, no bullets) — should not crash.

### `test_resumes.py` extensions

- `PUT /resumes/{id}/content` happy path; body persisted.
- Validation failure → 400.
- Resume belongs to another user → 404.
- `POST /resumes/from-tailor` happy path: new row inserted, S3 put called, returns detail.
- Base resume's `content_json` is null → 409.
- Base resume not owned → 404.
- Oversized title/summary → 400.

### Manual end-to-end

- Convert current master PDF → JSON, PUT to `/content`.
- Tailor a submission; verify the rendered PDF resembles the master visually (name header, section rules, right-aligned dates, bullets).
- Compare master PDF and rendered PDF side-by-side at 100% zoom.

## Deploy ordering

`make -C infra deploy-api` → `make -C infra migrate` → `make -C infra sync-frontend`.

(Per `feedback_migrate_after_deploy_api` memory: the migrate Lambda lives in
the api stack, so new `.sql` files are invisible to it until deploy-api
re-bundles `backend/src/`.)

## Out of scope (deferred)

- **SPA editor for `content_json`.** User edits the master in Word as today;
  JSON is regenerated from the PDF via AI conversion when the master is
  redesigned. A real editor is a future slice if/when the workflow demands it.
- **Tailoring of anything beyond title + summary.** Hard constraint from
  `project_ai_tailoring` memory.
- **Multiple master resumes.** The maintenance endpoint accepts content
  for any resume row, but the tailor flow uses whichever resume the
  submission already points to.
- **Visual templates / multiple looks.** One template, one font. Templating
  the template is a future slice.
- **Automatic re-parsing on upload.** If the user replaces the master PDF,
  `content_json` becomes stale silently. They must remember to re-PUT.
  Acceptable for "a few updates a year" cadence; add a UI banner if it
  becomes a problem.

## Starter task list

1. Migration `0009_resume_content_json.sql` (`ALTER TABLE resumes ADD content_json JSON NULL`).
2. Vendor `pdf_generator.py` into `backend/src/common/`; adapt (drop S3 font path, powertools logger, NumPy docstrings, add `row` and `section_header` content-item types).
3. Bundle Noto Sans Regular + Bold under `backend/src/common/fonts/`.
4. Define `common/resume_schema.py` (Pydantic) and `common/resume_template.py` (dict template referencing schema fields).
5. Extend `handlers/resumes.py`: `_set_content` + `_from_tailor`, route dispatch in `handler`.
6. Wire new routes in `infra/api/template.yaml` (`PUT /resumes/{id}/content`, `POST /resumes/from-tailor`).
7. Tests: `test_pdf_generator.py` (new), extend `test_resumes.py`.
8. Frontend: "Generate tailored PDF" button in submission tailor flow; "not parsed yet" banner on resume detail.
9. Deploy: `make -C infra deploy-api && make -C infra migrate && make -C infra sync-frontend`.
10. Out-of-band: convert the current master PDF → JSON, PUT via maintenance endpoint, smoke-test tailoring.
11. Manual end-to-end visual compare.
12. End-of-slice: write `docs/slices/14-*.md` plan (slice 14 TBD — no successor planned at this point).

## Resumption notes

- Branch `slice/13-tailored-pdf-generator` was created during slice 11
  planning; check it out and rebase onto develop after slice 11 merges.
- The conversation that produced this plan walked through three rejected
  paths: option A (body in code) and option C (normalized tables) — see
  `project_ai_tailoring` memory's "PDF generation — scoping discussion"
  section for the rationale.
- `pdf_generator.py` in the `looch` project is the source of truth for the
  renderer. Confirm the file still exists at the path above before vendoring.
- Font choice was driven by `pdffonts` output on the current master, not
  guessed. Run `pdffonts "Brian Saastad-resume-eng.pdf"` again before
  bundling to confirm nothing has changed in the master file since 2026-05-09.
- The one-time AI conversion of the master PDF → JSON is the soft step
  that most needs validation: the rendered output's fidelity to the
  visual master is entirely a function of how cleanly that JSON captures
  the structural distinctions the renderer cares about.