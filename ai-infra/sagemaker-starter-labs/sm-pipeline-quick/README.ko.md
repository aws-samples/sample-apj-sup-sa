[English](README.md) | 한국어

# SageMaker XGBoost Pipeline Flow

[`sm-xgboost-lab`](../sm-xgboost-lab/README.ko.md)의 local data preparation과 단일
Training Job 다음에 보는 **SageMaker Pipeline 흐름 참고 예제**다. Redshift snapshot,
Processing, Automatic Model Tuning, managed MLflow, Model Registry quality gate가 한 DAG로
연결되는 모습을 보여 준다.

> [!IMPORTANT]
> 이 repository의 Redshift view, 날짜, Processing image, UNLOAD role은 모두 **흐름 설명용
> 가상 값**이다. 기본 목적은 SageMaker Studio에서 Pipeline DAG를 보는 것이다.
> `--start`로 실행하면 안 된다.

## 흐름

```text
Redshift source view (illustrative)
  -> S3 raw Parquet snapshot
  -> Processing: pandas + awswrangler
  -> train / validation / OOT CSV
  -> AMT: XGBoost script mode + managed MLflow
  -> candidate versus champion evaluation
  -> Model Registry registration gate
```

| Step | 역할 | 실행 코드 |
| --- | --- | --- |
| `ExportRedshift` | 실행별 raw Parquet snapshot 생성 | `export_redshift.py` |
| `PrepareFeatures` | 조인, 결측 처리, feature와 시간 분할 | `prepare.py` |
| `TrainTuneModel` | AMT로 XGBoost 학습과 hyperparameter 탐색 | `train.py` |
| `EvaluateCandidate` | 동일 OOT data로 candidate와 champion 비교 | `evaluate.py` |
| `RegisterOnlyIfBetter` | 통과 모델만 Registry에 등록 | SageMaker API call |

`step_*.py`는 DAG Step을 정의한다. `train.py`, `prepare.py`처럼 `step_`이 없는 파일은
각 SageMaker container에서 실행되는 코드다.

## sm-xgboost-lab과 연결

이 예제는 `sm-xgboost-lab`이 만드는 resource를 그대로 읽는다.

| 항목 | 사용 값 |
| --- | --- |
| CloudFormation stack | `sagemaker-xgboost-lab` |
| S3 bucket | stack output `ArtifactBucketName` |
| Pipeline S3 prefix | `sagemaker-xgboost-lab/credit-card-default/pipeline/` |
| execution role | stack output `SageMakerExecutionRoleArn` |
| MLflow App | `sagemaker-xgboost-lab` |
| Pipeline name | `sagemaker-xgboost-lab-pipeline` |
| MLflow experiment | `sagemaker-xgboost-lab-pipeline` |

먼저 인프라를 준비한다.

```bash
cd ../sm-xgboost-lab
./scripts/bootstrap-infra.sh
```

그다음 이 repository에서 launcher를 실행한다.

```bash
cd ../sm-pipeline-quick
uv sync --extra dev
./scripts/run-pipeline.sh --upsert
```

launcher는 CloudFormation output과 준비된 MLflow App ARN을 읽는다. Redshift와
Processing image에는 DAG 표시용 가상 값을 넣는다. `--upsert`는 Pipeline 정의와 code artifact를
S3에 올리고 Studio에서 볼 Pipeline을 등록하지만, Redshift query, Processing, Training을
실행하지 않는다.

Studio에서 **Pipelines**를 열고 `sagemaker-xgboost-lab-pipeline`을 선택한 뒤 최신
version의 DAG를 확인한다. Designer에서 바꾼 Step 연결과 parameter는 실험용으로만 사용하고,
유지할 변경은 `pipeline.py`와 `step_*.py`에 반영한다.

## 가상 데이터 계약

`export_redshift.py`의 view와 SQL은 실제 database를 전제하지 않는 예시다. 실제 데이터로
바꿀 때는 `sm-xgboost-lab`의 공개 credit default data와 같은 계약을 먼저 고정한다.

| 가상 view | 예시 컬럼 | 목적 |
| --- | --- | --- |
| `analytics.ml_seller_applications` | `application_id`, `seller_id`, `observation_date`, `advance_amount`, `default_payment_next_month` | 기준 시점과 성숙된 label |
| `analytics.ml_seller_profiles` | `seller_id`, `industry_code`, `business_age_days` | 기준 시점에 이용 가능한 profile |
| `analytics.ml_daily_settlements` | `seller_id`, `settlement_date`, `scheduled_amount`, `settled_amount`, `days_late` | 기준일 이전 정산 이력 |

`default_payment_next_month`는 `sm-xgboost-lab`의 공개 data와 같은 양성 label 의미를
가리키는 가상 column이다. 실제 warehouse에서는 label의 성과 윈도우가 끝난 시점과
feature의 관측 시점을 별도 column 또는 view 계약으로 보장해야 한다.

`PrepareFeatures`는 정산일이 `observation_date` 이후인 row를 제외하고, train split에서만
결측값과 category mapping을 fit한다. 각 train, validation, OOT split에는 양성·음성 label이
모두 있어야 AUC를 계산할 수 있다.

### Athena에서 S3를 읽고 저장하는 참고 흐름

Athena external table이 S3 raw data를 가리키고 있다면, 아래처럼 query 결과를 실행별 S3
prefix에 Parquet로 저장할 수 있다. 이 SQL은 Pipeline에 의해 실행되지는 않으며, S3 input
-> feature 처리 -> S3 output이라는 같은 흐름을 보여 주는 참고 예시다.

```sql
CREATE TABLE analytics.pipeline_prepared_<run_id>
WITH (
  format = 'PARQUET',
  write_compression = 'SNAPPY',
  external_location = 's3://<artifact-bucket>/sagemaker-xgboost-lab/credit-card-default/pipeline/executions/<run-id>/athena-prepared/',
  partitioned_by = ARRAY['split']
) AS
WITH applications AS (
  SELECT
    application_id,
    seller_id,
    observation_date,
    advance_amount,
    requested_limit,
    default_payment_next_month
  FROM raw_credit_default.applications
  WHERE label_available_at <= DATE '<label-maturity-date>'
),
settlement_30d AS (
  SELECT
    a.application_id,
    SUM(s.settled_amount) AS settled_amount_30d,
    SUM(s.scheduled_amount) AS scheduled_amount_30d
  FROM applications a
  LEFT JOIN raw_credit_default.settlements s
    ON a.seller_id = s.seller_id
   AND s.settlement_date <= a.observation_date
   AND s.settlement_date > date_add('day', -30, a.observation_date)
  GROUP BY a.application_id
)
SELECT
  a.default_payment_next_month AS label,
  a.advance_amount,
  a.requested_limit,
  COALESCE(h.settled_amount_30d / NULLIF(h.scheduled_amount_30d, 0), 0.0) AS settlement_ratio,
  CASE
    WHEN a.observation_date < DATE '<validation-start>' THEN 'train'
    WHEN a.observation_date < DATE '<oot-start>' THEN 'validation'
    ELSE 'oot'
  END AS split
FROM applications a
LEFT JOIN settlement_30d h ON a.application_id = h.application_id;
```

예시의 `label_available_at`은 label 성숙 여부를 표현하는 가상 column이다. 실제 schema에
맞는 성숙 조건으로 바꿔야 하며, profile feature도 반드시 `observation_date` 당시의
snapshot을 사용해야 한다.

## 실제 실행으로 확장할 때

이 예제를 실행 가능한 Pipeline으로 바꾸려면 다음을 실제 값으로 교체한다.

- `scripts/run-pipeline.sh`의 Redshift workgroup, database, UNLOAD role
- Processing ECR image: `./scripts/build-processing-image.sh`
- 실제 source view와 label maturity 조건
- `sm-xgboost-lab` execution role의 Pipeline, Processing, Redshift Data API, Model Registry 권한

그 뒤에만 명시적인 날짜 parameter로 `start-pipeline-execution`을 호출한다. 첫 model은
`ProductionModelPackageArn`을 생략하고, candidate가 champion보다 AUC가 `0.01` 이상 높으며
Brier score가 `0.005`보다 많이 나빠지지 않을 때만 `PendingManualApproval`로 등록된다.

## 테스트

```bash
uv run --extra dev ruff check .
uv run python -m pytest
```
