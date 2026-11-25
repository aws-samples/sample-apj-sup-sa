English | [한국어](README.ko.md)

# SageMaker XGBoost Pipeline Flow

This is a **SageMaker Pipeline flow reference example** to look at after the local data preparation and
single Training Job in [`sm-xgboost-lab`](../sm-xgboost-lab/README.md). It shows how a Redshift snapshot,
Processing, Automatic Model Tuning, managed MLflow, and a Model Registry quality gate are connected in a
single DAG.

> [!IMPORTANT]
> The Redshift views, dates, Processing image, and UNLOAD role in this repository are all **illustrative
> placeholder values** for explaining the flow. The primary purpose is to view the Pipeline DAG in SageMaker Studio.
> Do not run it with `--start`.

## Flow

```text
Redshift source view (illustrative)
  -> S3 raw Parquet snapshot
  -> Processing: pandas + awswrangler
  -> train / validation / OOT CSV
  -> AMT: XGBoost script mode + managed MLflow
  -> candidate versus champion evaluation
  -> Model Registry registration gate
```

| Step | Role | Code |
| --- | --- | --- |
| `ExportRedshift` | Create a per-execution raw Parquet snapshot | `export_redshift.py` |
| `PrepareFeatures` | Joins, missing-value handling, features, and time-based split | `prepare.py` |
| `TrainTuneModel` | XGBoost training and hyperparameter search with AMT | `train.py` |
| `EvaluateCandidate` | Compare candidate and champion on the same OOT data | `evaluate.py` |
| `RegisterOnlyIfBetter` | Register only models that pass in the Registry | SageMaker API call |

The `step_*.py` files define the DAG Steps. Files without the `step_` prefix, such as `train.py` and
`prepare.py`, contain the code that runs inside each SageMaker container.

## Connection to sm-xgboost-lab

This example reads the resources created by `sm-xgboost-lab` as is.

| Item | Value used |
| --- | --- |
| CloudFormation stack | `sagemaker-xgboost-lab` |
| S3 bucket | stack output `ArtifactBucketName` |
| Pipeline S3 prefix | `sagemaker-xgboost-lab/credit-card-default/pipeline/` |
| execution role | stack output `SageMakerExecutionRoleArn` |
| MLflow App | `sagemaker-xgboost-lab` |
| Pipeline name | `sagemaker-xgboost-lab-pipeline` |
| MLflow experiment | `sagemaker-xgboost-lab-pipeline` |

First, prepare the infrastructure.

```bash
cd ../sm-xgboost-lab
./scripts/bootstrap-infra.sh
```

Then run the launcher from this repository.

```bash
cd ../sm-pipeline-quick
uv sync --extra dev
./scripts/run-pipeline.sh --upsert
```

The launcher reads the CloudFormation outputs and the ARN of the prepared MLflow App. For Redshift and the
Processing image, it fills in placeholder values used only to display the DAG. `--upsert` uploads the Pipeline
definition and code artifacts to S3 and registers the Pipeline so you can view it in Studio, but it does not
run any Redshift query, Processing, or Training.

In Studio, open **Pipelines**, select `sagemaker-xgboost-lab-pipeline`, and inspect the DAG of the latest
version. Use Step connections and parameters changed in the Designer only for experimentation; apply changes
you want to keep to `pipeline.py` and `step_*.py`.

## Illustrative data contract

The views and SQL in `export_redshift.py` are examples that do not assume a real database. When switching to
real data, first pin down the same contract as the public credit default data used in `sm-xgboost-lab`.

| Illustrative view | Example columns | Purpose |
| --- | --- | --- |
| `analytics.ml_seller_applications` | `application_id`, `seller_id`, `observation_date`, `advance_amount`, `default_payment_next_month` | Observation point and matured label |
| `analytics.ml_seller_profiles` | `seller_id`, `industry_code`, `business_age_days` | Profile available as of the observation point |
| `analytics.ml_daily_settlements` | `seller_id`, `settlement_date`, `scheduled_amount`, `settled_amount`, `days_late` | Settlement history prior to the observation date |

`default_payment_next_month` is an illustrative column with the same positive-label meaning as in the public
data used by `sm-xgboost-lab`. In a real warehouse, you must guarantee, through separate columns or a view
contract, when the label's performance window ends and when the features were observed.

`PrepareFeatures` excludes rows whose settlement date is after `observation_date`, and fits missing-value
handling and category mappings on the train split only. Each of the train, validation, and OOT splits must
contain both positive and negative labels for AUC to be computable.

### Reference flow for reading and writing S3 with Athena

If an Athena external table points to raw data in S3, you can save query results as Parquet to a per-execution
S3 prefix as shown below. This SQL is not run by the Pipeline; it is a reference example that illustrates the
same S3 input -> feature processing -> S3 output flow.

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

`label_available_at` in the example is an illustrative column that indicates whether the label has matured.
Replace it with a maturity condition that fits your actual schema, and make sure profile features also use the
snapshot as of `observation_date`.

## Extending to a real run

To turn this example into a runnable Pipeline, replace the following with real values.

- The Redshift workgroup, database, and UNLOAD role in `scripts/run-pipeline.sh`
- The Processing ECR image: `./scripts/build-processing-image.sh`
- The actual source views and label maturity condition
- Pipeline, Processing, Redshift Data API, and Model Registry permissions on the `sm-xgboost-lab` execution role

Only after that, call `start-pipeline-execution` with explicit date parameters. For the first model, omit
`ProductionModelPackageArn`. A candidate is registered as `PendingManualApproval` only if its AUC is at least
`0.01` higher than the champion's and its Brier score is not worse by more than `0.005`.

## Tests

```bash
uv run --extra dev ruff check .
uv run python -m pytest
```
