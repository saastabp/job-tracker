# RDS Consolidation — Full Cutover Runbook (copy-paste)

Companion to `rds-consolidation-plan.md`. End-to-end commands for the real
production cutover: snapshot → bootstrap → data-move → repoint → decommission
(plan Phases A–D). **Brian runs all of these.**

Prerequisite: the sandbox rehearsal (`rds-consolidation-sandbox-rehearsal.md`)
has already validated the mechanism (dump/load, IAM auth, per-schema isolation).
Phases A–B are read-only against the legacy source. Phase C repoints the apps at
the shared instance **without destroying anything** — the old instance stays
live as a rollback. Phase D verifies, unfreezes, and only then — as a separate,
deliberate step — tears down the old instance + VPC. Run C onward only after B4
shows ROW COUNTS MATCH, inside the write-freeze window.

**Decision change — the sandbox is kept, not dropped.** It already lives on the
shared instance (from the rehearsal). Phase C redeploys it onto durable config;
Phase D must **not** delete the sandbox stacks.

All commands assume:

- Profile `brian-admin`, region `us-west-2`.
- Run from the job-tracker repo root so the relative paths resolve, or rely on
  the absolute CA path baked into the commands below.
- A local `mysql` / `mysqldump` client (MySQL 8.x to match server 8.4).

| | value |
|---|---|
| Shared (target) endpoint | `jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com` |
| Shared master user | `jtadmin` |
| Shared master secret | `arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-2a8e01b9-fcca-4fe9-ba9a-760bcd98c70c-Y6ssRX` |
| Legacy (source) endpoint | `legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com` |
| Legacy master user | `ltadmin` |
| Legacy master secret | `arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-138e398b-50f3-42c4-9721-2b421cc5c2bf-681gf4` |
| CA bundle | `/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem` |

### Prep — code edits (already staged in the legacy repo)

Four edits make **both** legacy apps durably read the shared instance through
the republished `/legacytracker/data/*` namespace — no CLI overrides, no
reference to the old instance. **All four are already applied** in
`~/360-balanced-living/legacy-tracker` (staged ahead of the run so you can work
straight through). Full rationale in plan §4; summary of what's in place:

1. `infra/data/template.yaml` → **two revisions** (the RDS teardown is
   deliberately separated from the repoint):
   - **Rev 1 = `infra/data/template.yaml` (applied now; deploys in Phase C —
     non-destructive):** adds `SharedDb*` params sourced from `/jobtracker/data/*`
     and changes the `/legacytracker/data/{db-endpoint,db-port,db-resource-id}`
     SSM param *values* from `!GetAtt Db.*` to those shared coords. **Keeps** the
     `Db` (`AWS::RDS::DBInstance`) + `DbSubnetGroup` — the old instance stays
     alive. `db-name` stays `legacytracker`.
   - **Rev 2 = `infra/data/template-rev2.yaml` (staged; deploys in Phase D —
     destructive):** the SSM-only version with the `Db` + `DbSubnetGroup`
     removed (they keep `DeletionPolicy: Snapshot`, so the delete takes a final
     snapshot). At D4 you `cp` it over `template.yaml` and deploy. This is the
     **only** step that deletes `legacytracker-db`, and it runs after Phase C is
     verified.
2. `infra/api/template.yaml` → `DbUser` default `app` → `legacytracker_app`.
3. `infra/api/template-sandbox.yaml` → `DbUser` default `app` →
   `legacytracker_sandbox_app` (endpoint/resource-id defaults **stay**
   `/legacytracker/data/*` — they resolve to shared after edit #1).
4. `infra/Makefile` → removed `deploy-network` from `deploy-data`/`deploy-api`/
   `deploy-all`/`.PHONY`/`help` and deleted the `deploy-network` target; dropped
   the `deploy-network deploy-data` prereqs from `deploy-sandbox-api`; repointed
   `db-creds` to `/jobtracker/data/db-master-secret-arn`. (`bootstrap-sandbox-db`
   is left in place with a "superseded" comment — do not run it post-cutover.)
   **`deploy-api` and `deploy-sandbox-api` now pass `DbUser` explicitly** in
   `--parameter-overrides` (`legacytracker_app` / `legacytracker_sandbox_app`) —
   the template `Default:` change in #2/#3 is necessary but does **not** apply to
   an already-deployed stack, because `sam deploy` reuses the stack's existing
   parameter value for anything not passed. This is the fix for the "pages 500
   with `Access denied for user 'app'`" symptom.

Nothing here deploys until the cutover: Rev 1 deploys in Phase C (C1), Rev 2 in
Phase D (D4). Deploying Rev 2 is what deletes the old RDS, so it must come after
the Phase B data move **and** Phase C verification. Since the four edits are
already in the working tree, review the diff (`git -C ~/360-balanced-living/legacy-tracker diff`) before you start, and commit whenever you like.

### Step 0 — session setup

```sh
export AWS_PROFILE=brian-admin AWS_REGION=us-west-2
```

```sh
mysql --version && mysqldump --version
```

**Safety — tag the pre-cutover state** so the Phase D3 rollback is a clean
restore. Run this **before** committing the staged cutover edits (it marks the
original templates; if you've already committed them, append the parent commit
hash: `git ... tag rds-pre-consolidation <hash>`):

```sh
git -C ~/360-balanced-living/legacy-tracker tag rds-pre-consolidation
```

Load the two master passwords into shell vars (used by every `mysql`/`mysqldump` call below):

```sh
SHARED_PW=$(aws secretsmanager get-secret-value --secret-id 'arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-2a8e01b9-fcca-4fe9-ba9a-760bcd98c70c-Y6ssRX' --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
```

```sh
LEGACY_PW=$(aws secretsmanager get-secret-value --secret-id 'arn:aws:secretsmanager:us-west-2:381492047863:secret:rds!db-138e398b-50f3-42c4-9721-2b421cc5c2bf-681gf4' --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
```

Sanity-check both connect (each should print its schema list):

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e 'SHOW DATABASES;'
```

```sh
MYSQL_PWD=$LEGACY_PW mysql -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e 'SHOW DATABASES;'
```

---

## Phase A — snapshots + bootstrap

### A1 — manual safety snapshots (both instances)

```sh
aws rds create-db-snapshot --db-instance-identifier legacytracker-db --db-snapshot-identifier legacytracker-preconsolidation
```

```sh
aws rds create-db-snapshot --db-instance-identifier jobtracker-db --db-snapshot-identifier jobtracker-preconsolidation
```

Wait until both are `available` before touching data:

```sh
aws rds wait db-snapshot-available --db-snapshot-identifier legacytracker-preconsolidation && aws rds wait db-snapshot-available --db-snapshot-identifier jobtracker-preconsolidation && echo "snapshots ready"
```

### A2 — bootstrap the legacy schema + IAM user on the SHARED instance

The SQL being run (as `jtadmin` against the shared instance):

```sql
CREATE DATABASE IF NOT EXISTS legacytracker CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'legacytracker_app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON legacytracker.* TO 'legacytracker_app'@'%';
FLUSH PRIVILEGES;
```

Run it as a single command:

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e "CREATE DATABASE IF NOT EXISTS legacytracker CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; CREATE USER 'legacytracker_app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS'; GRANT ALL PRIVILEGES ON legacytracker.* TO 'legacytracker_app'@'%'; FLUSH PRIVILEGES;"
```

Verify the user landed and is scoped to `legacytracker.*` only (and NOT `jobtracker.*`):

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -e "SHOW GRANTS FOR 'legacytracker_app'@'%';"
```

---

## Phase B — move the data

> **Schema verified dump-safe** (legacy migrations 0001–0005, checked
> 2026-06-01): no generated/virtual/stored columns, no triggers, views,
> stored procedures, functions, or events. Every table is uniform
> `BIGINT UNSIGNED AUTO_INCREMENT` PK, `ENGINE=InnoDB`, `DEFAULT CHARSET=utf8mb4`
> — no `utf8mb3`, no exotic collations. `mysqldump` emits `AUTO_INCREMENT=<n>`
> per table so counters and existing id values carry over exactly. This is a
> plain logical copy with no manual intervention required; the
> `--routines --triggers --events` flags below are harmless no-ops here.

> **Freeze legacy writes first** — just don't use the legacy app during this
> window (it's single-user). The dump is consistent as of its start via
> `--single-transaction`, but anything written after the dump won't come along.

### B1 — record source row counts (for the post-load diff)

```sh
MYSQL_PWD=$LEGACY_PW mysql -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N -e "SELECT table_name, COUNT(*) FROM information_schema.tables WHERE table_schema='legacytracker'; SELECT CONCAT(table_name,'=',table_rows) FROM information_schema.tables WHERE table_schema='legacytracker' ORDER BY table_name;"
```

`table_rows` from information_schema is an InnoDB **estimate** — fine as a quick
diff, but B4 below does exact `COUNT(*)` for the real check.

### B2 — dump the legacy schema

```sh
MYSQL_PWD=$LEGACY_PW mysqldump -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA --single-transaction --set-gtid-purged=OFF --no-tablespaces --routines --triggers --events --databases legacytracker > /tmp/legacytracker.sql
```

Notes on the flags:
- `--single-transaction` — consistent snapshot without locking (InnoDB).
- `--set-gtid-purged=OFF` — strips GTID state; this is a logical copy into an
  unrelated instance, not replication.
- `--no-tablespaces` — avoids needing the `PROCESS` privilege the RDS master
  user doesn't have.
- `--routines --triggers --events` — carry stored programs if legacy has any
  (harmless if it doesn't).
- `--databases legacytracker` — emits `CREATE DATABASE IF NOT EXISTS` + `USE`,
  so the load targets the right schema regardless of client default.

Quick eyeball of the dump:

```sh
ls -lh /tmp/legacytracker.sql && grep -c 'CREATE TABLE' /tmp/legacytracker.sql
```

### B3 — load into the shared instance (as `jtadmin`)

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA < /tmp/legacytracker.sql
```

### B4 — verify: exact row counts match, source vs target

Generate per-table `COUNT(*)` on each side and diff. Source:

```sh
MYSQL_PWD=$LEGACY_PW mysql -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N -e "SELECT CONCAT('SELECT ''',table_name,''' AS t, COUNT(*) AS n FROM legacytracker.\`',table_name,'\`;') FROM information_schema.tables WHERE table_schema='legacytracker' AND table_type='BASE TABLE';" | MYSQL_PWD=$LEGACY_PW mysql -h legacytracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u ltadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N | sort > /tmp/legacy-counts-source.txt
```

Target (same tables, on the shared instance):

```sh
MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N -e "SELECT CONCAT('SELECT ''',table_name,''' AS t, COUNT(*) AS n FROM legacytracker.\`',table_name,'\`;') FROM information_schema.tables WHERE table_schema='legacytracker' AND table_type='BASE TABLE';" | MYSQL_PWD=$SHARED_PW mysql -h jobtracker-db.cbkm4o8661hx.us-west-2.rds.amazonaws.com -u jtadmin --ssl-ca=/home/brians/projects/job-tracker/backend/src/rds-ca-bundle.pem --ssl-mode=VERIFY_CA -N | sort > /tmp/legacy-counts-target.txt
```

```sh
diff /tmp/legacy-counts-source.txt /tmp/legacy-counts-target.txt && echo "ROW COUNTS MATCH" || echo "MISMATCH — investigate before proceeding"
```

A clean `diff` (exit 0, "ROW COUNTS MATCH") means every legacy table has
identical row counts on the shared instance. Only then proceed to **Phase C**
(repoint the legacy `data`/`api` stacks — see the plan) and, after that
verifies, **Phase D** (decommission).

---

## Optional — prove IAM auth works as `legacytracker_app` before cutover

After the legacy api stack is redeployed with `DbUser=legacytracker_app`
(Phase C), the Lambdas authenticate with an IAM token, not the master
password. You can't easily mint that token by hand, but you can confirm the
plumbing by hitting a legacy API endpoint and checking CloudWatch for a clean
`db: connection opened` log. If it fails with `Access denied`, re-check the
`SHOW GRANTS` output from A2 and that `EnableIAMDatabaseAuthentication` is on
for the shared instance (it is, per the jobtracker-data template).

---

## Phase C — repoint prod + sandbox at the shared instance (non-destructive)

Deploys the Prep edits' **Rev 1**. After this, `/legacytracker/data/*` resolves
to the **shared** instance and both legacy apps read it, each scoped by its own
`DbName` + `DbUser`. **Nothing is destroyed here — the old `legacytracker-db`
stays alive** as a rollback net. Run only after **B4 shows ROW COUNTS MATCH**,
inside the freeze.

No prod-down window: because the old instance is still up, prod keeps serving
before *and* after the redeploy — C2 just switches which instance the Lambdas
resolve to, and the shared copy already has the verified data.

### C1 — deploy data-stack Rev 1 (repoint the SSM coords, keep the old RDS)

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make deploy-data
```

Updates `/legacytracker/data/{db-endpoint,db-port,db-resource-id}` to the shared
instance's values. The `Db` resource is untouched — the old instance keeps
running, just unreferenced. Confirm the params now point at shared (endpoint
should read `jobtracker-db…`, resource-id `db-S3W4OGB2Q5LC2HMLD4IZIS6FLQ`):

```sh
for p in db-endpoint db-port db-resource-id; do aws ssm get-parameter --name /legacytracker/data/$p --query Parameter.Value --output text; done
```

### C2 — redeploy the prod api

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make deploy-api
```

`deploy-api` passes `DbUser=legacytracker_app` **explicitly** in its
`--parameter-overrides`. This is required: `sam deploy` reuses a stack's
*existing* parameter value for anything not passed, so changing the template
`Default:` alone leaves an already-deployed stack on `DbUser=app` (which then
gets `Access denied` on `legacytracker`). It also picks up the republished
`/legacytracker/data/*` (= shared). The IAM ARN `dbuser:${DbResourceId}/${DbUser}`
auto-targets `dbuser:<sharedResourceId>/legacytracker_app`.

### C3 — redeploy the sandbox api onto durable config

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make deploy-sandbox-api
```

`deploy-sandbox-api` passes `DbName=legacytracker_sandbox` and
`DbUser=legacytracker_sandbox_app` **explicitly** (same `sam deploy` reuse rule
as C2). Its DB-endpoint params keep their existing stored values
(`/jobtracker/data/*` from the rehearsal), which resolve to the same shared
instance — so the sandbox is on the shared DB either way. Both legacy apps now
authenticate as their own per-schema user against `jobtracker-db`.

---

## Phase D — verify, unfreeze, then tear down the old instance (separate + destructive)

The old instance is still alive until D4, so a verification failure rolls back
**live** — no snapshot restore.

### D1 — verify on the shared instance

**Prod IAM-auth probe** — the migrate Lambda is invoked directly (bypasses the
Cognito authorizer), so it's the cleanest scriptable prod check. It connects as
`legacytracker_app`; prod is at 0007, so expect nothing pending:

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make migrate
```

Expect `{"applied": []}` (already at head). A clean return proves the prod Lambda
authenticates as `legacytracker_app` against `jobtracker-db` via IAM.

**Sandbox IAM-auth probe** — same, as `legacytracker_sandbox_app`:

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make migrate-sandbox
```

**Sandbox health endpoint** — the sandbox api has no Cognito, so it curls
directly; expect `{"ok": true, ..., "db": {"ok": 1}}`:

```sh
SB_API=$(aws ssm get-parameter --name /legacytracker-sandbox/api/url --query Parameter.Value --output text --region us-west-2) && curl -s "$SB_API/health" && echo
```

**Prod end-user path** — prod `/health` sits behind Cognito, so it can't be
curled unauthenticated. Open the prod SPA, log in, and exercise a real read +
write:

```sh
aws ssm get-parameter --name /legacytracker/frontend/cloudfront-url --query Parameter.Value --output text --region us-west-2
```

Then confirm that request path hit the shared DB cleanly as `legacytracker_app`
(substitute the function you exercised; `legacytracker-health` shown as example):

```sh
aws logs tail /aws/lambda/legacytracker-health --since 10m --region us-west-2 --format short
```

(look for a clean `db: connection opened` and no `Access denied`.)

### D2 — unfreeze
Once D1 is green, tell the user they're clear. They're now on the shared
instance; the old one is running but idle. You can leave it as a rollback net for
a cooling-off period before D4 — at the cost of paying for both instances until
then.

### D3 — rollback checkpoint
If anything looked wrong in D1, abort here — the old instance is still alive.
Restore the four pre-cutover templates from the Step 0 tag:

```sh
git -C ~/360-balanced-living/legacy-tracker checkout rds-pre-consolidation -- infra/data/template.yaml infra/api/template.yaml infra/api/template-sandbox.yaml infra/Makefile
```

Then redeploy — this repoints `/legacytracker/data/*` back to the old instance
and returns both apps to `DbUser=app`:

```sh
cd ~/360-balanced-living/legacy-tracker/infra && make deploy-data && make deploy-api && make deploy-sandbox-api
```

Both apps are now back on the still-alive `legacytracker-db`, whose data is
intact (the dump was read-only and the freeze blocked new writes). Investigate,
then re-attempt from Phase C when ready. Only proceed past this point (to D4)
once D1 is green.

### D4 — tear down the old RDS (data-stack Rev 2)
Swap the pre-staged Rev 2 template in for `template.yaml` (it removes the `Db` +
`DbSubnetGroup`), then deploy:

```sh
cp ~/360-balanced-living/legacy-tracker/infra/data/template-rev2.yaml ~/360-balanced-living/legacy-tracker/infra/data/template.yaml && cd ~/360-balanced-living/legacy-tracker/infra && make deploy-data
```

CFN deletes `legacytracker-db`, firing the `Snapshot` deletion policy (final
automatic snapshot). This is the **only** instance-destroying step, and it runs
entirely after prod is confirmed on the shared instance.

### D5 — delete the legacy VPC stack

```sh
aws cloudformation delete-stack --stack-name legacytracker-network --region us-west-2
```

Safe: the Lambdas were never in-VPC, and the old RDS that used the subnet group
was just deleted in D4. No ENI-cleanup concern (that gotcha only applies when
removing `VpcConfig` from in-VPC functions).

### D6 — confirm + follow-ups

```sh
aws rds describe-db-instances --db-instance-identifier legacytracker-db --region us-west-2 2>&1 | head -3
```

(expect `DBInstanceNotFound`.)

```sh
aws rds describe-db-snapshots --db-instance-identifier legacytracker-db --region us-west-2 --query "DBSnapshots[].DBSnapshotIdentifier" --output text
```

> **Do NOT delete the sandbox stacks.** The sandbox is kept and now lives on the
> shared instance. Only `legacytracker-network` is deleted here (the old RDS went
> in D4). `legacytracker-sandbox-api` and the sandbox frontend stack stay.

- **Buy the 1-yr no-upfront Reserved Instance** on `db.t4g.micro` MySQL
  us-west-2 — auto-applies to the survivor, ~34% off compute, no downtime.
  Highest-value post-cutover action.
- After a ~30-day cooling-off, delete the pre-consolidation snapshots
  (`jobtracker-preconsolidation`, `legacytracker-preconsolidation`) to stop
  paying snapshot storage.

---

## Cleanup of this session's shell state

```sh
unset SHARED_PW LEGACY_PW && rm -f /tmp/legacytracker.sql /tmp/legacy-counts-source.txt /tmp/legacy-counts-target.txt
```