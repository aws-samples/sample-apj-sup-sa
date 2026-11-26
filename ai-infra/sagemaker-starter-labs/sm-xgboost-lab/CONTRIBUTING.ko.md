[English](CONTRIBUTING.md) | 한국어

# 기여 안내

이 문서는 이 repository를 수정하거나 개선하려는 사람을 위한 최소 개발 규칙입니다.

## 개발 환경 설정

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pytest
uv run pre-commit install
uv run pre-commit run --all-files
```

## 기여 시 지킬 점

- unit test는 AWS credential 없이 실행되어야 합니다.
- SageMaker 및 XGBoost version은 명시적으로 고정합니다.
- 처음 보는 사람이 Training과 MLflow 흐름을 따라갈 수 있도록 core code를 작게 유지합니다.
- production용 governance 기능은 core Training 흐름에 직접 추가하지 않습니다.
- data 또는 metric 동작을 바꾸면 test도 갱신합니다.
- 실제 customer data를 repository에 추가하지 않습니다.
