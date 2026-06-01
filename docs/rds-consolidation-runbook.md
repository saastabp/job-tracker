# RDS Consolidation — Phase A–B Runbook (copy-paste)

Companion to `rds-consolidation-plan.md`. Concrete commands for the
snapshot → bootstrap → data-move steps. **Brian runs all of these.** They are
read-only against the legacy source until the very end; nothing here deletes an
instance (that's Phase D in the plan).

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

### Step 0 — session setup

```sh
export AWS_PROFILE=brian-admin AWS_REGION=us-west-2
```

```sh
mysql --version && mysqldump --version
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

## Cleanup of this session's shell state

```sh
unset SHARED_PW LEGACY_PW && rm -f /tmp/legacytracker.sql /tmp/legacy-counts-source.txt /tmp/legacy-counts-target.txt
```