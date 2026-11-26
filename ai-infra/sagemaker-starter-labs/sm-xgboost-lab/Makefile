.DEFAULT_GOAL := help

UV ?= uv
INSTANCE_TYPE ?= ml.c6i.xlarge

.PHONY: help install lint test pre-commit run run-simple run-autolog

help:
	@printf "Targets:\n"
	@printf "  make install   Install package and development dependencies\n"
	@printf "  make lint      Run Ruff\n"
	@printf "  make test      Run unit tests without AWS calls\n"
	@printf "  make pre-commit Run repository pre-commit hooks\n"
	@printf "  make run       Launch managed SageMaker training jobs\n"
	@printf "  make run-simple Launch one built-in Training Job without MLflow\n"
	@printf "  make run-autolog Launch script-mode training with MLflow autolog\n"

install:
	$(UV) sync --extra dev

lint:
	$(UV) run ruff check src tests

test:
	$(UV) run python -m pytest

pre-commit:
	$(UV) run pre-commit run --all-files

run:
	./scripts/run-training.sh --instance-type $(INSTANCE_TYPE)

run-simple:
	./scripts/run-simple-training.sh --instance-type $(INSTANCE_TYPE)

run-autolog:
	./scripts/run-autolog-training.sh --instance-type $(INSTANCE_TYPE)
