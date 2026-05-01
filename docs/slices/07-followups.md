# Slice 07 — Follow-up reminders

Status: implemented. Branch: `slice/07-followups`.

## What landed

- New SAM stack `infra/scheduler/` (`template.yaml` + `samconfig.toml`).
  - `AWS::Scheduler::ScheduleGroup` named `jobtracker-followups`
    (dedicated namespace keeps the api-stack IAM scope tight).
  - `AWS::IAM::Role` for EventBridge Scheduler with `lambda:InvokeFunction`
    on the notify Lambda only.
  - `jobtracker-followup-notify` Lambda — IN VPC (needs to read
    `users.email` + `submissions` + write `follow_ups.notified_at`).
    SES policy scoped to the verified sender identity.
  - `AWS::SES::EmailIdentity` for the `SenderEmail` parameter
    (default `saastabp@gmail.com`). User clicks the verification email
    once before reminders will send.
  - SSM params published: `/jobtracker/scheduler/group-name`,
    `/jobtracker/scheduler/notify-arn`, `/jobtracker/scheduler/exec-role-arn`.
- Migration `0005_followups_scheduling.sql` — adds
  `notified_at TIMESTAMP NULL` and `auto_created BOOL DEFAULT FALSE`
  to `follow_ups`. Schedule names are deterministic (`followup-<id>`)
  so no `schedule_name` column.
- `backend/src/common/scheduler.py` — `schedule_followup` /
  `cancel_followup` helpers. Soft-fails when env vars empty (scheduler
  stack absent → api stack still works, dashboard pending count still
  correct, reminders just don't fire).
- `backend/src/handlers/followups.py` (10 unit tests) —
  GET `/follow-ups` (filterable by `?pending=1`),
  POST `/submissions/{id}/follow-ups` (manual create),
  PUT `/follow-ups/{id}` (edit `due_at` / `notes` / set `actioned`),
  DELETE `/follow-ups/{id}` (soft-delete).
  Each mutation reaches into the scheduler stack to register or cancel.
- `submissions.py` `_auto_queue_followup` — after a successful
  submission INSERT (and, if relevant, the JD upsert) inserts a
  follow-up row at `submitted_on + users.follow_up_days @ 14:00 UTC`,
  flagged `auto_created = TRUE`, schedules it. Skipped silently if
  `submitted_on` isn't supplied; soft-fails on the schedule call.
- `backend/src/handlers/followup_notify.py` (6 unit tests) — invoked
  by EventBridge Scheduler with `{"follow_up_id": <int>}`. Loads the
  row + parent submission + user email, renders a plain-text reminder,
  sends via SES, sets `notified_at`. Idempotent: skip on already-
  actioned / already-notified / soft-deleted rows.
- `infra/api/template.yaml` — new `FollowUpsFunction` + scheduler:*
  IAM grants (gated by `HasScheduler` condition: empty
  `SchedulerGroupName` parameter → no policy emitted, no env vars set,
  `common/scheduler` no-ops). `SubmissionsFunction` gains the same
  scheduler permissions. Sentinel ARN-shaped defaults on the two ARN
  parameters keep `cfn-lint` happy when the scheduler stack isn't
  deployed.
- Makefile: `deploy-scheduler` (depends on `deploy-data`, runs `sam
  build && sam deploy`), `delete-scheduler` (also clears the SSM
  params), `deploy-api` now reads `/jobtracker/scheduler/*` SSM and
  passes `SchedulerGroupName` / `Notify` / `RoleArn` overrides when
  present, `deploy-all` order is now
  `network → data → auth → scheduler → api → frontend → ai →
  wire-frontend`.
- Frontend:
  - `frontend/src/pages/FollowUps.tsx` — full follow-ups page (table
    of pending follow-ups; toggle for pending-only; Mark done /
    Delete inline actions). Linked from the new sidebar entry and
    the dashboard "Pending follow-ups" card.
  - `App.tsx` route `/follow-ups`. `AppShell.tsx` sidebar gains a
    "Follow-ups" entry between Contacts and Targets.
  - `SubmissionDetail.tsx` — disabled "Trigger follow-up" button
    replaced with a real "Add follow-up" affordance (prompts for due
    date, defaults to today + 7 at 14:00 UTC); inline list expanded
    with Mark done / Reschedule / Delete buttons + "auto" / "emailed"
    badges; FollowUp interface gains `notified_at` + `auto_created`.
  - `Dashboard.tsx` — "Pending follow-ups" card body is now a
    `<Link to="/follow-ups">`.

## Forks resolved

1. Creation path — **Both** (auto + manual). Auto creates one row at
   `submitted_on + users.follow_up_days`, manual via the new POST
   route + the SubmissionDetail "Add follow-up" button.
2. Send path — **Direct SES** (recommended path accepted). Notify
   Lambda calls `ses.send_email` in-line. Documented in
   `common/scheduler.py`: swap to SQS+consumer later is one Lambda's
   worth of change; nothing else moves.
3. Submission ↔ contact linking bundle — **No** (deferred). It gets
   its own micro-slice 08 (see plan below).

## Operational notes

- **First deploy of the scheduler stack** sends an SES verification
  email to `SenderEmail`. The user must click the link before any
  reminder will send (sandbox policy). `_send` catches missing
  `SENDER_EMAIL` cleanly and returns `skipped:no_sender` rather than
  raising — but a real `SES.MessageRejected` from sandbox-blocked
  recipients will raise out of the Lambda.
- **Tearing down the scheduler stack** (`make delete-scheduler`)
  leaves `follow_ups` rows alone. The dashboard pending count keeps
  working. The next `make deploy-api` re-emits the api Lambdas with
  empty scheduler env vars and the `HasScheduler` condition omits
  the IAM policy, restoring the api stack to scheduler-less mode
  cleanly.
- **Auto-create defaults to 14:00 UTC**. That's a global compromise
  (07 PT / 10 ET / 15 UK). Per-user timezone is still deferred (see
  `dashboard.py` time-semantics note).

## Out of scope (deferred)

- Email pipeline / inbound responses (`email` stack — likely slice 09).
- Submission ↔ contact linking (slice 08, see `08-submission-contacts.md`).
- SES production-access request (only matters if sending to
  non-verified addresses).
- DLQ on the notify Lambda's failures (re-add when a missed reminder
  becomes a real complaint, not theoretical).
- Per-user timezone (still on the `dashboard.py` deferral list).

---

# Original plan

(below for reference)



## Why this slice next

Three candidates were on deck after slice 06 (per slice-06 doc):

1. **Follow-up reminders** (new `scheduler` stack).
2. **Email pipeline** (new `email` stack — SES inbound + responses CRUD).
3. Submission ↔ contact linking (small, fits inside existing api
   stack).

Picking follow-ups for slice 07 because:

- The `follow_ups` table already exists from slice 01; the dashboard
  already counts pending follow-ups (`dashboard.py`). The piece missing
  is a way to **create** them and **be reminded** about them — the
  user-visible loop from slice-02's "Follow-ups pending" widget back to
  the user is broken without it.
- It exercises the `scheduler` stack shape (EventBridge → Lambda →
  outbound channel) which sets up the same plumbing used by future
  recurring jobs (e.g., a weekly digest, JD-snapshot purge sweeps).
- Email pipeline is bigger (SES verified domain, MX records, inbound
  rule sets, processor Lambda, response classification) and benefits
  from waiting until a real domain is on Route 53. Defer to slice 08.
- Submission ↔ contact linking is small enough to ride along inside
  this slice (a junction table + two routes + a UI affordance) **if**
  the user wants — flag and decide before code starts.

## Locked decisions to carry in

(From `architecture_decisions` memory + slice-stack-segregation rule.)

- **`scheduler` is its own SAM stack** (`infra/scheduler/`) so it can be
  torn down without touching `data` / `api` / `ai`.
- **Notification channel: SES outbound** (no Twilio, no push). The
  user's email is already in Cognito; SES sandbox is fine for personal
  use until production access is requested. Cost: $0.10/1000 emails →
  effectively free.
- **EventBridge Scheduler, not EventBridge rules.** EventBridge
  Scheduler ($1/M invocations) handles one-shot future-dated triggers
  natively; cron rules don't.

## Forks the user needs to decide before code starts

1. **Where does follow-up creation happen?**
   - *Auto-create on submission*: a follow-up is queued N days after
     `submitted_on` (configurable per user, default 7).
   - *Manual-only*: user clicks "Add follow-up" on the submission
     detail page; no auto-creation.
   - *Both*: auto-create the first one; user adds more by hand.
   - **Recommend Both**, defaulting to a single auto-created follow-up
     7 days after submission. Matches how the user is likely already
     thinking about it; respects the manual override.

2. **Reminder send mechanism — synchronous SES from the scheduler
   Lambda, or queue → SES?**
   - *Direct SES*: scheduler Lambda fires SES SendEmail in-line. Simple,
     fewer moving parts.
   - *SQS → email Lambda*: scheduler Lambda enqueues, a second Lambda
     consumes and sends. Decouples scheduling from delivery; lets you
     retry independently.
   - **Recommend Direct SES.** At personal-use volumes the queueing
     buys nothing. Revisit if the project ever sends > 100 emails/day.

3. **Bundle submission ↔ contact linking into this slice?**
   - *Yes*: small additive migration (`submission_contacts` junction),
     two API routes, a contacts-multiselect on submission detail.
     Maybe ~half a day extra.
   - *No*: punt to slice 08 or its own micro-slice.
   - **Recommend No.** Keeps slice 07 narrow and the scheduler stack
     can ship cleanly. Submission↔contact is fine as a follow-up.

## Proposed routes

| Method | Path | Purpose |
|---|---|---|
| POST | `/submissions/{id}/follow-ups` | create a follow-up (manual) |
| PUT | `/follow-ups/{id}` | mark actioned, update due_at, edit notes |
| DELETE | `/follow-ups/{id}` | soft-delete |
| GET | `/follow-ups` | list (filter `?pending=1`, etc.) |

Auto-create runs in the existing `submissions.py` create path
(transaction-attached, no new endpoint).

## New stack: `infra/scheduler/`

- `infra/scheduler/template.yaml` — one Lambda
  (`jobtracker-followup-notify`) + EventBridge Scheduler permission +
  SES SendEmail policy (scoped to the user's verified identity).
- The scheduler creates a one-shot schedule per follow-up (target =
  the notify Lambda, payload = `{ follow_up_id }`). On send,
  the Lambda updates `follow_ups.notified_at` so the next dashboard
  load sees the right state.
- VPC config: probably **outside the VPC** (the Lambda needs DB to
  read user email + follow-up details — but the email is in Cognito,
  retrievable via AdminGetUser, and the follow-up notes can travel in
  the schedule payload). Decide during design.

## Test plan

- `test_followups.py` for the new handler.
- `test_submissions.py` extended for auto-create-on-create.
- `test_scheduler_notify.py` for the notify Lambda — mock SES + DB.
- Manual: create a submission, see auto-follow-up scheduled, fast-
  forward by editing `due_at` to ~now+1 min, verify email arrives.

## Frontend wiring

- `SubmissionDetail.tsx`: replace the "Trigger follow-up" disabled
  button with a real one. Inline list of follow-ups (already
  rendered) gains action buttons: mark actioned, edit due date,
  delete.
- New `FollowUpsPage.tsx` that lists all pending follow-ups across
  submissions, sortable by due date.
- Sidebar: a "Follow-ups (N pending)" link.

## Out of scope (deferred)

- Email pipeline / inbound responses (slice 08, `email` stack).
- Submission ↔ contact linking (slice 09 or micro-slice).
- SES production-access request (only matters when sending to
  non-verified addresses).

## Starter task list

1. Decide forks 1–3 (user).
2. Migration: any new follow-up columns (e.g., `notified_at`,
   `auto_created`) — check current schema first.
3. New stack: `infra/scheduler/template.yaml` + Makefile targets.
4. `backend/src/handlers/followups.py` + tests.
5. Auto-create wiring in `submissions.py` + tests.
6. Notify Lambda + SES policy.
7. Frontend wiring.
8. Manual end-to-end test against real SES.
9. End-of-slice: write `docs/slices/08-?.md` plan.