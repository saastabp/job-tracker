# Slice 09 — Recruiter response capture via Gmail (PLAN)

Status: planned, not started. Branch: `slice/09-email` (active; name kept
even though the implementation pivoted away from SES inbound).

## Pivot from the previous draft

Earlier drafts of this slice routed inbound recruiter mail through SES
inbound + a per-submission `apply+<token>@<domain>` Reply-To alias. That
design fell apart on two counts: (1) recruiters see a randomized opaque
alias instead of the user's real gmail address, which damages
candidate-side perception, and (2) back-and-forth threads break unless
the user re-injects the Reply-To header on every outbound message — i.e.
turning a job tracker into a chore.

This plan replaces that with **read-only Gmail API access**. The user
keeps applying through their normal Gmail client, recruiters see
`saastabp@gmail.com` as they always would, and the app reads the gmail
inbox via a refresh-token-authorized poller that links replies back to
submissions by `thread_id`. No alias, no MX records, no SES inbound
stack, no DNS changes.

## Why this slice next

Same as before. After slice 08 the only major missing piece in the
user-visible loop is **recording responses**. The `responses` table
exists from slice 01 and `submissions._detail` already surfaces
`responses[]` — but nothing writes to it. A submission's status moves
forward only when the user clicks a dropdown; there is no signal from
the recruiter side feeding back in. This slice closes that loop.

## Locked decisions to carry in

- **New SAM stack `infra/gmail/`** — separable from `data` / `api` /
  `scheduler` / `ai` / `dns`. Per `feedback_stacks`, anything tearable
  on its own gets its own stack. Holds the OAuth client config (in
  SSM), the poller Lambda, its EventBridge schedule, and the KMS key
  used to encrypt refresh tokens. The OAuth start/callback HTTP routes
  ride the existing `infra/api/` API Gateway (matches the pattern
  where the api stack hosts the schedule-creation route while the
  scheduler stack hosts the notifier).
- **Read-only scope (`https://www.googleapis.com/auth/gmail.readonly`).**
  No labeling, no sending, no draft creation. Smallest scope that still
  lets us read message bodies for classification and archival.
- **Thread-id is the link**, not message-id. A submission has 0..1
  `gmail_thread_id`; every message in that gmail thread becomes a
  `responses` row. Back-and-forth chains attach automatically because
  Gmail's own threading produces a stable `threadId`.
- **`responses` and `response_classifications` already exist.** Migration
  0001 created them with seeds. No catalog change for the basic pipeline.
- **S3 archival of raw email stays.** `responses.raw_email_s3_key` is
  in the schema; we keep using it. Reason: if OAuth gets revoked or the
  user changes gmail accounts, we still have the bodies for re-running
  classification later. Cost is trivial.
- **Catalog-friendly classification** — adding a class is one seed
  insert, per `project_extensibility`.
- **Multi-tenant from day 1**, per `project_extensibility` and the
  wife's tracker-tickler fork. Refresh tokens are stored per-user, not
  globally.
- **Per-deployment Google Cloud project.** Each fork (this repo, the
  tracker-tickler fork, future forks) creates its own GCP project +
  OAuth client. The OAuth client id/secret live in SSM, not in code.
- **Outbound is still out of scope.** Same as the previous draft. The
  user's normal mail client sends; we only read.

## Forks the user needs to decide before code starts

### Fork 1 — Polling vs Pub/Sub push

- *Polling*: EventBridge schedule fires the poller Lambda every N minutes.
  Lambda calls `users.history.list(startHistoryId=last_history_id)` per
  user, processes new messages in watched threads, advances the cursor.
  Simple, fully AWS-side, idempotent via the `historyId` cursor.
- *Pub/Sub push*: Gmail `users.watch` posts change notifications to a
  GCP Pub/Sub topic. A push subscription forwards them to a Lambda URL
  on AWS. Near-realtime, but adds a GCP project resource (the topic +
  subscription), webhook auth, and a 7-day re-`watch` requirement
  (Gmail expires watches after 7 days).
- **Recommend Polling at 10-minute cadence.** Recruiter responses
  arrive over hours/days; sub-minute latency buys nothing. Polling
  keeps the entire infra inside AWS, avoids the GCP push surface
  area, and the cadence is trivial to tune later. Pub/Sub is the
  right call only if the user starts wanting "ping me the moment a
  reply lands" UX, which slice 09 isn't doing anyway.

### Fork 2 — Classification: heuristic-only, AI-assisted, or both?

(Carried forward from the previous draft — Gmail-vs-SES doesn't
change the classifier shape.)

- *Heuristic*: keyword patterns on subject/body
  (`unfortunately|not moving forward` → rejection, `schedule|calendly`
  → interview_invite, etc.). Cheap, predictable, occasionally wrong.
- *AI via Bedrock*: re-use the existing `infra/ai/` stack to call
  Claude with a small prompt, return a `short_name` from the catalog.
  Better recall on edge cases, ~$0.001/email.
- *Both* (heuristic first, AI on miss/`other`): default to heuristic;
  fall through to Bedrock when heuristic returns `other` or low
  confidence.
- **Recommend Both.** Heuristic catches the obvious 80%; Bedrock handles
  the long tail without paying per-email when not needed. Bedrock call
  gated by env var so the AI stack is still tear-downable
  (`AI_STACK_ENABLED=false` → heuristic-only fallback).

### Fork 3 — What does the user see when a response lands?

(Carried forward.)

- *Just the row*: `responses[]` shows up in the submission detail; no
  active notification.
- *Status bump*: classifier output drives an automatic status change
  (rejection → `rejected`, interview_invite → `interviewing`); user
  reverts manually if wrong.
- *Email reminder*: scheduler stack's notify Lambda sends a "you got a
  reply on <role> @ <company>" email.
- **Recommend Just the row + Status bump (no email).** Adding a "you
  got a reply" email when the user already saw the original reply in
  their gmail inbox is noise. Status bumps are reversible from the UI.
  Email reminders can land in a later slice if dashboard misses become
  a real complaint.

### Fork 4 — Refresh-token storage

- *KMS-encrypted column on `gmail_credentials`*: Lambda holds a KMS-key
  ARN, encrypts at write, decrypts at read. One DB row per user; the
  cursor (`last_history_id`) lives on the same row, so polling state
  is co-located with the credential. Adds a KMS key to the gmail stack
  (~$1/mo).
- *SSM Parameter Store SecureString, one per user*: `/jobtracker/gmail/
  refresh-token/<user_id>`. Free for standard params (10K/region),
  AWS-managed encryption at rest, no app-side KMS calls. Cursor lives
  separately in `gmail_credentials` (split storage).
- **Recommend KMS-encrypted column.** Co-locates the credential and the
  polling cursor — one row per user holds everything the poller needs,
  one transaction reads/updates both. The $1/mo KMS-key cost is
  acceptable; stack-tear-down deletes the key, invalidating leftover
  ciphertext, which is the right safety story.

## Proposed schema

Combine into one migration `0007_gmail_integration.sql`:

```sql
-- Per-submission link to a Gmail thread. Nullable — submissions created
-- before slice 09 (and submissions the user never bothers to link)
-- carry NULL and simply never get auto-attached responses. The user
-- pastes an RFC 822 Message-ID (any single message in the thread); the
-- backend resolves it to the API thread_id via Gmail search and stores
-- the thread_id here. Thread, not message, because Gmail's threading
-- gives us the back-and-forth chain for free once we have the thread.
ALTER TABLE submissions
    ADD COLUMN gmail_thread_id VARCHAR(64) NULL AFTER notes,
    ADD UNIQUE KEY uq_submissions_gmail_thread (gmail_thread_id);

-- Idempotency anchor on responses: every Gmail message has a unique id;
-- the unique key prevents the poller from inserting the same message
-- twice if a poll cycle is retried after partial failure.
ALTER TABLE responses
    ADD COLUMN gmail_message_id VARCHAR(64) NULL AFTER raw_email_s3_key,
    ADD UNIQUE KEY uq_responses_gmail_message (gmail_message_id);

-- One row per user who has connected their gmail. Refresh token is
-- stored as a KMS-encrypted blob; the gmail stack's KMS key id is
-- baked into the poller Lambda's env. Soft-delete via deleted_at when
-- the user disconnects — keeping the row lets us show "last connected"
-- in the UI without losing the audit trail.
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
- No `gmail_credentials_*` catalog table — `scopes` is a denormalized
  string because OAuth scopes are an external system's concept and
  pinning them to a catalog adds friction every time Google changes
  scope strings.
- `last_history_id` is `VARCHAR(64)` to match Gmail's API contract
  (string-typed even though numeric in practice).
- The `responses` ALTER is additive and safe on existing rows.

## Proposed routes

The poller Lambda is invoked by EventBridge — no public route. The
OAuth dance and admin actions live on the existing API:

| Method | Path | Purpose |
|---|---|---|
| GET | `/integrations/gmail/status` | Returns `{ connected: bool, gmail_address: str?, last_polled_at: ts? }` |
| GET | `/integrations/gmail/oauth/start` | Generates state token, returns `{ consent_url }` for the SPA to redirect to |
| GET | `/integrations/gmail/oauth/callback` | Receives `code` + `state` from Google, exchanges for tokens, stores refresh token, redirects to SPA |
| DELETE | `/integrations/gmail` | Disconnects: revokes refresh token at Google, soft-deletes `gmail_credentials` row |
| PUT | `/submissions/{id}/gmail-link` | Body `{ mid }`. Accepts a `mid:` URI, an angle-bracketed Message-ID, or a bare Message-ID. Backend strips the prefix variants, resolves to `threadId` via `users.messages.list(q='rfc822msgid:<id>')`, stores `submissions.gmail_thread_id`. |
| DELETE | `/submissions/{id}/gmail-link` | Clears the link |

`submissions._detail` continues to render `responses[]` — no
read-route change needed.

## Linking workflow (the manual touch)

The link is established by pasting **an RFC 822 Message-ID from any one
message in the thread** — typically the application email the user just
sent. Backend resolves that message to its `threadId` server-side via
`users.messages.list(q='rfc822msgid:<id>')`, stores the `threadId` on
`submissions.gmail_thread_id`, and from then on every message in the
thread (recruiter's reply, the user's follow-up, the recruiter's next
reply, etc.) auto-attaches via the poller.

### Why Message-ID instead of the gmail web URL

The earlier draft had the user paste the Gmail web URL
(`https://mail.google.com/.../#sent/FMfcgz...`). That doesn't work:
the `FMfcgz...` form is a permalink/share ID, not the API `threadId`,
and `users.threads.get()` rejects it. Message-IDs are the RFC standard,
universally exposed by every mail client, and resolvable to a `threadId`
via one API search. They also work cross-client — gmail web, Thunderbird,
Apple Mail, mutt all surface them.

### Copy paths by client

- **Thunderbird**: open the message in Sent →
  **Right-click → Organize → Copy Message Link**. Result is a `mid:` URI
  like `mid:CAFus6uRsoh0ymLiiGb_uz1kN5w1QL8P9DoRv39W-UmRvr+nF1A@mail.gmail.com`.
- **Gmail web**: open the message → ⋮ menu → **Show original** → copy the
  value of the `Message-ID:` header. Gmail wraps it in `<...>`; the
  brackets are fine — the backend strips them.
- **Other clients**: any client that exposes the `Message-ID:` header
  works. The backend accepts the same input regardless of source.

### Walkthrough

1. User sends the application email through their normal mail client.
   No app involvement.
2. User opens the SPA, on the submission detail page (created earlier
   or right now — doesn't matter; `gmail_thread_id` is nullable and
   editable).
3. User pastes any text that *contains* a Message-ID. The parser is
   tolerant — anything below works:
   - `Message-ID: <77b734db-...@gmail.com>` (single-line copy from
     Gmail web's "Show original" header table — most common path)
   - `Message-Id: <...>` / `MESSAGE-ID: <...>` (header name is
     case-insensitive per RFC 822; parser is too)
   - A whole pasted block of headers, even with line breaks
     collapsed by the browser. Example real paste:
     `...boundary="------------..."Message-ID: <77b734db-5cce-4ae1-94ab-82a0aece8e4e@gmail.com>Date: Sat, 2 May 2026...References: <FCOjk_3KQOS9m_kpolPp5w@geopod-ismtpd-100>...`
     The parser grabs the `Message-ID:` value and ignores the
     adjacent `References:` and `In-Reply-To:` (which are also
     Message-IDs but identify *other* messages).
   - `mid:CAFus6uR...+nF1A@mail.gmail.com` (Thunderbird URI form)
   - `<CAFus6uR...+nF1A@mail.gmail.com>` (RFC 822 angle-bracket form)
   - `CAFus6uR...+nF1A@mail.gmail.com` (bare)
4. Backend normalizes in two steps:
   - **Step 1 — header-anchored extraction.** Run the regex
     `(?i)message-id\s*:\s*<?([^\s<>]+@[^\s<>]+)>?` against the
     input. The `(?i)` makes it case-insensitive (catches
     `Message-ID` / `Message-Id` / `MESSAGE-ID`). Anchoring on
     `message-id` specifically is what keeps `References:` and
     `In-Reply-To:` from matching — those substrings don't
     contain `message-id`. If the regex matches, use group 1.
   - **Step 2 — fallback for prefix-free pastes.** If step 1
     doesn't match, strip a leading `mid:`, surrounding `<>`,
     and whitespace from the entire input. Whatever is left is
     the candidate.
   Then call `users.messages.list(q='rfc822msgid:<normalized>')`
   with the user's gmail credential, pick the returned `threadId`,
   store it on `submissions.gmail_thread_id`. The lookup itself
   is the ownership check — if the message isn't in the user's
   mailbox, the search returns empty and the API returns `404`.
5. From that point on, every message in that thread arrives as a
   `responses` row within the next poll cycle.

### Parser notes

- **Preserve `+` characters.** Gmail-generated Message-IDs frequently
  contain `+` (e.g., `...UmRvr+nF1A@mail.gmail.com`). JSON request
  bodies preserve `+` natively; do NOT URL-decode the value or `+`
  becomes space and the search returns nothing. (The route accepts
  the value via JSON body for exactly this reason — query strings
  would force encoding decisions.)
- **Don't trust client-side validation alone.** The server runs the
  same normalization regardless of what the SPA sent.

### Deferred enhancement

A "recent sent threads" picker that lists the user's last N sent
messages and lets them click instead of paste. Considered for v1
when the URL-paste plan fell through; rejected because Thunderbird's
one-click "Copy Message Link" closes most of the friction gap. Pulls
back in if the picker becomes the user's preferred shape.

## New stack: `infra/gmail/`

- `infra/gmail/template.yaml`
  - `AWS::KMS::Key` `GmailRefreshTokenKey` — symmetric, used by the
    OAuth-callback Lambda (encrypt) and poller Lambda (decrypt). Key
    policy grants only those two role ARNs.
  - `AWS::Lambda::Function` `jobtracker-gmail-poller` — VPC'd into the
    data subnet (RDS access); env: `GMAIL_KMS_KEY_ID`, DB params,
    `GMAIL_OAUTH_CLIENT_SSM_PATH`, `AI_STACK_ENABLED`,
    `INBOUND_BUCKET_NAME`.
  - `AWS::Events::Rule` ScheduleExpression `rate(10 minutes)` invoking
    the poller. Concurrency 1 per user via Lambda reserved concurrency
    + per-user advisory lock in DB to prevent overlapping polls.
  - `AWS::S3::Bucket` `jobtracker-gmail-archive-<acct>` — private,
    SSE-S3, lifecycle expiring objects after 180 days. The poller
    writes raw RFC822 bytes here keyed by
    `<user_id>/<gmail_message_id>.eml`; `responses.raw_email_s3_key`
    points at it.
  - SSM SecureString `/jobtracker/gmail/oauth-client` — JSON
    `{ client_id, client_secret }` populated manually post-deploy from
    the GCP console. Poller Lambda has `ssm:GetParameter` for this path.
- `infra/api/template.yaml` — additive: route handlers
  `gmail_oauth.py` (start + callback) and `gmail_admin.py` (status,
  disconnect, link/unlink). The api stack already owns the
  HttpApi; adding routes is a same-pattern addition.
- Makefile: `deploy-gmail` (depends on `deploy-data`), `delete-gmail`.
  Order in `deploy-all`:
  `network → data → auth → scheduler → api → frontend → ai → gmail
  → wire-frontend`. Note: `deploy-api` must run before `make migrate`
  per `feedback_migrate_after_deploy_api`; new `0007_gmail_integration
  .sql` won't be picked up until deploy-api re-bundles backend/src/.
  Frontend changes need `make sync-frontend` per
  `feedback_sync_frontend_in_deploy_list`.

## One-time GCP setup (manual, per deployment)

The Google Cloud side isn't IaC-friendly — has to be clicked through
once per deployment (this repo, the wife's fork, etc.). Documented
in `infra/gmail/GCP-SETUP.md` (created with the slice):

1. Create a GCP project (e.g., `jobtracker-prod-saastabp`).
2. Enable the Gmail API.
3. Configure the OAuth consent screen — *External*, *Testing* mode is
   fine for personal use; published mode requires verification which
   is overkill for a single-user app. Add the user's gmail address as
   a test user.
4. Create an OAuth 2.0 Client ID (type: Web application). Authorized
   redirect URI: `https://<api-domain>/integrations/gmail/oauth/callback`.
5. Copy the client_id + client_secret into the SSM SecureString
   created by the gmail stack.
6. (Re-)deploy the gmail stack and run a connection test from the SPA.

`feedback_verify_dns_delegation` doesn't apply here (we are not
adding domain records), but the redirect URI does have to match
exactly between GCP and the deployed API domain — the most common
"OAuth fails silently" cause.

## Test plan

- `test_gmail_oauth.py`:
  - state-token generation/validation (CSRF defense on the callback)
  - code → token exchange (mocked Google response)
  - refresh-token KMS encryption + DB persist
  - duplicate connection: existing `gmail_credentials` row gets updated
    not duplicated (the UNIQUE on `user_id` enforces this; test the
    upsert path)
  - disconnect path: revoke endpoint called, soft-delete applied
- `test_gmail_admin.py`:
  - Message-ID parser matrix:
    - `Message-ID: <77b734db-...@gmail.com>` (Gmail web "Show
      original" line copy, with brackets, with header-name prefix)
    - `Message-Id: <...>` and `MESSAGE-ID: <...>` (case variants)
    - Multi-header blob — paste a fixture string containing
      `Content-Type:`, `Message-ID:`, `Date:`, `References:`,
      `In-Reply-To:`, all run together with no line breaks
      (simulates browser-collapsed copy). Assert: regex extracts
      the `Message-ID:` value, NOT the `References:` or
      `In-Reply-To:` values
    - `mid:CAFus6uR...+nF1A@mail.gmail.com` (Thunderbird URI form)
    - `<CAFus6uR...+nF1A@mail.gmail.com>` (RFC 822 angle-bracket form)
    - `CAFus6uR...+nF1A@mail.gmail.com` (bare)
    - `mid:eb7d3002-ca80-4c68-984d-74e0cb802de0@gmail.com` (UUID-style
      Gmail auto-generated form, distinct from the `CA*@mail.gmail.com`
      form)
    - leading/trailing whitespace stripped
    - `+` characters preserved verbatim — assert by Message-ID round-trip
      through `json.dumps`/`json.loads` and into the search query
    - garbage input (no `@`, no `Message-ID:`) returns `400`
      from the route, not `404` from a Gmail miss
  - resolution: `users.messages.list(q='rfc822msgid:<id>')` returns a
    message → `threadId` extracted → stored on `submissions`
  - resolution miss: empty search result → `404` from the route, no
    write to `submissions`
  - ownership: the search runs against *the requesting user's* gmail
    credential, so a Message-ID belonging to another user's mailbox
    naturally returns empty (no extra check needed; document this)
  - unlink: clears `gmail_thread_id`
- `test_gmail_poller.py`:
  - history.list pagination (mocked)
  - watched-thread filter: only messages whose threadId is on a
    `submissions.gmail_thread_id` get processed
  - heuristic classifier matrix (rejection / interview / auto-ack /
    recruiter)
  - Bedrock fallback on heuristic miss (mocked AI client; respects
    `AI_STACK_ENABLED=false`)
  - status-bump matrix: rejection → rejected, interview_invite →
    interviewing, others no-op
  - idempotency: same `gmail_message_id` arriving twice produces one
    response row (UNIQUE key on `responses.gmail_message_id` is the
    enforcement; test that the second insert is caught and logged)
  - cursor advances only after batch success
  - S3 archival writes the RFC822 body before the row insert
- Manual end-to-end: run OAuth flow against a test gmail account,
  send a real recruiter-style email from a second account, confirm
  the next poll cycle creates a `responses` row + status bump.

## Frontend wiring

- New `Settings.tsx` (or extend existing): "Connect Gmail" button →
  hits `/integrations/gmail/oauth/start`, redirects to consent URL,
  returns through the callback, polls `/integrations/gmail/status`
  for `connected: true`. Show `last_polled_at` once set.
- `Submissions.tsx` detail page:
  - Add "Gmail message" field with paste-and-save UX. Help-text under
    the field: *"Paste from your sent application email — the parser
    grabs the Message-ID out of whatever you give it. In Thunderbird:
    Right-click → Organize → Copy Message Link. In Gmail web: ⋮ →
    Show original → triple-click the Message-ID line (or copy the
    whole header block; the parser will find it)."* Shows "Linked ✓
    (thread <abbreviated_id>)" or "Not linked" once set. Clear button
    calls DELETE.
  - `responses[]` rows already render; add a colored classification
    badge re-using `StatusBadge` styling.
  - Inline "promote to status" affordance becomes redundant once
    auto-bump lands (per fork 3) — strikethrough non-applicable here.

## Out of scope (deferred)

- Manual reclassification UI (`PUT /responses/{id}`).
- "Recent sent threads" picker — manual paste suffices for v1.
- Outbound email from the user (sending applications from inside the
  app). Same as previous draft.
- Pub/Sub push notifications (see fork 1).
- Per-user timezone for status bumps / dashboards.
- DLQ on the poller Lambda — re-add when missed responses become a
  real complaint, not theoretical.
- Multi-account support (one user with two gmail addresses) — schema
  allows it via the UNIQUE on `user_id` being relaxed later, but the
  UI assumes 1:1 in v1.
- Sent-side observation (capturing the user's outbound application
  emails so we can derive `gmail_thread_id` automatically) — possible
  later refinement; for v1 the user pastes the URL.

## Starter task list

1. **User decides forks 1–4.**
2. Create `infra/gmail/GCP-SETUP.md` and walk through the GCP project
   creation; capture the client id/secret. (This blocks deploy-time
   testing but no other tasks.)
3. Migration `0007_gmail_integration.sql` — submissions column,
   responses column, `gmail_credentials` table.
4. New stack `infra/gmail/template.yaml` + `samconfig.toml` — KMS
   key, poller Lambda skeleton, EventBridge rule, archive bucket,
   SSM SecureString placeholder.
5. `backend/src/handlers/gmail_oauth.py` — start + callback handlers.
   Tests.
6. `backend/src/handlers/gmail_admin.py` — status, disconnect,
   link/unlink-thread. Tests.
7. `backend/src/handlers/gmail_poller.py` — history.list loop,
   classifier dispatch, response insert, status bump, S3 archival.
   Tests.
8. `backend/src/common/gmail_client.py` — thin wrapper over
   `google-api-python-client` with refresh-token-aware credential
   loading. Reused by oauth + admin + poller.
9. `infra/api/template.yaml` additive routes for the OAuth + admin
   handlers.
10. Frontend: `Settings.tsx` connect/disconnect, `Submissions.tsx`
    thread field + classification badge.
11. Manual end-to-end test through a real gmail account.
12. End-of-slice: write `docs/slices/10-?.md` plan based on what's
    left (manual reclassification UI? sent-side observation? per-user
    timezone? Pub/Sub upgrade if latency complaints surface?).