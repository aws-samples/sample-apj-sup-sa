PYTHON  ?= uv run -q --python 3.12 --with pytest --with boto3 python

.PHONY: install build test synth deploy destroy require-profile

install:
	cd frontend && npm ci
	cd edge-auth && npm ci
	cd infra && npm ci

build:
	cd frontend && npm run build
	cd edge-auth && npm run build

test: build
	$(PYTHON) -m pytest -q -p no:cacheprovider backend/tests agent/test_agent.py
	cd edge-auth && npm test
	cd infra && npx tsc --noEmit && npx jest

# synth, deploy and destroy need the AWS profile for the target account: make deploy PROFILE=<name>
require-profile:
	@if [ -z "$(PROFILE)" ]; then \
		echo "PROFILE is required, for example: make $(MAKECMDGOALS) PROFILE=my-aws-profile" >&2; exit 1; \
	fi

synth: require-profile build
	cd infra && npx cdk synth --profile $(PROFILE) -q

# Builds the arm64 agent image (QEMU on x86 hosts), then deploys everything to us-east-1.
deploy: require-profile build
	cd infra && npx cdk deploy --profile $(PROFILE) --require-approval never

destroy: require-profile
	cd infra && npx cdk destroy --profile $(PROFILE)
