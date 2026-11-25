English | [한국어](CONTRIBUTING.ko.md)

# Contributing

This document describes the minimum development rules for anyone who wants to modify or improve this repository.

## Development environment setup

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pytest
uv run pre-commit install
uv run pre-commit run --all-files
```

## Contribution guidelines

- Unit tests must run without AWS credentials.
- Pin SageMaker and XGBoost versions explicitly.
- Keep the core code small so that newcomers can follow the Training and MLflow flow.
- Do not add production governance features directly to the core Training flow.
- Update the tests when you change data or metric behavior.
- Do not add real customer data to the repository.
