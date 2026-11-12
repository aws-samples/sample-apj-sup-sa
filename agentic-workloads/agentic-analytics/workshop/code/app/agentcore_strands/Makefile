# =============================================================================
#  Workshop Makefile — thin wrapper around the AgentCore top-up stack
# =============================================================================
#  You EDIT agentcore-topup-stack.yaml (uncomment one fenced section per step),
#  then run `make deploy`. This Makefile just runs the long `aws cloudformation
#  deploy` command for you and reads the base coordinates from config.env.
#
#  Targets:
#    make deploy   - deploy / update the AgentCore top-up CloudFormation stack
#    make build    - rebuild the agent container image + roll the runtime
#                    (use after editing unicorn_rental_agent.py — code changes
#                     don't redeploy via `make deploy`)
#    make outputs  - print the stack outputs (Gateway URL, Runtime ARN, ...)
#    make status   - show the stack's current status
#    make users    - list the demo users with their role and password
#    make help     - this message
#
#  Everything reads config.env (seeded for you on the Code Editor). Override on
#  the command line, e.g.  make deploy POLICY_MODE=ENFORCE
# =============================================================================

SHELL := /bin/bash

# ---- config.env -------------------------------------------------------------
# Read individual keys from config.env without `include` (ARN values contain ':'
# and would confuse make). Each ?= lets you override on the command line.
CONFIG_ENV   := config.env
cfg = $(shell grep -E '^$(1)=' $(CONFIG_ENV) 2>/dev/null | head -1 | cut -d= -f2-)

REGION           ?= $(call cfg,AWS_REGION)
ARTIFACTS_BUCKET ?= $(call cfg,ARTIFACTS_BUCKET)
ARTIFACTS_PREFIX ?= $(call cfg,ARTIFACTS_PREFIX)

# Stack + parameters
ENV_NAME    ?= agentic-analytics
STACK       ?= agentic-analytics-agentcore
TEMPLATE    ?= agentcore-topup-stack.yaml
# Left EMPTY like FRONT_DOOR: `make deploy POLICY_MODE=ENFORCE` sets it once and a later bare
# `make deploy` keeps it. (A fixed default here used to flip the gateway back to LOG_ONLY.)
POLICY_MODE ?=
# Step 7c: `make deploy FRONT_DOOR=true` puts the front-door gateway + WAF in front of
# the runtime. Left EMPTY so a later bare `make deploy` keeps whatever the stack has.
FRONT_DOOR  ?=
# Container image tag the runtime points at. Left EMPTY on purpose: a bare
# `make deploy` then omits the AgentImageTag override, so CloudFormation keeps
# the stack's existing tag (and uses the template default 'latest' on first
# deploy). `make build` sets this to a fresh build-<timestamp> to force a rebuild.
AGENT_IMAGE_TAG ?=

# CFN needs templates >51,200 bytes staged in S3. The workshop CodeEditor role has
# s3:* on bedrock-agentcore-* buckets, so we stage there (created on first deploy).
ACCOUNT_ID  ?= $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
CFN_BUCKET  ?= bedrock-agentcore-cfn-$(ACCOUNT_ID)-$(REGION)

# Agent code zip (CodeBuild source). Must match AgentCodeS3Key in the template.
AGENT_CODE_KEY ?= agent/agent_code.zip

# Optional Voice step (after Cube). The voice runtime is its own stack/template,
# shipped next to this Makefile. VPC/subnet/Cognito values come from config.env.
VOICE_STACK     ?= agentic-analytics-voice
VOICE_TEMPLATE  ?= voice-agentcore-stack.yaml
VOICE_CODE_KEY  ?= voice/voice_agent_code.zip
VOICE_DIR       ?= ../voice
DEEPGRAM_API_KEY ?=
DEEPGRAM_VOICE_ID ?= aura-2-apollo-en
VPC_ID           ?= $(call cfg,VPC_ID)
SUBNET_IDS       ?= $(call cfg,SUBNET_IDS)
SUBNET1          ?= $(firstword $(subst $(comma), ,$(SUBNET_IDS)))
SUBNET2          ?= $(lastword $(subst $(comma), ,$(SUBNET_IDS)))
COGNITO_POOL     ?= $(call cfg,COGNITO_USER_POOL_ID)
COGNITO_CLIENT   ?= $(call cfg,COGNITO_USER_LOGIN_CLIENT_ID)
comma := ,
# The voice Runtime builds its own AgentCore-supported-AZ subnets (the base private
# subnets can land in an unsupported AZ like use1-az6). They attach to the base VPC's
# NAT-routed private route table — discovered here from the VPC so no config.env change.
PRIVATE_RT       ?= $(shell aws ec2 describe-route-tables --region $(REGION) \
  --filters "Name=vpc-id,Values=$(VPC_ID)" "Name=tag:Name,Values=*private*" \
  --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null)

# ---- guards -----------------------------------------------------------------
ifeq ($(strip $(REGION)),)
  $(error AWS_REGION not found in config.env — run from app/agentcore_strands/ where config.env lives)
endif

.PHONY: help deploy build outputs status users voice-deploy schema-sync approve reject

help:
	@sed -n '2,18p' $(firstword $(MAKEFILE_LIST)) | sed 's/^# \{0,1\}//'

# ---- deploy: structural CFN changes (uncomment a fence, flip a value) --------
# The event's assets bucket ($ARTIFACTS_BUCKET) is READ-ONLY to participants, but
# the agent must WRITE chart PNGs and `make build` must re-upload the agent zip.
# So we use ONE writable bucket ($CFN_BUCKET, which the CodeEditor role owns via
# its s3:* on bedrock-agentcore-*): copy the pre-baked Lambda/agent zips into it
# once, then point the stack's ArtifactsBucket at it (prefix empty). Charts, code
# reads, and rebuilds all use this single writable bucket.
deploy: stage-assets
	@echo "==> Deploying $(STACK) (PolicyMode=$(or $(POLICY_MODE),unchanged), region=$(REGION))"
	aws cloudformation deploy \
	  --template-file $(TEMPLATE) \
	  --stack-name $(STACK) \
	  --s3-bucket $(CFN_BUCKET) \
	  --s3-prefix cfn-staging \
	  --parameter-overrides \
	      EnvironmentName=$(ENV_NAME) \
	      ArtifactsBucket=$(CFN_BUCKET) \
	      ArtifactsPrefix= \
	      $(if $(strip $(POLICY_MODE)),PolicyMode=$(POLICY_MODE),) \
	      $(if $(strip $(AGENT_IMAGE_TAG)),AgentImageTag=$(AGENT_IMAGE_TAG),) \
	      $(if $(strip $(FRONT_DOOR)),EnableFrontDoor=$(FRONT_DOOR),) \
	  --capabilities CAPABILITY_NAMED_IAM \
	  --region $(REGION)

# Ensure the writable bucket exists and holds the pre-baked zips the stack reads.
stage-assets:
	@aws s3api head-bucket --bucket $(CFN_BUCKET) 2>/dev/null || \
	  { echo "==> creating deploy bucket $(CFN_BUCKET)"; aws s3 mb s3://$(CFN_BUCKET) --region $(REGION); }
	@echo "==> staging pre-baked assets into s3://$(CFN_BUCKET)/ (from the event assets bucket)"
	@for k in lambda/datafoundation_lambda.zip lambdas/gateway_interceptor.zip \
	          lambdas/api_integration_toolset.zip lambdas/custom_sql_toolset.zip lambdas/schema_sync.zip \
	          layers/psycopg2-py312.zip layers/pyjwt-py312.zip agent/agent_code.zip; do \
	  aws s3 cp "s3://$(ARTIFACTS_BUCKET)/$(ARTIFACTS_PREFIX)$$k" "s3://$(CFN_BUCKET)/$$k" --region $(REGION) >/dev/null 2>&1 \
	    && echo "    staged $$k" || echo "    [skip] $$k not in assets (ok if unused)"; \
	done
	@echo "==> Done. Run 'make outputs' to see the Gateway URL and Runtime ARN."

# ---- outputs / status -------------------------------------------------------
outputs:
	@aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query 'Stacks[0].Outputs' --output table

status:
	@aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query 'Stacks[0].StackStatus' --output text

# The demo users share one password, generated when your environment was created.
users:
	@aws ssm get-parameter --name /$(ENV_NAME)/demo/users --with-decryption --region $(REGION) \
	  --query Parameter.Value --output text \
	  | jq -r '["EMAIL","ROLE","PASSWORD"], (.[] | [.email, .role, .password]) | @tsv' | column -t

# ---- Step 11: schema change pipeline ----------------------------------------
# `make schema-sync` starts a run by hand (the Glue crawler also starts one when it
# finishes). `make approve` / `make reject` answer the newest run that is waiting
# for approval: the Lambda wrote its task token to s3://$(CFN_BUCKET)/schema/pending/.
SCHEMA_SM = arn:aws:states:$(REGION):$(ACCOUNT_ID):stateMachine:$(ENV_NAME)-schema-sync

schema-sync:
	aws stepfunctions start-execution --state-machine-arn $(SCHEMA_SM) --region $(REGION) \
	  --input '{"trigger":"manual"}' --query executionArn --output text

approve reject:
	@KEY=$$(aws s3api list-objects-v2 --bucket $(CFN_BUCKET) --prefix schema/pending/ --region $(REGION) \
	  --query 'sort_by(Contents,&LastModified)[-1].Key' --output text); \
	[ "$$KEY" != "None" ] || { echo "No run is waiting for approval."; exit 1; }; \
	aws s3api get-object --bucket $(CFN_BUCKET) --key "$$KEY" --region $(REGION) /tmp/schema-pending.json >/dev/null; \
	TOKEN=$$(python3 -c 'import json; print(json.load(open("/tmp/schema-pending.json"))["task_token"])'); \
	if [ "$@" = approve ]; then \
	  aws stepfunctions send-task-success --task-token "$$TOKEN" --task-output '{"approved":true}' --region $(REGION); \
	else \
	  aws stepfunctions send-task-failure --task-token "$$TOKEN" --error Rejected --cause "Rejected in the Code Editor" --region $(REGION); \
	fi && aws s3api delete-object --bucket $(CFN_BUCKET) --key "$$KEY" --region $(REGION) && rm -f /tmp/schema-pending.json && echo "$@: $$KEY"

# ---- build: rebuild the agent image after a code edit -----------------------
# Run this after editing unicorn_rental_agent.py or the SOP. `make deploy` only
# applies template changes; it does NOT rebuild the image. The real work lives in
# a plain bash script (scripts/rebuild-agent.sh) so it's easy to read: it zips +
# uploads the code and runs `make deploy` with a fresh image tag, and then
# CloudFormation rebuilds the image and rolls the runtime. Open the script to see
# exactly what happens — there's no hidden magic.
build:
	@bash scripts/rebuild-agent.sh

# ---- voice-deploy: OPTIONAL voice step (after Cube) -------------------------
# Deploys voice-agentcore-stack.yaml — a SEPARATE stack with its own AgentCore
# Runtime (Pipecat voice pipeline, WebRTC + KVS managed TURN) and a JWT signaling
# proxy. It reuses the analytics agent you built: the StrandsAgentRuntimeArn comes
# from the top-up stack's output. VPC/subnet/Cognito values come from config.env.
# Requires DEEPGRAM_API_KEY=... on the command line.
voice-deploy:
	@set -euo pipefail; \
	[ -n "$(DEEPGRAM_API_KEY)" ] || { echo "Set DEEPGRAM_API_KEY=... (get a free key at deepgram.com)" >&2; exit 1; }; \
	echo "==> [voice] reading the analytics Runtime ARN from $(STACK)"; \
	STRANDS_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text); \
	[ -n "$$STRANDS_ARN" ] || { echo "AgentRuntimeArn not found — deploy the analytics top-up first (make deploy)" >&2; exit 1; }; \
	FRONTDOOR_URL=$$(aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query "Stacks[0].Outputs[?OutputKey=='FrontDoorUrl'].OutputValue" --output text); \
	[ "$$FRONTDOOR_URL" = "None" ] && FRONTDOOR_URL=""; \
	echo "==> [voice] front door: $${FRONTDOOR_URL:-off (Step 7c not enabled), calling the runtime directly}"; \
	aws s3api head-bucket --bucket $(CFN_BUCKET) 2>/dev/null || \
	  { echo "==> [voice] creating staging bucket $(CFN_BUCKET)"; aws s3 mb s3://$(CFN_BUCKET) --region $(REGION); }; \
	echo "==> [voice] zipping voice bot from $(VOICE_DIR)"; \
	ZIP=$$(mktemp).zip; \
	(cd $(VOICE_DIR) && zip -r "$$ZIP" bot.py analytics_processor.py auth.py pyproject.toml uv.lock -x '*.pyc' '*__pycache__*' '*tests*' >/dev/null); \
	HASH=$$(shasum -a 256 "$$ZIP" 2>/dev/null | cut -c1-12 || sha256sum "$$ZIP" | cut -c1-12); \
	CODE_KEY="voice/voice_agent_code-$$HASH.zip"; \
	echo "==> [voice] uploading -> s3://$(CFN_BUCKET)/$$CODE_KEY"; \
	aws s3 cp "$$ZIP" "s3://$(CFN_BUCKET)/$$CODE_KEY" --region $(REGION); \
	rm -f "$$ZIP"; \
	echo "==> [voice] deploying $(VOICE_STACK)"; \
	aws cloudformation deploy \
	  --template-file $(VOICE_TEMPLATE) \
	  --stack-name $(VOICE_STACK) \
	  --s3-bucket $(CFN_BUCKET) --s3-prefix cfn-staging \
	  --parameter-overrides \
	      EnvironmentName=$(ENV_NAME) \
	      ArtifactsBucket=$(CFN_BUCKET) \
	      ArtifactsPrefix= \
	      VoiceAgentCodeS3Key=$$CODE_KEY \
	      CognitoUserPoolId=$(COGNITO_POOL) \
	      CognitoClientId=$(COGNITO_CLIENT) \
	      StrandsAgentRuntimeArn=$$STRANDS_ARN \
	      StrandsAgentFrontDoorUrl=$$FRONTDOOR_URL \
	      ChartBucket=$(ARTIFACTS_BUCKET) \
	      DeepgramApiKey=$(DEEPGRAM_API_KEY) \
	      DeepgramVoiceId=$(DEEPGRAM_VOICE_ID) \
	      VpcId=$(VPC_ID) \
	      PrivateSubnet1Id=$(SUBNET1) \
	      PrivateSubnet2Id=$(SUBNET2) \
	      PrivateRouteTableId=$(PRIVATE_RT) \
	  --capabilities CAPABILITY_NAMED_IAM \
	  --region $(REGION); \
	echo "==> [voice] done. Get the signaling URL with:"; \
	echo "    aws cloudformation describe-stacks --stack-name $(VOICE_STACK) --region $(REGION) \\"; \
	echo "      --query \"Stacks[0].Outputs[?OutputKey=='VoiceSignalingUrl'].OutputValue\" --output text"
