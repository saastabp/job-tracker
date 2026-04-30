# Slice 05 — Contacts + outreach logging

Status: implemented. Branch: `slice/05-contacts`.

## What landed

- Migration `0003_outreach_metadata.sql`: `outreach_methods` catalog
  (email/linkedin/phone/in_person/other), `outreach_directions` catalog
  (outbound/inbound), `contacts.primary_method_id`, and per-event
  `outreach_method_id` + `outreach_direction_id` (NOT NULL) on
  `contact_outreach`.
- `backend/src/handlers/contacts.py` + `backend/tests/unit/test_contacts.py`
  (21 tests). Routes: list (filter `?kind=`, `?company_id=`), create,
  detail (with outreach timeline), update, soft-delete, log outreach,
  soft-delete an outreach event.
- `ContactsFunction` wired into `infra/api/template.yaml`.
- Dashboard outreach widgets now filter to `direction = 'outbound'` so
  inbound recruiter pings don't inflate the user's outreach targets.
  Test added in `test_dashboard.py`.
- Frontend: `Contacts.tsx`, `ContactDetail.tsx`, `ContactForm.tsx`;
  `App.tsx` routes wired (replaced `ComingSoon`); sidebar nav already
  pointed at `/contacts`.
- Slice-04 follow-up: stripped `AllowMajorVersionUpgrade: true` from
  `infra/data/template.yaml`.

## Post-deploy fixes (same slice, after live testing)

- **`CLIENT_FOUND_ROWS` flag added to `common/db.py`.** Without it,
  pymysql's `rowcount` reports *rows changed*, not *rows matched*. Every
  handler treats `rowcount == 0` on UPDATE as 404, so an UPDATE that
  matched a row but happened to set every column to its existing value
  was misclassified as "row not found." Tripped first when the contacts
  detail form re-posted an unchanged record. Latent in
  companies/submissions/resumes too — fixed once at the connection
  layer.
- **Migration `0004_contacts_phone.sql`.** Added `contacts.phone
  VARCHAR(64) NULL`. Original schema assumed phone numbers could live
  in `notes`; in practice the form expected a structured field. Wired
  through handler + ContactForm + ContactDetail.

## Forks resolved

1. Outreach method: catalog table (`outreach_methods`).
2. `contact_outreach` deletability: soft-delete (`deleted_at`).
3. Per-event metadata: both `outreach_method_id` and
   `outreach_direction_id` landed.

## Out of scope (deferred)

- Submission ↔ contact linking (slice 07 candidate).
- Bulk outreach actions; CSV/vCard import; stale-follow-up reminders.

## Original plan

## Why this slice

The dashboard has personal-outreach and recruiter-outreach target widgets that are stuck on zero because outreach can't be logged. Schema is already in place from slice 01: `contacts`, `contact_kinds` (catalog with `personal` / `recruiter` rows seeded), `contact_outreach`. This slice wires up CRUD + logging + dashboard hookup. Sidebar nav already points at `/contacts → ComingSoon`.

## Scope

**In scope:**
- Contacts CRUD: list, create, detail, update, soft-delete (mirror the resumes/companies pattern).
- Outreach event logging: `POST /contacts/{id}/outreach` records a `contact_outreach` row.
- Frontend: `Contacts.tsx` list (filter by kind), `ContactDetail.tsx` (with outreach timeline + "Log outreach" action), `ContactForm.tsx` modal, sidebar wiring (replace `ComingSoon`).
- Dashboard wiring: the personal/recruiter outreach target widgets get their query backed by `contact_outreach` rows.
- Tests in `backend/tests/unit/test_contacts.py` mirroring `test_resumes.py`'s shape.

**Out of scope (deliberately, candidates for later slices):**
- Submission ↔ contact linking (e.g., "I emailed Jane about the Acme application"). Slice 07 candidate.
- Bulk outreach actions (send templated message to N contacts).
- Contact import (CSV / vCard / LinkedIn export).
- Reminders to follow up with stale contacts.

## Forks the user needs to decide before code starts

1. **Per-contact "primary outreach method"** (email / LinkedIn / phone): catalog table (extensible, new methods are an `INSERT`) or a single string column on `contacts`?
   - Recommend the catalog. The project_extensibility memory says favor catalogs over hardcoded strings.
2. **`contact_outreach` deletability:** soft-delete (the row stays, `deleted_at` set) or hard-delete? Outreach events are factual records; the user might want to undo a fat-finger but probably not edit history.
   - Recommend soft-delete to match every other table; UI shows live rows only.
3. **Outreach event metadata:** what fields beyond `outreach_at` and `notes`?
   - `outreach_method_id` (FK to the catalog from fork 1)?
   - `direction` (outbound / inbound — covers "they reached out to me")?
   - Recommend both, but each adds a column on `contact_outreach` and bumps the form complexity.

## Route shape (proposed; revisit after forks)

| Method | Path | Purpose |
|---|---|---|
| GET | `/contacts` | list (filter `?kind=personal\|recruiter`, `?company_id=N`) |
| POST | `/contacts` | create |
| GET | `/contacts/{id}` | detail + outreach timeline |
| PUT | `/contacts/{id}` | update name/email/linkedin_url/notes/company_id/kind |
| DELETE | `/contacts/{id}` | soft-delete |
| POST | `/contacts/{id}/outreach` | log an outreach event |
| DELETE | `/contacts/{contact_id}/outreach/{id}` | undo a fat-finger (if soft-delete chosen) |

Optional convenience: `GET /outreach?since=YYYY-MM-DD` for the dashboard widget's count query (alternative is the dashboard handler joins `contact_outreach` directly, which it probably should).

## Migration

Likely none — slice 01 already created `contacts`, `contact_kinds`, and `contact_outreach`. Confirm before code that the existing columns cover the chosen forks; if fork 1 (catalog) and fork 3 (`outreach_method_id` / `direction`) land, write `0003_outreach_metadata.sql` with additive columns.

## Test plan

`backend/tests/unit/test_contacts.py`, mirroring `test_resumes.py`:
- list with kind/company filters
- create (kind catalog resolution by short_name)
- detail with outreach timeline (LEFT JOIN `companies`, ORDER BY `outreach_at DESC`)
- update each mutable field
- soft-delete
- outreach POST (success + invalid contact 404)
- outreach DELETE (if applicable)
- error paths: 400 on missing name, 404 on path-id miss, unknown route 404, LookupError → 404 regression

## Frontend pages

- `Contacts.tsx` — table (kind badge, name, company, last outreach, outreach count). Filter by kind. `+ New contact` button.
- `ContactForm.tsx` — modal: kind, name, email, linkedin_url, company picker (reuse the company dropdown pattern from `SubmissionForm`), notes.
- `ContactDetail.tsx` — editable fields, outreach timeline (vertical list with date + method + notes), "Log outreach" inline form, soft-delete.
- Wire `App.tsx` — replace `ComingSoon` at `/contacts` with `Contacts`; add `/contacts/:id`.

## Dashboard wiring

The slice-02 dashboard handler currently returns zero for outreach widgets. After this slice:
- `personal_outreach` count = `contact_outreach` rows for the user joined to contacts where `kind = personal`, scoped to today / this week per the cadence.
- `recruiter_outreach` count = same with `kind = recruiter`.

This is one query change in `backend/src/handlers/dashboard.py` plus a tiny test.

## Starter task list

1. Decide forks 1–3 (user).
2. If migrations needed, write `0003_*.sql`.
3. `backend/src/handlers/contacts.py` + tests.
4. Add `ContactsFunction` to `infra/api/template.yaml`.
5. Frontend pages + sidebar wiring + form.
6. Dashboard handler update + test.
7. README "Project layout" + this doc updated to "implemented" status.
8. End-of-slice: write `docs/slices/06-ai-tailoring.md` plan (slice 06 is the next big one, with the locked-in non-VPC AI architecture).

## Open architectural decisions to honor

- AI architecture for slice 06 is **locked** (see `docs/slices/04-resumes.md` slice-06 section + `architecture_decisions.md` memory): non-VPC AI Lambda, no Bedrock interface endpoint. Don't relitigate.
- MySQL 8.4 LTS upgrade ran during slice 04 deploy. If `AllowMajorVersionUpgrade: true` is still in `infra/data/template.yaml`, strip it as part of this slice's first commit (the slice-04 doc flagged this as a follow-up).