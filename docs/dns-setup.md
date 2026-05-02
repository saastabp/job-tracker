# DNS / custom-domain setup — shipped 2026-05-02

Standalone infra task done outside any slice. Result: SPA is served at
`https://jobs.kololecat.link` with an ACM cert in us-east-1 and a Route 53
alias record fronting the CloudFront distribution.

## Final state

- **Domain:** `kololecat.link`, registered via Route 53 Domains, expires
  2027-05-02. (Earlier attempt at `crazylittledog.link` was abandoned —
  hosted zone existed but the domain was never registered at the .link
  TLD, which silently stalled ACM validation for 8+ hours. Lesson saved
  to memory: `dig NS <domain> @8.8.8.8 +short` is now a prerequisite
  check before relying on auto-validation.)
- **Hosted zone:** `Z0729608URC9WKRYW5YI` — registrar-created, NS
  records `awsdns-54.com / 55.org / 31.co.uk / 54.net`, delegated at
  the .link TLD.
- **SSM (us-west-2):** `/jobtracker/dns/cert-arn` and
  `/jobtracker/dns/app-url=https://jobs.kololecat.link` — written by
  `deploy-dns` after the us-east-1 stack outputs them.

## Files

- `infra/dns/template.yaml` — us-east-1 stack: ACM cert (DNS-validated
  via `DomainValidationOptions.HostedZoneId`) + Route 53 A/AAAA alias
  records pointing at the SPA's CloudFront distribution. Defaults
  baked in: `HostedZoneId=Z0729608URC9WKRYW5YI`,
  `AppDomain=jobs.kololecat.link`.
- `infra/dns/samconfig.toml` — `stack_name=jobtracker-dns`,
  `region=us-east-1`.
- `infra/frontend/template.yaml` — optional `AppDomain` +
  `CertificateArn` parameters and a `HasCustomDomain` condition that
  gates `Aliases` + `ViewerCertificate { sni-only, TLSv1.2_2021 }`.
  Empty defaults preserve the cloudfront.net-only behavior.
- `infra/Makefile` — `deploy-dns` / `delete-dns` targets, plus
  `deploy-frontend`, `deploy-auth`, `deploy-api`, `deploy-ai`, and
  `wire-frontend` thread `/jobtracker/dns/app-url` through
  Cognito callbacks and API/AI CORS when the SSM param exists.
  `deploy-dns` is intentionally *not* in `deploy-all`'s dep chain —
  custom domain is opt-in.

## Operational notes

### Idempotency

`make deploy-dns` is standalone-safe: it deploys the dns stack,
writes the cert ARN + app URL to SSM, then re-runs `deploy-frontend`
to attach the alias in the same shot. (Earlier ran into a one-time
glitch where the recursive `$(MAKE) deploy-frontend` didn't fire,
leaving the cert issued but the alias unattached and CloudFront
returning 403. Re-running `make deploy-frontend` once SSM is populated
is the recovery path. Recipe is now in place to handle this in one
invocation, but worth knowing if it ever gets stuck again.)

### Tearing down

`make delete-dns` deletes the stack (cert + alias records) and the two
SSM params. The next `make deploy-frontend` will see empty SSM params
and re-deploy the distribution without aliases — falling back to the
default cloudfront.net domain. Cognito callbacks / API CORS will
*still* contain the custom origin until the next `wire-frontend`
clears it (no harm, just noise).

### Re-deploying or migrating to a new domain

1. `dig NS <new-domain> @8.8.8.8 +short` → expect AWS NS records.
2. Edit `infra/dns/template.yaml` defaults (`HostedZoneId`,
   `AppDomain`).
3. `make delete-dns` then `make deploy-dns`.
4. `make wire-frontend && make sync-frontend`.