# SSM Parameter Naming Convention

All cross-stack values flow through SSM Parameter Store under `/jobtracker/<concern>/<resource>`. This avoids the CloudFormation Export/Import lock problem — an exporter can't be replaced while an importer references its export, which would defeat the "tearable-down independently" goal of the stack split.

## Parameters by stack

### `network` (producer: `infra/network/template.yaml`)
- `/jobtracker/network/vpc-id`
- `/jobtracker/network/private-subnet-ids` (StringList)
- `/jobtracker/network/public-subnet-ids` (StringList)
- `/jobtracker/network/lambda-sg-id`
- `/jobtracker/network/rds-sg-id`

### `data` (producer: `infra/data/template.yaml`)
- `/jobtracker/data/db-endpoint`
- `/jobtracker/data/db-port`
- `/jobtracker/data/db-name`
- `/jobtracker/data/db-resource-id` — used in `rds-db:connect` IAM resource ARN
- `/jobtracker/data/db-master-secret-arn` — RDS-managed Secrets Manager secret
- `/jobtracker/data/resume-bucket-name`
- `/jobtracker/data/resume-bucket-arn`
- `/jobtracker/data/inbound-email-bucket-name`
- `/jobtracker/data/inbound-email-bucket-arn`

### `auth` (producer: `infra/auth/template.yaml`)
- `/jobtracker/auth/user-pool-id`
- `/jobtracker/auth/user-pool-arn`
- `/jobtracker/auth/user-pool-provider-url` — JWT issuer URL for API Gateway authorizer
- `/jobtracker/auth/spa-client-id`
- `/jobtracker/auth/cognito-domain` — Hosted UI hostname

### `api` (producer: `infra/api/template.yaml`)
- `/jobtracker/api/url`
- `/jobtracker/api/post-confirmation-function-arn` — read by `deploy-auth` to wire the Cognito post-confirmation Lambda

### `frontend` (producer: `infra/frontend/template.yaml`)
- `/jobtracker/frontend/cloudfront-domain`
- `/jobtracker/frontend/cloudfront-url`
- `/jobtracker/frontend/spa-bucket-name`
- `/jobtracker/frontend/distribution-id`

### `ai` (producer: `infra/ai/template.yaml`)
- `/jobtracker/ai/url` — base URL of the AI HTTP API; consumed by the SPA via `VITE_AI_API_URL`

### `scheduler` (producer: `infra/scheduler/template.yaml`)
- `/jobtracker/scheduler/group-name` — EventBridge Scheduler group for follow-up reminders
- `/jobtracker/scheduler/notify-arn` — ARN of the notify Lambda the scheduler invokes
- `/jobtracker/scheduler/exec-role-arn` — role EventBridge Scheduler assumes to invoke the notify Lambda

The api stack reads all three of these at deploy-time to wire IAM for `scheduler:CreateSchedule`. Empty `group-name` (when the scheduler stack isn't deployed) flips a `HasScheduler` condition that omits the IAM bindings entirely — submissions then no-op the schedule write, reminders simply don't fire.

### `dns` (producer: `infra/dns/template.yaml`, but written by `deploy-dns` Makefile target)
- `/jobtracker/dns/cert-arn` — ACM certificate ARN (us-east-1)
- `/jobtracker/dns/app-url` — `https://<custom-domain>` for the SPA

These live in **us-west-2 SSM** even though the dns stack runs in us-east-1. The Makefile reads the dns stack's outputs after `sam deploy` and writes them here so the rest of the recipes (which all read SSM in us-west-2) don't need cross-region awareness. Empty when the dns stack isn't deployed; `deploy-frontend` skips the alias attach in that case.

## How consumer stacks read params

Each consumer template declares the dependency as an `AWS::SSM::Parameter::Value<...>` template parameter, with the SSM path as the `Default`. CloudFormation resolves the value at deploy time and substitutes it.

```yaml
Parameters:
  PrivateSubnetIds:
    Type: AWS::SSM::Parameter::Value<List<AWS::EC2::Subnet::Id>>
    Default: /jobtracker/network/private-subnet-ids
```

The `<List<AWS::EC2::Subnet::Id>>` form gives CloudFormation type-checking — it validates the resolved value is actually a comma-separated list of valid subnet IDs in the deploy account.

For string values where no AWS-resource type fits:

```yaml
DbEndpoint:
  Type: AWS::SSM::Parameter::Value<String>
  Default: /jobtracker/data/db-endpoint
```

## Adding a new parameter

1. In the producer template, add an `AWS::SSM::Parameter` resource with `Name: /jobtracker/<concern>/<resource>` and `Type: String` (or `StringList`).
2. Add it to the table above.
3. Consumers reference it via `AWS::SSM::Parameter::Value<...>` parameter declaration.

## List all parameters

```
make list-ssm
```

Or directly:

```
aws ssm get-parameters-by-path --path /jobtracker --recursive \
  --query 'Parameters[].[Name,Value]' --output table --region us-west-2
```