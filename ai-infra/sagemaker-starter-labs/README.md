English | [한국어](README.ko.md)

# SageMaker Training Examples

This repository contains examples that show several ways to train and operate models on Amazon SageMaker.
It includes examples that compare the basic structure of **SageMaker Training Jobs** through MNIST
classification, and **a hands-on lab that uses a public credit default dataset to cover XGBoost, managed MLflow, Model Registry, and Pipeline workflows**.

These examples assume a workflow where you write training code in an IDE on your local PC and submit
training jobs to SageMaker by running commands in a terminal. Running them inside SageMaker Studio or a
notebook instance is not covered.

The key point is that there are two separate environments.

- **Local environment (your local PC)** — where you write training code, submit jobs, and check their status.
  It only needs control-plane packages such as `sagemaker` and `boto3`.
- **Training container (AWS)** — where the actual training runs on GPU/CPU. Training frameworks such as
  `torch` and CUDA live here.

That is why you do not install `torch` locally. The training code runs inside a container launched by AWS,
not on your local PC. This keeps the local environment lightweight and makes it clear what runs where.

## Per-project uv environments

Each example is an independent project, and its environment is managed with [uv](https://docs.astral.sh/uv/).
Each project has its own `pyproject.toml`, `uv.lock`, and `.python-version`, so you can reproduce exactly the
same dependencies without projects interfering with each other.

To use one, go into the project folder, create the environment with `uv sync`, and then run scripts with
`uv run <script>`. Because `uv run` executes in that project's virtual environment, you do not need to `activate` it.

```bash
cd sm-training-job/hello-mnist
uv sync
uv run launch.py --dry-run
```

The only local dependencies are `sagemaker` (v3) and `boto3`. Training frameworks are included in each
example's container definition (a managed DLC for script mode, `container/Dockerfile` for BYOC).

## Examples

- **[hello-mnist](sm-training-job/hello-mnist/README.md) — Script mode**
  The simplest approach. You use the AWS-managed PyTorch container (DLC) as is and pass only your training
  code (`train.py`); SageMaker handles the rest. Because you do not need to deal with Docker, this is the best
  place to start if you are new to SageMaker training.

- **[byoc-mnist](sm-training-job/byoc-mnist/README.md) — BYOC (custom container)**
  You build your own Docker image, push it to ECR, and train with that image (Bring Your Own Container).
  You have to follow the container contract yourself (`/opt/ml` paths, how the container is invoked, and so
  on), but in return you get full control over the runtime. Comparing it with hello-mnist reveals what
  SageMaker does for you behind the scenes in script mode.

- **[sm-xgboost-lab](sm-xgboost-lab/README.md) — XGBoost, MLflow, Model Registry**
  A hands-on lab that runs a built-in XGBoost Training Job on the public UCI credit default dataset, compares
  experiment results in managed MLflow, and continues through Model Registry approval and Batch Transform.
  It creates the required infrastructure with CloudFormation and requires SageMaker Studio.

- **[sm-pipeline-quick](sm-pipeline-quick/README.md) — SageMaker Pipeline flow reference**
  A reference example, built on the `sm-xgboost-lab` infrastructure, that walks through a Pipeline DAG
  connecting a Redshift snapshot, Processing, Automatic Model Tuning, managed MLflow, and a Model Registry
  quality gate. Before running it, you must first create the bootstrap infrastructure in `sm-xgboost-lab` with
  `./scripts/bootstrap-infra.sh`. The default launcher only registers the Pipeline definition, so it does not
  run any actual Redshift query or Training Job.

## Prerequisites (common)

- **uv** — Python environment and package management
- **AWS CLI v2** + credentials and a default region (verify with `aws configure` and `aws sts get-caller-identity`)
- **SageMaker execution role** — `launch.py` in the MNIST examples detects it automatically from your account.
  For the XGBoost lab, `scripts/bootstrap-infra.sh` creates the required roles with CloudFormation.
  See each project's README for details.
- **Docker** — Required to build the image in the BYOC example (byoc-mnist). It is also required if you extend
  the XGBoost Pipeline reference example to run actual Processing.
- **SageMaker Studio** — Required to view managed MLflow and Model Registry in `sm-xgboost-lab`.

Specific instructions and settings for each example are in that project's README.
