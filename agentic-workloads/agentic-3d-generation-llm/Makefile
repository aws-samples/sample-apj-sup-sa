PROFILE ?= isg-cde-admin
PYTHON  ?= uv run -q --python 3.12 --with pytest --with boto3 python

.PHONY: install build test synth deploy destroy

install:
	cd frontend && npm ci
	cd infra && npm ci

build:
	cd frontend && npm run build

test: build
	$(PYTHON) -m pytest -q -p no:cacheprovider backend/tests agent/test_agent.py
	cd infra && npx tsc --noEmit && npx jest

synth: build
	cd infra && npx cdk synth --profile $(PROFILE) -q

# Builds the arm64 agent image (QEMU on x86 hosts), then deploys everything to us-east-1.
deploy: build
	cd infra && npx cdk deploy --profile $(PROFILE) --require-approval never

destroy:
	cd infra && npx cdk destroy --profile $(PROFILE)
