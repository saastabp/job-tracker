# Slice 04 — Resumes

Status: implemented on branch `slice/04-resumes`, ready to deploy.

## What landed

### Backend

- **Migration `0002_resumes_filename.sql`** — adds `original_filename VARCHAR(255) NULL` to the existing `resumes` table (the table itself was created in slice 01).
- **`backend/src/handlers/resumes.py`** — new dispatcher Lambda with eight routes:
  - `GET    /resumes` — list (filterable by `?include_deleted=true`)
  - `POST   /resumes` — create (text only; PDF uploads via the upload-url route)
  - `GET    /resumes/{id}` — detail with transient `download_url` if a file is attached + linked submissions
  - `PUT    /resumes/{id}` — update title / summary / is_master
  - `DELETE /resumes/{id}` — soft-delete (sets `deleted_at`, clears `is_master`)
  - `POST   /resumes/{id}/restore` — undo a soft-delete
  - `POST   /resumes/{id}/purge` — hard-delete (S3 file + DB row); only allowed on already soft-deleted rows. Submissions referencing the row get `resume_id = NULL` via the existing FK `ON DELETE SET NULL`
  - `POST   /resumes/{id}/upload-url` — returns a 5-minute presigned PUT URL
- **Browser-direct uploads via presigned PUT.** PDF only, 5 MB cap. Bytes never traverse Lambda. The handler optimistically records `file_s3_key` + `original_filename` before the browser PUT lands, mirroring the JD-snapshot graceful-miss pattern.
- **Master-resume invariant.** "At most one master per user" enforced in handler logic, in-transaction. First resume a user creates is auto-master.
- **Submissions handler updated** to LEFT JOIN `resumes` (with `r.deleted_at IS NULL`), surfacing `resume_title` on `/submissions/{id}`. The `_update` path also accepts `jd_text` — non-empty upserts via `_store_jd`, empty soft-deletes the snapshot row.
- **Tests:** `backend/tests/unit/test_resumes.py` (26 cases) + 2 new `test_submissions.py` cases for the JD-text edit path. Whole suite: **66 passing**.

### Infra

- `infra/data/template.yaml` — adds `CorsConfiguration` to the resume bucket (`AllowedOrigins: ['*']`; the bucket stays private — auth is the time-limited presigned-URL signature).
- `infra/api/template.yaml` — `ResumesFunction` modeled on `SubmissionsFunction`, VPC + RDS-IAM + `s3:PutObject / GetObject / DeleteObject` on `${ResumeBucketArn}/users/*`. Eight HttpApi event mappings (matches the eight routes).
- `infra/Makefile` — `sync-frontend` now waits on `aws cloudfront wait invalidation-completed` so the recipe blocks until the cache flush is actually live.

### Frontend

- **New shared component** `frontend/src/components/PdfDropZone.tsx` — click-or-drop file picker with synchronous PDF/size validation. Used by both modals.
- **`Resumes.tsx`** — list with "Show deleted" toggle. Deleted rows render at 55% opacity; row click is suppressed; inline `Restore` and `Delete forever` buttons (the latter has a hard-confirm dialog).
- **`ResumeForm.tsx`** (create modal) — title + summary + `is_master` + optional PDF via the drop zone. On submit: creates the row, then chains upload-url + S3 PUT before navigating to detail. (Auto-prefilling the title/summary from the PDF was attempted with regex heuristics during slice 04 and pulled back out — see "Deferred to slice 06" below.)
- **`ResumeUploadModal.tsx`** — drop zone + Browse + progress states; used from `ResumeDetail` for upload/replace.
- **`ResumeDetail.tsx`** — editable title/summary, master toggle, file section (download link + replace), inline PDF iframe preview when a file is attached, soft-delete button (the confirm dialog points at "Show deleted" on the list as the path to restore or permanent delete), linked-submissions list.
- **`SubmissionForm.tsx`** — adds a Resume `<Form.Select>` defaulted to the user's master.
- **`SubmissionDetail.tsx`** — multiple changes:
  - Click-to-edit role title (Enter/blur saves, Escape cancels).
  - Submitted-on is now an editable `type="date"` input.
  - "JD URL" renamed to "Link to Job Description" and is editable.
  - "JD text" renamed to "Job Description"; an editable textarea (collapse/expand if a snapshot exists, auto-expand if not). Empty + Save clears the snapshot.
  - Resume picker on the detail page mirrors the form's; opens the linked resume in one click.

## Decisions made (not in code comments)

- **PDF only.** DOCX deferred until AI tailoring needs it.
- **Parsing into `parsed_text` deferred** to slice 06 (AI). The user-typed `summary` field is the AI input the tailoring memory commits to.
- **Master uniqueness via handler transaction**, not DB constraint. MySQL has no partial unique index; the generated-column workaround is obscure. Handler logic matches the codebase idiom (see `_find_or_create_company` in submissions).
- **Resume bucket CORS uses `AllowedOrigins: ['*']`.** The bucket is private (BlockPublicPolicy etc. on); CORS is just a browser preflight gate, real auth is the IAM-signed presigned URL with a 5-min TTL. Keeps the data stack from coupling to the frontend stack's CloudFront URL.
- **Soft-delete by default; purge only after soft-delete.** Two-step destruction prevents fat-fingered loss. Purge is best-effort on S3 (a missing object is logged, not fatal) but always commits the row delete.
- **`_detail` no longer filters `deleted_at IS NULL`.** Needed so the Restore flow can return the row right after restoring; live-only filtering happens in `_list`.
- **`POST` for restore/purge** (not `PUT`/`DELETE`). They're state-transition actions, not idempotent writes against a resource representation; `POST /{id}/{verb}` matches the codebase's emerging idiom (`/upload-url`).
- **Optimistic `file_s3_key` write.** `POST /resumes/{id}/upload-url` records the key+filename before the browser PUT completes. If the PUT fails, the row points at a missing object; the GET path's `try/except` returns `download_url=None`.

## Deploy + handoff steps

```sh
make -C infra deploy-data        # adds CORS to resume bucket + bumps MySQL 8.0 -> 8.4
make -C infra deploy-api         # adds ResumesFunction + new submissions handler logic
make -C infra migrate            # applies 0002_resumes_filename.sql (REQUIRED before SPA goes live)
make -C infra sync-frontend      # builds + uploads + waits for CloudFront invalidation
```

**`deploy-data` notes** — two changes ride together this round:
- Resume bucket gains a CORS rule (additive, no replacement).
- RDS engine version bumps from MySQL 8.0 to 8.4 LTS. AWS classifies this as a major upgrade so `AllowMajorVersionUpgrade: true` is set in the template. The DB will be unavailable for ~10–20 min while RDS runs the in-place upgrade. Single-AZ — no failover.

`make migrate` is non-optional this round: the new upload flow writes `original_filename`, which only exists after `0002_*.sql` runs.

### Post-upgrade follow-up (one commit, after `deploy-data` succeeds)

Remove `AllowMajorVersionUpgrade: true` (and its comment) from `infra/data/template.yaml`, then `make deploy-data` again — it's a no-op since `EngineVersion` won't be changing, but it strips the footgun so the next major-version bump requires re-adding the flag explicitly. Tracked as a TODO in the template itself.

## What's NOT in this slice (intentional)

- PDF parsing (text extraction → `parsed_text`)
- DOCX uploads
- Inline "create new resume from submission form" (the picker is "select existing" only)
- Multi-version history per resume in the UI (S3 versioning is on; we don't surface it)
- Bulk import / drag-drop multi-file
- Filtering resume list by has-file
- Server-side enforcement that a soft-deleted resume can't be set as a submission's `resume_id` (the UI naturally avoids this; if a future API client wires it up, we can add a 400)

## Deferred to slice 06: PDF → title/summary auto-prefill

We tried regex/heuristic mining of the uploaded PDF (lazy-loaded `pdfjs-dist` + a `components/pdfExtract.ts` module). On real-world resumes it failed about half the time:

- **Title:** when a resume has both a tagline-style headline below the candidate's name AND role keywords inside an experience section, pdf.js's text extraction order isn't always visual reading order, so the regex picked the wrong one (e.g., grabbing "Senior Solutions Architect" from the first job entry instead of the "Enterprise Solutions Architecture | Hands-On Engineering" tagline).
- **Summary:** modern resume templates routinely skip the literal "Summary" / "Profile" heading and open straight into a paragraph after the tagline. Without an anchor heading the regex returns null.

50%-accurate confident auto-fill is worse than no auto-fill — it makes users delete wrong content rather than type fresh. Full discussion is in the conversation; the heuristic code (`pdfExtract.ts`, `pdfjs-dist` dep, `mining`/`autofilled` state in `ResumeForm`) was reverted. Deploy artifacts are clean.

## Suggested next slice

**Slice 05 candidate: contacts CRUD + outreach logging.** The `contacts`, `contact_kinds`, and `contact_outreach` tables are already in the slice-01 schema (with `personal` / `recruiter` catalog rows seeded). The dashboard's outreach-target widgets will start moving off zero once outreach can be logged. Sidebar nav already points at `/contacts → ComingSoon`.

To resume next session: `Read docs/slices/05-contacts.md and go.` (Or, if no plan file yet: read this doc + `feedback_slice_handoff.md`, then plan slice 05.)

## Slice 06 — AI integration: architectural decisions to lock in

These came up while planning slice 04 and need to be written down so slice 06 doesn't relitigate them. Both override defaults that the original `architecture_decisions.md` memory implied.

1. **AI Lambda runs OUTSIDE the VPC.** It receives all inputs (master title + summary + JD text) in the request body — it doesn't need DB access. This avoids the ~$15/mo Bedrock interface VPC endpoint cost while keeping IAM auth and the Bedrock-over-direct-API demo angle intact. The VPC-endpoint-per-AI-feature reasoning in `architecture_decisions.md` was a default; for stateless AI calls it doesn't apply.
   - *Alternative considered and rejected:* spin up the VPC endpoint on app load, tear down on exit. Endpoint creation takes 1–3 min, deletion 30 s — too slow for an SPA load event, and concurrency/idle tracking turns the savings into ops complexity.
   - *Future caveat:* if a later AI feature needs to read from the DB inside the AI call (e.g., "summarize my whole submissions history with one prompt"), use a VPC orchestrator Lambda to fetch the data, then invoke the non-VPC AI Lambda with the prepared inputs. Keeps the AI Lambda VPC-free.

2. **AI-mining of resume PDFs is AI-primary, not a heuristic-first fallback.** The slice-04 attempt at regex-based extraction failed on common resume layouts (~50% accuracy — see "Deferred to slice 06" above). The slice 06 plan:
   - Bring `pdfjs-dist` back as a frontend dep. Use it to extract the PDF text client-side and POST that text (not the binary) to a new `/ai/mine-resume` endpoint.
   - The AI Lambda runs Haiku 4.5 with a tight prompt: "Given this resume text, extract the candidate's role headline (the tagline below their name, NOT a job title from experience) and their professional summary paragraph. Return JSON `{title, summary}` with nulls if you can't find either."
   - Wire the result into the create modal's title/summary fields with the "auto-filled — edit if needed" badge UX that we built and reverted in slice 04 (lift the pattern from git history).
   - Cost: ~$0.0001 per call. Fires once per resume create. Negligible.
   - Heuristics as backup are NOT worth keeping — the original failure cases are exactly what AI handles cleanly, and a regex baseline would just reintroduce the wrong-answer-confidently failure mode.