# Slice 06 — AI integration: resume tailoring + PDF mining

Status: implemented. Branch: `slice/06-ai-tailoring`.

## What landed

- New SAM stack `infra/ai/` (`template.yaml` + `samconfig.toml`).
  Two non-VPC Lambdas (`jobtracker-ai-mine`, `jobtracker-ai-tailor`),
  Bedrock `InvokeModel` policy scoped to `anthropic.*` foundation models
  + cross-region inference profiles. Own HttpApi with the same Cognito
  JWT authorizer config the api stack uses (read from SSM, no CFN
  exports — keeps the AI stack tearable-down per `feedback_stacks`).
- Makefile: `deploy-ai` / `delete-ai` standalone targets,
  `gen-frontend-env` writes `VITE_AI_API_URL`, `wire-frontend` re-applies
  CORS to the AI stack only when it exists, `deploy-all` includes ai.
- `backend/src/handlers/ai_mine.py` + `ai_tailor.py` using
  `AnthropicBedrock` (matches the regular Anthropic SDK surface so a
  swap is a 2-line change). Haiku 4.5 inference profile
  (`us.anthropic.claude-haiku-4-5-20251001-v1:0`). Custom
  `_AiServiceError` distinguishes "model returned garbage" (502) from
  "user input bad" (400).
- `backend/tests/unit/test_ai_mine.py` (7 tests),
  `test_ai_tailor.py` (10 tests). Patches the bedrock client; asserts
  prompt construction carries master fields verbatim, fenced-JSON
  parsing, parse failures and empty-field outputs both map to 502.
- Frontend:
  - `frontend/src/api/client.ts` routes `/ai/*` paths to
    `VITE_AI_API_URL`. Friendly error if AI stack not yet deployed.
  - `frontend/src/components/pdfText.ts` — pdfjs-dist text extraction,
    lazy-imported on first use so the ~480 KB pdfjs payload doesn't
    bloat the SPA initial bundle.
  - `ResumeForm.tsx` auto-prefill: drop a PDF with blank title/summary
    → pdfjs extracts text → POST `/ai/mine-resume` → fields populate
    with an "auto-filled — edit if needed" badge.
  - `SubmissionDetail.tsx` "Tailor with AI" button: pulls the master
    resume's title+summary + the JD text, POSTs `/ai/tailor`,
    populates the tailored fields. User reviews + saves (suggest-only,
    fork 2).

## Forks resolved

1. PDF extraction — **client-side via pdfjs-dist** (recommended path
   accepted). Lambda stays text-only, no S3 read or PDF library.
2. Tailored output — **suggest-only** (recommended path accepted). User
   can re-roll without polluting the row.
3. Final tailored PDF — **MVP copy-paste** (recommended path accepted).
   Structured-resume rendering deferred indefinitely.

## Out of scope (still deferred)

- AI response classification (lands with `email` stack).
- Tailoring multi-section experience bullets (locked OFF per
  `project_ai_tailoring`).
- Structured-resume PDF rendering.

---

# Original plan

(below for reference)



## Why this slice

Two AI features pay for the whole `ai` stack:

1. **Resume tailoring** — the user pastes a JD, sees an AI-suggested
   tailored title + summary scoped to that role. This is the headline
   feature that justifies the project. Tailored output already has a
   home: `submissions.tailored_title` / `submissions.tailored_summary`
   columns are in the slice-01 schema and the submission detail UI
   already renders them.
2. **PDF → title/summary auto-prefill on resume create.** Slice 04
   tried regex/heuristic extraction and pulled it out at ~50% accuracy
   (see slice-04 doc, "Deferred to slice 06"). AI handles the failure
   modes cleanly — different problem at $0.0001 per call.

Both share the same AI Lambda + the same Bedrock policy + the same
non-VPC networking. Bundling them into one slice means the `ai` stack
gets stood up once.

## Architectural decisions — already locked, do NOT relitigate

(See `architecture_decisions.md` memory + `docs/slices/04-resumes.md`
"Slice 06" section for the full reasoning.)

1. **AI Lambda runs OUTSIDE the VPC.** No Bedrock interface VPC endpoint
   (~$15/mo saved). All inputs come in the request body; the Lambda
   never touches the DB. Bedrock is reached over AWS-managed networking
   — IAM still authenticates the call.
2. **AI provider: Bedrock via `AnthropicBedrock` Python client.** API
   surface matches the regular Anthropic SDK so swapping providers
   later is a 2-line change.
3. **Model: Haiku 4.5** for both features. Cheap, fast, plenty smart for
   the scoped task.
4. **AI-primary, no heuristic fallback** for PDF mining. Reintroducing a
   regex baseline would bring back the wrong-answer-confidently failure
   mode that got it deferred in the first place.

## AI tailoring — scope and prompt constraints

(From `project_ai_tailoring.md` memory — also locked.)

- **Tailored fields are limited to TITLE and SUMMARY only.** Work
  experience, accomplishments, bullets, skills, education, dates,
  company names are OFF-LIMITS to the model. Letting AI rewrite
  experience risks fabrication; interview-time, that's brutal.
- **Prompt design constraints:**
  - Preserve the user's voice — pull style cues from the master
    title/summary, don't superimpose generic resumespeak.
  - Avoid AI-detection signatures (no "Strategic [adjective] [noun]
    with [N] years of experience…" filler — top GPTZero/Originality
    signal). Vary sentence length and rhythm.
  - Keep the tailored title under ~80 chars and the summary under
    ~3 sentences. Both are short surfaces — AI-detection signals
    don't reliably fire on text that small.

## Forks the user needs to decide before code starts

1. **PDF text extraction lives client-side or server-side?**
   - *Client-side (lift from slice-04 git history):* `pdfjs-dist` runs
     in the browser, the text is POSTed as a string to `/ai/mine-resume`.
     AI Lambda stays text-only — no S3 read, no PDF library in the
     deployment package.
   - *Server-side:* AI Lambda fetches the PDF from S3 (presigned GET),
     parses it with a Python PDF lib, then prompts. Larger Lambda
     bundle, S3 read perms needed, but no pdfjs in the SPA bundle.
   - **Recommend client-side.** The slice-04 PDF infra (drop zone,
     direct-to-S3 upload) keeps the Lambda lean; pdfjs-dist is already
     proven in this stack from slice-04 (later reverted, but the dep
     and lazy-loading shape are well understood).

2. **Where does the tailored output land?**
   - *Direct write:* `POST /ai/tailor` writes
     `submissions.tailored_title` / `tailored_summary` and returns the
     row.
   - *Suggest-only:* the endpoint returns the suggested title/summary;
     the SPA shows them in the submission detail's tailored fields with
     an "Apply" button that saves.
   - **Recommend suggest-only.** The user-voice preservation goal
     implies the human still gets a final review before persistence;
     also lets them re-roll without polluting the row.

3. **How does the final tailored PDF get produced?** (Open question
   from `project_ai_tailoring.md` — defer is fine.)
   - *MVP:* user copies the tailored title+summary into their own
     resume editor, exports PDF, uploads as a new submission-attached
     resume.
   - *Better UX (later):* store resumes as structured sections, render
     PDF on-demand with the tailored overrides applied. Big lift.
   - **Recommend MVP.** Punt the structured-resume work until users
     actually ask for it — copy-paste is fine for personal use and
     keeps slice 06 narrow.

## Proposed routes

| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/mine-resume` | text → `{title, summary}` (Haiku 4.5) |
| POST | `/ai/tailor` | `{master_title, master_summary, jd_text}` → `{tailored_title, tailored_summary}` |

Both Lambdas live in a new `ai` SAM stack (`infra/ai/template.yaml`).
Both reuse the existing Cognito JWT authorizer on `HttpApi` — the API
stack exports the authorizer and the AI stack imports it (or the
HttpApi gets re-imported and the routes attach to it).

## New stack: `infra/ai/`

- `infra/ai/template.yaml` — two Lambdas + Bedrock invoke policy.
  - **No** `VpcConfig` block (the lock-in decision).
  - **No** Bedrock interface VPC endpoint.
- `infra/Makefile` gets `deploy-ai` / `delete-ai` targets that follow
  the existing per-stack pattern (idempotent — the
  `feedback_idempotent_deploys.md` memory).

## Test plan

- `backend/tests/unit/test_ai_mine.py`: prompt construction,
  Bedrock-client mock returning canned JSON, parse failures handled,
  request-body validation.
- `backend/tests/unit/test_ai_tailor.py`: same shape; assertion that
  tailored output uses the master summary's signals (not generic
  filler) is hard to test without a real model — instead, assert the
  prompt contains the master fields verbatim, and trust the model
  evals at deploy time.
- *Manual eval at deploy time:* run a half-dozen real resumes + JDs
  through both endpoints; check that the tailored output preserves
  voice and the mined output gets title/summary right ≥ 80% of the
  time. If accuracy drops, iterate on the prompt before merging.

## Frontend wiring

- `ResumeForm.tsx`: lift the auto-prefill UX that was built and
  reverted in slice 04 (the "auto-filled — edit if needed" badge over
  AI-prefilled fields). Hit `/ai/mine-resume` after the PDF lands.
- `SubmissionDetail.tsx`: a "Tailor with AI" button on the tailored
  title/summary card. POST to `/ai/tailor`, populate the inputs with
  the suggestion, user reviews + saves.

## Out of scope (deferred to later slices)

- AI for response classification (lands with the `email` stack — every
  classification needs the inbound email body, which doesn't exist
  yet).
- Tailoring multi-section experience bullets (locked OFF per
  `project_ai_tailoring.md`; doesn't move).
- Structured-resume PDF rendering (see fork 3).

## Starter task list

1. Decide forks 1–3 (user).
2. New stack: `infra/ai/template.yaml` + Makefile targets.
3. `backend/src/handlers/ai_mine.py` + `ai_tailor.py` (each its own
   handler — keeps blast radius per-feature, matches the per-handler
   convention).
4. Tests for each.
5. Frontend wiring per "Frontend wiring" section.
6. Manual eval pass; iterate on prompts.
7. Update README "Project layout" + "What's NOT in this scaffold"
   (remove the AI bullet).
8. End-of-slice: write `docs/slices/07-?.md` plan. Likely candidates:
   submission ↔ contact linking (slice 05 deferred), email pipeline
   (`email` stack), or follow-up reminders (`scheduler` stack).