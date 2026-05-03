.PHONY: help check-prereqs fetch-rds-ca \
        deploy-network deploy-data deploy-auth deploy-api deploy-frontend deploy-ai deploy-scheduler deploy-dns deploy-all \
        wire-frontend migrate delete-ai delete-scheduler delete-dns db-creds \
        gen-frontend-env dev-frontend sync-frontend \
        list-ssm clean

REGION := us-west-2
ROOT := $(shell git rev-parse --show-toplevel)

# AI features in the SPA require Bedrock service quotas in this region.
# Default 0 while the AWS support case is open. When quotas land, change
# this to 1 (or pass `AI_ENABLED=1` on the command line) and re-run
# `make sync-frontend` to publish a build that wires VITE_AI_API_URL.
AI_ENABLED ?= 1

help:
	@echo "Targets:"
	@echo "  check-prereqs     Verify aws CLI, SAM, node, python, etc."
	@echo "  fetch-rds-ca      Download the RDS CA bundle (run once before deploy-api)"
	@echo "  deploy-network    Deploy VPC + subnets + S3 gateway endpoint"
	@echo "  deploy-data       Deploy RDS MySQL + S3 buckets"
	@echo "  deploy-auth       Deploy Cognito user pool + Hosted UI domain"
	@echo "  deploy-api        Build + deploy API Gateway + Health Lambda"
	@echo "  deploy-frontend   Deploy S3 SPA bucket + CloudFront"
	@echo "  deploy-ai         Deploy AI stack (Bedrock-backed Lambdas, no VPC)"
	@echo "  delete-ai         Tear down the AI stack"
	@echo "  deploy-scheduler  Deploy scheduler stack (EventBridge Scheduler + follow-up notify Lambda + SES verified identity)"
	@echo "  delete-scheduler  Tear down the scheduler stack (follow_ups rows persist; reminders stop firing)"
	@echo "  deploy-dns        Deploy dns stack in us-east-1 (ACM cert + Route 53 alias) and re-deploy frontend with the alias attached"
	@echo "  delete-dns        Tear down the dns stack (cert + alias records); SPA falls back to its cloudfront.net domain after deploy-frontend"
	@echo "  deploy-all        Deploy all stacks in dependency order, then wire-frontend"
	@echo "  wire-frontend     Re-deploy auth + api + ai with the CloudFront URL added to callbacks/CORS"
	@echo "  migrate           Invoke the MigrationRunner Lambda (applies pending backend/migrations/*.sql)"
	@echo "  db-creds          Print RDS master credentials JSON from Secrets Manager"
	@echo "  gen-frontend-env  Write frontend/.env.local from current SSM values"
	@echo "  dev-frontend      Generate env, npm install, run Vite dev server (localhost:5173)"
	@echo "  sync-frontend     Generate env, build SPA, sync to S3, invalidate CloudFront"
	@echo "  list-ssm          List all /jobtracker SSM parameters"
	@echo "  clean             Remove sam build artifacts and frontend dist"

check-prereqs:
	@$(ROOT)/infra/scripts/check-prereqs.sh

fetch-rds-ca:
	@mkdir -p $(ROOT)/backend/src
	curl -fsSL https://truststore.pki.rds.amazonaws.com/$(REGION)/$(REGION)-bundle.pem \
		-o $(ROOT)/backend/src/rds-ca-bundle.pem
	@echo "RDS CA bundle saved to backend/src/rds-ca-bundle.pem"

deploy-network:
	cd $(ROOT)/infra/network && sam deploy

deploy-data: deploy-network
	cd $(ROOT)/infra/data && sam deploy

# deploy-auth and deploy-api auto-detect the CloudFront URL from SSM and pass it as
# a parameter override. This keeps the auth callback list and API CORS allow-list
# wired correctly even when run standalone (without going through deploy-all).
# On first-ever deploy, the CloudFront URL doesn't exist in SSM yet, so the
# template defaults (localhost-only) apply; deploy-all's final wire-frontend step
# fixes this once the frontend stack creates the URL.
deploy-auth:
	@CF_URL=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	APP_URL=$$(aws ssm get-parameter --name /jobtracker/dns/app-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	PCF_ARN=$$(aws ssm get-parameter --name /jobtracker/api/post-confirmation-function-arn --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	ARGS=""; \
	if [ -n "$$CF_URL" ]; then \
		URLS="http://localhost:5173/,$$CF_URL/"; \
		if [ -n "$$APP_URL" ]; then URLS="$$URLS,$$APP_URL/"; fi; \
		ARGS="$$ARGS CallbackUrls=$$URLS LogoutUrls=$$URLS"; \
	fi; \
	if [ -n "$$PCF_ARN" ]; then ARGS="$$ARGS PostConfirmationFunctionArn=$$PCF_ARN"; fi; \
	if [ -n "$$ARGS" ]; then \
		cd $(ROOT)/infra/auth && sam deploy --parameter-overrides $$ARGS; \
	else \
		cd $(ROOT)/infra/auth && sam deploy; \
	fi

deploy-api: deploy-network deploy-data deploy-auth fetch-rds-ca
	@CF_URL=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	APP_URL=$$(aws ssm get-parameter --name /jobtracker/dns/app-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	SCHED_GROUP=$$(aws ssm get-parameter --name /jobtracker/scheduler/group-name --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	SCHED_TARGET=$$(aws ssm get-parameter --name /jobtracker/scheduler/notify-arn --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	SCHED_ROLE=$$(aws ssm get-parameter --name /jobtracker/scheduler/exec-role-arn --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	ARGS=""; \
	if [ -n "$$CF_URL" ]; then \
		ORIGINS="http://localhost:5173,$$CF_URL"; \
		if [ -n "$$APP_URL" ]; then ORIGINS="$$ORIGINS,$$APP_URL"; fi; \
		ARGS="$$ARGS CorsOrigins=$$ORIGINS"; \
	fi; \
	if [ -n "$$SCHED_GROUP" ]; then ARGS="$$ARGS SchedulerGroupName=$$SCHED_GROUP SchedulerNotifyArn=$$SCHED_TARGET SchedulerRoleArn=$$SCHED_ROLE"; fi; \
	if [ -n "$$ARGS" ]; then \
		cd $(ROOT)/infra/api && sam build && sam deploy --parameter-overrides $$ARGS; \
	else \
		cd $(ROOT)/infra/api && sam build && sam deploy; \
	fi

deploy-frontend:
	@CERT_ARN=$$(aws ssm get-parameter --name /jobtracker/dns/cert-arn --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	APP_URL=$$(aws ssm get-parameter --name /jobtracker/dns/app-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	APP_DOMAIN=$$(echo "$$APP_URL" | sed 's|^https://||'); \
	if [ -n "$$CERT_ARN" ] && [ -n "$$APP_DOMAIN" ]; then \
		cd $(ROOT)/infra/frontend && sam deploy --parameter-overrides "AppDomain=$$APP_DOMAIN CertificateArn=$$CERT_ARN"; \
	else \
		cd $(ROOT)/infra/frontend && sam deploy; \
	fi

# DNS stack lives in us-east-1 because CloudFront requires its viewer
# certificate to be issued there. The hosted zone (Route 53) is global, so
# the alias records are also created from this stack. Per stack-segregation
# rule, this stack owns ONLY the cert + alias records — nothing else
# depends on it for data. Tearing it down drops the SPA back to its
# cloudfront.net domain after a follow-up deploy-frontend.
#
# Standalone idempotency: deploy-dns re-runs deploy-frontend at the end so
# the alias is actually attached to CloudFront in one shot. Without that,
# the new A/AAAA records would resolve to a CloudFront distribution that
# rejects requests for the custom hostname (TLS handshake works against
# the default cert, but CloudFront returns 403/SSL_ERROR_HANDSHAKE_FAILURE
# for the alias until ViewerCertificate is updated).
deploy-dns: deploy-frontend
	@CF_DOMAIN=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-domain --query Parameter.Value --output text --region $(REGION)) && \
	[ -n "$$CF_DOMAIN" ] || { echo "error: /jobtracker/frontend/cloudfront-domain not in SSM (deploy-frontend first)" >&2; exit 1; } && \
	echo "Deploying dns stack in us-east-1 (cert validation typically takes 5-30 min)..." && \
	cd $(ROOT)/infra/dns && sam deploy --parameter-overrides "CloudFrontDomainName=$$CF_DOMAIN" && \
	CERT_ARN=$$(aws cloudformation describe-stacks --stack-name jobtracker-dns --region us-east-1 \
		--query 'Stacks[0].Outputs[?OutputKey==`CertificateArn`].OutputValue' --output text) && \
	APP_URL=$$(aws cloudformation describe-stacks --stack-name jobtracker-dns --region us-east-1 \
		--query 'Stacks[0].Outputs[?OutputKey==`AppUrl`].OutputValue' --output text) && \
	aws ssm put-parameter --name /jobtracker/dns/cert-arn --type String --value "$$CERT_ARN" --overwrite --region $(REGION) >/dev/null && \
	aws ssm put-parameter --name /jobtracker/dns/app-url --type String --value "$$APP_URL" --overwrite --region $(REGION) >/dev/null && \
	echo "DNS stack deployed; re-running deploy-frontend to attach the alias..." && \
	$(MAKE) deploy-frontend

delete-dns:
	cd $(ROOT)/infra/dns && sam delete --no-prompts --region us-east-1
	@aws ssm delete-parameter --name /jobtracker/dns/cert-arn --region $(REGION) 2>/dev/null || true
	@aws ssm delete-parameter --name /jobtracker/dns/app-url --region $(REGION) 2>/dev/null || true

# AI stack runs OUTSIDE the VPC and only depends on auth (for the JWT
# authorizer's user-pool params via SSM). Deployed independently of api/data
# so it can be torn down without touching either.
deploy-ai: deploy-auth
	@CF_URL=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	APP_URL=$$(aws ssm get-parameter --name /jobtracker/dns/app-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	if [ -n "$$CF_URL" ]; then \
		ORIGINS="http://localhost:5173,$$CF_URL"; \
		if [ -n "$$APP_URL" ]; then ORIGINS="$$ORIGINS,$$APP_URL"; fi; \
		cd $(ROOT)/infra/ai && sam build && sam deploy --parameter-overrides "CorsOrigins=$$ORIGINS"; \
	else \
		cd $(ROOT)/infra/ai && sam build && sam deploy; \
	fi

delete-ai:
	cd $(ROOT)/infra/ai && sam delete --no-prompts
	@aws ssm delete-parameter --name /jobtracker/ai/url --region $(REGION) 2>/dev/null || true

# Scheduler stack runs OUTSIDE the VPC and only depends on auth (the notify
# Lambda receives all reminder fields in the EventBridge Input payload, so
# it never touches the DB). The api stack reads its /jobtracker/scheduler/*
# SSM params during deploy-api (graceful no-op if this stack isn't
# deployed). Per stack-segregation rule.
deploy-scheduler:
	cd $(ROOT)/infra/scheduler && sam build && sam deploy

delete-scheduler:
	cd $(ROOT)/infra/scheduler && sam delete --no-prompts
	@aws ssm delete-parameter --name /jobtracker/scheduler/group-name --region $(REGION) 2>/dev/null || true
	@aws ssm delete-parameter --name /jobtracker/scheduler/notify-arn --region $(REGION) 2>/dev/null || true
	@aws ssm delete-parameter --name /jobtracker/scheduler/exec-role-arn --region $(REGION) 2>/dev/null || true

# deploy-dns is intentionally not in deploy-all's dependency chain — it's a
# manual upgrade step (custom domain is opt-in, ACM validation is slow).
# Run `make deploy-dns` separately after deploy-all if you want the alias.
deploy-all: deploy-network deploy-data deploy-auth deploy-scheduler deploy-api deploy-frontend deploy-ai wire-frontend
	@echo
	@echo "All stacks deployed and CloudFront URL wired into auth + api + ai."
	@echo "Next: one-time bootstrap of the MySQL 'app' user (see README), and"
	@echo "  click the SES verification email sent to the SenderEmail address."
	@echo "  For a custom domain (e.g. jobs.crazylittledog.link), run 'make deploy-dns'."

wire-frontend:
	@CF_URL=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-url \
		--query Parameter.Value --output text --region $(REGION) 2>/dev/null) && \
	[ -n "$$CF_URL" ] || { echo "error: /jobtracker/frontend/cloudfront-url not in SSM (deploy-frontend first)" >&2; exit 1; } && \
	APP_URL=$$(aws ssm get-parameter --name /jobtracker/dns/app-url \
		--query Parameter.Value --output text --region $(REGION) 2>/dev/null) && \
	PCF_ARN=$$(aws ssm get-parameter --name /jobtracker/api/post-confirmation-function-arn \
		--query Parameter.Value --output text --region $(REGION) 2>/dev/null) && \
	if [ -n "$$APP_URL" ]; then \
		echo "Wiring CloudFront + custom domain into auth + api + ai: $$CF_URL, $$APP_URL"; \
		CB="http://localhost:5173/,$$CF_URL/,$$APP_URL/"; \
		ORIGINS="http://localhost:5173,$$CF_URL,$$APP_URL"; \
	else \
		echo "Wiring CloudFront URL into auth + api + ai: $$CF_URL"; \
		CB="http://localhost:5173/,$$CF_URL/"; \
		ORIGINS="http://localhost:5173,$$CF_URL"; \
	fi; \
	AUTH_ARGS="CallbackUrls=$$CB LogoutUrls=$$CB"; \
	if [ -n "$$PCF_ARN" ]; then AUTH_ARGS="$$AUTH_ARGS PostConfirmationFunctionArn=$$PCF_ARN"; fi; \
	cd $(ROOT)/infra/auth && sam deploy --parameter-overrides $$AUTH_ARGS && \
	cd $(ROOT)/infra/api && sam build && sam deploy --parameter-overrides \
		"CorsOrigins=$$ORIGINS" && \
	if aws cloudformation describe-stacks --stack-name jobtracker-ai --region $(REGION) >/dev/null 2>&1; then \
		cd $(ROOT)/infra/ai && sam build && sam deploy --parameter-overrides \
			"CorsOrigins=$$ORIGINS"; \
	fi

migrate:
	aws lambda invoke --function-name jobtracker-migrate --region $(REGION) --no-cli-pager /tmp/jobtracker-migrate.json
	@cat /tmp/jobtracker-migrate.json && echo

db-creds:
	@SECRET_ARN=$$(aws ssm get-parameter --name /jobtracker/data/db-master-secret-arn \
		--query 'Parameter.Value' --output text --region $(REGION)) && \
	aws secretsmanager get-secret-value --secret-id $$SECRET_ARN \
		--query SecretString --output text --region $(REGION)

gen-frontend-env:
	@API_URL=$$(aws ssm get-parameter --name /jobtracker/api/url \
		--query Parameter.Value --output text --region $(REGION)) && \
	AUTHORITY=$$(aws ssm get-parameter --name /jobtracker/auth/user-pool-provider-url \
		--query Parameter.Value --output text --region $(REGION)) && \
	CLIENT_ID=$$(aws ssm get-parameter --name /jobtracker/auth/spa-client-id \
		--query Parameter.Value --output text --region $(REGION)) && \
	COGNITO_DOMAIN=$$(aws ssm get-parameter --name /jobtracker/auth/cognito-domain \
		--query Parameter.Value --output text --region $(REGION)) && \
	{ \
		printf 'VITE_API_URL=%s\nVITE_COGNITO_AUTHORITY=%s\nVITE_COGNITO_CLIENT_ID=%s\nVITE_COGNITO_DOMAIN=%s\n' \
			"$$API_URL" "$$AUTHORITY" "$$CLIENT_ID" "$$COGNITO_DOMAIN"; \
		if [ "$(AI_ENABLED)" = "1" ]; then \
			AI_URL=$$(aws ssm get-parameter --name /jobtracker/ai/url \
				--query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
			printf 'VITE_AI_API_URL=%s\n' "$$AI_URL"; \
		fi; \
	} > $(ROOT)/frontend/.env.local && \
	echo "Wrote $(ROOT)/frontend/.env.local (AI_ENABLED=$(AI_ENABLED))"

dev-frontend: gen-frontend-env
	cd $(ROOT)/frontend && npm install && npm run dev

sync-frontend: gen-frontend-env
	cd $(ROOT)/frontend && npm install && npm run build
	@BUCKET=$$(aws ssm get-parameter --name /jobtracker/frontend/spa-bucket-name \
		--query 'Parameter.Value' --output text --region $(REGION)) && \
	DIST=$$(aws ssm get-parameter --name /jobtracker/frontend/distribution-id \
		--query 'Parameter.Value' --output text --region $(REGION)) && \
	aws s3 sync $(ROOT)/frontend/dist/ s3://$$BUCKET/ --delete --region $(REGION) && \
	INV_ID=$$(aws cloudfront create-invalidation --distribution-id $$DIST --paths '/*' \
		--query 'Invalidation.Id' --output text) && \
	echo "Invalidation $$INV_ID created; waiting for completion (CloudFront typically takes 1-5 min)..." && \
	aws cloudfront wait invalidation-completed --distribution-id $$DIST --id $$INV_ID && \
	echo "Invalidation $$INV_ID completed — fresh SPA is live."

list-ssm:
	aws ssm get-parameters-by-path --path /jobtracker --recursive \
		--query 'Parameters[].[Name,Value]' --output table --region $(REGION)

clean:
	rm -rf $(ROOT)/infra/api/.aws-sam $(ROOT)/infra/ai/.aws-sam $(ROOT)/frontend/dist
