# Slice 12 — AI-tailored email body at compose time (PLAN)

Status: planned, not started. Branch: `slice/12-ai-email-draft` (TBD).
Depends on slice 06 (AI tailor — `ai_tailor` Lambda + Bedrock plumbing)
and slice 10 (compose flow) shipped, both of which they are.

## Why this slice

Today the user manually drafts every email body in the compose
modal. They already use AI to tailor the resume title/summary to a
JD via slice 06 — the same JD + tailored summary + master resume
context is sitting right there when they're composing. Letting the
SPA generate a draft body from that context reduces the friction of
"I want to apply right now and follow up with a custom email" to a
two-click flow:

1. Click Compose.
2. Click "Draft with AI" in the modal — body fills with a tailored
   first draft anchored to the role + JD + user's voice. User edits
   before sending.

The user's locked guardrails (memory: `project_ai_tailoring`) carry
forward verbatim:

- Output must not trip ATS-side AI-detection filters.
- Preserve the user's voice — no template-y phrasing, no obvious
  LLM tics.
- Output is a *draft*, not the final send. The user reviews and
  edits before the message goes out.

Crucially: the AI never auto-sends. It only populates the body
field. Send still requires an explicit click.

## Locked decisions to carry in

- **Submission-anchored drafts only** for v1. The AI prompt needs
  the JD + role + tailored summary as input; cold contact-only
  outreach (no submission) doesn't have that context. Compose modal
  shows the "Draft with AI" button only when a submission is
  selected in the picker.
- **Reuse existing `ai_tailor` Lambda + Bedrock plumbing.** Don't
  spin up a new AI handler — extend the existing one with a new
  prompt mode, or add a sibling endpoint that shares its Bedrock
  client / IAM. Cost-minimization rule applies (memory:
  `feedback_cost`).
- **No AI-tailor in reply mode for v1.** Replying to a thread is
  context-rich (the original email body) but stylistically different
  enough that a single prompt template won't cover both compose and
  reply well. Defer reply-mode AI to slice 12.5.
- **No streaming UI.** Wait-then-replace, matching the existing
  resume-tailor flow. Single non-streamed Bedrock call.

## Forks the user needs to decide

### Fork 1 — Where the prompt context comes from

The AI needs: role title, company name, JD text, user's master
resume title/summary (or tailored equivalent), recipient context (who
are we writing to). What gets pulled?

- *Backend pulls everything* — SPA sends `{submission_id,
  contact_id?, recipient_role?: 'recruiter'|'hm'|'unknown'}`; the
  Lambda fetches submission detail (JD, role, company, tailored or
  master resume), contact detail (name, kind), composes the prompt
  internally, calls Bedrock, returns `{body}`.
- *Frontend assembles the prompt body* — SPA gathers the same fields
  it already has rendered (it's looking at SubmissionDetail before
  opening Compose) and passes them to a thin AI endpoint.
- **Recommend Backend pulls everything.** Less data on the wire,
  fewer ways to forget a field, and the prompt-construction logic
  belongs server-side anyway (where the model/prompt template lives).
  SPA just sends the foreign-key ids.

### Fork 2 — Where to put the new endpoint

- *Extend `ai_tailor.py`* with a new route key
  `POST /ai/draft-email` (or a new mode in the existing route).
- *Sibling Lambda* `ai_email_draft.py` with its own route, sharing
  Bedrock IAM via the ai stack template.
- **Recommend Sibling Lambda.** Slice-06 `ai_tailor.py` has a
  narrow contract (master title/summary + JD → tailored title/summary).
  Mixing email-draft generation into it muddies the contract and
  makes the prompt template harder to evolve. The cost is a few more
  CFN lines in `infra/ai/template.yaml`; trivial.

### Fork 3 — How the user picks the recipient role / tone

The same JD might warrant a different message depending on whether
you're writing to the recruiter or the hiring manager. The model
needs that signal.

- *No selector* — let the user write the To: line and trust the
  model to infer from email domain + name. Brittle.
- *Selector: recruiter / hiring manager / referral* — three radio
  buttons in the modal. Influences the prompt template.
- *Selector + free-text style hint* — same three buttons + an
  optional one-liner the user types (e.g. "warm, brief, mention
  shared interest in distributed systems").
- **Recommend Selector + free-text hint.** Keeps the v1 surface
  small but lets the user steer when the default isn't right. Hint
  is optional; left empty, the model uses just the role context.

### Fork 4 — When the contact is set, does AI know about them?

If the user is composing to a known contact, we have name + kind +
prior outreach history. The AI prompt could reference them by name
and check whether prior outreach justifies a "thanks for the chat
last week" opener.

- *Yes, include contact context* — name, kind, last outreach date if
  recent.
- *No, anonymous* — model gets only the role + JD + recipient role
  selector; user fills in name themselves.
- **Recommend Yes, include contact context.** Names + recent prior-
  outreach context are exactly what makes the draft feel non-template.
  Risk: the model fabricates details from a sparse context. Mitigate
  by feeding only what we have (no fields = don't reference them in
  the prompt) and instructing the model to never invent specifics.

### Fork 5 — Persistence of generated drafts

- *Don't persist* — the body lives in the modal until the user
  hits Send (and the existing send-handler writes it to
  `contact_outreach`/Gmail). If the user closes the modal, the
  draft is gone.
- *Auto-save to a `drafts` table* — keyed on submission_id, so the
  user can come back to it.
- *Save the AI-generated *prompt input* on the submission for
  audit/regeneration*.
- **Recommend Don't persist.** YAGNI for v1; the modal is a
  short-lived edit surface. Drafts table is a different feature
  ("save my work in progress") that belongs in its own slice.

## Proposed schema

No schema changes. The submission's `tailored_title`/`tailored_summary`
already exist (slice 06); JD text exists (slice 04 jd_snapshots);
contact context queries are already supported.

## Proposed routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ai/draft-email` | Body: `{ submission_id, contact_id?: int, recipient_role: 'recruiter' \| 'hiring_manager' \| 'referral', style_hint?: string }`. Returns `{ body: str }`. Conditional on the ai stack being deployed. |

## Backend behavior

### New `ai_email_draft.py` handler

1. Validate body. Reject if `submission_id` missing. `contact_id`
   optional. `recipient_role` required, must match the enum above.
   `style_hint` optional, length-capped (e.g. 280 chars).
2. Fetch submission detail (role_title, company_name via join,
   jd_text from jd_snapshot, tailored_title, tailored_summary OR
   master title/summary if tailored absent). Verify ownership.
3. If `contact_id` provided, fetch contact (name, kind,
   last_outreach_at, last_outreach_method). Verify ownership.
4. Compose prompt template (see below). Call Bedrock via the same
   client setup as `ai_tailor`.
5. Strip any AI-tic phrasing ("As an AI…", " — Claude") from the
   response. (Trust-but-verify guardrail.)
6. Return `{ body: str }`.

### Prompt template (sketch)

```
You are drafting an email body for {USER}, a {USER_ROLE_FROM_RESUME}
applying to a {ROLE_TITLE} role at {COMPANY_NAME}. The recipient is
the {RECIPIENT_ROLE}{IF CONTACT_NAME: , {CONTACT_NAME}}.

Here is the role's job description:
{JD_TEXT}

Here is {USER}'s master resume summary (use it to ground the email
in their actual background; don't invent achievements):
{MASTER_OR_TAILORED_SUMMARY}

{IF STYLE_HINT: Style: {STYLE_HINT}}

Write the email body only. No subject line. Plain prose. Concise
(150-250 words). Conversational, not formal-corporate. Reference one
specific thing from the JD that connects to {USER}'s background. Do
not invent details about {USER}'s history or the company beyond what
is provided. Do not include placeholder text like [your name] or
[company]. Sign off with a single short line like "Best, {USER}".
```

The prompt design carries the locked guardrails (no inventions, no
template tics). Specific phrases tuned during implementation as the
user reviews drafts.

### Cost guardrail

Reuse the existing Bedrock model from slice 06 (Claude Haiku per
architecture decisions, or whatever is configured). Per-call cost is
similar to existing tailor flow. No new IAM beyond what
`ai_tailor.py` already has, just a new Lambda sharing the same
Bedrock invoke permission.

## Frontend wiring

### `GmailComposeModal.tsx` extensions

- Conditional **"Draft with AI"** button next to the Body textarea
  label. Only rendered when:
  - `submissionPick` is non-empty (a submission is selected),
  - `ai/draft-email` route is registered (gated similarly to the
    existing AI tailor button on SubmissionDetail — `aiEnabled`
    check).
- New props:
  - `defaultRecipientRole?: 'recruiter' | 'hiring_manager' | 'referral'`
    — set from the contact's `kind` when launching from ContactDetail
    (recruiter contact → 'recruiter'; personal contact → 'referral'),
    or null when launched from SubmissionDetail.
- New state:
  - `recipientRole`: radio-bound, defaults from prop or 'recruiter'.
  - `styleHint`: optional text input, defaults empty.
  - `aiBusy`: bool, disables Draft button + spinner while generating.
  - `aiMessage`: optional inline status ("Draft loaded — review and
    edit before sending.").
- "Draft with AI" → POST `/ai/draft-email` with the picker'd
  submission_id + contact_id + recipientRole + styleHint. Replaces
  the current `body` state with the response. (If the user already
  typed something, confirm before overwriting.)

### `SubmissionDetail.tsx` integration

Already wired through `defaultSubmissionId`; no change. The Compose
modal now offers AI drafting since a submission is anchored.

### `ContactDetail.tsx` integration

When opening Compose from a contact, the contact's `kind` flows
through as `defaultRecipientRole` (recruiter → 'recruiter', personal
→ 'referral'). User can still change in the modal.

## Test plan

### `test_ai_email_draft.py` (new)

- Happy path: submission + contact + recipient_role → returns
  non-empty body string.
- Validation: missing submission_id → 400; bad recipient_role → 400;
  style_hint over 280 chars → 400.
- Submission not owned → 404.
- Contact not owned (when provided) → 404.
- Bedrock 5xx → 502 (or whatever the existing ai_tailor returns; be
  consistent).
- Prompt construction: when contact provided, the prompt includes
  contact name; when absent, doesn't reference name.
- AI-tic stripping: a stubbed response containing "As an AI" gets
  scrubbed before return.

### Frontend manual test

- From SubmissionDetail Compose: button visible, click drafts, body
  fills, can edit + send.
- From ContactDetail Compose: pick a submission in picker, button
  becomes visible. Defaults recipient role from contact kind.
- Without a picked submission: button hidden / disabled.
- Reply mode: button hidden (out of scope for v1).

## Out of scope (deferred)

- **AI in reply mode**: drafting a reply to an incoming message
  needs a different prompt + the original message context. Slice 12.5
  candidate.
- **Streaming output**: for a 250-word draft, wait-then-replace is
  fine. If the user complains about latency, revisit.
- **Multi-draft / variations**: "give me three options to pick
  from." More tokens, more cost; defer.
- **Persistence of drafts**: see Fork 5.
- **AI-detection-evasion testing**: there's no objective metric.
  Trust the prompt design + user review. If a draft does trip an
  ATS filter, the user catches it on review.
- **Tone presets**: "formal / casual / brief / detailed" radio.
  Style hint covers this in v1.

## Starter task list

1. User decides forks 1–5.
2. Backend: new `ai_email_draft.py` Lambda + tests.
3. Infra: register the new function in `infra/ai/template.yaml` with
   Bedrock invoke permission. Register the route in
   `infra/api/template.yaml` (or wherever AI routes are registered
   today).
4. Frontend: extend `GmailComposeModal.tsx` with recipient-role
   radios, optional style hint, "Draft with AI" button, and the
   submission-picker gate.
5. Frontend: pass `defaultRecipientRole` from `ContactDetail.tsx`
   (and skip from `SubmissionDetail.tsx`).
6. Manual end-to-end with real Bedrock + a real submission.
7. End-of-slice handoff doc.

## Resumption notes

- Slices 06 (AI tailor) and 10 (compose) are shipped and verified.
- The AI guardrails memory (`project_ai_tailoring`) is the source of
  truth for what the output must look like.
- Cost is small but non-zero; each "Draft with AI" click is a
  Bedrock call. The user is fine with that for personal use.
- `aiEnabled` flag in `frontend/src/api/client.ts` already gates the
  existing tailor button — reuse that gate for the new button.