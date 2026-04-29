.PHONY: help check-prereqs fetch-rds-ca \
        deploy-network deploy-data deploy-auth deploy-api deploy-frontend deploy-all \
        wire-frontend migrate \
        deploy-bastion destroy-bastion tunnel db-creds \
        gen-frontend-env dev-frontend sync-frontend \
        list-ssm clean

REGION := us-west-2
ROOT := $(shell git rev-parse --show-toplevel)

# Bastion overrides (override on the command line if t4g.nano in subnet 0 is out of capacity):
#   BASTION_SUBNET=1 make deploy-bastion
#   BASTION_INSTANCE_TYPE=t4g.micro make deploy-bastion
BASTION_SUBNET ?= 0
BASTION_INSTANCE_TYPE ?= t4g.nano

help:
	@echo "Targets:"
	@echo "  check-prereqs     Verify aws CLI, SAM, session-manager-plugin, node, python, etc."
	@echo "  fetch-rds-ca      Download the RDS CA bundle (run once before deploy-api)"
	@echo "  deploy-network    Deploy VPC + subnets + S3 gateway endpoint"
	@echo "  deploy-data       Deploy RDS MySQL + S3 buckets"
	@echo "  deploy-auth       Deploy Cognito user pool + Hosted UI domain"
	@echo "  deploy-api        Build + deploy API Gateway + Health Lambda"
	@echo "  deploy-frontend   Deploy S3 SPA bucket + CloudFront"
	@echo "  deploy-all        Deploy all stacks in dependency order, then wire-frontend"
	@echo "  wire-frontend     Re-deploy auth + api with the CloudFront URL added to callbacks/CORS"
	@echo "  migrate           Invoke the MigrationRunner Lambda (applies pending backend/migrations/*.sql)"
	@echo "  deploy-bastion    Deploy transient bastion EC2 (for DB admin tunnel)"
	@echo "  destroy-bastion   Tear down bastion EC2 when admin work is done"
	@echo "  tunnel            Open SSM port-forward to RDS (requires deploy-bastion)"
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
	PCF_ARN=$$(aws ssm get-parameter --name /jobtracker/api/post-confirmation-function-arn --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	ARGS=""; \
	if [ -n "$$CF_URL" ]; then ARGS="$$ARGS CallbackUrls=http://localhost:5173/,$$CF_URL/ LogoutUrls=http://localhost:5173/,$$CF_URL/"; fi; \
	if [ -n "$$PCF_ARN" ]; then ARGS="$$ARGS PostConfirmationFunctionArn=$$PCF_ARN"; fi; \
	if [ -n "$$ARGS" ]; then \
		cd $(ROOT)/infra/auth && sam deploy --parameter-overrides $$ARGS; \
	else \
		cd $(ROOT)/infra/auth && sam deploy; \
	fi

deploy-api: deploy-network deploy-data deploy-auth fetch-rds-ca
	@CF_URL=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-url --query Parameter.Value --output text --region $(REGION) 2>/dev/null); \
	if [ -n "$$CF_URL" ]; then \
		cd $(ROOT)/infra/api && sam build && sam deploy --parameter-overrides "CorsOrigins=http://localhost:5173,$$CF_URL"; \
	else \
		cd $(ROOT)/infra/api && sam build && sam deploy; \
	fi

deploy-frontend:
	cd $(ROOT)/infra/frontend && sam deploy

deploy-all: deploy-network deploy-data deploy-auth deploy-api deploy-frontend wire-frontend
	@echo
	@echo "All stacks deployed and CloudFront URL wired into auth + api."
	@echo "Next: one-time bootstrap of the MySQL 'app' user (see README)."

wire-frontend:
	@CF_URL=$$(aws ssm get-parameter --name /jobtracker/frontend/cloudfront-url \
		--query Parameter.Value --output text --region $(REGION) 2>/dev/null) && \
	[ -n "$$CF_URL" ] || { echo "error: /jobtracker/frontend/cloudfront-url not in SSM (deploy-frontend first)" >&2; exit 1; } && \
	PCF_ARN=$$(aws ssm get-parameter --name /jobtracker/api/post-confirmation-function-arn \
		--query Parameter.Value --output text --region $(REGION) 2>/dev/null) && \
	echo "Wiring CloudFront URL into auth + api: $$CF_URL" && \
	AUTH_ARGS="CallbackUrls=http://localhost:5173/,$$CF_URL/ LogoutUrls=http://localhost:5173/,$$CF_URL/"; \
	if [ -n "$$PCF_ARN" ]; then AUTH_ARGS="$$AUTH_ARGS PostConfirmationFunctionArn=$$PCF_ARN"; fi; \
	cd $(ROOT)/infra/auth && sam deploy --parameter-overrides $$AUTH_ARGS && \
	cd $(ROOT)/infra/api && sam build && sam deploy --parameter-overrides \
		"CorsOrigins=http://localhost:5173,$$CF_URL"

migrate:
	aws lambda invoke --function-name jobtracker-migrate --region $(REGION) --no-cli-pager /tmp/jobtracker-migrate.json
	@cat /tmp/jobtracker-migrate.json && echo

deploy-bastion: deploy-network
	cd $(ROOT)/infra/bastion && sam deploy \
		--parameter-overrides PublicSubnetIndex=$(BASTION_SUBNET) InstanceType=$(BASTION_INSTANCE_TYPE)

destroy-bastion:
	cd $(ROOT)/infra/bastion && sam delete --no-prompts

tunnel:
	@$(ROOT)/infra/scripts/tunnel.sh

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
	printf 'VITE_API_URL=%s\nVITE_COGNITO_AUTHORITY=%s\nVITE_COGNITO_CLIENT_ID=%s\nVITE_COGNITO_DOMAIN=%s\n' \
		"$$API_URL" "$$AUTHORITY" "$$CLIENT_ID" "$$COGNITO_DOMAIN" \
		> $(ROOT)/frontend/.env.local && \
	echo "Wrote $(ROOT)/frontend/.env.local"

dev-frontend: gen-frontend-env
	cd $(ROOT)/frontend && npm install && npm run dev

sync-frontend: gen-frontend-env
	cd $(ROOT)/frontend && npm install && npm run build
	@BUCKET=$$(aws ssm get-parameter --name /jobtracker/frontend/spa-bucket-name \
		--query 'Parameter.Value' --output text --region $(REGION)) && \
	DIST=$$(aws ssm get-parameter --name /jobtracker/frontend/distribution-id \
		--query 'Parameter.Value' --output text --region $(REGION)) && \
	aws s3 sync $(ROOT)/frontend/dist/ s3://$$BUCKET/ --delete --region $(REGION) && \
	aws cloudfront create-invalidation --distribution-id $$DIST --paths '/*'

list-ssm:
	aws ssm get-parameters-by-path --path /jobtracker --recursive \
		--query 'Parameters[].[Name,Value]' --output table --region $(REGION)

clean:
	rm -rf $(ROOT)/infra/api/.aws-sam $(ROOT)/frontend/dist
