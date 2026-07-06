# RDS Consolidation — Sandbox Rehearsal (dress run before the real cutover)

Companion to `rds-consolidation-plan.md` and `rds-consolidation-runbook.md`.
Before touching production legacy data, rehearse the entire consolidation
mechanism against the **legacy sandbox** — a throwaway environment that already
shares the legacy RDS instance under its own `legacytracker_sandbox` schema.

**Brian runs all commands.** Every step here is read-only against the two
production schemas (`jobtracker`, `legacytracker`); nothing deletes an instance
or touches prod stacks. The whole rehearsal is reversible in two commands (§6).

> **Where you run these — this is a legacy-tracker operation.** Run everything
> from the **legacy repo** (`~/360-balanced-living/legacy-tracker`): the
> `sam` / `make` steps (§3, §6, §7) require it, and the `mysql` / `mysqldump`
> steps use that repo's CA bundle (`backend/src/rds-ca-bundle.pem`, produced by
> `make fetch-rds-ca`). The only thing that belongs to job-tracker is the
> *target* — the shared `jobtracker-db` instance the sandbox data moves into.
> This doc lives in the job-tracker repo only because it's a companion to the
> consolidation plan; operationally it's the legacy project's rehearsal.

> **Note — this amends the committed plan.** `rds-consolidation-plan.md` §Decided
> says the sandbox is *dropped, not migrated*, with no sandbox user on the shared
> instance. This rehearsal deliberately (and temporarily) puts a **third** schema
> — `legacytracker_sandbox` — on `jobtracker-db` as a dry run. Tear it down after
> (§6), or promote it to a permanent home (§7). Either way, do this **before**
> the Phase A–B prod runbook.

---

## Why the sandbox is a faithful rehearsal (and where it isn't)

The sandbox and prod legacy differ in exactly the way that makes the sandbox
safe to experiment on: **same RDS instance, different schema, different app +
frontend stacks.** Repointing the sandbox does not touch any prod stack.

Because the sandbox api template (`infra/api/template-sandbox.yaml`) takes its
DB coordinates as `AWS::SSM::Parameter::Value<String>` parameters
(`DbEndpoint` / `DbPort` / `DbResourceId`, defaulting to `/legacytracker/data/*`),
the sandbox can be repointed at the shared `jobtracker-db` instance **purely via
`--parameter-overrides`** — pointing those three at `/jobtracker/data/*` and
flipping `DbUser` / `DbName`. No data-stack surgery.

**What this rehearsal faithfully validates** (the high-risk parts of the real
cutover, exercised identically):

- Creating a second-tenant schema + IAM DB-auth user + per-schema `GRANT` on the
  shared instance, and proving the grant is scoped (can't see `jobtracker.*`).
- `mysqldump` → load fidelity with an exact row-count diff.
- A Lambda authenticating as a **new, non-`app`** user via IAM token against the
  shared instance — the one real infra change the prod cutover makes.

**What it does NOT cover** (know these are still untested going into prod):

- The `legacytracker-data` stack → **SSM-only rewrite** (Phase C in the plan).
  The sandbox has no data stack of its own, so this rehearsal substitutes
  param-name overrides. Lower risk (it only changes *where the coords come
  from*), but it is the one prod step the sandbox skips.
- **Decommission** of the old instance / VPC (terminal — not rehearsable).

---

## Connection values

| | value |
|---|---|
| Shared (target) endpoint | `jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com` |
| Shared master user | `jtadmin` |
| Shared master secret | `arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-2a8e01b9-fcca-4fe9-ba9a-760bcd98c70c-Y6ssRX` |
| Legacy (source) endpoint | `legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com` |
| Legacy master user | `ltadmin` |
| Legacy master secret | `arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-138e398b-50f3-42c4-9721-2b421cc5c2bf-681gf4` |
| CA bundle | `/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem` |
| Sandbox source schema | `legacytracker_sandbox` (on the legacy instance) |
| New sandbox user (on shared) | `legacytracker_sandbox_app` |
| Legacy repo root | `~/360-balanced-living/legacy-tracker` |

---

## Step 0 — session setup

```sh
export AWS_PROFILE=brian-admin AWS_REGION=us-west-2
```

```sh
SHARED_PW=$(aws secretsmanager get-secret-value --secret-id 'arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-2a8e01b9-fcca-4fe9-ba9a-760bcd98c70c-Y6ssRX' --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
```

```sh
LEGACY_PW=$(aws secretsmanager get-secret-value --secret-id 'arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-138e398b-50f3-42c4-9721-2b421cc5c2bf-681gf4' --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
```

Precheck — confirm the shared instance's SSM coords exist (the repoint in §4 depends on these three names resolving):

```sh
for p in db-endpoint db-port db-resource-id; do aws ssm get-parameter --name /jobtracker/data/$p --query Parameter.Value --output text; done
```

---

## Step 1 — bootstrap the sandbox schema + IAM user on the SHARED instance

SQL run as `jtadmin` against `jobtracker-db` (mirrors the prod runbook A2, but for the sandbox tenant):

```sql
CREATE DATABASE IF NOT EXISTS legacytracker_sandbox CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'legacytracker_sandbox_app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON legacytracker_sandbox.* TO 'legacytracker_sandbox_app'@'%';
FLUSH PRIVILEGES;
```

Run it as one command:

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e "CREATE DATABASE IF NOT EXISTS legacytracker_sandbox CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; CREATE USER 'legacytracker_sandbox_app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS'; GRANT ALL PRIVILEGES ON legacytracker_sandbox.* TO 'legacytracker_sandbox_app'@'%'; FLUSH PRIVILEGES;"
```

Verify the new user is scoped to `legacytracker_sandbox.*` ONLY (must NOT list `jobtracker.*` or `legacytracker.*` — this is the isolation proof):

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e "SHOW GRANTS FOR 'legacytracker_sandbox_app'@'%';"
```

---

## Step 2 — move the sandbox data (dump → load → verify)

The dump is read-only against the legacy instance. It's the sandbox schema, so
no write-freeze on prod legacy is needed — but don't use the *sandbox app*
during this window.

### 2a — dump the sandbox schema from the legacy instance (as `ltadmin`)

```sh
MYSQL_PWD=$LEGACY_PW mysqldump -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA --single-transaction --set-gtid-purged=OFF --no-tablespaces --routines --triggers --events --databases legacytracker_sandbox > /tmp/legacytracker_sandbox.sql
```

```sh
ls -lh /tmp/legacytracker_sandbox.sql && grep -c 'CREATE TABLE' /tmp/legacytracker_sandbox.sql
```

### 2b — load into the shared instance (as `jtadmin`)

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA < /tmp/legacytracker_sandbox.sql
```

### 2c — verify exact row counts match, source vs target

Source:

```sh
MYSQL_PWD=$LEGACY_PW mysql -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N -e "SELECT CONCAT('SELECT ''',table_name,''' AS t, COUNT(*) AS n FROM legacytracker_sandbox.\`',table_name,'\`;') FROM information_schema.tables WHERE table_schema='legacytracker_sandbox' AND table_type='BASE TABLE';" | MYSQL_PWD=$LEGACY_PW mysql -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N | sort > /tmp/sandbox-counts-source.txt
```

Target:

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N -e "SELECT CONCAT('SELECT ''',table_name,''' AS t, COUNT(*) AS n FROM legacytracker_sandbox.\`',table_name,'\`;') FROM information_schema.tables WHERE table_schema='legacytracker_sandbox' AND table_type='BASE TABLE';" | MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N | sort > /tmp/sandbox-counts-target.txt
```

```sh
diff /tmp/sandbox-counts-source.txt /tmp/sandbox-counts-target.txt && echo "ROW COUNTS MATCH" || echo "MISMATCH — investigate before repointing"
```

---

## Step 3 — repoint the sandbox api at the shared instance

Redeploy `legacytracker-sandbox-api` with the three DB-coord params pointed at
`/jobtracker/data/*` and the user/schema flipped. This is the api-side of the
real cutover, exercised on the sandbox. Only the `legacytracker-sandbox-api`
stack changes; no prod stack is touched.

```sh
cd ~/360-balanced-living/legacy-tracker/infra/api && CF_URL=$(aws ssm get-parameter --name /legacytracker-sandbox/frontend/cloudfront-url --query Parameter.Value --output text --region us-west-2 2>/dev/null) && sam build --config-env sandbox && sam deploy --config-env sandbox --parameter-overrides "DbEndpoint=/jobtracker/data/db-endpoint DbPort=/jobtracker/data/db-port DbResourceId=/jobtracker/data/db-resource-id DbName=legacytracker_sandbox DbUser=legacytracker_sandbox_app CorsOrigins=http://localhost:5173,$CF_URL"
```

> Because `DbEndpoint` / `DbPort` / `DbResourceId` are `SSM::Parameter::Value`
> params, CFN dereferences the *name* you pass — so `DbResourceId=/jobtracker/data/db-resource-id`
> injects the shared instance's resource id, and the IAM policy ARN
> `dbuser:${DbResourceId}/${DbUser}` auto-targets
> `dbuser:<sharedResourceId>/legacytracker_sandbox_app`. Nothing else to change.

---

## Step 4 — verify IAM auth + isolation against the shared instance

### 4a — clean IAM-auth probe via the migrate Lambda

The migrate Lambda is in the sandbox api stack, so it now authenticates as
`legacytracker_sandbox_app` via IAM token against the shared instance. The data
was already loaded at head in §2, so this should report **no pending
migrations** — a clean pass proves IAM auth + schema resolution works.

```sh
aws lambda invoke --function-name legacytracker-sandbox-migrate --region us-west-2 --no-cli-pager /tmp/legacytracker-sandbox-migrate.json && cat /tmp/legacytracker-sandbox-migrate.json && echo
```

If it fails with `Access denied`, re-check the `SHOW GRANTS` from §1 and that the
repoint in §3 actually set `DbUser=legacytracker_sandbox_app` (inspect the
deployed stack's parameters).

### 4b — end-to-end app smoke test

Open the sandbox SPA and exercise it (it's dev-auth, no Cognito):

```sh
aws ssm get-parameter --name /legacytracker-sandbox/frontend/cloudfront-url --query Parameter.Value --output text --region us-west-2
```

Confirm reads/writes work and CloudWatch for a sandbox Lambda shows a clean
`db: connection opened` as `legacytracker_sandbox_app`. **A clean pass here is
the green light to run the real Phase A–B prod runbook.**

---

## Step 5 — capture what you learned

If anything in §1–4 needed a tweak (a flag, a grant, an override), fold it into
`rds-consolidation-runbook.md` **before** the prod run — that's the entire point
of the rehearsal.

---

## Step 6 — teardown (revert to pre-rehearsal state)

Two commands. First, revert the sandbox api to its original coords — the normal
deploy target passes only `DbName` + `CorsOrigins`, so the omitted params fall
back to their template defaults (`/legacytracker/data/*`, `DbUser=app`):

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make deploy-sandbox-api
```

Then drop the rehearsal schema + user from the shared instance:

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/360-balanced-living/legacy-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e "DROP DATABASE IF EXISTS legacytracker_sandbox; DROP USER IF EXISTS 'legacytracker_sandbox_app'@'%'; FLUSH PRIVILEGES;"
```

Shell cleanup:

```sh
unset SHARED_PW LEGACY_PW && rm -f /tmp/legacytracker_sandbox.sql /tmp/sandbox-counts-source.txt /tmp/sandbox-counts-target.txt
```

`jobtracker-db` is now back to just `jobtracker` (+ its `app` user). Proceed to
the real `rds-consolidation-runbook.md`.

---

## Step 7 — alternative: keep the sandbox on the shared instance

If instead you want the sandbox to *stay* on `jobtracker-db` permanently (so the
sandbox is already consolidated too), **don't run §6.** Instead make the repoint
durable by changing the template defaults in the legacy repo (a code change —
needs the usual confirmation):

- `infra/api/template-sandbox.yaml` — `DbEndpoint` / `DbPort` / `DbResourceId`
  defaults `/legacytracker/data/*` → `/jobtracker/data/*`; `DbUser` default
  `app` → `legacytracker_sandbox_app`.
- `infra/Makefile` — drop the `deploy-network deploy-data` prerequisites from
  `deploy-sandbox-api` (the sandbox no longer depends on the legacy data/network
  stacks). Point `bootstrap-sandbox-db` at the shared instance's master secret.

This is optional and independent of the prod cutover; the plan's cost math
(one instance) is unaffected either way.