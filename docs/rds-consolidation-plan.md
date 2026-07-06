# RDS Consolidation Plan — one instance for job-tracker + legacy-tracker

**Status:** proposed · **Author:** drafted with Claude · **Date:** 2026-06-01

Collapse the two `db.t4g.micro` MySQL instances (`jobtracker-db` and
`legacytracker-db`) onto a single shared instance, and tear down the
now-redundant legacy VPC. Both projects are single-user personal apps; a
second always-on instance buys nothing but cost.

---

## 1. Current state

Two independent stacks in account `381492047863`, region `us-west-2`:

| | job-tracker | legacy-tracker |
|---|---|---|
| RDS instance | `jobtracker-db` (t4g.micro, 20 GB gp3) | `legacytracker-db` (t4g.micro, 20 GB gp3) |
| Owning CFN stack | `jobtracker-data` | `legacytracker-data` |
| VPC stack | `jobtracker-network` | `legacytracker-network` |
| Schema (DBName) | `jobtracker` | `legacytracker` |
| Master user | `jtadmin` (Secrets Manager) | `ltadmin` (Secrets Manager) |
| Lambda DB user | `app` (IAM auth, `GRANT ALL ON jobtracker.*`) | `app` (IAM auth, `GRANT ALL ON legacytracker.*`) |
| Lambdas in VPC? | **No** — connect over public endpoint + IAM | **No** — same |

Both repos share the **identical** connection helper (`common/db.py`): IAM
auth token + TLS over the public RDS endpoint, Lambda authenticates as the
`DbUser` env var against `dbuser:${DbResourceId}/${DbUser}`.

Billed run-rate today ≈ **$28.80/mo** (two instances + 2× storage + backups).

### Why two exist
Historical only — each project was scaffolded from the same template, each
with its own data + network stack. There is no capacity, version, region, or
isolation requirement forcing separation. User confirmed: no reason to keep
them apart.

---

## 2. Target state

One instance hosts both schemas; legacy keeps its own SSM namespace and repo
so the two codebases stay independent at the application layer.

| | After |
|---|---|
| RDS instance | `jobtracker-db` only (survivor) |
| Schemas | `jobtracker` **and** `legacytracker` on the one instance |
| VPC | `jobtracker-network` only |
| job-tracker Lambda user | `app` → `GRANT ALL ON jobtracker.*` (**unchanged**) |
| legacy Lambda user | **`legacytracker_app`** → `GRANT ALL ON legacytracker.*` (new) |
| `legacytracker-data` stack | RDS removed; becomes a thin SSM-only stack that republishes the shared endpoint/resource-id under `/legacytracker/data/*` |
| `legacytracker-network` stack | **deleted** |

Billed run-rate ≈ **$14.50/mo** (one instance). With a 1-yr no-upfront
Reserved Instance on the survivor: **≈ $10.50/mo**.

### The isolation model (the crux)
A shared instance means logical isolation replaces physical. Two
independent gates, both required to cross schemas — neither exists for the
wrong tenant:

1. **IAM** — each project's Lambda role only allows `rds-db:connect` to its
   own `dbuser:<sharedResourceId>/<user>`. job-tracker → `app`, legacy →
   `legacytracker_app`. A Lambda literally cannot mint an auth token for the
   other user.
2. **MySQL GRANT** — each user is `GRANT ALL` on its own schema only. Even
   with a token, the other schema is invisible.

This is why the legacy Lambda user **must be renamed** off `app`: on
separate instances both could be `app`; on one instance there is a single
`app` principal, so the second tenant needs its own distinct user.

> Note: eliminating the legacy VPC is correct (less to maintain, smaller
> attack surface, one fewer stack) but is **not itself a cost saving** — a
> VPC, its subnets, IGW, route tables, and security group are all free. The
> entire ~$14/mo saving is the second RDS instance going away. There is no
> NAT gateway in either VPC, so nothing else to reclaim.

---

## 3. Cutover steps

Ordered to keep both apps live until the final repoint, with a snapshot
safety net at every destructive step. Steps that touch AWS or code are
flagged; **Brian runs all deploys/migrations/SQL and commits** (per repo
conventions — Claude drafts, does not execute).

### Phase A — Prep (no downtime)
1. **Snapshot both instances manually** (belt-and-suspenders; the stacks are
   already `DeletionPolicy: Snapshot`):
   `aws rds create-db-snapshot --db-instance-identifier legacytracker-db --db-snapshot-identifier legacytracker-preconsolidation`
   (and the same for `jobtracker-db`).
2. **Create the legacy schema + user on the shared instance.** Connect to
   `jobtracker-db` as `jtadmin` (master secret) and run:
   ```sql
   CREATE DATABASE legacytracker CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   CREATE USER 'legacytracker_app'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
   GRANT ALL PRIVILEGES ON legacytracker.* TO 'legacytracker_app'@'%';
   FLUSH PRIVILEGES;
   ```
   job-tracker's `app` user is left untouched and stays scoped to
   `jobtracker.*`.

### Phase B — Move the data (brief legacy write-freeze)
3. **Freeze legacy writes** (it's single-user — just don't use the legacy app
   during this window).
4. **Dump → load** the legacy data:
   `mysqldump --single-transaction --set-gtid-purged=OFF -h legacytracker-db... -u ltadmin -p legacytracker > legacytracker.sql`
   then load into the shared instance:
   `mysql -h jobtracker-db... -u jtadmin -p legacytracker < legacytracker.sql`
   (TLS flags / CA bundle as in the README connect step.)
5. **Spot-check** row counts on the shared `legacytracker` schema vs the dump.

### Phase C — Repoint legacy to the shared instance (legacy repo)
6. **Convert `legacytracker-data` into an SSM-only stack** (see §4). It stops
   creating an RDS instance and instead republishes the shared instance's
   endpoint/port/resource-id under `/legacytracker/data/*`, with
   `db-name = legacytracker`. Deploy it as an **update** (CFN deletes the
   `Db` resource → fires the `Snapshot` DeletionPolicy → keeps a final
   automatic snapshot of `legacytracker-db`).
7. **Set the legacy Lambda user to `legacytracker_app`** — change the
   `DbUser` parameter default (or samconfig override) in
   `legacy-tracker/infra/api`. The IAM resource ARN is already templated as
   `dbuser:${DbResourceId}/${DbUser}`, and `DbResourceId` now resolves (via
   SSM) to the **shared** instance's resource id — so the policy auto-targets
   `dbuser:<sharedResourceId>/legacytracker_app`. Redeploy the legacy api
   stack.
8. **Smoke-test the legacy app** end-to-end against the shared instance.

### Phase D — Decommission (destructive — only after C verifies)
9. **Drop the sandbox** (decided: not migrated). Delete the
   `legacytracker-sandbox-api` and sandbox-frontend stacks:
   `aws cloudformation delete-stack --stack-name legacytracker-sandbox-api`.
   The `legacytracker_sandbox` schema is never moved to the shared instance —
   it dies with the old `legacytracker-db`. No sandbox user is created on the
   shared instance.
10. **Delete the `legacytracker-data` RDS** — already handled by the step-6
    update if the `Db` resource was removed there; confirm the instance is
    gone and a final snapshot exists.
11. **Delete the `legacytracker-network` stack** (the VPC). Safe because no
    Lambda runs in it and the RDS that used its subnet group is gone. No ENI
    cleanup concern — the Lambdas were never VPC-attached (contrast the
    `lambda_eni_cleanup` gotcha, which only applies when removing `VpcConfig`
    from in-VPC functions).
12. **Retain the pre-consolidation snapshots** for a cooling-off period
    (e.g. 30 days), then delete to stop paying snapshot storage.

job-tracker requires **no changes and no redeploy** — its instance, VPC,
user, and SSM are all untouched.

---

## 4. Concrete changes

> Exact drafted diffs for all of the below live in the 2026-06-01 chat and
> should be applied to the legacy repo (with Brian's approval) — summarized
> here.

### legacy-tracker repo
- **`infra/data/template.yaml`** — full rewrite to **SSM-only**. Remove the
  `Db` (`AWS::RDS::DBInstance`), `DbSubnetGroup`, the
  `PublicSubnetIds`/`RdsSgId`/`DbInstanceClass` params, and
  `SsmDbMasterSecretArn` (legacy no longer owns a master credential). Add
  params sourced from job-tracker's SSM and republish under
  `/legacytracker/data/*`:
  ```yaml
  Parameters:
    SharedDbEndpoint:   { Type: AWS::SSM::Parameter::Value<String>, Default: /jobtracker/data/db-endpoint }
    SharedDbPort:       { Type: AWS::SSM::Parameter::Value<String>, Default: /jobtracker/data/db-port }
    SharedDbResourceId: { Type: AWS::SSM::Parameter::Value<String>, Default: /jobtracker/data/db-resource-id }
    DbName:             { Type: String, Default: legacytracker }
  ```
  `SsmDbEndpoint`/`SsmDbPort`/`SsmDbResourceId` now take `!Ref Shared*`;
  `SsmDbName` stays `legacytracker`. The legacy `api` stack keeps reading
  `/legacytracker/data/*` and needs **no** SSM-path edits.
- **`infra/api/template.yaml`** — `DbUser` default `app` →
  `legacytracker_app`. No other api changes; `db.py` and handlers untouched.
  The IAM ARN `dbuser:${DbResourceId}/${DbUser}` auto-targets the shared
  instance once `DbResourceId` resolves to the republished shared id.
- **`infra/Makefile`** — drop `deploy-network` from `deploy-data`,
  `deploy-api`, `deploy-all`, `.PHONY`, and `help`; delete the
  `deploy-network` target. Repoint `db-creds` from
  `/legacytracker/data/db-master-secret-arn` →
  `/jobtracker/data/db-master-secret-arn`. **Remove all sandbox machinery**
  (`SANDBOX_DB_NAME`, `bootstrap-sandbox-db`, `deploy-sandbox-*`,
  `wire-sandbox-frontend`) — sandbox is being dropped.
- **`infra/network/`** and **`infra/api/template-sandbox.yaml`** — deleted
  with their stacks (Phase D). Leave a short note in the legacy README that
  the DB now lives in the shared `jobtracker-db` instance.

### Decided: sandbox is dropped
The live `legacytracker-sandbox-api` (shared the old instance via a
`legacytracker_sandbox` schema as `app`) is **not** migrated. Only one legacy
user is created on the shared instance: `legacytracker_app`,
`GRANT ALL ON legacytracker.*` only.

> **Amendment (rehearsal-first):** before the real Phase A–B run, the sandbox is
> used as a **dress rehearsal** of the whole mechanism — its
> `legacytracker_sandbox` schema is temporarily migrated to the shared instance
> under a `legacytracker_sandbox_app` user, the sandbox api is repointed, and
> the flow is verified end-to-end. See `rds-consolidation-sandbox-rehearsal.md`.
> That rehearsal is then torn down (§6 of that doc), restoring this
> "sandbox is dropped" end state — *unless* the sandbox is deliberately kept on
> the shared instance (§7 there), in which case a `legacytracker_sandbox_app`
> user persists alongside `legacytracker_app`.

### job-tracker repo
- **None to code or infra.** Optionally document in this repo's README that
  `jobtracker-db` is now shared and hosts a second `legacytracker` schema
  owned by the legacy-tracker project, so a future teardown of
  `jobtracker-data` is known to also affect legacy.

---

## 5. Rollback

- **Through Phase C:** legacy still has its original instance until step 9.
  If anything fails, revert the legacy `data`/`api` stacks to the prior
  template (recreates/repoints to `legacytracker-db`) — data there is intact
  because the dump in Phase B was read-only against it.
- **After Phase D:** restore `legacytracker-db` from
  `legacytracker-preconsolidation` (or the automatic deletion snapshot) into a
  fresh instance, and revert the two legacy stacks. This is why the snapshots
  are held 30 days before deletion.

---

## 6. Post-cutover follow-ups

- **Buy the Reserved Instance.** 1-yr no-upfront on the `t4g` family,
  `db.t4g.micro`, MySQL, us-west-2 — auto-applies to the survivor, ~34% off
  compute, no downtime. This is the single highest-value action after
  consolidation and has no UX cost (unlike stop/start or Aurora auto-pause,
  which were rejected for the multi-minute / ~15s cold-start on first visit).
- **Backups** now bill once instead of twice; no action needed.
- Consider tightening the `app` / `legacytracker_app` grants from `ALL` to
  the specific privileges each app uses (the README already flags this as a
  hardening TODO).

---

## 7. Expected outcome

| | Before | After consolidation | After + RI |
|---|---|---|---|
| Instances | 2 | 1 | 1 |
| VPC stacks | 2 | 1 | 1 |
| Monthly RDS cost | ~$28.80 | ~$14.50 | **~$10.50** |
| Annualized saving | — | ~$170 | **~$220** |

No capacity, availability, or isolation regression: two single-user apps
share one burstable micro comfortably, and IAM + per-schema GRANTs keep the
tenants fully walled off.
