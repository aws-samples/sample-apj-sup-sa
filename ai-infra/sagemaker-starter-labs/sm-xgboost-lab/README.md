English | [한국어](README.ko.md)

# SageMaker Credit Default XGBoost + MLflow

This hands-on lab trains SageMaker XGBoost on the public [UCI Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients) dataset, compares the results in managed MLflow, and then walks through SageMaker Model Registry approval and Batch Transform.

The core path of this repository is as follows.

```text
clone
  -> Create infrastructure
  -> Single built-in XGBoost training run
  -> Log two configurations to MLflow and compare them
  -> Register the selected model in SageMaker Model Registry and change it to Approved
  -> Predict probabilities with Batch Transform
```

The goal is to understand the smallest operational flow from model training to inference.

## Cost

This lab incurs pay-as-you-go charges. You are billed for SageMaker Training Job, managed MLflow App, and Batch Transform run time, as well as S3 artifact storage. Run only the core flow first, and run Automatic Model Tuning only when you need it. After the lab, delete the MLflow App and artifacts by following [Clean up resources](#clean-up-resources).

Estimate the cost with the [AWS Pricing Calculator](https://calculator.aws/) for your Region as follows.

- Training: hourly instance price × Job run time. The default flow consists of 1 simple job and 2 MLflow training jobs.
- MLflow: hourly Tracking Server price × the time the App is kept running.
- Batch Transform: hourly `ml.c5.xlarge` price × Job run time.
- S3: artifact storage (GB-month) × retention period.

Running Automatic Model Tuning adds Training cost for each completed trial.

## What this example creates

- An XGBoost Training Job launched by SageMaker SDK v3 `ModelTrainer`
- A model that predicts whether a UCI Default of Credit Card Clients customer will default next month
- Managed MLflow experiments, runs, metrics, parameters, and dataset lineage
- A model version selected in MLflow and registered in SageMaker Model Registry
- A Batch Transform Job that runs with the latest `Approved` model

The dataset is a numeric table of 30,000 rows. It predicts next-month default from 23 attributes, including credit limit, repayment status, bill amounts, and payment amounts for the last six months, and demographic information. `SEX`, `EDUCATION`, `MARRIAGE`, and `AGE` are included for the learning experience, but using them for real credit decisions requires prior regulatory and fairness review and per-group performance evaluation.

## Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/)
- AWS CLI with configured AWS credentials
- Permissions to use CloudFormation, IAM, S3, and SageMaker AI
- SageMaker Studio access and permissions to create and run Studio

First, check your AWS account and Region. The default Region for this example is Seoul, `ap-northeast-2`.

```bash
aws sts get-caller-identity
aws configure get region
```

For the required permissions and the AWS resources that are created, see [Infrastructure deployment](infra/README.md). Training Jobs, the MLflow App, S3 storage, and Batch Transform may incur charges.

## 1. Clone and prepare the local environment

Clone the repository using its GitHub URL.

```bash
git clone <repository-url>
cd sm-xgboost-lab
```

`uv` installs the project dependencies and development tools. Unit tests do not create any AWS resources.

```bash
uv sync --extra dev
uv run ruff check src tests
uv run python -m pytest
```

When modifying the lab, you can optionally install and run its pre-commit hook from
`sm-xgboost-lab`. The hook checks only this lab's files and may apply Ruff fixes.

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

Do not add customer data or credentials to the repository. Review encryption, network access,
and data retention before adapting the lab to real data.

## 2. Create the infrastructure

Run this only once. It creates the S3 artifact bucket, the SageMaker Training execution role, and the role required by the managed MLflow App, and then waits until the MLflow App is ready.

```bash
./scripts/bootstrap-infra.sh
```

The bucket name is generated automatically in the form `sagemaker-xgboost-lab-<account-id>`. Because the script passes the CloudFormation outputs to subsequent runs, you do not need to `export` the bucket name or role ARN yourself.

## 3. Run built-in XGBoost once without MLflow

First, use the example below to see the smallest SageMaker Training Job flow.

```bash
./scripts/run-simple-training.sh
```

This command prepares the public dataset locally, uploads it to S3, and runs a single SageMaker built-in XGBoost Training Job. When it completes, the terminal shows the `Validation AUC`, the Training Job name, and the S3 URI of the model artifact.

This step does not read the MLflow App ARN and does not create an MLflow run or a Registry version. For the code and the meaning of the `train`/`validation` channels, see [Simple built-in XGBoost training](docs/simple-training.md).

## 4. Training runs logged to MLflow

Next, run two built-in XGBoost configurations against the same validation dataset and log the results to managed MLflow.

```bash
./scripts/run-training.sh
```

The default CPU instance is `ml.c6i.xlarge`, which is sufficient for this small public dataset. When the run finishes, it prints the `default` and `tuned` Training Jobs, the `validation-auc` of each, and the model S3 URIs.

```text
[1/4] Connect to the MLflow App
[2/4] Prepare the public dataset and upload it to S3
[3/4] SageMaker Training Job 1/2: default
[3/4] SageMaker Training Job 2/2: tuned
[4/4] Complete MLflow logging and result comparison
```

In this example, `default` is a baseline intentionally kept small so that the improvement is easy to see. For real problems, select a model using not only AUC but also a separate test or out-of-time dataset and business metrics. The code flow is described in [Training execution flow](docs/training.md).

## 5. Compare results in MLflow

1. In the AWS Console, select the `ap-northeast-2` Region.
2. Go to **Amazon SageMaker AI → SageMaker Studio**. If you do not have Studio, complete **Create SageMaker Studio**.
3. In the created Studio, choose **Open Studio**.
4. In the left navigation of Studio, choose the **MLflow** icon and open the `sagemaker-xgboost-lab` App.
5. Choose **Open MLflow**, and then choose **Model training** in the upper left.
6. Open the `sagemaker-xgboost-lab` experiment, select the completed `default` and `tuned` runs with the same run ID suffix, and choose **Compare**.
7. Compare `validation-auc`, the XGBoost parameters, the Training Job name, and the model S3 URI.

`validation-auc` measures ranking performance in separating defaulters from non-defaulters on the validation data. Closer to `1.0` is better, and `0.5` is the level of random guessing. For a detailed walkthrough of the screens and metric interpretation, see the [MLflow results guide](docs/mlflow-guide.md).

## 6. Register and approve the selected model in the Registry

MLflow keeps all experiment results, while SageMaker Model Registry holds only actual inference candidates.

1. In MLflow, open the selected run and go to **Artifacts → model**.
2. Choose **Register model**.
3. Enter `sagemaker-xgboost-lab` as the model name and register it.
4. In SageMaker Studio, go to **JumpStart / Models → My models → Logged**.
5. Open the model group whose name starts with `sagemaker-xgboost-lab-` and the new version.
6. Change the **Pending Approval** dropdown to **Approved**.

Running **Register model** in MLflow creates the same version in both the MLflow Model Registry and the SageMaker Model Registry. `Approved` does not automatically create an endpoint. In this example, it is an explicit human approval step that selects which model version Batch Transform may use.

For the Studio screens and the meaning of the approval statuses, see [Model Registration and Batch Inference](docs/model-promotion.md).

## 7. Check predictions with Batch Transform

Run this after changing at least one model version to `Approved`.

```bash
./scripts/run-batch-inference.sh
```

The script finds the latest `Approved` version compatible with the Batch Transform instance in the Model Package Group created by MLflow. It uploads a sample CSV without labels to S3, runs an `ml.c5.xlarge` Batch Transform Job, and downloads the probability predictions to the following local file.

```text
artifacts/batch/<run-id>/predictions.csv.out
```

The terminal shows only the first 5 results as a preview. The values vary depending on the model you ran.

```text
Predictions: artifacts/batch/<run-id>/predictions.csv.out
First predicted probabilities of default next month:
- 0.16083382070064545
- 0.1926967203617096
- 0.12307227402925491
- 0.15535558760166168
- 0.03271584212779999
```

The full result file contains 6,000 probabilities in the same order as the validation input. The number on each line, between `0.0` and `1.0`, is the probability that the customer will default next month. For example, `0.99` is a high-risk prediction and `0.01` is a low-risk prediction. In real operations, set the threshold after reviewing business policy and calibration, and do not make automated decisions based on this number alone.

Model selection, parallel processing, and the result file format for Batch Transform are described in [Model Registration and Batch Inference](docs/model-promotion.md).

## Optional labs

Run the following only as needed after completing the core flow. Each document covers the purpose, cost, commands to run, and how to check the results.

| Lab | When to run it | Detailed guide |
|---|---|---|
| MLflow autolog | When you want to see `xgboost.train()` and automatic logging inside the SageMaker container | [MLflow autolog guide](docs/mlflow-autolog.md) |
| Automatic Model Tuning | When you want SageMaker to explore multiple hyperparameter combinations | [Automatic Model Tuning guide](docs/automatic-model-tuning.md) |
| Data schema and CSV | When you want a closer look at features, label-first CSV, and the `train`/`validation` channels | [Data preparation guide](docs/data-preparation.md) |
| Architecture | When you want a single view of the roles of the local process, SageMaker, MLflow, and the Registry | [Architecture](docs/architecture.md) |
| Troubleshooting | When you hit MLflow App, quota, Registry approval, or Batch Transform errors | [Troubleshooting](docs/troubleshooting.md) |

## Source layout

| File | Role |
|---|---|
| `data_prep.py` | Downloads the public dataset, performs the train/validation split, and creates SageMaker CSVs |
| `train_simple.py` | Runs a single built-in XGBoost Training Job without MLflow |
| `train.py` | Runs two built-in XGBoost Training Jobs and logs them to MLflow |
| `train_autolog.py` / `autolog.py` | Script mode XGBoost Training with MLflow autolog |
| `train_tunner.py` | Runs SageMaker Automatic Model Tuning and logs the best result |
| `batch_transform.py` | Runs Batch Transform with the latest Approved version |

## Clean up resources

Input data and Batch Transform predictions remain locally, and Training input and model artifacts remain in S3.

```text
artifacts/<run-id>/
artifacts/simple/<run-id>/
artifacts/batch/<run-id>/predictions.csv.out

s3://sagemaker-xgboost-lab-<account-id>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/
```

When you finish the lab, delete the MLflow App first.

```bash
aws sagemaker list-mlflow-apps \
  --region ap-northeast-2 \
  --query "Summaries[?Name=='sagemaker-xgboost-lab'].[Arn,Status]" \
  --output table

aws sagemaker delete-mlflow-app \
  --arn '<mlflow-app-arn>' \
  --region ap-northeast-2
```

If you no longer need the S3 artifacts, empty the bucket and then delete the CloudFormation stack. An empty artifact bucket created by the current stack is deleted along with the stack. Delete separately any bucket that was left behind by the previous `Retain` policy and reused by bootstrap.

```bash
aws s3 rm "s3://sagemaker-xgboost-lab-<account-id>/" --recursive
aws cloudformation delete-stack \
  --stack-name sagemaker-xgboost-lab \
  --region ap-northeast-2
```
