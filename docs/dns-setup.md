# DNS / custom-domain setup — in progress

Standalone infra task done outside any slice. Goal: serve the SPA at
`https://jobs.kololecat.link` instead of the bare CloudFront domain.

## State as of 2026-05-02

- **Domain:** `kololecat.link` registration in flight via Route 53 Domains
  (us-east-1). Was switched from `crazylittledog.link` after that domain
  turned out to have a hosted zone but no actual TLD delegation, which
  silently stalled ACM validation for 8+ hours. Previous broken stack
  and hosted zone have been deleted.
- **Hosted zone (kept):** `Z0729608URC9WKRYW5YI` — the registrar-created
  zone. NS records visible in console: `awsdns-54.com / 55.org / 31.co.uk
  / 54.net`. Duplicate manual zone for the same name was deleted.
- **Code in place** (uncommitted on branch `slice/08-submission-contacts`):
  - `infra/dns/template.yaml` — us-east-1 stack: ACM cert (DNS-validated,
    auto-write CNAME via `DomainValidationOptions.HostedZoneId`) +
    Route 53 A/AAAA alias records pointing at the SPA's CloudFront.
    Defaults baked in: `HostedZoneId=Z0729608URC9WKRYW5YI`,
    `AppDomain=jobs.kololecat.link`.
  - `infra/dns/samconfig.toml` — `stack_name=jobtracker-dns`,
    `region=us-east-1`.
  - `infra/frontend/template.yaml` — added optional `AppDomain` +
    `CertificateArn` parameters and `HasCustomDomain` condition that
    gates `Aliases` + `ViewerCertificate`. Empty defaults preserve
    cloudfront.net-only behavior.
  - `infra/Makefile` — new `deploy-dns` (depends on `deploy-frontend`,
    runs `sam deploy` in us-east-1, writes cert ARN + app URL to
    us-west-2 SSM, then re-invokes `deploy-frontend` so the alias
    attaches in one shot per the idempotent-individual-deploys rule).
    New `delete-dns`. Updated `deploy-frontend`, `deploy-auth`,
    `deploy-api`, `deploy-ai`, `wire-frontend` to thread
    `/jobtracker/dns/app-url` through callbacks/CORS when present.
    `deploy-dns` deliberately not in `deploy-all`'s dep chain — opt-in
    upgrade.

## Resume runbook (when registration finishes)

1. **Verify the .link TLD has the delegation** (the step that should
   have been done before the prior failed attempt):

       dig NS kololecat.link @8.8.8.8 +short

   Expect four `awsdns-*` nameservers. If empty, the domain isn't
   delegated yet — wait. `aws route53domains get-domain-detail
   --domain-name kololecat.link --region us-east-1` should return
   without error once registration is committed.

2. **Deploy:**

       make -C infra deploy-dns

   This blocks on ACM validation (typically 5–15 min once delegation is
   in place), then re-runs `deploy-frontend` to attach the alias.

3. **Wire callbacks + CORS:**

       make -C infra wire-frontend

4. **Republish SPA + invalidate CloudFront:**

       make -C infra sync-frontend

5. **Smoke test:** `https://jobs.kololecat.link/` should load and
   sign-in (Cognito) should succeed against the new origin.

## Side note: the slice/07-followups branch

Has uncommitted work — Cadence card moved from Targets to Follow-ups
page (FollowUps.tsx) plus a Targets.tsx revert. Independent of the DNS
work; commit whenever you're ready.