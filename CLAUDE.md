# job-tracker — repo conventions for Claude

Project-local guidance. Global preferences live in `~/.claude/CLAUDE.md`;
this file only documents things specific to this repo.

## Confirm before editing code or infra

Any change to code (`.py`, `.ts`, `.tsx`, `.js`), tests, infrastructure
templates (`.yaml` under `infra/`), Makefiles, or other config files
requires explicit confirmation from the user first. Describe what you'd
change as text, end with a clear "OK to proceed?", and wait for "yes" /
"go" / "do it" before editing.

This applies even to one-line fixes and even to changes that respond
directly to a problem the user just identified. Pointing out a gap
isn't the same as authorizing the fix.

`.md` files (this CLAUDE.md, README, slice docs under `docs/slices/`,
memory files under `~/.claude/projects/.../memory/`) are explicitly the
exception — edit them directly without separate confirmation.

## Lambda logging

Every Lambda in `backend/src/handlers/` follows the same logging shape:

- **Entry log** at the top of the handler. Include `route_key`, `user_sub`,
  and any other useful request context. Don't log full request bodies (PII).
- **Exit log** at the success path. Include status / key result fields.
- **Step logs** at every major operation: DB query, S3/SES/Bedrock call,
  state transition, branching decision. The bar is "if production fails
  here, can I tell from CloudWatch alone where it stopped?"
- **Every caught exception** uses `logger.exception(...)` (which auto-attaches
  the stack trace via `exc_info`), never plain `logger.error(...)` — the
  hard rule is *no error log line without an associated stack trace*.
- **Re-raise after logging.** Logging is not handling. Let API Gateway turn
  it into a 5xx and CloudWatch record the unhandled exception. The two
  exceptions to this rule in the codebase (`common/scheduler.py` swallows
  scheduler-API errors for graceful degradation when the scheduler stack
  isn't deployed; `submissions._read_jd_text` swallows S3 errors so a
  missing snapshot doesn't 500 the detail view) carry inline comments at
  the catch site explaining why.
- Use `aws_lambda_powertools.Logger` (already wired in
  `backend/src/common/logger.py`). Decorate handlers with
  `@logger.inject_lambda_context(log_event=False)` — `log_event=False` is
  intentional (full event = PII leak; pick fields explicitly via `extra=`).
- Never put `msg`, `name`, or other LogRecord-reserved keys inside
  `extra={...}` — collision crashes the formatter (see `feedback_logger_extra`
  memory).

## Suggested shell commands

When proposing a shell command the user will copy-paste from terminal
output, **keep the entire command on one line**. No `\` line-continuations.

**Why:** terminal copy includes all the whitespace following the `\`, and
the user has to backspace ~100 characters per continuation before running
the command. Single-line commands paste-and-run cleanly even when they
wrap visually.

This applies only to commands the user will *run*. Inside scripts and
Makefiles checked into the repo, line continuations are fine.

## Frontend build

- **Vite is the only thing that emits JS.** `tsc` is type-check only —
  `frontend/tsconfig.json` has `"noEmit": true` and `package.json`'s
  `"lint": "tsc --noEmit"` reflects that.
- **Never commit compiled output to source control.** `frontend/dist/`
  and any emitted `.js` / `.js.map` / `.d.ts` files inside
  `frontend/src/` are build artifacts. The root `.gitignore` blocks
  them as a guard rail; don't `git add -f` past it.
- **If you ever need to run `tsc` with emit on for some reason** (e.g.
  to inspect the JS output of a transform), clean the output before
  committing — `find frontend/src -type f -name '*.js' -delete` and
  similar for `.d.ts` / `.js.map`. The `npm run build` script runs
  `tsc && vite build`; with `noEmit: true` set, the `tsc` half just
  type-checks and exits, then Vite emits to `frontend/dist/`.
