# Slice 07 — Follow-up reminders (PLAN)

Status: planned, not started. Branch: `slice/07-followups` (TBD).

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