# job-tracker

Web app for tracking job-search activity (resume submissions, responses, follow-ups, daily/weekly targets), built as an AWS-native serverless reference architecture.

This repo is the **foundational scaffold** — a vertical slice that proves the spine works end-to-end (Cognito login → JWT-authorized API call → Lambda in VPC → IAM-auth'd MySQL query → JSON back to React). Business features (submissions CRUD, AI-assisted resume tailoring, SES inbound email pipeline, scheduled follow-up reminders) land in follow-up plans on top of this skeleton.

## Architecture

Region: **us-west-2**. ACM cert for CloudFront (added with the future `domain` stack) goes in **us-east-1**.

Five SAM stacks, deployable independently and wired together via SSM Parameter Store under `/jobtracker/<concern>/<resource>` (see [`infra/shared/ssm-naming.md`](infra/shared/ssm-naming.md)):

| Stack | Stability | What's in it |
|---|---|---|
| `network` | rarely changes | VPC, public + private subnets (2 AZs), security groups, S3 gateway endpoint |
| `data` | "do not casually destroy" | RDS MySQL `db.t4g.micro` (IAM auth, no public access), S3 buckets (resumes, inbound email) |
| `auth` | rarely changes | Cognito user pool, SPA app client, Hosted UI domain |
| `api` | iterates often | HTTP API Gateway (Cognito JWT authorizer), Health Lambda in private subnets |
| `frontend` | iterates often | Private S3 SPA bucket + CloudFront distribution (default `*.cloudfront.net` URL) |

Future stacks (in follow-up plans, drop in without modifying the above): `ai`, `email`, `scheduler`, `domain`, `ci`.

**Key design choices** (full rationale in `infra/shared/ssm-naming.md` and the design memory):
- No NAT gateway. Lambdas in private subnets reach AWS via VPC endpoints (S3 gateway endpoint = free; interface endpoints added only when a feature needs them, e.g., Bedrock with the `ai` stack).
- IAM authentication from Lambda to MySQL. Master credential in Secrets Manager, used only for admin access via SSM Session Manager tunnel + BeeKeeper.
- Bedrock (deferred to `ai` stack) over direct Anthropic API — no API key to manage, IAM-authed, fits the no-NAT story.
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
| Session Manager plugin | Powers `make tunnel` (SSM port-forward to RDS) | `session-manager-plugin` (says "successfully installed" or similar) | `curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o /tmp/sm.deb && sudo dpkg -i /tmp/sm.deb` |
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

- VPC + EC2 (subnets, route tables, IGW, security groups, EC2 instance for bastion)
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

### 3. Deploy all stacks

```sh
make -C infra deploy-all
```

This deploys, in dependency order: `network` → `data` (RDS takes ~8 min on first create) → `auth` → `api` → `frontend` (CloudFront takes ~5 min). Total first deploy: 15–20 minutes.

### 4. Inspect the resulting SSM parameters

```sh
make -C infra list-ssm
```

The SPA reads four of these (`/jobtracker/api/url`, `/jobtracker/auth/user-pool-provider-url`, `/jobtracker/auth/spa-client-id`, `/jobtracker/auth/cognito-domain`); `make -C infra dev-frontend` and `make -C infra sync-frontend` generate `frontend/.env.local` from them automatically.

### One-time MySQL `app` user bootstrap

The Lambda authenticates to MySQL as user `app` via RDS IAM auth. That user has to exist in the database. The data stack created master credentials (`jtadmin`) but not the app user — you create it once through a transient bastion + SSM port-forward tunnel.

```sh
# 1. Deploy the bastion (t4g.nano in a public subnet, SSM-managed; ~2 min)
make -C infra deploy-bastion

# 2. Print the RDS master credentials
make -C infra db-creds
# → {"username":"jtadmin","password":"..."}

# 3. Open the tunnel (foreground; Ctrl-C to close)
make -C infra tunnel
# → tunnel: localhost:13306 -> jobtracker-db.xxx.us-west-2.rds.amazonaws.com:3306
```

If `deploy-bastion` fails with "We currently do not have sufficient t4g.nano capacity in the Availability Zone" (AWS hits this periodically), retry with the other AZ or a slightly larger instance:

```sh
BASTION_SUBNET=1 make -C infra deploy-bastion                                # us-west-2b instead of us-west-2a
BASTION_INSTANCE_TYPE=t4g.micro make -C infra deploy-bastion                 # ~6¢/hr instead of ~3¢/hr
BASTION_SUBNET=1 BASTION_INSTANCE_TYPE=t4g.micro make -C infra deploy-bastion # both
```

Run `make -C infra destroy-bastion` first if a previous attempt rolled back, before retrying with overrides.

In another terminal, point BeeKeeper (or any MySQL client) at:

- **Host**: `localhost`
- **Port**: `13306`
- **User**: `jtadmin`
- **Password**: from step 2
- **Database**: `jobtracker`

Run:

```sql
CREATE USER 'app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON jobtracker.* TO 'app'@'%';
FLUSH PRIVILEGES;
```

Then close the tunnel (Ctrl-C) and tear the bastion down to stop charges:

```sh
make -C infra destroy-bastion
```

Bastion cost: $0 when destroyed; ~3 cents/hour while running. Tighten the `app` grants once real tables exist.

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

## Operational safety — what's safe to re-run standalone

| Operation | Safe alone? | Notes |
|---|---|---|
| `make deploy-all` | **Always** | Idempotent. The "put everything right" hammer. Always converges to a working state, including the `wire-frontend` final step. |
| `make deploy-network` | Mostly | SG/tag/route changes update in place. Changing the VPC CIDR or AZ count would force-replace subnets and cascade into RDS replacement (data loss). Don't change those properties without thinking twice. |
| `make deploy-data` | Mostly | Most updates are in-place (instance class, storage growth). Some properties (engine major version, encryption-at-rest toggle) trigger replacement; `DeletionPolicy: Snapshot` is a backstop, not a substitute. |
| `make deploy-auth` | **Yes** | Auto-detects CloudFront URL from SSM and preserves Cognito callback wiring on every run. |
| `make deploy-api` | **Yes** | Auto-detects CloudFront URL and preserves CORS wiring on every run. Re-runs `sam build` so Lambda code changes are picked up. |
| `make deploy-frontend` | Yes | No parameter overrides to lose. CloudFront update can be slow (5–15 min). |
| `make wire-frontend` | Yes (after `deploy-frontend`) | Forces re-application of the CloudFront URL to auth + api. Errors clearly if frontend not yet deployed. Mostly redundant now that auth/api auto-detect, but kept for explicit-intent uses. |
| `make sync-frontend` | Yes | Generates `.env.local` from SSM, builds, syncs to S3, invalidates CloudFront. |
| `make deploy-bastion` / `make destroy-bastion` | Yes | Symmetric. `destroy-bastion` only removes bastion-specific resources — never touches the other stacks. |

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
│   ├── bastion/                # transient EC2 for SSM port-forward to RDS
│   ├── scripts/tunnel.sh       # opens SSM port-forward to RDS via bastion
│   ├── shared/ssm-naming.md    # cross-stack SSM convention
│   └── Makefile                # deploy orchestration (local convenience)
├── backend/                    # Python Lambda code
│   ├── src/
│   │   ├── common/             # shared: db (IAM auth), auth (JWT claims), logger
│   │   ├── handlers/           # Lambda entrypoints
│   │   └── requirements.txt    # runtime deps for sam build
│   ├── tests/                  # pytest unit tests
│   └── pyproject.toml          # local dev environment + dev deps
├── frontend/                   # React + Vite + react-bootstrap + react-oidc-context
│   ├── src/
│   │   ├── auth/config.ts      # OIDC config + Cognito logout helper
│   │   ├── api/client.ts       # useApi() hook — fetch with bearer token
│   │   ├── pages/{Health,Login}.tsx
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

- AI integration (Bedrock VPC endpoint + AI Lambdas) — `ai` stack
- Email pipeline (SES inbound + processor Lambda) — `email` stack
- Follow-up reminder scheduler (EventBridge + Lambda + SES outbound) — `scheduler` stack
- Custom domain (Route 53, ACM us-east-1, CloudFront alias, Cognito custom domain, API Gateway custom domain) — `domain` stack
- CI/CD via GitHub Actions (OIDC trust + deploy role) — `ci` stack + `.github/workflows/`
- Submissions / targets / responses CRUD endpoints + UI
- Migrations tooling (Alembic vs SQL files)
- DLQs, alarms, dashboards, WAF