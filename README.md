# Job Search Tracker

Web app for tracking job-search activity (resume submissions, responses, follow-ups, daily/weekly targets), built as an AWS-native serverless reference architecture.

The repo started as a **foundational scaffold** — a vertical slice proving the spine end-to-end (Cognito login → JWT-authorized API call → Lambda in VPC → IAM-auth'd MySQL query → JSON back to React) — and has since grown a data model (slice 01), a dashboard with daily/weekly target widgets (slice 02), submissions + companies CRUD with JD-snapshot archival to S3 (slice 03), resumes CRUD with browser-direct presigned-PUT uploads + submission linking (slice 04), contacts + outreach CRUD wired into the dashboard (slice 05), AI-assisted resume mining + tailoring via Bedrock-backed Lambdas (slice 06), and scheduled follow-up reminders via EventBridge Scheduler + SES outbound (slice 07). Remaining business features (SES inbound email pipeline + responses CRUD, submission ↔ contact linking) land in follow-up slices on top of this skeleton.

## Architecture

Region: **us-west-2** for everything except the `dns` stack, which is in **us-east-1** because CloudFront's viewer certificate must be issued there.

SAM stacks, deployable independently and wired together via SSM Parameter Store under `/jobtracker/<concern>/<resource>` (see [`infra/shared/ssm-naming.md`](infra/shared/ssm-naming.md)):

| Stack | Stability | What's in it |
|---|---|---|
| `network` | rarely changes | VPC, two subnets in 2 AZs (RDS subnet group only), IGW + default routes, RDS security group, S3 gateway endpoint |
| `data` | "do not casually destroy" | RDS MySQL `db.t4g.micro` (publicly accessible, IAM auth + TLS), S3 buckets (resumes, inbound email) |
| `auth` | rarely changes | Cognito user pool, SPA app client, Hosted UI domain |
| `api` | iterates often | HTTP API Gateway (Cognito JWT authorizer), Lambda handlers (all run **outside** the VPC) |
| `frontend` | iterates often | Private S3 SPA bucket + CloudFront distribution (default `*.cloudfront.net` URL; optional custom-domain alias from `dns` stack) |
| `ai` | iterates often | HTTP API Gateway + AI Lambdas (Bedrock-backed resume mining + tailoring), runs **outside** the VPC |
| `scheduler` | rarely changes | EventBridge Scheduler group + follow-up notify Lambda (outside the VPC, sends SES reminders directly) + SES verified sender identity |
| `dns` | rarely changes; **us-east-1** | ACM cert (DNS-validated against your hosted zone) + Route 53 A/AAAA alias records → CloudFront. Opt-in; not in `deploy-all`'s dep chain. |

Future stacks (in follow-up plans, drop in without modifying the above): `email`, `ci`.

**Key design choices** (full rationale in `infra/shared/ssm-naming.md` and the design memory):
- No NAT gateway, no VPC interface endpoints. Every Lambda runs **outside** the VPC; the VPC is essentially a wrapper around RDS's subnet-group requirement. Lambdas reach RDS over the public endpoint with IAM auth + TLS. The threat model is bounded — without an IAM token, attempts at the RDS endpoint just hit `Access denied`. This pattern (slice 07 flatten) replaced an earlier in-VPC topology that needed bastion + interface endpoints.
- IAM authentication from Lambda to MySQL. Master credential in Secrets Manager; admin access is `aws rds generate-db-auth-token` + `mysql -h <public-endpoint>` from anywhere. No bastion, no SSM tunnel.
- Bedrock over direct Anthropic API — no API key to manage, IAM-authed. The AI Lambdas live in the `ai` stack and (like every Lambda) run outside the VPC; they never touch the DB, all inputs come in the request body.
- React SPA uses `react-oidc-context` (~30 KB) for OIDC redirect, not Amplify (~200 KB+). Hosted UI domain is the standard Cognito `*.amazoncognito.com`.

## Prerequisites

### One-shot check

```sh
make -C infra check-prereqs
```

The script prints `OK` / `MISS` / `WARN` for each tool and a non-zero exit if anything required is missing. Re-run after installing pieces.

### Required tools

| Tool | Why | Check command | Linux/Ubuntu install |
|---|---|---|---|
| AWS CLI v2 | Deploys, queries SSM/Secrets, runs port-forward | `aws --version` (must be `aws-cli/2.x`) | `curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install` |
| SAM CLI | Builds and deploys CloudFormation stacks | `sam --version` | `pip install --user aws-sam-cli` |
| Node.js 20+ | Frontend build (Vite + React) | `node --version` (must be `v20.x` or higher) | `curl -fsSL https://deb.nodesource.com/setup_20.x \| sudo -E bash - && sudo apt install -y nodejs` |
| Python 3.12+ | Local backend dev + sam build | `python3 --version` | Ubuntu 24.04: already 3.12. Older: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.12 python3.12-venv` |
| make | Recipe runner | `make --version` | `sudo apt install make` |
| curl | RDS CA bundle download | `curl --version` | `sudo apt install curl` |
| jq (optional) | JSON formatting in some recipes | `jq --version` | `sudo apt install jq` |
| BeeKeeper Studio (or `mysql` CLI) | One-time `app` user bootstrap | `mysql --version` (or just install BeeKeeper) | `sudo apt install mysql-client` (or [BeeKeeper Studio](https://www.beekeeperstudio.io/get)) |

### AWS credentials configuration

The Makefile + scripts use the **default profile** in `~/.aws/credentials`. Set it up once:

```sh
aws configure
# AWS Access Key ID:     <paste from IAM>
# AWS Secret Access Key: <paste from IAM>
# Default region name:   us-west-2     # important — project deploys here
# Default output format: json
```

Verify:

```sh
aws sts get-caller-identity
# → { "UserId": "...", "Account": "...", "Arn": "arn:aws:iam::...:user/..." }

aws configure get region
# → us-west-2
```

If your `default region` is something other than `us-west-2`, that's fine — every command in this README explicitly passes `--region us-west-2` or pulls it from `Makefile`'s `REGION` variable. But you may prefer to switch the default for less typing.

### IAM rights needed on that user

To deploy this scaffold, the IAM principal needs broad rights to create the resources we provision. The simplest path for personal use is the AWS-managed `AdministratorAccess` policy. If you want least-privilege, you'll need create/delete on:

- VPC + EC2 (subnets, route tables, IGW, security groups — RDS subnet group only; no compute)
- RDS (MySQL instance, subnet groups, IAM-auth)
- Cognito user pools, app clients, Hosted UI domain
- API Gateway HTTP APIs + authorizers
- Lambda + IAM (creating execution roles)
- S3 buckets + bucket policies
- CloudFront distributions + Origin Access Control
- SSM Parameter Store (read/write under `/jobtracker/*`)
- Secrets Manager (read/write the RDS-managed master secret)
- CloudFormation (create/update/delete stacks)

## Deploy

### 1. Verify prerequisites

```sh
make -C infra check-prereqs
```

### 2. Download the RDS CA bundle (one-time)

The Lambda uses this for TLS verification when connecting to MySQL via IAM auth:

```sh
make -C infra fetch-rds-ca
```

(Saves `backend/src/rds-ca-bundle.pem`. The Makefile re-runs this automatically as part of `deploy-api`, but running it now confirms internet access works.)

### 3. Provision all stacks

```sh
make -C infra deploy-all
```

This deploys, in dependency order: `network` → `data` (RDS takes ~8 min on first create) → `auth` → `api` → `frontend` (CloudFront takes ~5 min) → `ai` → `wire-frontend`. Total first provision: 15–20 minutes.

**`deploy-all` provisions the infrastructure but does not finish the bootstrap.** A working end-to-end app additionally requires:

1. Creating the MySQL `app` user (one-time manual step — see below).
2. Running schema migrations (`make migrate`).
3. Building and publishing the SPA to S3 + CloudFront (`make sync-frontend`).

The order is: `deploy-all` → `app` user bootstrap → `migrate` → `sync-frontend`. Each step is documented in the subsections below.

### 4. Inspect the resulting SSM parameters

```sh
make -C infra list-ssm
```

The SPA reads five of these (`/jobtracker/api/url`, `/jobtracker/ai/url`, `/jobtracker/auth/user-pool-provider-url`, `/jobtracker/auth/spa-client-id`, `/jobtracker/auth/cognito-domain`); `make -C infra dev-frontend` and `make -C infra sync-frontend` generate `frontend/.env.local` from them automatically. `/jobtracker/ai/url` is optional — paths under `/ai/` only resolve once the `ai` stack is deployed.

### One-time MySQL `app` user bootstrap

The Lambda authenticates to MySQL as user `app` via RDS IAM auth. That user has to exist in the database. The data stack created master credentials (`jtadmin`) but not the app user — you create it once by connecting directly to the public RDS endpoint with the master password.

```sh
# 1. Print the RDS master credentials
make -C infra db-creds
# → {"username":"jtadmin","password":"..."}

# 2. Look up the public RDS endpoint
aws ssm get-parameter --name /jobtracker/data/db-endpoint --query Parameter.Value --output text --region us-west-2
# → jobtracker-db.xxxxxxxx.us-west-2.rds.amazonaws.com
```

In BeeKeeper (or any MySQL client) point at:

- **Host**: the endpoint from step 2
- **Port**: `3306`
- **User**: `jtadmin`
- **Password**: from step 1
- **Database**: `jobtracker`
- **TLS**: enabled, CA = `backend/src/rds-ca-bundle.pem` (run `make -C infra fetch-rds-ca` if not yet downloaded)

Run:

```sql
CREATE USER 'app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON jobtracker.* TO 'app'@'%';
FLUSH PRIVILEGES;
```

Tighten the `app` grants once real tables exist. The IAM auth + TLS gate the public endpoint; nothing here exposes the master password to the internet.

### Run schema migrations

The `data` stack stands up an empty MySQL database. Schema is applied by the `MigrationFunction` Lambda, which runs every forward-only SQL file under `backend/src/migrations/` in order. Run it after the `app` user exists (the migration Lambda authenticates as `app`):

```sh
make -C infra migrate
```

Re-runnable: the migration runner records applied versions in a `schema_migrations` table and skips files it's already run. Run again after every `git pull` that introduces a new migration file, and after any `deploy-api` that ships migration changes.

### Cognito callbacks + API CORS for the deployed frontend

`make deploy-auth` and `make deploy-api` auto-detect the CloudFront URL from SSM and pass it as a parameter override, so they keep the Cognito callback list and API CORS allow-list correct on every standalone re-deploy. On the first-ever `deploy-all` (when CloudFront doesn't yet exist), the final `wire-frontend` step re-runs auth + api once the URL is available.

You'd only call `wire-frontend` manually if you bypassed the Makefile entirely (e.g., `cd infra/auth && sam deploy` directly without `--parameter-overrides`):

```sh
make -C infra wire-frontend
```

### Build and push the SPA

For local development (Vite dev server at `http://localhost:5173`):

```sh
make -C infra dev-frontend
```

That target generates `frontend/.env.local` from the current SSM values, runs `npm install`, then starts Vite. Re-run after any `auth`/`api`/`frontend` stack change to pick up new SSM values.

For the deployed SPA (build and push to S3 + invalidate CloudFront):

```sh
make -C infra sync-frontend
```

`gen-frontend-env` (used by both targets) writes only the four `VITE_*` values that don't depend on runtime URL. `VITE_REDIRECT_URL` is intentionally not set — `auth/config.ts` falls back to `window.location.origin`, so the same bundle works locally (`http://localhost:5173/`) and deployed (`https://...cloudfront.net/`).

#### `AI_ENABLED` knob — temporarily disabling Bedrock features

The Makefile defines `AI_ENABLED ?= 0`. While that's 0, `gen-frontend-env` omits `VITE_AI_API_URL` from `frontend/.env.local`; the SPA's `aiEnabled` flag is false, and the AI tailor / mine-resume buttons hide. This is the current state while the AWS support case for Bedrock service quotas is open.

Once Bedrock is configured in the region, flip the default by changing `AI_ENABLED ?= 0` to `AI_ENABLED ?= 1` in `infra/Makefile`, then re-run `make sync-frontend`. (One-shot alternative: `AI_ENABLED=1 make -C infra sync-frontend`.) The AI SAM stack itself stays running unless you explicitly `make delete-ai` — the knob is purely a frontend concern.

## Operational safety — what's safe to re-run standalone

| Operation | Safe alone? | Notes |
|---|---|---|
| `make deploy-all` | **Always** | Idempotent. The "put everything right" hammer for **infrastructure** — converges every stack and runs `wire-frontend`. Does **not** create the MySQL `app` user, run `migrate`, or `sync-frontend`; those still need to follow on a fresh deploy (see Deploy section). |
| `make deploy-network` | Mostly | SG/tag/route changes update in place. Changing the VPC CIDR or AZ count would force-replace subnets and cascade into RDS replacement (data loss). Don't change those properties without thinking twice. |
| `make deploy-data` | Mostly | Most updates are in-place (instance class, storage growth). Some properties (engine major version, encryption-at-rest toggle) trigger replacement; `DeletionPolicy: Snapshot` is a backstop, not a substitute. |
| `make deploy-auth` | **Yes** | Auto-detects CloudFront URL from SSM and preserves Cognito callback wiring on every run. |
| `make deploy-api` | **Yes** | Auto-detects CloudFront URL and preserves CORS wiring on every run. Re-runs `sam build` so Lambda code changes are picked up. |
| `make deploy-frontend` | Yes | No parameter overrides to lose. CloudFront update can be slow (5–15 min). |
| `make deploy-ai` | **Yes** | Auto-detects CloudFront URL from SSM, preserves CORS wiring. Standalone — does not touch `data`/`api`/`network`. |
| `make delete-ai` | **Yes** | Tears down the AI stack and removes its SSM URL parameter. Frontend AI features show a friendly "set VITE_AI_API_URL" error until the stack is back. |
| `make deploy-scheduler` | **Yes** | Standalone — depends only on `data`. First deploy creates an `AWS::SES::EmailIdentity` for the `SenderEmail` parameter; AWS sends a verification email and reminders won't actually fire until the link is clicked. Re-running is idempotent; the SSM params it publishes (`/jobtracker/scheduler/{group-name,notify-arn,exec-role-arn}`) get picked up by `deploy-api` automatically. |
| `make delete-scheduler` | **Yes** | Tears down the scheduler stack + its SSM params. `follow_ups` rows persist; the dashboard pending count keeps working; reminders just stop firing. The next `make deploy-api` re-emits the api Lambdas without scheduler IAM (the `HasScheduler` condition is keyed off the empty SSM param). |
| `make deploy-dns` | **Yes** | Opt-in custom-domain attach. Deploys the dns stack in us-east-1, writes cert ARN + app URL to SSM, then re-runs `deploy-frontend` to attach the alias. Blocks ~5–15 min on ACM validation. Standalone-safe. See [`docs/dns-setup.md`](docs/dns-setup.md). |
| `make delete-dns` | **Yes** | Tears down the dns stack and its SSM params. The next `deploy-frontend` will fall back to the default cloudfront.net domain. Cognito/CORS allow-lists keep the old origin until the next `wire-frontend` rebuilds them — harmless but noisy. |
| `make wire-frontend` | Yes (after `deploy-frontend`) | Forces re-application of the CloudFront URL (and custom-domain URL, if `dns` stack is deployed) to auth + api + ai. Errors clearly if frontend not yet deployed. Mostly redundant now that the per-stack targets auto-detect, but kept for explicit-intent uses. |
| `make sync-frontend` | Yes | Generates `.env.local` from SSM, builds, syncs to S3, invalidates CloudFront. |

Rule of thumb: if you're not sure of the safety of a sequence, run `make deploy-all`. It's the canonical convergence command.

## Verification

End-to-end smoke test:

1. Visit the CloudFront URL (`make list-ssm` to find it) — React app loads.
2. Click **Sign in** → Cognito Hosted UI → sign up / log in → redirect back with auth code → app exchanges for tokens.
3. The Health page fires `GET /health`. Response should be:
   ```json
   { "ok": true, "user_sub": "...", "user_email": "you@…", "db": { "ok": 1 } }
   ```
4. That confirms: CloudFront → S3 SPA → Cognito → API Gateway JWT validation → Lambda in VPC → RDS via IAM auth → MySQL `SELECT 1`.

Failure-mode checklist:

- DB connection times out → security group ingress wrong, or VPC endpoints missing
- IAM auth fails → `app` user not created in MySQL with `AWSAuthenticationPlugin`, or the IAM policy in `api/template.yaml` doesn't reference the right `db-resource-id`
- 401 from API → JWT authorizer misconfigured, or wrong client ID/issuer in SSM
- Lambda times out at the configured `Timeout` value → first-call cold-start TLS handshake to RDS can take 2–6s; `Timeout: 15` (api template) is the recommended floor.
- CORS blocked in browser → API CORS allow-list doesn't include the CloudFront URL. Run `make -C infra deploy-api` (auto-detects CloudFront URL from SSM).
- Sign-in fails with `redirect_mismatch` → Cognito callback URLs don't include the CloudFront URL. Run `make -C infra deploy-auth` (same auto-detect).

## Project layout

```
job-tracker/
├── infra/
│   ├── network/                # VPC stack
│   ├── data/                   # RDS + S3 buckets stack
│   ├── auth/                   # Cognito stack
│   ├── api/                    # API Gateway + Lambdas stack
│   ├── frontend/               # CloudFront + SPA bucket stack
│   ├── ai/                     # Bedrock-backed AI Lambdas stack (no VPC)
│   ├── scheduler/              # EventBridge Scheduler + follow-up notify Lambda + SES sender identity
│   ├── dns/                    # us-east-1 ACM cert + Route 53 alias for custom-domain CloudFront (opt-in)
│   ├── shared/ssm-naming.md    # cross-stack SSM convention
│   └── Makefile                # deploy orchestration (local convenience)
├── backend/                    # Python Lambda code
│   ├── src/
│   │   ├── common/             # shared: db (IAM auth), auth (JWT claims), users, logger
│   │   ├── handlers/           # Lambda entrypoints (health, migrate, post_confirmation,
│   │   │                       #   targets, dashboard, companies, submissions, resumes,
│   │   │                       #   contacts, ai_mine, ai_tailor, followups, followup_notify)
│   │   ├── migrations/         # forward-only SQL files run by handlers/migrate.py
│   │   └── requirements.txt    # runtime deps for sam build
│   ├── tests/                  # pytest unit tests (one test_<handler>.py per Lambda)
│   └── pyproject.toml          # local dev environment + dev deps
├── frontend/                   # React + Vite + react-bootstrap + react-oidc-context
│   ├── src/
│   │   ├── auth/config.ts      # OIDC config + Cognito logout helper
│   │   ├── api/client.ts       # useApi() hook — fetch with bearer token, routes /ai/* to AI stack URL
│   │   ├── layout/AppShell.tsx # navbar + sidebar shell wrapping all routed pages
│   │   ├── components/         # cross-page UI: PdfDropZone, pdfText (lazy-loaded pdfjs-dist extractor)
│   │   ├── pages/              # Dashboard, Submissions, SubmissionDetail, SubmissionForm,
│   │   │                       #   Companies, CompanyDetail, Resumes, ResumeDetail, ResumeForm,
│   │   │                       #   ResumeUploadModal, Contacts, ContactDetail, ContactForm,
│   │   │                       #   FollowUps, Targets, Health, Login, ComingSoon
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
└── README.md
```

## Approximate cost (us-west-2, personal-use volumes)

| Item | Monthly |
|---|---|
| RDS MySQL `db.t4g.micro` + 20 GB gp3 | ~$14 |
| Lambda, API Gateway, S3, CloudFront, Cognito, SSM, Secrets Manager, CloudWatch | cents |
| **Total** | **~$14** |

(Or **$0/mo for the first 12 months** if the AWS account qualifies for the RDS Free Tier.)

## What's NOT in this scaffold

These are tracked for follow-up plans and have stack-shaped homes ready for them:

- Email pipeline (SES inbound + processor Lambda) — `email` stack
- Submission ↔ contact linking (slice 08, additive in the `api` stack)
- Cognito custom domain + API Gateway custom domain. The SPA's custom-domain alias is shipped via the `dns` stack; folding the Cognito Hosted UI and the API endpoint behind matching subdomains is still pending and would extend the `dns` stack rather than add a new one.
- CI/CD via GitHub Actions (OIDC trust + deploy role) — `ci` stack + `.github/workflows/`
- Responses CRUD + UI (lands with the `email` stack — responses are inbound-email-driven)
- AI response classification (lands with the `email` stack — needs the inbound email body, which doesn't exist yet)
- Structured-resume PDF rendering with tailored overrides (slice 06 punted in favor of copy-paste MVP)
- DLQs, alarms, dashboards, WAF