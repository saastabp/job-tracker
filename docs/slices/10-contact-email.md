# Slice 10 — Contact-initiated email + unified compose (SHIPPED)

Status: shipped and verified end-to-end (2026-05-05). Branch: `slice/10-contact-email`.

## Post-ship add-ons (2026-05-06)

### Submission delete

Real use case: user started logging a submission for a remote posting,
went to apply, and the posting had been pulled by the employer. The app
had no way to remove the row.

Added a `DELETE /submissions/{id}` route + a Delete button on the
submission detail page. Soft-deletes the submission row and cascades
soft-deletes to `follow_ups`, `responses`, and `jd_snapshots`;
hard-deletes `submission_contacts` junction rows; cancels EventBridge
schedules for any auto-created follow-ups via `cancel_followup`. No
schema change — every table involved already had `deleted_at` (or is a
junction table by convention).

### Standalone company create

Companies were previously only creatable as a side-effect of the
inline `company_name` on a submission, which meant a contact couldn't
be associated with a company until an application had been logged
for it. Added:

- `frontend/src/components/NewCompanyModal.tsx` — small reusable modal
  (name + notes) that POSTs to the existing `/companies` endpoint.
- "New company" button on the Companies index page.
- "+ Add new company…" sentinel option in the Company `<select>` on
  both `ContactForm` (new contact) and `ContactDetail` (existing
  contact). Selecting it opens the modal; on success the new company
  is spliced into the local options list and auto-selected.

No backend or schema changes — `POST /companies` already existed.

### HTML-only email body extraction

Importing a Thunderbird-originated thread (recruiter sent through an
ATS that emits HTML-only, no plaintext sibling) stored a `body_text`
that included the entire `<style>` block — hundreds of lines of
`@import url(...)` and `.atsEmail{...}` CSS. Root cause: the
fallback path in `gmail_poller._extract_body_text` ran
`re.sub(r"<[^>]+>", " ", html)`, which only removes angle-bracket
tags and leaves the contents of `<style>`/`<script>` blocks intact.

Fixed by introducing `_html_to_text(html)`, which:

1. Drops `<style>`/`<script>` blocks **with their contents** before any
   tag-stripping.
2. Replaces block-level tags (`<p>`, `<br>`, `<div>`, `<li>`, `<tr>`,
   headings) with newlines so paragraph structure survives.
3. Decodes HTML entities (`&nbsp;` / `&amp;` / `&#39;` / ...) via
   `html.unescape`.
4. Collapses runs of horizontal whitespace while preserving newlines.

Two regression tests in `test_gmail_poller.py` cover the `<style>`
case and entity decoding.

Already-stored polluted rows aren't backfilled automatically; users
can delete the affected `contact_outreach` row and re-import via the
Message-ID paste form to refresh.

### Soft-delete + re-import revival

After implementing the HTML-only body fix above, the user deleted the
polluted `contact_outreach` row and re-imported via the Message-ID
paste form, expecting to see the freshly-parsed body. Instead the
import returned `imported 0 (1 already on file)`.

Cause: the unique key `uq_contact_outreach_gmail_message` (and its
sibling `uq_responses_gmail_message`) is a raw MySQL `UNIQUE` on
`gmail_message_id` — it does not filter on `deleted_at`. A soft-deleted
row keeps its message id, so `INSERT IGNORE` collides and the handler
silently counts `skipped:exists`.

Fix: replaced `INSERT IGNORE` with `INSERT … ON DUPLICATE KEY UPDATE`
in both `_insert_contact_outreach_row` and `_insert_response_row`.
The UPDATE clause:

- Sets `deleted_at = NULL` (revives the soft-deleted row).
- Refreshes the parsed-from-email columns (`subject`, `body_text`,
  `from_email`, `outreach_at`, `gmail_thread_id`, plus
  `raw_email_s3_key` and `response_classification_id` for responses).
- For `contact_outreach`, **re-attaches the row to the importing
  contact** via `contact_id = VALUES(contact_id)`. The user clicking
  Import on a contact's page is an authoritative override of the
  poller's "most-recent contact" Fork 1 heuristic. Without this, a
  row first auto-attached by the poller to contact A would silently
  stay on A even after the user explicitly re-imports on contact B,
  and would never appear in B's timeline.
- `user_id` is left out of the UPDATE list (cross-user security
  boundary). For `_insert_response_row`, `submission_id` is also left
  out — that helper is called in a loop over `submission_ids` for
  threads linked to multiple submissions, and including it would
  cause the row to bounce on the second iteration.

Counter semantics: `rowcount == 1` (true insert) and `rowcount == 2`
(MySQL convention for duplicate-key UPDATE that touched a row) both
count as `"inserted"` — revival is what the user expects when they
re-import. `rowcount == 0` (live row, identical content) keeps
`"skipped:exists"`. For responses specifically, the status-bump
UPDATE only fires on a true insert (rowcount 1) — re-imports must not
re-bump a status the user may have manually adjusted since.

Two regression tests added in `test_gmail_poller.py`:
`test_revives_soft_deleted_row_on_duplicate_key` on each of
`TestInsertResponseRow` and `TestInsertContactOutreachRow`.

### Deploy

`make deploy-api && make sync-frontend` (no migration).

## What actually landed

Beyond the locked plan below, the following were folded in mid-slice:

- **`/submissions` list now projects `gmail_thread_id`** — needed by
  the modal's submission picker to filter out already-linked submissions.
  Small additive change to `submissions._row_to_summary`.
- **Slice-9 resumes-picker bug fix** — `GmailComposeModal` was reading
  the resumes response as `{items: [...]}` but the handler returns a
  bare array, so the picker was silently empty. One-liner fix.
- **Sync gmail-thread import for contacts** —
  `PUT /contacts/{id}/gmail-link`. Paste a Message-ID from any thread
  message; the server resolves it via Gmail search, fetches the whole
  thread, and dual-writes `contact_outreach` rows immediately (calls
  the poller's `process_thread_messages` helper with explicit anchors
  and `archive_bucket=""` to skip S3 — that Lambda lacks the gmail
  bucket policy and parsed subject/body still land on the rows). New
  card on `ContactDetail`.
- **Reply-from-contact (originally a 10.5 candidate)** — lifted into
  this slice because the user hit it the moment they imported a
  contact-only thread. Reply mode now resolves the thread anchor from
  `submissions.gmail_thread_id` OR (when only `contact_id` is set)
  the contact's most-recent `contact_outreach.gmail_thread_id`. Reply
  buttons added to email-sourced rows on `ContactDetail`.
- **Dashboard inbound outreach counters** — surfaced inbound
  `contact_outreach` rows alongside the goal-tracked outbound
  counters. Added `inbound_today` / `inbound_week` to the dashboard
  metric shape and a small "↓ N inbound" line on each outreach
  ProgressTile. Goal mechanics (denominator) unchanged. The dashboard
  outreach query no longer filters direction in SQL.

## Deferred (deliberately, not regressions)

- **Week picker** for dashboard/outreach history — see slice 11 plan.
- **AI-tailored email body at compose time** — see slice 12 plan.
- **Multi-contact thread granular tracking** — when N contacts share
  a thread, current behavior is "most-recent contact_outreach row's
  contact_id wins for inbound" (Fork 1 locked). A junction table for
  N-contacts-per-message is the next step if requested.
- **Bulk send** / **template / signature support** / **inbound
  classification on contact_outreach rows**. None requested.

## Locked fork decisions (2026-05-05)

- **Fork 1**: most-recent contact only. Inbound row picks the contact_id of
  the thread's most-recent existing `contact_outreach` row. Unique key on
  `gmail_message_id` stays globally unique.
- **Fork 2**: implicit-yes via `threads.get`. No new code in the link
  handler; the next poll cycle backfills `responses` retroactively.
- **Fork 3**: same row format expanded. One chronological list, direction
  icon, email rows show subject + click-to-expand body, manual rows show
  notes.
- **Fork 4**: reject 400 when neither `submission_id` nor `contact_id` set.
- **Fork 5**: open compose modal with empty To when contact lacks email.
Depends on slice 9 (`docs/slices/09-email.md`) shipped, deployed, and
verified end-to-end (it is — `gmail_credentials` populated, OAuth +
poll + compose + reply all working through the SPA against real
Gmail).

## Why this slice

Slice 9's compose flow is anchored on submissions: the user clicks
**Compose** on a submission detail page, and the resulting email
auto-links that submission to a Gmail thread. That works for the
"I'm applying for a job" flow, but leaves a real gap: cold-emailing
a contact who isn't (yet) tied to a specific submission.

Three real workflows the user wants:

1. **Contact-only outreach.** "I want to reach out to this recruiter
   about general opportunities, no specific role yet." Email goes
   from the SPA, replies get tracked, conversation is visible from
   the contact detail page. If a real opportunity emerges, the user
   can later create a submission and link the thread retroactively.

2. **Submission-bound contact-aware send.** "I'm applying via this
   recruiter for a specific role." Email goes out tied to a
   submission AND logged against the contact. Replies show up in
   both submission detail and contact detail.

3. **Submission-bound, contact unspecified** (existing slice-9 path).
   "I'm applying directly without going through a known recruiter."
   Email goes out tied only to the submission.

User-stated principle (locked):

> The user should be able to initiate an email from a contact page
> or from a submission page. The default linkage of the message
> (linked to submission or not) associates with its source, but can
> be changed in both cases. The email component should NOT be
> duplicated — both routes should land in the same place.

> If the user is contacting someone from within the app, it is
> reasonable to assume they want to track/follow the interaction.
> Otherwise, just use out-of-app email/contact methods.

That second principle is what makes contact-only thread polling a
yes, not a no. Slice 9's poller only watches `submissions.gmail_
thread_id`; slice 10 expands it to also watch
`contact_outreach.gmail_thread_id`.

## Locked decisions to carry in

- **Single shared modal**, two launch points (submission detail +
  contact detail). The component lives in
  `frontend/src/components/GmailComposeModal.tsx` (already created in
  slice 9). Defaults change based on the launch point but the
  component itself is unchanged.
- **Single backend route** `POST /messages/send` replaces slice 9's
  `POST /submissions/{id}/send`. Body carries `submission_id?` and
  `contact_id?`; at least one is required. Both can be set, in which
  case both writes happen.
- **Polling extends to contact-only threads.** Per-cycle thread
  enumeration is the union of `submissions.gmail_thread_id` and
  `contact_outreach.gmail_thread_id`. Inbound messages get
  dual-written when the thread maps to both a submission and a
  contact.
- **`contact_outreach` becomes the home for email events** (both
  outbound and inbound) when the source was the SPA's compose flow.
  Manual outreach logging (existing UX) continues to work
  unchanged — the new email-specific columns are nullable and stay
  NULL on manually-logged rows.
- **Plaintext body only** — same as slice 9. No rich text.
- **Send-immediate** — no Drafts. Same as slice 9.
- **Subject + body stored on the contact_outreach row.** Lets the
  contact detail timeline render the conversation without joining
  back to anything else.
- **Self-sent filter still applies.** User's outbound (which is
  itself a `contact_outreach` row from the send handler) doesn't
  *also* get re-inserted by the poller when the thread fetch returns
  it.

## Forks the user needs to decide

### Fork 1 — Multi-contact thread inbound handling

A thread can be linked to multiple contacts if, e.g., the user
emails a recruiter and CCs another contact, or sends two outbound
messages on different days to two contacts that wound up in the same
thread. When an inbound reply arrives, where does the
`contact_outreach` inbound row go?

- *All contacts* — one inbound row per contact_id present in the
  thread's existing `contact_outreach` rows. Multiple rows for the
  same Gmail message → no idempotency conflict because
  `uq_contact_outreach_gmail_message` is on `gmail_message_id` alone.
  Wait — actually the unique key would block this. Need to either
  scope the unique key to `(contact_id, gmail_message_id)` OR pick
  one contact.
- *Most-recent contact only* — the thread's most-recent
  `contact_outreach` row's `contact_id` wins. Inbound row gets that
  contact_id only.
- **Recommend Most-recent contact only**, with `(gmail_message_id)`
  globally unique. Reasons: simpler schema (no compound key),
  matches user mental model ("I last spoke to recruiter A about this
  thread, so the reply is from recruiter A"), and the rare case of
  multiple contacts on one thread is usually a CC scenario where the
  primary correspondent is the most-recent sender anyway. If the
  user really has co-occurring contacts on a thread, they can
  manually add a second `contact_outreach` row from the contact UI
  (existing log-outreach affordance).

### Fork 2 — Retroactive submission linking on a contact-only thread

User emails a contact with no submission link. Poller writes inbound
replies to `contact_outreach`. Later, user creates a submission and
links the thread (via the Gmail-link paste field). Should the
existing inbound rows in `contact_outreach` be back-filled into
`responses`?

- *Yes, retroactive* — when the gmail-link handler sets a
  submission's `gmail_thread_id`, also enumerate the historical
  messages in that thread and INSERT IGNORE them into `responses`.
  Idempotent because of `uq_responses_gmail_message`.
- *No, cutoff* — only messages arriving *after* the submission link
  populate `responses`. Pre-link replies stay only on
  `contact_outreach`. User has to navigate to contact detail to see
  the early conversation.
- *Implicit-yes via threads.get* — the existing poller fetches all
  messages in a thread on every cycle (cheap because of INSERT
  IGNORE). So once a submission is linked, the very next poll cycle
  retroactively populates `responses` for that thread without any
  extra code. This is what already happens.
- **Recommend Implicit-yes via threads.get** — it falls out of the
  existing poller design for free. No code change needed in the
  link handler; the next poll cycle does the work. Up to a 10-minute
  delay between linking and seeing the replies populate, which is
  acceptable.

### Fork 3 — ContactDetail outreach timeline display

Existing timeline filters to `direction=outbound` and renders `notes`
in a row per outreach event. With email-sourced rows now carrying
subject/body/from_email/etc, two display options:

- *Same row format, expanded.* Each row shows the subject (when
  present) on the headline, with a click-to-expand for body content
  matching the SubmissionDetail responses pattern. Manually-logged
  rows (notes only, no subject/body) render as before.
  Inbound vs outbound distinguished by an arrow icon or color.
- *Separate sections.* "Email conversation" up top with a threaded
  view of subject/body inbound + outbound, "Manually logged" below
  with the existing format.
- **Recommend Same row format, expanded.** Cleaner visually, lets
  the user see manual-logged and email-sourced events in chronological
  order, matches the responses-card pattern they already know.
  Direction indicated by an arrow icon (↑ outbound, ↓ inbound) or a
  badge color. Manual rows render notes; email rows render
  subject + expand-for-body.

### Fork 4 — Send-without-anchor edge case

What if the SPA sends `POST /messages/send` with neither
`submission_id` nor `contact_id`? Two takes:

- *Reject 400.* "Tracking required — at least one anchor field
  must be set." Forces the user to either pick a submission, pick a
  contact, or use Gmail directly.
- *Allow as untracked send.* The handler builds and sends but
  doesn't log anywhere. Effectively a "thin Gmail wrapper" with the
  attachment / scope-gate path.
- **Recommend Reject 400.** Per the user's principle: "If the user
  is contacting someone from within the app, it is reasonable to
  assume they want to track/follow the interaction. Otherwise, just
  use out-of-app email/contact methods." An untracked send through
  the SPA serves no purpose Gmail can't serve better.

### Fork 5 — Empty contact email at compose time

A contact's `email` column is nullable. If the user opens Compose
from a contact whose email is missing, what?

- *Open with empty To, user types.* Allows ad-hoc recipient entry
  even when the contact lacks a stored email (e.g., the user knows
  the email from elsewhere but hasn't updated the contact record).
- *Block until contact has email.* Modal won't open; show "add an
  email to this contact first" message.
- **Recommend Open with empty To.** Less friction; the user might
  have the address in clipboard or know it. The compose handler
  already validates that `to[]` is non-empty, so a forgetful user
  who hits Send without typing gets a clear 400.

## Proposed schema

Migration `0008_contact_outreach_gmail.sql`:

```sql
-- Slice 10: extend contact_outreach to track email-sourced events.
--
-- Five additive nullable columns. Manually-logged rows (existing UX)
-- keep working with all five NULL. Email-sourced rows (created by
-- the slice 10 compose handler on outbound, by the poller on inbound)
-- populate the columns with content fetched from Gmail.
--
-- The unique key on gmail_message_id is the idempotency primitive
-- mirroring `uq_responses_gmail_message`. Together they ensure no
-- duplicate inserts during poller retries OR when a thread maps to
-- both a submission and a contact (each table has its own unique
-- key, so a single Gmail message can produce one row in each).

ALTER TABLE contact_outreach
    ADD COLUMN gmail_thread_id  VARCHAR(64)  NULL,
    ADD COLUMN gmail_message_id VARCHAR(64)  NULL,
    ADD COLUMN subject          VARCHAR(998) NULL,
    ADD COLUMN body_text        MEDIUMTEXT   NULL,
    ADD COLUMN from_email       VARCHAR(320) NULL,
    ADD UNIQUE KEY uq_contact_outreach_gmail_message (gmail_message_id),
    ADD KEY ix_contact_outreach_gmail_thread (gmail_thread_id);
```

No changes to `responses`, `submissions`, or `gmail_credentials` —
slice 10 builds on the existing slice 9 shape.

## Proposed routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/messages/send` | **NEW**, replaces `/submissions/{id}/send`. Body: `{ to: str[], cc?: str[], bcc?: str[], subject, body, resume_id?, in_reply_to_message_id?, submission_id?: int | null, contact_id?: int | null }`. At least one of `submission_id` / `contact_id` required. Returns `{ thread_id, gmail_message_id }`. |
| `POST` | `/submissions/{id}/send` | **REMOVED** in this slice. Frontend updated to use `/messages/send` in the same deploy. |

All other slice-9 routes (`/integrations/gmail/*`,
`/submissions/{id}/gmail-link`, etc.) unchanged.

## Backend behavior

### Compose handler (`backend/src/handlers/gmail_compose.py`)

Refactor the existing handler:

1. Accept route_key `POST /messages/send`. Drop the
   `/submissions/{id}/send` dispatch.
2. Body validation:
   - `to[]` non-empty (existing)
   - `subject` non-empty (existing)
   - `body` non-empty (existing)
   - At least one of `submission_id` / `contact_id` present (new — 400 if
     both null, per fork 4)
3. Resolve user, decrypt token, build credentials + service (existing).
4. Verify `gmail.send` scope (existing).
5. Look up the linked entities, ownership-check both:
   - If `submission_id` provided: SELECT id, gmail_thread_id FROM
     submissions WHERE id = ? AND user_id = ? AND deleted_at IS NULL.
     404 on miss.
   - If `contact_id` provided: SELECT id, email FROM contacts WHERE
     id = ? AND user_id = ? AND deleted_at IS NULL. 404 on miss.
6. Mode determination + threading (existing slice-9 logic, but now
   conditional on `submission_id`):
   - Reply mode (`in_reply_to_message_id` present) requires
     `submission_id` AND that submission to have `gmail_thread_id`.
     Reply against a contact-only thread isn't supported in v1
     (see Out of scope) — the user replies via the contact's
     timeline only after slice 10.5 adds it.
   - Compose mode: if `submission_id` present and submission
     already has `gmail_thread_id`, error 400 ("submission already
     linked"). Same as slice 9.
7. Optional resume attachment fetch from S3 (existing).
8. Build MIME, send via Gmail (existing).
9. **Persistence (the new bit)** — within a single DB transaction:
   - If `submission_id` present and submission had no
     `gmail_thread_id`: write the returned threadId.
   - If `contact_id` present: INSERT a `contact_outreach` row with
     `direction_id = (outbound)`,
     `outreach_method_id = (email)`,
     `outreach_at = NOW()`,
     `subject`, `body_text` (the body the user typed),
     `from_email = gmail_credentials.gmail_address`,
     `gmail_thread_id`, `gmail_message_id` (= the sent message's id).
   Both writes land or both roll back.
10. Return `{ thread_id, gmail_message_id }` (existing).

Helper extraction: the MIME build + Gmail send chunk should be a
shared `_send_via_gmail()` to keep the handler readable. The
post-send persistence is what differs by mode.

### Poller (`backend/src/handlers/gmail_poller.py`)

Two changes:

1. **Dual thread enumeration.** Replace the existing query that
   enumerates `submissions.gmail_thread_id` with a UNION:

   ```sql
   SELECT DISTINCT thread_id, NULL AS submission_id, NULL AS contact_id
   FROM (
       SELECT gmail_thread_id AS thread_id FROM submissions
        WHERE user_id = ? AND deleted_at IS NULL
          AND gmail_thread_id IS NOT NULL
       UNION
       SELECT gmail_thread_id AS thread_id FROM contact_outreach
        WHERE user_id = ? AND deleted_at IS NULL
          AND gmail_thread_id IS NOT NULL
   ) t
   ```

   For each unique thread_id, fetch via `users.threads.get(format=
   'full')` once.

2. **Dual-write per inbound message.** For each non-self-sent
   message in a thread:

   ```python
   # Find what this thread maps to.
   submission_ids = SELECT id FROM submissions
                    WHERE user_id = ? AND gmail_thread_id = ?
                      AND deleted_at IS NULL
   contact_outreach_existing = SELECT contact_id FROM contact_outreach
                               WHERE user_id = ? AND gmail_thread_id = ?
                                 AND deleted_at IS NULL
                               ORDER BY outreach_at DESC LIMIT 1

   for submission_id in submission_ids:
       INSERT IGNORE INTO responses (submission_id, ...,
                                     gmail_message_id) VALUES (...)

   if contact_outreach_existing:
       contact_id = contact_outreach_existing[0]['contact_id']
       INSERT IGNORE INTO contact_outreach (
           user_id, contact_id, outreach_at, direction_id (inbound),
           outreach_method_id (email), subject, body_text,
           from_email, gmail_thread_id, gmail_message_id
       ) VALUES (...)
   ```

   Per fork 1, the inbound row picks the **most-recent** existing
   contact_outreach row's `contact_id`. The unique key on
   `gmail_message_id` ensures one row per message per side.

3. **Self-sent filter** still applies (existing). The user's
   outbound `contact_outreach` row from the compose handler is
   already in place; the poller's fetch returns it; the self-sent
   filter (`from_email == gmail_credentials.gmail_address`) drops it
   before any insert is attempted.

## Frontend wiring

### `GmailComposeModal.tsx` — extended

New props:

```ts
interface Props {
  defaultSubmissionId?: number | null;
  defaultContactId?: number | null;
  // existing: replyTo, onSent, onNeedReconsent, show, onHide
}
```

Behavior:

- On mount, fetch `/submissions` (open submissions list) for the
  picker. (Use a small in-memory cache so opening the modal twice in
  a row doesn't re-fetch.)
- Submission picker: dropdown with all open submissions + a "(none)"
  option. Defaults to `defaultSubmissionId`. User can change to any
  other submission OR clear.
- Contact context indicator: when `defaultContactId` is set, a
  read-only banner above the form: "Sending email and logging
  outreach for [Contact Name]". The contact_id is locked at modal
  open — user can't change which contact this email is for. (To send
  to a different contact, close and re-open from a different page.)
- Compose vs reply mode: existing logic. Reply mode requires
  `defaultSubmissionId` (submission must be linked); the picker is
  hidden in reply mode (the link is implicit from the response
  being replied to).
- On send: POST `/messages/send` with `submission_id` from the
  picker (NULL if the user selected "(none)") AND `contact_id` from
  `defaultContactId` (passed through). Server validates at least one
  is set.

### `SubmissionDetail.tsx` — minor prop rename

Change `submissionId={data.id}` to `defaultSubmissionId={data.id}`.
Pass `defaultContactId={null}`. Otherwise unchanged.

### `ContactDetail.tsx` — new Compose button + outreach timeline rework

- Add **Compose email** button at the top of the page,
  scope-gated on `gmail.send` per the existing slice-9 pattern.
  Clicking opens the modal with `defaultContactId={contact.id}`,
  `defaultSubmissionId={null}`, To prefilled to `contact.email`.
- Existing **Log outreach** affordance unchanged — manual
  outreach logging stays as-is.
- Outreach timeline rework per fork 3:
  - Each event row shows direction icon (↑ outbound / ↓ inbound),
    method (`email` / `linkedin` / `phone` / etc), date, and:
    - For email-sourced events (`gmail_message_id` non-null):
      subject as the headline + click-to-expand body
    - For manually-logged events: notes as the headline (no
      expand)
  - All events in a single chronological list (no separate
    sections).
  - The contact's email may carry a small "via Gmail" badge for
    email-sourced rows so the user knows the source at a glance.

### `Settings.tsx` — banner-flash bug fix (slice-9 carryover)

Two-line tweak: capture `oauthSuccess` / `oauthError` into component
state on mount (`useState` initialized from `searchParams.get(...)`),
strip the URL params in the same effect, render banners from the
captured state. Prevents the flash-out where the URL strip happens
before the alert paints.

## Test plan

### `test_messages_send.py` (replaces `test_gmail_compose.py`)

Migrate every existing test to the new shape (route, body), then
add:

- `submission_id` only (existing slice-9 path): same coverage as
  before, just route-shape changes
- `contact_id` only:
  - happy path: contact_outreach inbound row inserted with subject,
    body_text, from_email, thread_id, message_id. submission untouched.
  - reply mode rejected (400) — slice-10 v1 doesn't support
    reply-from-contact
- `submission_id` + `contact_id` (both):
  - happy path: BOTH `submissions.gmail_thread_id` set (compose mode)
    AND `contact_outreach` row inserted. Returned `thread_id`
    consistent across both writes.
- Validation: neither set → 400. Both set with submission already
  linked → 400. Resume not owned → 404. Contact not owned → 404.
- Atomicity: if the contact_outreach insert fails (e.g., FK
  violation from a deleted contact), the submission update should
  not have committed.

### `test_gmail_poller.py` additions

- Dual enumeration: a user with one submission-linked thread + one
  contact-linked thread should produce two thread-fetches per cycle,
  not one.
- Inbound to thread mapped to submission only: response row
  inserted, no contact_outreach row.
- Inbound to thread mapped to contact only: contact_outreach inbound
  row inserted, no responses row.
- Inbound to thread mapped to both: BOTH rows inserted.
- Multi-contact thread fork (fork 1): when the thread has
  contact_outreach rows for two different contacts, the inbound goes
  to the most-recent contact's id.
- Idempotency: rerun produces no new rows in either table.

### Manual end-to-end

- From submission detail: Compose → fill → Send. Verify
  `submissions.gmail_thread_id` set; no contact_outreach row.
- From contact detail: Compose → fill (no submission selected) →
  Send. Verify contact_outreach outbound row inserted; no
  submission touched. Reply from external account → next poll
  inserts contact_outreach inbound row.
- From contact detail: Compose → fill + pick a submission → Send.
  Verify both submissions.gmail_thread_id AND a contact_outreach
  outbound row. Reply from external → next poll inserts BOTH a
  responses row AND a contact_outreach inbound row.
- ContactDetail timeline: see chronological order, expand body on
  email-sourced rows, direction icons render correctly.
- After contact-only thread, create a submission and paste-link the
  thread → next poll cycle should retroactively populate `responses`
  with the historical messages (per fork 2).

## Out of scope (deferred)

- **Reply-from-contact (no submission)**: user clicks Reply on a
  contact_outreach inbound row → modal in reply mode against a
  contact-only thread. v1 requires a submission for reply mode; if
  the user wants to reply from inside the app, they create a
  submission first and link the thread. Slice 10.5 candidate.
- **Multi-contact thread granular tracking**: per fork 1, v1 picks
  one contact for inbound rows. Tracking N contacts per inbound
  message would need a junction table. Defer until requested.
- **Outbound visibility in contact timeline distinct from outreach
  log**: maybe we want outbound emails to display differently from
  manually-logged outreach, beyond just the icon. UX iteration after
  the user lives with v1.
- **Bulk send**: emailing multiple contacts in one operation.
- **Template / signature support**: Gmail-side concern; defer
  indefinitely.
- **Inbound classification on contact_outreach rows**: `responses`
  has a classification (rejection/interview/etc). Should
  contact_outreach inbound also be classified? For v1, no — these
  are general-purpose conversation tracking, not application
  funnels. Add if a use case emerges.
- **AI-tailored body at compose time**: combines slice 06 with slice
  9.5/10. Interesting future slice.

## Starter task list

1. User decides forks 1–5.
2. Migration `0008_contact_outreach_gmail.sql` — add the five
   nullable columns + unique key + index.
3. Backend: refactor `gmail_compose.py` to the new route + dual
   write, with migrated tests. Drop the old `/submissions/{id}/send`
   route from the api template.
4. Backend: extend `gmail_poller.py` with dual-enumeration +
   dual-insert. Update tests.
5. Frontend: extend `GmailComposeModal.tsx` with submission picker
   + contact context indicator. Update prop names.
6. Frontend: `SubmissionDetail.tsx` prop rename.
7. Frontend: `ContactDetail.tsx` Compose button + outreach
   timeline rework (chronological, direction icons, email-row
   expand-collapse).
8. Frontend: `Settings.tsx` banner-flash bug fix (slice-9
   carryover).
9. Manual end-to-end test through all four send paths
   (submission-only, contact-only, both, reply mode).
10. End-of-slice: write `docs/slices/11-?.md` plan based on what's
    next.

## Resumption notes for the next session

- Slice 9 is **shipped and verified** end-to-end. `gmail_credentials`
  is populated, OAuth + poller + compose + reply all work against
  real Gmail. No deployment work needed for slice 9 itself.
- The `/messages/send` rename is the breaking change in this slice.
  Frontend MUST be updated and re-deployed in the same window as the
  api stack to avoid a stale-frontend-calls-deleted-route window.
  Sequence:
  ```
  cd infra && make deploy-api && make migrate && make sync-frontend
  ```
- The slice-9 banner-flash bug is item 8 above; not a blocker if
  deferred to a later slice.
- Schema-wise this slice is purely additive to `contact_outreach`.
  No data backfill required — existing manually-logged rows keep
  working with their email columns NULL.
- The user's two locked principles from the slice-9 follow-up
  conversation are reproduced verbatim in the "Why this slice"
  section above so the next session has the exact wording.