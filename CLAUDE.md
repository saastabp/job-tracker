# job-tracker — repo conventions for Claude

Project-local guidance. Global preferences live in `~/.claude/CLAUDE.md`;
this file only documents things specific to this repo.

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
