# Slice 09 — Inbound email pipeline (PLAN)

Status: planned, not started. Branch: `slice/09-email` (TBD).

## Why this slice next

After slice 08 the only major missing piece in the user-visible loop is
**recording responses**. Right now the `responses` table exists from
slice 01 and `submissions._detail` already surfaces `responses[]` in the
JSON response — but nothing writes to it. A submission's status moves
forward only because the user clicks a dropdown; there's no signal from
the recruiter side feeding back in.

Email pipeline closes that loop. SES inbound → S3 → processor Lambda →
classify → insert a `responses` row → optionally bump the submission's
status. The pieces are well-understood; what makes the slice non-trivial
is the new SAM stack, MX records on the hosted zone, and the
classification policy.

## Locked decisions to carry in

- **New SAM stack `infra/email/`** — separable from `data` / `api` /
  `scheduler` / `ai`. Per `feedback_stacks`, anything that can be torn
  down independently should be. SES inbound rule sets, the receipt-
  processor Lambda, and an inbound S3 bucket all live here.
- **DNS already in place.** Slice 08.5 added `infra/dns/` with a Route 53
  hosted zone (commit `af9fd62`). The MX records for SES inbound are an
  additive change inside `infra/dns/template.yaml` — no new domain
  acquisition.
- **`responses` and `response_classifications` already exist.** Migration
  0001 created them and seeded
  `rejection / interview_invite / auto_ack / recruiter_outreach / other`.
  No schema change is required for the basic pipeline.
- **Catalog-friendly classification** — adding a class later is one seed
  insert. Per `project_extensibility`.
- **S3 archival of raw email** — `responses.raw_email_s3_key` is already
  in the schema. Use it. Pattern mirrors `jd_snapshots` — body lives in
  S3, metadata lives in the row.

## Forks the user needs to decide before code starts

1. **How does inbound mail get tied back to a submission?**
   - *Per-submission alias*: every new submission generates a unique
     reply-to like `apply+<token>@<domain>`; outbound emails the user
     sends through their normal client carry that as the reply-to (or
     it's used as the From: when *we* send on the user's behalf, which
     we don't today). The token in the recipient address is the link.
     Cleanest, no parsing.
   - *Catch-all + heuristic match*: any inbound to `*@<domain>` lands in
     the bucket; the processor matches via the From: header against
     `contacts.email` plus the Subject: against company/role tokens.
     Lossy; only works when the recruiter replies to a thread the user
     started from a known address.
   - **Recommend per-submission alias.** The catch-all path requires a
     separate "user manually links the response to a submission" step
     for every miss, which defeats the point. Adds a `reply_token`
     column to `submissions` (or a side table) — small additive
     migration.

2. **Classification: heuristic-only, AI-assisted, or both?**
   - *Heuristic*: keyword patterns on subject/body
     (`unfortunately|not moving forward` → rejection, `schedule|calendly`
     → interview_invite, etc.). Cheap, predictable, occasionally wrong.
   - *AI via Bedrock*: re-use the existing `infra/ai/` stack to call
     Claude with a small prompt, return a `short_name` from the catalog.
     Better recall on edge cases, costs ~$0.001/email.
   - *Both* (heuristic first, AI on miss/`other`): default to heuristic;
     fall through to Bedrock when heuristic returns `other` or
     low-confidence.
   - **Recommend Both.** Heuristic catches the obvious 80%; Bedrock
     handles the long tail without paying per-email when not needed.
     The AI stack's tear-down doesn't break the email pipeline (Bedrock
     call is gated by env var, like the scheduler stack — pattern
     already in the codebase).

3. **What does the user see when a response lands?**
   - *Just the row*: `responses[]` shows up in the submission detail; no
     active notification.
   - *Email reminder*: the scheduler stack's notify Lambda sends a "you
     got a reply on <role> @ <company>" email.
   - *Status bump*: classifier output drives an automatic status change
     (rejection → status `rejected`; interview_invite → `interviewing`;
     etc.); user can revert by hand.
   - **Recommend Just the row + Status bump (no email).** Adding a "you
     got a reply" email when the user already saw the original reply in
     their actual inbox is noise. Status bumps are reversible from the
     UI. Email reminders can land in a later slice if the user actually
     misses replies in the dashboard.

## Proposed schema

```sql
ALTER TABLE submissions
    ADD COLUMN reply_token CHAR(16) NULL AFTER notes,
    ADD UNIQUE KEY uq_submissions_reply_token (reply_token);
```

Per-submission token, generated on INSERT (16 hex chars = 64 bits, plenty
of entropy for a personal-use namespace). Nullable so existing rows
don't blow up; backfilled lazily on next update or via a one-shot
migration.

## Proposed routes

The processor Lambda is invoked by SES (S3-upload trigger), not by API
Gateway — no public route. Two read-only routes round out the UI:

| Method | Path | Purpose |
|---|---|---|
| (none new) | — | `submissions._detail` already surfaces `responses[]` |

A future slice may add `PUT /responses/{id}` for manual reclassification;
out of scope here.

## New stack: `infra/email/`

- `infra/email/template.yaml`
  - `AWS::S3::Bucket` `jobtracker-inbound-email-<acct>` (private,
    server-side encrypted, lifecycle rule auto-expires raw email after
    180 days).
  - `AWS::SES::ReceiptRuleSet` + active `ReceiptRule` matching
    `*@<domain>`, action: store to S3 + invoke processor Lambda.
  - `AWS::Lambda::Function` `jobtracker-email-processor` —
    triggered by S3 ObjectCreated. Parses the email, extracts the
    `reply_token` from `To:` (or `Delivered-To:` if SES rewrites),
    looks up the submission, classifies, inserts `responses` row,
    bumps status if applicable.
  - SSM params published: `/jobtracker/email/inbound-bucket-name`,
    `/jobtracker/email/processor-arn`.
- `infra/dns/template.yaml` — additive: MX records pointing at
  `inbound-smtp.us-west-2.amazonaws.com` (priority 10), TXT for SPF/
  DMARC.
- Makefile: `deploy-email` (depends on `deploy-data` + `deploy-dns`),
  `delete-email`. Order in `deploy-all`:
  `network → data → auth → scheduler → email → api → frontend → ai →
  wire-frontend`.

## Verifying the domain

SES inbound requires the domain to be verified in SES (separate from
the outbound SES identity verified by the scheduler stack). Adds a
`AWS::SES::EmailIdentity` for the apex domain in the email stack, with
DKIM enabled — emits the three CNAME records the user adds to the
hosted zone.

Per `feedback_verify_dns_delegation`, before deploying the email stack:
`dig NS <domain> @8.8.8.8` to confirm the hosted zone NS records are
delegated upstream. Skipping this is the usual cause of "the stack
deployed but no mail arrives" symptoms.

## Test plan

- `test_email_processor.py` — unit tests for the processor Lambda:
  - parse a fixture email, extract reply_token from To:
  - heuristic classifier matrix (rejection / interview / auto-ack /
    recruiter)
  - Bedrock fallback on heuristic miss (mock the AI client)
  - status bump on rejection / interview_invite
  - missing reply_token → row inserted with submission_id NULL?
    Fork: drop on the floor vs. file as orphan. Recommend: log + skip,
    don't insert. Orphans add UX friction with no real benefit.
- `test_submissions.py` extended for the `reply_token` column on
  create + the migration for backfilling.
- Manual: send a real email to `apply+<token>@<domain>`; verify the
  S3 object lands, the Lambda fires, the response shows up on the
  submission detail page.

## Frontend wiring

The submission detail page already renders `responses[]` (slice 01).
Tweaks:

- Show the response classification with a colored badge — re-use
  existing badge styling from `Submissions.tsx::StatusBadge`.
- Inline "promote to status" affordance disappears once auto-bump lands
  (decided per fork 3 above). Strikethrough non-applicable here.

## Out of scope (deferred)

- Manual reclassification UI (`PUT /responses/{id}`).
- Outbound email from the user (sending the initial application from
  inside the app). Out-of-scope and probably never in scope — the user
  uses their normal mail client.
- DLQ on the processor Lambda (re-add when a missed inbound becomes
  a real complaint, not theoretical).
- Bounce / complaint handling — the inbound rule catches everything;
  bounces handled by SES default behavior.
- Per-thread linking: if a recruiter replies to *the user's* reply,
  the In-Reply-To header would let us link that message to the same
  response thread. Defer; current shape is "one row per inbound."

## Starter task list

1. Decide forks 1–3 (user).
2. Migration `0007_submission_reply_token.sql` — add column + unique key.
3. `submissions.py` create path — generate `reply_token` on INSERT.
   Extend tests.
4. New stack `infra/email/template.yaml` + `samconfig.toml`.
5. `backend/src/handlers/email_processor.py` — parse, classify, insert.
   Tests.
6. `infra/dns/template.yaml` — add MX + TXT (SPF/DMARC) + DKIM CNAMEs.
7. Makefile: `deploy-email` / `delete-email` / update `deploy-all`.
8. Manual end-to-end test: send a real email through.
9. End-of-slice: write `docs/slices/10-?.md` plan based on what's left
   (manual reclassification UI? outbound-email sweeps for stale auto
   follow-ups? per-user timezone?).