# Slice 14 — Passkey authentication + silent-refresh bypass (PLAN)

Status: planned, not started. Captured 2026-05-29 at user request
("I will eventually want to do this, but not right this second").
Branch: `slice/14-passkey-auth` (TBD).
No dependencies on unshipped slices; touches the auth stack + frontend
auth layer only. Backend request handlers are unaffected.

Reference implementation: the sibling app at
`/home/brians/360-balanced-living/ghl/admin/web/app.js` already
implements this exact pattern (vanilla JS). This slice ports it into
job-tracker's React/TS frontend. Read `app.js` before starting — it is
the canonical source for the Cognito + WebAuthn ceremony glue.

## What the user asked for

> Update the signon sequence to allow passkey authentication, with the
> ability to create a passkey. Give it a 365 day TTL. Check for the
> passkey at load time and completely bypass the sign in process if it
> is present.

## The framing correction (important — don't build to the literal ask)

A WebAuthn passkey **cannot** be silently checked at load and used to
bypass sign-in. Every passkey assertion requires a fresh user gesture
(touch / biometric / PIN) by design — that is the security guarantee,
and there is no API to circumvent it.

What actually produces the "reload and land straight in the app, zero
interaction" behavior in the reference app is the **refresh token
persisted in IndexedDB** plus a silent `REFRESH_TOKEN_AUTH` on load
(`trySilentRefresh()` in `app.js`). The passkey is the **one-touch
re-login** used only when there is no valid refresh token (new device,
after the refresh token's TTL, or after explicit sign-out).

So the three asks map to:

| Ask | Mechanism |
|---|---|
| "Bypass sign-in at load if present" | Refresh token in IndexedDB + silent `REFRESH_TOKEN_AUTH` on bootstrap |
| "365-day TTL" | Cognito app-client `RefreshTokenValidity` (currently `30` days → `365`). This is the token that governs how long the load-time bypass keeps working. Passkey credentials themselves carry no TTL in Cognito. |
| "Create a passkey / passkey auth" | Cognito WebAuthn enroll (`StartWebAuthnRegistration` / `CompleteWebAuthnRegistration`) + sign-in (`USER_AUTH` flow, `WEB_AUTHN` challenge) |

Built together these deliver exactly the desired experience; the doc
just records that the passkey is not the load-time bypass.

## The architectural mismatch (this is NOT an additive change)

The reference app and job-tracker use **different Cognito auth
foundations**. Porting the pattern means *replacing* job-tracker's
foundation, not adding to it.

| | `ghl/admin` (reference) | `job-tracker` (today) |
|---|---|---|
| Stack | Vanilla JS | React + `react-oidc-context` |
| Flow | Direct Cognito HTTP API (`InitiateAuth`: `USER_AUTH` / `USER_PASSWORD_AUTH`) | **Hosted UI OIDC redirect** — `signinRedirect()` → Cognito hosted page → back with `?code=` |
| Login UI | In-app email/password + passkey buttons | Single "Sign in with Cognito" button; Cognito's hosted page collects credentials |
| Token storage | Refresh token in IndexedDB, access token in memory | Managed by `oidc-client-ts` in localStorage |

Consequence: this slice **rips out `react-oidc-context` and the
hosted-UI redirect flow** and replaces them with an in-app login form
that calls Cognito directly, plus hand-rolled token management.

## Locked decisions to carry in

- **Keep email/password as a fallback** (matches the reference). It is
  the bootstrap for enrolling the first passkey and the recovery path
  for a lost/wiped device. Passkey-only with no fallback locks the user
  out until an admin reset. (Decided 2026-05-29.)
- **Match the reference's `USER_PASSWORD_AUTH` for password sign-in**
  rather than SRP. SRP (`USER_SRP_AUTH`) avoids sending the password
  over the wire but is substantially more client code; the reference
  chose plain password over HTTPS. Revisit only if the threat model
  changes.
- **Backend Lambdas do not change.** They still validate the same
  Cognito access-token JWT via the API Gateway authorizer
  (`backend/src/common/auth.py` just reads verified claims).
- **365 days = `RefreshTokenValidity`**, not a passkey expiry.

## Caveats / open risks to resolve during the slice

1. **Cognito feature plan / cost.** Managed passkeys (the `USER_AUTH`
   choice-based flow) require the user pool on the **Essentials**
   feature plan; the free **Lite** plan does not support WebAuthn. For
   a 1-user pool the per-MAU cost is negligible, but it is a plan change
   and cost-minimization is a hard project constraint, so name it in the
   PR. The current `infra/auth/template.yaml` does not set
   `UserPoolTier` — **verify which tier the pool is actually on** before
   assuming a change is needed. (The sibling app already runs on
   Essentials.)
2. **RP-ID binding to `*.cloudfront.net`.** WebAuthn binds credentials
   to a relying-party ID (the host). job-tracker still serves from the
   default CloudFront URL (custom domain deferred). Passkeys enrolled
   against `dXXXX.cloudfront.net` **will not carry over** to a future
   custom domain — the user would re-enroll. `cloudfront.net` is on the
   Public Suffix List, so the RP ID must be the full distribution
   hostname. The reference app sidestepped this with a real domain
   (`admin.360balancedliving.com`).
3. **CloudFormation support for WebAuthn RP config.** Verify whether
   `AWS::Cognito::UserPool` can set the WebAuthn relying-party config
   (RP ID + allowed origins + user-verification) directly. If CFN does
   not expose it, it becomes a one-time `aws cognito-idp
   set-user-pool-mfa-config` / equivalent command **the user runs**
   (per the "user runs builds/deploys" and single-line-command rules).
   Document the exact command in the PR if so.

## Implementation plan

### Infra — `infra/auth/template.yaml`

- `SpaClient.ExplicitAuthFlows`: add `ALLOW_USER_AUTH` (passkey /
  choice-based) and `ALLOW_USER_PASSWORD_AUTH` (password sign-in +
  enrollment bootstrap); keep `ALLOW_REFRESH_TOKEN_AUTH`. Current value
  is `ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH`.
- `SpaClient.RefreshTokenValidity`: `30` → `365`.
- Add `UserPoolTier: ESSENTIALS` to the `UserPool` (pending caveat #1
  verification).
- Add the WebAuthn relying-party config to the `UserPool` (RP ID =
  distribution host, allowed origin = the CloudFront URL), or document
  the CLI step per caveat #3.
- The hosted-UI pieces (`AllowedOAuthFlows: [code]`, `UserPoolDomain`)
  can stay to avoid collateral churn — they simply go unused once the
  frontend stops redirecting. Removing them is optional cleanup.

### Frontend — port `app.js` into React/TS

New modules under `frontend/src/auth/`:

- `cognito.ts` — direct Cognito HTTP API calls (`InitiateAuth`,
  `RespondToAuthChallenge`, `StartWebAuthnRegistration`,
  `CompleteWebAuthnRegistration`, `REFRESH_TOKEN_AUTH`). No SDK; keep the
  bundle small (mirror the reference's `cognito()` helper).
- `tokenStore.ts` — IndexedDB refresh-token persistence + in-memory
  access token (mirror `openDb`/`dbGet`/`dbSet`/`dbClear` + `state`).
- `webauthn.ts` — base64url ↔ ArrayBuffer codecs and the
  encode/decode ceremony glue (`decodeRequestOptions`,
  `decodeCreationOptions`, `encodeAssertion`, `encodeAttestation`).
- `AuthContext.tsx` — replaces `react-oidc-context`'s `AuthProvider`.
  Exposes a hook with a surface compatible with current call sites:
  `{ isLoading, isAuthenticated, accessToken, username, signOut,
  signInWithPassword, signInWithPasskey, enrollPasskey }`.

Call sites to rewire (4 files — confirmed via grep 2026-05-29):

- `main.tsx` — swap `AuthProvider` (react-oidc-context) for the new
  `AuthContext` provider.
- `App.tsx` — read `isLoading` / `isAuthenticated` from the new hook
  (drop `auth.error`/oidc specifics).
- `layout/AppShell.tsx` — read `username` from the new hook; replace
  `cognitoLogout()` with the context's `signOut()`.
- `api/client.ts` — `useApi()` reads `accessToken` from the new hook
  instead of `auth.user?.access_token`.
- `pages/Login.tsx` — rewrite: email + password form + "Sign in with
  passkey" button; after the first successful password sign-in, offer
  passkey enrollment (mirror `offerPasskeyEnrollment`). Also handle the
  `NEW_PASSWORD_REQUIRED` challenge as the reference does.
- `auth/config.ts` — delete or gut (the oidc config + `cognitoLogout`).

Bootstrap (`AuthContext` init): on mount, `trySilentRefresh()` — read
the IndexedDB refresh token, attempt `REFRESH_TOKEN_AUTH`; if it
succeeds, enter the app with zero interaction; otherwise show the login
screen. This is the "bypass sign-in at load" behavior.

Dependencies: remove `react-oidc-context` and `oidc-client-ts` from
`frontend/package.json` once nothing imports them.

### Deploy sequence (user runs — do not run these)

1. `make deploy-auth`
2. one-time `aws cognito-idp …` WebAuthn config command, if caveat #3
   confirms CFN can't set it
3. `make gen-frontend-env` (or equivalent) to refresh client config
4. `make sync-frontend` (build + S3 sync + CloudFront invalidate)

## Definition of done

- Reload with a valid refresh token (within 365 days) lands in the app
  with no interaction.
- A first-time/cleared-state user can sign in with email + password and
  is offered passkey enrollment.
- After enrollment, "Sign in with passkey" performs a one-touch login
  on that device.
- Password fallback still works for recovery.
- `react-oidc-context` / `oidc-client-ts` fully removed; no dead hosted-
  UI redirect code.
- Backend handlers unchanged and still authorize against the same JWT.
- PR notes the Essentials-tier cost and the cloudfront.net RP-ID
  re-enrollment caveat.