[English](README.md) | 한국어

# Infrastructure 배포

Training 전에 다음 command를 한 번 실행합니다.

```bash
./scripts/bootstrap-infra.sh
```

script는 두 단계만 수행합니다.

## 1. S3 bucket과 IAM role

`infra/main.yaml`을 `sagemaker-xgboost-lab` CloudFormation stack으로 배포합니다.

| Resource | 용도 |
|---|---|
| S3 artifact bucket | Training input, model, MLflow artifact 저장 |
| SageMaker Training role | Training Job이 input과 container image를 읽고 model, log, metric을 저장 |
| MLflow App role | managed MLflow가 전용 S3 prefix를 읽고 쓰는 데 사용 |

bucket에는 Block Public Access와 HTTP 접근 거부 policy가 적용됩니다. Training role의 S3 권한은 project prefix로 제한하며, ECR image 조회, Training log와 metric 기록, autolog 예제의 MLflow App API 호출에 필요한 권한도 포함합니다. SageMaker SDK v3의 role 사전 검사를 통과하기 위한 network interface 권한도 포함되지만, 이 sample 자체는 VPC 설정을 사용하지 않습니다.

bucket 이름은 현재 account ID를 사용해 `sagemaker-xgboost-lab-<account-id>`로 정합니다. 이전 version에서 생성한 같은 bucket이 확인되면 새 stack이 bucket을 다시 만들지 않고 재사용합니다.

조직에서 IAM permissions boundary를 요구하면 environment variable로 전달할 수 있습니다.

```bash
PERMISSIONS_BOUNDARY_ARN='<boundary-arn>' ./scripts/bootstrap-infra.sh
```

## 2. MLflow App

CloudFormation output의 MLflow role과 S3 bucket을 사용해 `sagemaker-xgboost-lab` MLflow App을 생성합니다.

MLflow App은 SageMaker API에서 비동기로 생성됩니다. script는 10초마다 status를 확인하고 `Created` 또는 `Updated`가 될 때까지 최대 10분 기다립니다.

```text
[1/2] Deploy S3 bucket and IAM role
[2/2] Prepare MLflow App
MLflow App status: Creating
MLflow App status: Created
```

이미 준비된 App이 있으면 App의 IAM role과 artifact store를 현재 CloudFormation output과 비교합니다. 삭제된 예전 role을 참조하거나 다른 S3 경로를 사용하는 App, 실패 상태의 App은 삭제한 뒤 현재 설정으로 다시 생성합니다.

## 배포 결과

완료되면 다음 값이 출력됩니다.

```text
Infrastructure ready.
Artifact bucket: sagemaker-xgboost-lab-<account-id>
Execution role: <training-role-arn>
MLflow App: <mlflow-app-arn>
Next: ./scripts/run-training.sh
```

이 값은 `run-training.sh`가 자동으로 조회하므로 직접 `export`할 필요가 없습니다.

## 필요한 실행 권한

bootstrap을 실행하는 identity에는 다음 resource를 만들 권한이 필요합니다.

- CloudFormation stack
- S3 bucket과 bucket policy
- IAM role과 inline policy
- SageMaker MLflow App

Training을 실행하는 identity에는 S3 upload/download, SageMaker Training Job 생성,
execution role을 전달하는 `iam:PassRole`, MLflow App 접근 권한이 필요합니다. Automatic
Model Tuning을 실행하려면 SageMaker Hyperparameter Tuning Job 생성 및 조회 권한도
필요합니다.

### Batch Transform 실행 권한

`run-batch-inference.sh`를 실행하는 사용자 또는 CI identity는 SageMaker execution
role이 아닙니다. 다음 최소 권한을 별도로 부여합니다.

| 권한 | 범위 |
|---|---|
| `cloudformation:DescribeStacks` | `sagemaker-xgboost-lab` stack |
| `sagemaker:ListModelPackageGroups`, `sagemaker:ListModelPackages`, `sagemaker:DescribeModelPackage` | 등록된 Model Package 조회 |
| `sagemaker:CreateModel`, `sagemaker:DeleteModel` | `xgboost-lab-batch-*` 임시 model |
| `sagemaker:CreateTransformJob`, `sagemaker:DescribeTransformJob` | `xgboost-lab-batch-*` Transform Job |
| `iam:PassRole` | CloudFormation output의 `SageMakerExecutionRoleArn` 하나 |
| `s3:PutObject`, `s3:GetObject` | `sagemaker-xgboost-lab/credit-card-default/batch/*` prefix |

`iam:PassRole`은 반드시 이 sample의 SageMaker execution role로 제한합니다. Batch
Transform이 S3 input을 읽고 prediction을 쓰는 권한은 해당 execution role에 이미 포함되어
있습니다.

## 다시 실행

같은 command를 다시 실행해도 됩니다. CloudFormation은 변경된 부분만 update합니다. 기존 MLflow App의 IAM role과 artifact store가 현재 Infrastructure와 같으면 재사용하고, 다르면 App을 다시 생성합니다.

stack이 `ROLLBACK_COMPLETE`이면 bootstrap이 실패한 stack을 삭제한 뒤 다시 배포합니다. 배포가 실패하면 마지막 CloudFormation failure event를 terminal에 표시합니다.
