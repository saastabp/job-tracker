# Gmail Integration — Google Cloud Project Setup

The Gmail integration (slice 09) requires a one-time manual setup in
Google Cloud Console **per deployment**. This is the only Gmail-side
config that isn't IaC-friendly: AWS::SSM::Parameter cannot create
SecureString parameters, OAuth consent screens are click-through, and
each fork (this repo, the wife's tracker-tickler fork, future forks)
needs its own GCP project.

## Prerequisites

Before running these steps, the AWS side must be in this state:

- `make deploy-api` has run at least once (Step 4 needs `/jobtracker/api/url`,
  which is the API Gateway URL the OAuth callback rides on; that SSM param
  is published by the api stack).
- `make deploy-gmail` has run (this stack creates the KMS key, archive S3
  bucket, and the SSM placeholder params Step 5 will populate).

If you've already run `make deploy-all` at any point, both are satisfied —
no additional deploys are required just to do the GCP setup. The api stack
URL is stable across re-deploys (CloudFormation reuses the API Gateway ID),
so the redirect URI you register here in Step 4 stays valid even if the api
stack is later re-deployed for unrelated reasons.

## 1. Create the GCP project

[Google Cloud Console](https://console.cloud.google.com/) → top-left
project picker → **New Project**.

- Project name: `jobtracker-prod-<your-name>` (e.g.
  `jobtracker-prod-saastabp`)
- Organization: leave blank for a personal project
- Location: leave default

Note the **Project ID** Google generates — it's used in URLs but
nothing else needs to know it programmatically.

## 2. Enable the Gmail API

With the new project selected:

[APIs & Services → Library](https://console.cloud.google.com/apis/library)
→ search **Gmail API** → **Enable**.

This is per-project; nothing else to configure.

## 3. Configure the OAuth consent screen

[APIs & Services → OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)

- **User Type**: *External*. (Internal requires a Google Workspace
  organization.)
- Click **Create**.

On the OAuth consent screen edit page:

- **App name**: `Job Tracker` (or whatever you want users to see on
  the consent screen — for personal use this is just for you).
- **User support email**: your gmail address.
- **App logo**: skip.
- **App domain / Authorized domains**: skip — only required for
  published apps. Testing-mode apps don't need this.
- **Developer contact information**: your gmail address.
- **Save and continue**.

On the **Scopes** page: skip — you'll request scopes dynamically from
the Lambda. Click **Save and continue**.

On the **Test users** page: add your gmail address as a test user.
Without this, the consent screen rejects you with "this app isn't
verified." For a personal-use single-user app, leave the project in
**Testing** mode forever — published mode requires Google verification
(security review, weeks of waiting), which is overkill here.

Click **Save and continue** → **Back to dashboard**.

## 4. Create the OAuth 2.0 Client ID

[APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
→ **Create Credentials** → **OAuth client ID**.

- **Application type**: *Web application*.
- **Name**: `Job Tracker SPA OAuth Client` (or anything — just for your
  reference).
- **Authorized JavaScript origins**: leave blank.
- **Authorized redirect URIs**: add the deployed API's callback URL.

  The API ID is **not** created by this gmail stack — it was created by
  `infra/api/template.yaml` when you ran `make deploy-api`, and the full
  base URL (including the auto-generated API ID) is already published to
  SSM at `/jobtracker/api/url`. To get the exact callback URL to paste,
  run:

  ```
  echo "$(aws ssm get-parameter --name /jobtracker/api/url --query Parameter.Value --output text --region us-west-2)/integrations/gmail/oauth/callback"
  ```

  That prints something like:
  ```
  https://abc123xyz0.execute-api.us-west-2.amazonaws.com/integrations/gmail/oauth/callback
  ```

  Paste that exact value into the GCP redirect URIs list.

  If you've also deployed the dns stack (custom domain), add the custom-
  domain variant alongside it. Get the apex from SSM:

  ```
  echo "$(aws ssm get-parameter --name /jobtracker/dns/app-url --query Parameter.Value --output text --region us-west-2)/integrations/gmail/oauth/callback"
  ```

  Both URLs can coexist in the redirect URIs list — Google accepts
  whichever the OAuth start handler sends at request time.

  For local dev (Phase B testing without a deployed callback), you can
  also add `http://localhost:5173/integrations/gmail/oauth/callback`.

  **Note**: The Phase B OAuth start + callback handlers don't exist yet
  (this slice ships in phases). The redirect URI you register here is
  the URL those handlers *will* be hosted at once Phase B lands.
  Registering it now is fine — Google won't validate that the URL is
  reachable until the OAuth flow actually runs.

- **Create**.

A modal pops up with **Client ID** and **Client secret**. Copy both
NOW — the secret is shown once. (You can also rotate it later from
the Credentials page if needed.)

## 5. Store credentials in SSM as a SecureString

Run from any shell with AWS credentials configured:

```
aws ssm put-parameter --name /jobtracker/gmail/oauth-client --type SecureString --value '{"client_id":"<client_id>","client_secret":"<client_secret>"}' --region us-west-2
```

Replace `<client_id>` and `<client_secret>` with the values from step 4.
The Lambda functions (Phase B+) will read this via `ssm:GetParameter`.

To verify:

```
aws ssm get-parameter --name /jobtracker/gmail/oauth-client --with-decryption --query Parameter.Value --output text --region us-west-2
```

To rotate later (if the client secret leaks or you want to roll keys):
re-run step 4 with the same client (use the rotation feature on the
Credentials page), then re-run the `aws ssm put-parameter` command with
the new secret. The `--overwrite` flag is required to update an
existing parameter:

```
aws ssm put-parameter --name /jobtracker/gmail/oauth-client --type SecureString --value '{"client_id":"<client_id>","client_secret":"<NEW_secret>"}' --region us-west-2 --overwrite
```

## 6. Verify end-to-end (after Phase B ships)

Once the OAuth start + callback handlers are deployed (Phase B), open
the SPA's Settings page, click **Connect Gmail**, run through the
consent screen, and watch the network tab. A successful round-trip
populates a row in `gmail_credentials` with the encrypted refresh
token.

If the consent screen rejects you with "this app isn't verified," go
back to step 3 and double-check your gmail address is in the test
users list.

If the callback fails with `redirect_uri_mismatch`, the URL in step 4
doesn't match what the OAuth start handler is sending — check both
the redirect URIs list in GCP and the actual URL the api stack is
hosting.

## Tear-down

If you want to disconnect entirely (e.g. before deleting the gmail
stack):

1. Delete the SSM parameter:
   ```
   aws ssm delete-parameter --name /jobtracker/gmail/oauth-client --region us-west-2
   ```
2. (Optional) Delete the OAuth client from the GCP Credentials page.
3. (Optional) Delete the GCP project entirely from the project picker.
4. `make delete-gmail` removes the AWS-side resources (the KMS key
   and archive bucket are `DeletionPolicy: Retain` and require manual
   cleanup if you want them gone — they hold encrypted secrets and
   archived recruiter mail respectively).