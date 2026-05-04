# Slice 09 — Gmail integration: read + write (PLAN)

Status: planned, not started. Branch: `slice/09-email` (active; name kept
even though the implementation pivoted away from SES inbound).

## Pivot from the previous draft

Earlier drafts of this slice routed inbound recruiter mail through SES
inbound + a per-submission `apply+<token>@<domain>` Reply-To alias. That
design fell apart on two counts: (1) recruiters see a randomized opaque
alias instead of the user's real gmail address, which damages
candidate-side perception, and (2) back-and-forth threads break unless
the user re-injects the Reply-To header on every outbound message —
i.e. turning a job tracker into a chore.

This plan replaces that with **the Gmail API for both read and write**.
The user keeps using their normal Gmail identity for outbound (Gmail
sends from the OAuth identity, recruiters see `saastabp@gmail.com`
exactly as they would for a Gmail-composed email). The app reads
inbound replies via a refresh-token-authorized poller and links them
back to submissions by `thread_id`. The user can also compose, send,
and reply from inside the SPA — when they do, the returned `threadId`
auto-links the submission to the gmail thread, so no Message-ID paste
is required for app-originated submissions. No alias, no MX records,
no SES inbound stack, no DNS changes.

An earlier iteration of this plan split the work across slices 9
(read) and 9.5 (write). For a single-user new app where the project
owner is the only person who would ever exercise either half, the
split was engineering ceremony. Merged here.

## Why this slice next

After slice 08 the only major missing piece in the user-visible loop
is **recording responses**. The `responses` table exists from slice
01 and `submissions._detail` already surfaces `responses[]` — but
nothing writes to it. A submission's status moves forward only when
the user clicks a dropdown; there is no signal from the recruiter side
feeding back in. Reading inbound closes that loop. Writing outbound
removes the manual Message-ID paste from app-originated submissions
and lets the user reply to recruiter-originated chains without
leaving the SPA.

## Locked decisions

- **New SAM stack `infra/gmail/`** — separable from `data` / `api` /
  `scheduler` / `ai` / `dns`. Per `feedback_stacks`, anything tearable
  on its own gets its own stack. Holds the OAuth client config (in
  SSM), the poller Lambda, its EventBridge schedule, the archive S3
  bucket, and the KMS key used to encrypt refresh tokens. The OAuth
  start/callback and admin/compose HTTP routes ride the existing
  `infra/api/` API Gateway (same pattern as the scheduler stack: api
  hosts the routes, sister stack hosts the worker).
- **OAuth scopes from day 1**:
  `https://www.googleapis.com/auth/gmail.readonly` +
  `https://www.googleapis.com/auth/gmail.send`. Smallest scope set
  that lets the poller read message bodies for classification /
  archival AND lets the compose handler send. No `gmail.modify`, no
  drafts API, no labels.
- **Thread-id is the link**, not message-id. A submission has 0..1
  `gmail_thread_id`; every inbound message in that thread becomes a
  `responses` row. Back-and-forth chains attach automatically because
  Gmail's own threading produces a stable `threadId`.
- **App-originated submissions auto-link.** The compose handler
  captures the `threadId` returned by `users.messages.send` and
  writes it to `submissions.gmail_thread_id` in the same transaction.
  Manual Message-ID paste is reserved for submissions sent through
  another mail client and for recruiter-originated chains.
- **Plaintext body only for the compose handler.** No HTML, no
  inline images, no rich-text editor, no Markdown rendering, no
  signatures. Job application emails are 95% prose paragraphs; the
  5% that benefit from formatting are equally readable as plaintext
  with hyphens and capitalization. Backend body construction is
  `set_content(body, subtype='plain')` — one line, no
  multipart/alternative, no DOMPurify-equivalent, no +50KB editor
  bundle.
- **Send-immediate, no Drafts.** Compose modal has Send and Cancel.
  Drafts live in Gmail proper.
- **`responses` and `response_classifications` already exist.**
  Migration 0001 created them with seeds. No catalog change for the
  basic pipeline.
- **S3 archival of raw email stays.** `responses.raw_email_s3_key`
  is in the schema; the poller writes the raw RFC 822 bytes there.
  Reason: if OAuth gets revoked or the user changes gmail accounts,
  we still have the bodies for re-running classification later.
- **Self-sent filter at the poller.** Messages whose `from_email`
  matches the polling user's `gmail_credentials.gmail_address` are
  skipped at insert time. The user's outbound (whether sent from
  inside the SPA or from a different client into a linked thread)
  doesn't appear in `responses[]`. Cleaner timeline; the user can
  see their own sent in Gmail proper if they need it.
- **Catalog-friendly classification** — adding a class is one seed
  insert, per `project_extensibility`.
- **Multi-tenant from day 1**, per `project_extensibility` and the
  wife's tracker-tickler fork. Refresh tokens are stored per-user,
  not globally.
- **Per-deployment Google Cloud project.** Each fork (this repo, the
  tracker-tickler fork, future forks) creates its own GCP project +
  OAuth client. The OAuth client id/secret live in SSM, not in code.

## Decisions (resolved)

The seven forks below are settled. Each section keeps the analysis as
a record of the trade-offs, with a **Decision** line at the end.

### Fork 1 — Polling vs Pub/Sub push

- *Polling*: EventBridge schedule fires the poller Lambda every N
  minutes. Lambda calls
  `users.history.list(startHistoryId=last_history_id)` per user,
  processes new messages in watched threads, advances the cursor.
  Simple, fully AWS-side, idempotent via the `historyId` cursor.
- *Pub/Sub push*: Gmail `users.watch` posts change notifications to
  a GCP Pub/Sub topic. A push subscription forwards them to a Lambda
  URL on AWS. Near-realtime, but adds a GCP project resource (the
  topic + subscription), webhook auth, and a 7-day re-`watch`
  requirement (Gmail expires watches after 7 days).
- **Decision: Polling at 10-minute cadence.** Recruiter responses
  arrive over hours/days; sub-minute latency buys nothing. Polling
  keeps the entire infra inside AWS, avoids the GCP push surface
  area, and the cadence is trivial to tune.

### Fork 2 — Classification: heuristic-only, AI-assisted, or both?

- *Heuristic*: keyword patterns on subject/body
  (`unfortunately|not moving forward` → rejection, `schedule|calendly`
  → interview_invite, etc.). Cheap, predictable, occasionally wrong.
- *AI via Bedrock*: re-use the existing `infra/ai/` stack to call
  Claude with a small prompt, return a `short_name` from the catalog.
  Better recall on edge cases, ~$0.001/email.
- *Both* (heuristic first, AI on miss/`other`): default to heuristic;
  fall through to Bedrock when heuristic returns `other` or low
  confidence.
- **Decision: Heuristic-only for v1.** Reasons: (1) the "both" path
  means the gmail Lambda either HTTP-calls the AI Lambda (extra URL
  in env, latency, error handling) or calls Bedrock directly (extra
  IAM, second client). Both work, neither is free. (2) Heuristic on
  `unfortunately|not moving forward` / `schedule a call|calendly` /
  `we received your application` covers ~85% accurately. Misses fall
  into `other` and don't break anything — they just don't auto-bump
  status, which is fine. (3) AI fallback is a clean additive change
  later when real miss patterns surface; doing it speculatively now
  means tuning prompts against imagined emails. AI-side wiring
  (`AI_STACK_ENABLED` env var, Bedrock IAM in the gmail stack) is
  not added; gets revisited as a future slice when justified by
  observed misses.

### Fork 3 — What does the user see when a response lands?

- *Just the row*: `responses[]` shows up in the submission detail;
  no active notification.
- *Status bump*: classifier output drives an automatic status change
  (rejection → `rejected`, interview_invite → `interviewing`); user
  reverts manually if wrong.
- *Email reminder*: scheduler stack's notify Lambda sends a "you got
  a reply on <role> @ <company>" email.
- **Decision: Just the row + Status bump (no email).** Adding a "you
  got a reply" email when the user already saw the original reply in
  their gmail inbox is noise. Status bumps are reversible from the
  UI. Email reminders can land in a later slice if dashboard misses
  become a real complaint.

### Fork 4 — Refresh-token storage

- *KMS-encrypted column on `gmail_credentials`*: Lambda holds a
  KMS-key ARN, encrypts at write, decrypts at read. One DB row per
  user; the cursor (`last_history_id`) lives on the same row, so
  polling state is co-located with the credential. Adds a KMS key to
  the gmail stack (~$1/mo).
- *SSM Parameter Store SecureString, one per user*:
  `/jobtracker/gmail/refresh-token/<user_id>`. Free for standard
  params (10K/region), AWS-managed encryption at rest, no app-side
  KMS calls. Cursor lives separately in `gmail_credentials` (split
  storage).
- **Decision: KMS-encrypted column.** Co-locates the credential and
  the polling cursor — one row per user holds everything the poller
  needs, one transaction reads/updates both. The $1/mo KMS-key cost
  is acceptable; stack-tear-down deletes the key, invalidating
  leftover ciphertext, which is the right safety story.

### Fork 5 — Reply entry points

- *Per-response Reply button*: each row in `responses[]` gets a
  Reply button. Modal pre-populates To with that response's
  `from_email`, Subject with `Re: <original>`, In-Reply-To with that
  message's `gmail_message_id`.
- *Single Compose button + dropdown*: one Compose button on the
  submission detail header. If the thread has prior responses, the
  modal opens with a dropdown to pick which to thread under.
- *Both*: per-row Reply (specific message) + header Compose (fresh
  send into the thread).
- **Decision: Both.** Per-response Reply matches user intent ("I
  want to reply to *this* message"); header Compose covers "send my
  initial application" for app-originated submissions. Same modal
  component, two opener call sites.

### Fork 6 — CC / BCC support

- *To only*: simplest UI, smallest validation surface.
- *To + CC + BCC*: three inputs, comma-separated, validated server-
  side.
- **Decision: To + CC + BCC.** Common case: CC the friend who
  referred you, or BCC yourself for archival. Trivial extra code.

### Fork 7 — Resume attachment default

- *Always master*: every modal opens with the master resume
  pre-selected; user actively switches or removes.
- *No default, must pick*: dropdown opens empty.
- *Last-used per submission*: the resume attached on the previous
  send in this submission becomes the default for the next. Falls
  back to master if no prior send. Requires a small additive column
  on `submissions` to persist the choice.
- **Decision: Always default to master.** The persistent variant
  adds a column, an FK, and a write path for marginal UX gain. The
  picker is a dropdown with all the user's resumes — switching is
  one click. Follow-up replies usually use the same resume anyway,
  and the dropdown is right there. If "I keep picking the same
  non-master resume per submission" becomes annoying, the column
  is a cheap additive migration later.

## Proposed schema

Migration `0007_gmail_integration.sql`:

```sql
-- Per-submission link to a Gmail thread. Nullable — submissions
-- created before slice 09 carry NULL and never get auto-attached
-- responses unless the user later links them. The user pastes an
-- RFC 822 Message-ID for submissions sent through another mail client
-- (or for recruiter-originated chains); the backend resolves it to
-- the API thread_id via Gmail search and stores the thread_id here.
-- App-originated submissions skip the paste — the compose handler
-- writes this column directly from the send response's threadId.
ALTER TABLE submissions
    ADD COLUMN gmail_thread_id VARCHAR(64) NULL AFTER notes,
    ADD UNIQUE KEY uq_submissions_gmail_thread (gmail_thread_id);

-- Idempotency anchor on responses: every Gmail message has a unique
-- id; the unique key prevents the poller from inserting the same
-- message twice if a poll cycle is retried after partial failure.
ALTER TABLE responses
    ADD COLUMN gmail_message_id VARCHAR(64) NULL AFTER raw_email_s3_key,
    ADD UNIQUE KEY uq_responses_gmail_message (gmail_message_id);

-- One row per user who has connected their gmail. Refresh token is
-- stored as a KMS-encrypted blob; the gmail stack's KMS key id is
-- baked into the poller / compose Lambdas' env. Soft-delete via
-- deleted_at on disconnect — keeping the row lets us show "last
-- connected" in the UI without losing the audit trail. Scopes
-- column is populated from Google's token response (the actual
-- granted scopes, not a hard-coded string), so partial grants
-- (user denies a scope on the consent screen) are reflected
-- accurately.
CREATE TABLE gmail_credentials (
    id                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id                  BIGINT UNSIGNED NOT NULL,
    gmail_address            VARCHAR(320) NOT NULL,
    refresh_token_ciphertext VARBINARY(2048) NOT NULL,
    scopes                   VARCHAR(1024) NOT NULL,
    last_history_id          VARCHAR(64)  NULL,
    last_polled_at           TIMESTAMP    NULL DEFAULT NULL,
    created_at               TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at               TIMESTAMP    NULL DEFAULT NULL,
    UNIQUE KEY uq_gmail_credentials_user (user_id),
    CONSTRAINT fk_gmail_credentials_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Notes:
- No `gmail_credentials_*` catalog table — `scopes` is a
  denormalized string because OAuth scopes are an external system's
  concept and pinning them to a catalog adds friction every time
  Google changes scope strings.
- `last_history_id` is `VARCHAR(64)` to match Gmail's API contract
  (string-typed even though numeric in practice).
- The `responses` ALTER is additive and safe on existing rows.

## Proposed routes

Poller Lambda is invoked by EventBridge — no public route. The OAuth
dance, admin actions, and compose all live on the existing api stack:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/integrations/gmail/status` | Returns `{ connected: bool, gmail_address: str?, scopes: str[], last_polled_at: ts? }` |
| `GET` | `/integrations/gmail/oauth/start` | Generates state token, returns `{ consent_url }` requesting `gmail.readonly` + `gmail.send` |
| `GET` | `/integrations/gmail/oauth/callback` | Receives `code` + `state`, exchanges for tokens, stores refresh token + actual granted scopes (NOT a hard-coded scope string), redirects to SPA |
| `DELETE` | `/integrations/gmail` | Disconnects: revokes refresh token at Google, soft-deletes `gmail_credentials` row |
| `PUT` | `/submissions/{id}/gmail-link` | Body `{ mid }`. Accepts a `mid:` URI, an angle-bracketed Message-ID, or a bare Message-ID. Backend strips prefix variants, resolves to `threadId` via `users.messages.list(q='rfc822msgid:<id>')`, stores `submissions.gmail_thread_id`. |
| `DELETE` | `/submissions/{id}/gmail-link` | Clears the link |
| `POST` | `/submissions/{id}/send` | Compose / send / reply. Body: `{ to: str[], cc?: str[], bcc?: str[], subject: str, body: str, resume_id?: int, in_reply_to_message_id?: str }`. Reply mode (`in_reply_to_message_id` present) sets threading headers and preserves `gmail_thread_id`. Compose mode (absent) captures returned `threadId` and sets it on the submission. Returns `{ thread_id, gmail_message_id }`. |

`submissions._detail` continues to render `responses[]` — no
read-route change needed.

## Linking workflow (manual paste)

App-originated submissions don't need this — the compose handler
auto-links. Manual paste is for two cases:

1. The user sent the application through another mail client (apply-
   now button on a careers site that auto-composes in their default
   mail client, mobile, etc.).
2. **Recruiter-originated chains**: cold email arrives in the user's
   inbox before any submission exists. User decides to track it,
   creates the submission, then pastes the Message-ID to seed the
   link.

The mechanism: pasting **an RFC 822 Message-ID from any one message
in the thread**. Backend resolves that message to its `threadId`
server-side via `users.messages.list(q='rfc822msgid:<id>')`, stores
the `threadId` on `submissions.gmail_thread_id`, and from then on
every inbound message in the thread auto-attaches via the poller.

### Why Message-ID instead of the gmail web URL

The gmail web URL fragment (`https://mail.google.com/.../#sent/FMfcgz...`)
is a permalink/share ID, not the API `threadId`, and
`users.threads.get()` rejects it. Message-IDs are the RFC standard,
universally exposed by every mail client, and resolvable to a
`threadId` via one API search. They also work cross-client — gmail
web, Thunderbird, Apple Mail, mutt all surface them.

### Copy paths by client

- **Thunderbird**: open the message in Sent →
  **Right-click → Organize → Copy Message Link**. Result is a `mid:`
  URI like
  `mid:CAFus6uRsoh0ymLiiGb_uz1kN5w1QL8P9DoRv39W-UmRvr+nF1A@mail.gmail.com`.
- **Gmail web**: open the message → ⋮ menu → **Show original** →
  copy the value of the `Message-ID:` header. Gmail wraps it in
  `<...>`; the brackets are fine — the backend strips them.
- **Other clients**: any client that exposes the `Message-ID:`
  header works. The backend accepts the same input regardless of
  source.

### Walkthrough — user-sent-through-another-client

1. User sends the application email through their normal mail
   client. No app involvement.
2. User opens the SPA, on the submission detail page (created
   earlier or right now — doesn't matter; `gmail_thread_id` is
   nullable and editable).
3. User pastes any text that *contains* a Message-ID. The parser
   is tolerant — anything below works:
   - `Message-ID: <77b734db-...@gmail.com>` (single-line copy from
     Gmail web's "Show original" header table — most common path)
   - `Message-Id: <...>` / `MESSAGE-ID: <...>` (header name is
     case-insensitive per RFC 822; parser is too)
   - A whole pasted block of headers, even with line breaks
     collapsed by the browser. Example real paste:
     `...boundary="------------..."Message-ID: <77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com>Date: Sat, 2 May 2026...References: <FCOjk_3KQOS9m_kpolPp5w@geopod-ismtpd-100>...`
     The parser grabs the `Message-ID:` value and ignores the
     adjacent `References:` and `In-Reply-To:`.
   - `mid:CAFus6uR...+nF1A@mail.gmail.com` (Thunderbird URI form)
   - `<CAFus6uR...+nF1A@mail.gmail.com>` (RFC 822 angle-bracket form)
   - `CAFus6uR...+nF1A@mail.gmail.com` (bare)
4. Backend normalizes in two steps:
   - **Step 1 — header-anchored extraction.** Run the regex
     `(?i)message-id\s*:\s*<?([^\s<>]+@[^\s<>]+)>?` against the
     input. The `(?i)` makes it case-insensitive (catches
     `Message-ID` / `Message-Id` / `MESSAGE-ID`). Anchoring on
     `message-id` specifically is what keeps `References:` and
     `In-Reply-To:` from matching. If the regex matches, use group 1.
   - **Step 2 — fallback for prefix-free pastes.** If step 1
     doesn't match, strip a leading `mid:`, surrounding `<>`,
     and whitespace from the entire input. Whatever is left is
     the candidate.
   Then call `users.messages.list(q='rfc822msgid:<normalized>')`
   with the user's gmail credential, pick the returned `threadId`,
   store it on `submissions.gmail_thread_id`. The lookup itself is
   the ownership check — if the message isn't in the user's mailbox,
   the search returns empty and the API returns `404`.
5. From that point on, every message in that thread arrives as a
   `responses` row within the next poll cycle.

### Recruiter-originated chains

The walkthrough above assumes the user sent the first email. The
recruiter-cold-emails-you case works the same way with one tweak in
ordering:

1. Recruiter cold-emails you → message lands in your normal Gmail
   inbox.
2. You read it in Gmail/Thunderbird, decide it's worth tracking.
3. In the SPA: create a new submission, populate company/role from
   what's visible in the email body.
4. Copy the **recruiter's** Message-ID (any message in the chain
   works — same paths as above).
5. Paste into the submission's "Gmail message" field. Backend
   resolves to `threadId`.
6. Next poll cycle: the poller fetches every message in the linked
   thread — which at this point is just the recruiter's original —
   and inserts it as a `responses` row.
7. You click Reply on that row → compose modal opens in reply mode
   with In-Reply-To set; modify body → Send → reply lands in the
   same Gmail thread on the recruiter's side.

### Parser notes

- **Preserve `+` characters.** Gmail-generated Message-IDs
  frequently contain `+` (e.g.,
  `...UmRvr+nF1A@mail.gmail.com`). JSON request bodies preserve
  `+` natively; do NOT URL-decode the value or `+` becomes space
  and the search returns nothing.
- **Don't trust client-side validation alone.** The server runs the
  same normalization regardless of what the SPA sent.

### Deferred enhancement

A "recent sent threads" picker that lists the user's last N sent
messages and lets them click instead of paste. Considered for v1
when the URL-paste plan fell through; rejected because Thunderbird's
one-click "Copy Message Link" closes most of the friction gap, and
app-originated sends auto-link anyway. Pulls back in if the picker
becomes the user's preferred shape for the cross-client case.

## Compose handler behavior

`backend/src/handlers/gmail_compose.py`:

1. Validate request: at least one recipient in `to`; subject
   required; body required (≥ 1 char post-strip).
2. Resolve user → `gmail_credentials` row → decrypted refresh token
   → Gmail API client (re-using `gmail_client.py` shared lib).
3. Verify `gmail.send` is in the credential's scopes; if not, return
   `403` with `{"error": "send scope required",
   "needs_reconsent": true}`.
4. If `resume_id` present: fetch `resumes` row, verify
   `user_id == caller`, fetch PDF bytes from S3 via `file_s3_key`.
   Soft-deleted resumes rejected (defense-in-depth; the picker
   already filters them).
5. Build RFC 822 message via Python's `email.message.EmailMessage`:
   - Headers: To, Cc, Bcc, Subject, Date. From is omitted — Gmail
     fills it from the OAuth identity.
   - If reply mode: add `In-Reply-To: <mid>` and
     `References: <chain>`. Chain is built by reading the original
     message's `References` (via `users.messages.get(id=...)`) and
     appending its `Message-ID`.
   - Body: `set_content(body, subtype='plain')`.
   - Attachment: `add_attachment(pdf_bytes,
     maintype='application', subtype='pdf',
     filename=<resume.original_filename or "resume.pdf">)`.
6. Base64url-encode the raw bytes.
7. Call `users.messages.send` with `{"raw": "<encoded>",
   "threadId": "<from submission if reply mode>"}`. The `threadId`
   parameter is belt-and-suspenders alongside `In-Reply-To` —
   Gmail rejects the send if the threadId doesn't match what
   `In-Reply-To` resolves to, catching "user tried to reply into a
   different thread than the submission is linked to" bugs.
8. Capture returned `threadId`. Two cases:
   - Submission has no `gmail_thread_id` yet (compose mode): write
     it. The user's send becomes the seed for the inbound poller.
   - Already linked: assert returned value matches stored value. If
     not, that's a bug — `500` with logged details. Should never
     happen given step 7's guard.
9. Return `200` with `{"thread_id": "<threadId>",
   "gmail_message_id": "<sent message id>"}`.

## New stack: `infra/gmail/`

- `infra/gmail/template.yaml`
  - `AWS::KMS::Key` `GmailRefreshTokenKey` — symmetric, used by the
    OAuth-callback Lambda + compose Lambda (encrypt) and poller
    Lambda + compose Lambda (decrypt). Key policy grants only those
    role ARNs.
  - `AWS::Lambda::Function` `jobtracker-gmail-poller` — env:
    `GMAIL_KMS_KEY_ID`, DB params, `GMAIL_OAUTH_CLIENT_SSM_PATH`,
    `INBOUND_BUCKET_NAME`. (No `AI_STACK_ENABLED` per Decision 2 —
    classification is heuristic-only for v1.)
  - `AWS::Events::Rule` ScheduleExpression `rate(10 minutes)`
    invoking the poller. Concurrency 1 per user via Lambda reserved
    concurrency + per-user advisory lock in DB to prevent
    overlapping polls.
  - `AWS::S3::Bucket` `jobtracker-gmail-archive-<acct>` — private,
    SSE-S3, lifecycle expiring objects after 180 days. Poller writes
    raw RFC 822 bytes keyed by `<user_id>/<gmail_message_id>.eml`;
    `responses.raw_email_s3_key` points at it.
  - SSM SecureString `/jobtracker/gmail/oauth-client` — JSON
    `{ client_id, client_secret }` populated manually post-deploy
    from the GCP console. Poller + OAuth callback + compose
    Lambdas all have `ssm:GetParameter` for this path.
- `infra/api/template.yaml` — additive: route handlers
  `gmail_oauth.py` (start + callback), `gmail_admin.py` (status,
  disconnect, link/unlink), and `gmail_compose.py` (send). The api
  stack already owns the HttpApi; adding routes is a same-pattern
  addition.
- Makefile: `deploy-gmail` (depends on `deploy-data`),
  `delete-gmail`. Order in `deploy-all`:
  `network → data → auth → scheduler → api → frontend → ai → gmail
  → wire-frontend`. `deploy-api` must run before `make migrate` per
  `feedback_migrate_after_deploy_api`; new `0007_gmail_integration
  .sql` won't be picked up until deploy-api re-bundles
  backend/src/. Frontend changes need `make sync-frontend` per
  `feedback_sync_frontend_in_deploy_list`.

## One-time GCP setup (manual, per deployment)

The Google Cloud side isn't IaC-friendly — has to be clicked
through once per deployment (this repo, the wife's fork, etc.).
Documented in `infra/gmail/GCP-SETUP.md` (created with the slice):

1. Create a GCP project (e.g., `jobtracker-prod-saastabp`).
2. Enable the Gmail API.
3. Configure the OAuth consent screen — *External*, *Testing* mode
   is fine for personal use; published mode requires verification
   which is overkill for a single-user app. Add the user's gmail
   address as a test user.
4. Create an OAuth 2.0 Client ID (type: Web application).
   Authorized redirect URI:
   `https://<api-domain>/integrations/gmail/oauth/callback`.
5. Copy the client_id + client_secret into the SSM SecureString
   created by the gmail stack.
6. (Re-)deploy the gmail stack and run a connection test from the
   SPA.

The redirect URI must match exactly between GCP and the deployed
API domain — the most common "OAuth fails silently" cause.

## Test plan

- `test_gmail_oauth.py`:
  - state-token generation/validation (CSRF defense on the callback)
  - code → token exchange (mocked Google response)
  - refresh-token KMS encryption + DB persist
  - **scopes from response, not hard-coded**: callback writes the
    actual `scope` string from Google's token response into
    `gmail_credentials.scopes`. Test fixtures: full grant
    (`["gmail.readonly", "gmail.send"]`), partial grant
    (`["gmail.readonly"]` only — user denied send on consent screen)
  - duplicate connection: existing `gmail_credentials` row updated
    not duplicated (UNIQUE on `user_id` enforces; test the upsert
    path)
  - disconnect path: revoke endpoint called, soft-delete applied
- `test_gmail_admin.py`:
  - status route returns scopes alongside connection info
  - Message-ID parser matrix:
    - `Message-ID: <77b734db-...@gmail.com>` (Gmail web "Show
      original" line copy)
    - `Message-Id: <...>` and `MESSAGE-ID: <...>` (case variants)
    - Multi-header blob — paste a fixture string containing
      `Content-Type:`, `Message-ID:`, `Date:`, `References:`,
      `In-Reply-To:` all run together; assert the regex extracts
      the `Message-ID:` value, NOT the others
    - `mid:CAFus6uR...+nF1A@mail.gmail.com` (Thunderbird URI form)
    - `<CAFus6uR...+nF1A@mail.gmail.com>` (RFC 822 angle-bracket)
    - `CAFus6uR...+nF1A@mail.gmail.com` (bare)
    - `mid:eb7d3002-ca80-4c68-984d-74e0cb802de0@gmail.com`
      (UUID-style Gmail auto-generated form)
    - leading/trailing whitespace stripped
    - `+` characters preserved verbatim — assert via JSON round-trip
    - garbage input (no `@`, no `Message-ID:`) → `400`
  - resolution: `users.messages.list(q='rfc822msgid:<id>')` returns
    a message → `threadId` extracted → stored on `submissions`
  - resolution miss: empty search result → `404`
  - ownership: search runs against the requesting user's gmail
    credential, so a Message-ID belonging to another user's mailbox
    naturally returns empty
  - unlink: clears `gmail_thread_id`
- `test_gmail_compose.py`:
  - **MIME builder matrix**:
    - To-only: headers correct, plaintext body, no attachment
    - To + CC + BCC: all three headers present
    - With resume attachment: multipart/mixed, application/pdf part
      with correct filename (uses `resumes.original_filename` if
      present, fallback `resume.pdf`)
    - Reply mode: `In-Reply-To` set, `References` chained correctly
      (test fixtures: empty-prior-References, with-prior-References)
    - Subject Unicode → MIME encoded-word
  - **Send handler**:
    - Compose mode happy path: returns 200, writes
      `gmail_thread_id`
    - Reply mode happy path: stored thread_id preserved
    - Reply mode with mismatched threadId from API: `500` logged
    - Missing `gmail.send` scope: `403` with
      `needs_reconsent: true`
    - Resume ownership: another user's `resume_id` → `404`
    - Resume soft-deleted: `400` with explanatory message
    - Validation: empty `to` → `400`, empty `subject` → `400`,
      empty body → `400`
    - Gmail API failure → `502`
- `test_gmail_poller.py`:
  - history.list pagination (mocked)
  - watched-thread filter: only messages whose threadId is on a
    `submissions.gmail_thread_id` get processed
  - **self-sent filter**: messages whose `from_email` matches the
    polling user's `gmail_credentials.gmail_address` are skipped
    at insert time. Cursor still advances. Test fixture: a thread
    with [recruiter inbound, user outbound, recruiter inbound]
    produces two `responses` rows, not three.
  - heuristic classifier matrix (rejection / interview / auto-ack
    / recruiter / other-fallback for ambiguous bodies)
  - status-bump matrix: rejection → rejected, interview_invite →
    interviewing, others no-op
  - idempotency: same `gmail_message_id` arriving twice produces
    one response row (UNIQUE key on `responses.gmail_message_id`
    enforces; test second insert is caught and logged)
  - cursor advances only after batch success
  - S3 archival writes the RFC 822 body before the row insert
- Manual end-to-end:
  - OAuth flow: connect Gmail, observe both `gmail.readonly` and
    `gmail.send` granted; status returns full scope list.
  - **Compose path**: submission detail "Compose" → modal → fill +
    attach master resume → Send → success toast → submission's
    `gmail_thread_id` populated. Verify in Gmail Sent that the
    message went; verify recruiter's reply (next poll cycle) creates
    a `responses` row.
  - **Reply path**: click Reply on that response row → modal
    pre-populates → modify body → Send → reply lands in same
    thread on recruiter's side.
  - **Manual-paste path**: send a separate application through
    Thunderbird → Copy Message Link → paste into submission's
    Gmail-message field → recruiter replies → poller pulls the
    response on next cycle.
  - **Recruiter-originated path**: simulate by having a second test
    account send you a cold email → create a submission for it →
    paste the recruiter's Message-ID → poller pulls the email body
    → reply from app → confirm thread continuity on recruiter's
    side.
  - Verify the user's own outbound is *not* in `responses[]`
    (self-sent filter doing its job).

## Frontend wiring

- New `Settings.tsx` (or extend existing): "Connect Gmail" button →
  hits `/integrations/gmail/oauth/start`, redirects to consent URL,
  returns through callback, polls `/integrations/gmail/status` for
  `connected: true`. Show `gmail_address`, `last_polled_at`, and
  granted scopes once connected.
- `Submissions.tsx` detail page:
  - **Manual link field**: "Gmail message" with paste-and-save UX.
    Help-text under the field: *"For submissions you sent through
    another mail client, paste any Message-ID from the conversation.
    In Thunderbird: Right-click → Organize → Copy Message Link. In
    Gmail web: ⋮ → Show original → triple-click the Message-ID line
    (or copy the whole header block; the parser will find it)."*
    Shows "Linked ✓ (thread <abbreviated_id>)" or "Not linked" once
    set. Clear button calls DELETE.
  - **Header "Compose Gmail message" button**: scope-gated on
    `gmail.send`. Click → opens compose modal in `compose` mode.
  - **Per-response "Reply" button**: appears on each
    `responses[]` row. Same scope gating. Click → opens compose
    modal in `reply` mode prefilled from that row.
  - `responses[]` rows: add a colored classification badge
    re-using `StatusBadge` styling.
  - Inline "promote to status" affordance becomes redundant once
    auto-bump lands (per fork 3) — strikethrough non-applicable.
- `GmailComposeModal.tsx` (new component):
  - Props: `submission`, `mode` (`'compose' | 'reply'`),
    `replyTo?` (response object), `onClose`, `onSent`
  - Fields: To, Cc (collapsed by default), Bcc (collapsed by
    default), Subject, Body textarea, Resume picker (defaults to
    master, per Decision 7)
  - Reply-mode prefill: To from `replyTo.from_email`, Subject from
    `replyTo.subject` with `Re: ` prepended (idempotent — strip
    existing `Re: ` first), `in_reply_to_message_id` from
    `replyTo.gmail_message_id`
  - Send: POST `/submissions/{id}/send`. On 403 with
    `needs_reconsent` → close modal + redirect to OAuth start.
    On success → toast + `onSent()` → parent refreshes submission
    detail.

## Out of scope (deferred)

- Manual reclassification UI (`PUT /responses/{id}`)
- "Recent sent threads" picker — manual paste suffices for the
  cross-client case; app-originated sends auto-link
- Pub/Sub push notifications (see fork 1)
- Per-user timezone for status bumps / dashboards
- DLQ on the poller / compose Lambdas — re-add when missed events
  become a real complaint, not theoretical
- Multi-account support (one user with two gmail addresses) — the
  schema allows it via relaxing the UNIQUE on `user_id`, but the UI
  assumes 1:1 in v1
- Rich-text body / HTML email
- Drafts (`users.drafts.create`, persistent SPA-side draft state)
- Email templates / signatures (use Gmail proper)
- Quoted-original on reply
- Send-later / scheduled send
- Sent items list inside the SPA (use Gmail)
- Read receipts, link-click tracking, open tracking
- Outbound visibility in submission timeline (would be a reversal
  of the self-sent filter, plus a `direction_id` column on
  `responses`)
- Multi-attachment beyond one resume (cover letter PDF? portfolio
  link? defer)
- Forwarding inbound recruiter messages
- AI-tailored body at compose time (combines slice 06 tailoring
  with this slice's send — interesting future slice)

## Starter task list

1. Create `infra/gmail/GCP-SETUP.md` and walk through the GCP
   project creation; capture client_id + client_secret. (Blocks
   deploy-time end-to-end testing but no other tasks.)
2. Migration `0007_gmail_integration.sql` — submissions column,
   responses column, `gmail_credentials` table.
3. New stack `infra/gmail/template.yaml` + `samconfig.toml` — KMS
   key, poller Lambda skeleton, EventBridge rule, archive bucket,
   SSM SecureString placeholder.
4. `backend/src/common/gmail_client.py` — thin wrapper over
   `google-api-python-client` with refresh-token-aware credential
   loading and KMS encrypt/decrypt helpers. Reused by oauth +
   admin + poller + compose.
5. `backend/src/handlers/gmail_oauth.py` — start + callback. Tests.
6. `backend/src/handlers/gmail_admin.py` — status, disconnect,
   link/unlink, with the Message-ID parser. Tests.
7. `backend/src/handlers/gmail_poller.py` — history.list loop,
   watched-thread + self-sent filter, heuristic classifier
   dispatch, response insert, status bump, S3 archival. Tests.
8. `backend/src/handlers/gmail_compose.py` — MIME builder, resume
   S3 fetch, send + auto-link / reply-mode threading. Tests.
9. `infra/api/template.yaml` — additive routes for OAuth + admin +
   compose handlers.
10. Frontend: `Settings.tsx` connect/disconnect with scope display,
    `GmailComposeModal.tsx` component, `Submissions.tsx` detail-
    page wiring (paste field + Compose button + per-response Reply
    buttons + classification badges).
11. Manual end-to-end test through real Gmail accounts: all four
    paths (compose / reply / manual-paste / recruiter-originated).
12. End-of-slice: write `docs/slices/10-?.md` plan based on what's
    next (manual reclassification UI? AI-tailored body at compose
    time? cover-letter attachment? per-user timezone? Pub/Sub
    upgrade if latency complaints surface?).