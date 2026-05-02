# Slice 08 — Submission ↔ contact linking (PLAN)

Status: planned, not started. Branch: `slice/08-submission-contacts` (TBD).

## Why this slice next

Of the two candidates left after slice 07:

1. **Submission ↔ contact linking** — many-to-many between submissions
   and contacts. Fits inside the `api` stack: junction table, two
   routes, frontend multi-select. ~half a day.
2. **Email pipeline** (`email` stack) — SES inbound, MX records,
   processor Lambda, response classification. Larger; benefits from a
   real domain on Route 53 first.

Picking linking for slice 08 because:

- It's the smaller, lower-risk change — additive in stacks already
  deployed, no new SAM stack to stand up.
- Slice 07 just finished a relatively heavy slice (new stack, SES
  verification, scheduler IAM). Following with a small one keeps the
  cadence balanced.
- It unblocks dashboards / reports that want "who did I talk to about
  this submission?" without leaving the answer in `notes`.
- Email pipeline can land cleanly as slice 09 once a domain is sorted.

## Locked decisions to carry in

- **No new SAM stack** — everything fits in `infra/api/`.
- **Junction table convention** — per `feedback_db_conventions.md`
  junction tables get integer `id` + the two FKs but skip
  `created_at` / `updated_at`. Add a nullable `role` short_name FK
  later if it turns out we need to distinguish recruiter/HM/peer; out
  of scope for slice 08.
- **Catalog-friendly** — no ENUM on the link itself; if we add
  per-link role later, it's a `submission_contact_roles` catalog
  table. Per `project_extensibility.md`.

## Forks (decided 2026-05-01)

1. **API shape: Set.** `PUT /submissions/{id}/contacts` with body
   `{contact_ids: [3, 7, 12]}` replaces the full set. One round-trip
   per save; matches how the SPA form will post. Pair-semantics
   (POST/DELETE per link) rejected.

2. **List both directions: Both.** `GET /submissions/{id}` adds
   `contacts: [...]`; `GET /contacts/{id}` adds `linked_submissions:
   [...]`. Reverse list is one JOIN, no extra round-trip, makes the
   contact detail page useful for outreach planning.

3. **`?submission_id=` filter on contacts list: No.** Personal-use
   pool is small; SPA pulls full list and diffs locally. Avoids a
   special-case query.

## Proposed schema

```sql
CREATE TABLE submission_contacts (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT UNSIGNED NOT NULL,
    contact_id    BIGINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_submission_contacts (submission_id, contact_id),
    CONSTRAINT fk_submission_contacts_submission
        FOREIGN KEY (submission_id) REFERENCES submissions (id) ON DELETE CASCADE,
    CONSTRAINT fk_submission_contacts_contact
        FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Junction-table shape per `feedback_db_conventions`: `id` PK, no
timestamps. `ON DELETE CASCADE` on both sides because a hard-delete
of either parent should sweep the link rows automatically.

## Proposed routes

| Method | Path | Purpose |
|---|---|---|
| PUT | `/submissions/{id}/contacts` | replace the linked contact set |

Plus extending the existing detail responses:

- `GET /submissions/{id}` adds `contacts: [{id, name, email, kind}]`.
- `GET /contacts/{id}` adds `linked_submissions: [{id, role_title,
  company_name, status}]`.

## Test plan

- `test_submissions.py` — extend the detail test to assert the
  `contacts` array, add a test for `PUT /submissions/{id}/contacts`
  (insert + delete diff against existing).
- `test_contacts.py` — extend the detail test to assert
  `linked_submissions`.
- Migration: bring up the new table on RDS, hit the new route from
  the SPA, verify both detail endpoints reflect the link.

## Frontend wiring

- `SubmissionDetail.tsx` — add a "Contacts" card below Status with a
  multiselect (react-bootstrap doesn't ship one; either pull
  `react-select` in if needed, or roll a checkbox list since the
  contact pool is small).
- `ContactDetail.tsx` — add a "Linked submissions" card with a list
  of role + company → `<Link to="/submissions/{id}">`.

## Out of scope (deferred)

- Per-link role (recruiter / HM / peer / panel). Add when the user
  asks for it; until then notes can carry the distinction.
- Email pipeline (slice 09).

## Starter task list

1. Decide forks 1–3 (user).
2. Migration `0006_submission_contacts.sql` — create the junction.
3. `submissions.py` — extend `_detail` to return `contacts`, add
   `PUT /submissions/{id}/contacts` handler. Extend tests.
4. `contacts.py` — extend `_detail` to return `linked_submissions`.
   Extend tests.
5. `infra/api/template.yaml` — register the new PUT route on
   `SubmissionsFunction`.
6. Frontend wiring per "Frontend wiring" section.
7. Manual end-to-end test.
8. End-of-slice: write `docs/slices/09-email.md` plan.
