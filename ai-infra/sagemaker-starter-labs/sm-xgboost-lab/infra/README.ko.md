[English](README.md) | 한국어

# 인프라 배포

학습을 시작하기 전에 다음 명령을 한 번 실행합니다.

```bash
./scripts/bootstrap-infra.sh
```

스크립트는 다음 두 단계를 수행합니다.

## 1. S3 버킷과 IAM 역할

`infra/main.yaml`을 `sagemaker-xgboost-lab` CloudFormation stack으로 배포합니다.

| 리소스 | 용도 |
|---|---|
| S3 아티팩트 버킷 | 학습 입력 데이터, 모델, MLflow 아티팩트 저장 |
| SageMaker Training 역할 | Training Job이 입력 데이터와 컨테이너 이미지를 읽고 모델, 로그, 지표를 저장 |
| MLflow App 역할 | managed MLflow가 전용 S3 prefix를 읽고 쓰는 데 사용 |

버킷에는 Block Public Access와 HTTP 접근을 거부하는 정책이 적용됩니다. Training 역할의 S3 권한은 프로젝트 prefix로 제한되며, ECR 이미지 조회, 학습 로그와 지표 기록, autolog 예제의 MLflow App API 호출에 필요한 권한도 포함합니다. SageMaker SDK v3의 역할 사전 검사를 통과하기 위한 network interface 권한도 포함하지만, 이 샘플 자체는 VPC 설정을 사용하지 않습니다.

버킷 이름은 현재 계정 ID를 사용해 `sagemaker-xgboost-lab-<account-id>`로 정합니다. 이전 버전에서 생성한 같은 이름의 버킷이 있으면 새 stack은 버킷을 다시 만들지 않고 재사용합니다.

조직에서 IAM permissions boundary를 요구하면 환경 변수로 전달할 수 있습니다.

```bash
PERMISSIONS_BOUNDARY_ARN='<boundary-arn>' ./scripts/bootstrap-infra.sh
```

## 2. MLflow App

CloudFormation output의 MLflow 역할과 S3 버킷을 사용해 `sagemaker-xgboost-lab` MLflow App을 생성합니다.

MLflow App은 SageMaker API에서 비동기로 생성됩니다. 스크립트는 10초마다 상태를 확인하며, `Created` 또는 `Updated`가 될 때까지 최대 10분 동안 기다립니다.

```text
[1/2] Deploy S3 bucket and IAM role
[2/2] Prepare MLflow App
MLflow App status: Creating
MLflow App status: Created
```

이미 준비된 App이 있으면 해당 App의 IAM 역할과 artifact store를 현재 CloudFormation output과 비교합니다. 삭제된 예전 역할을 참조하거나 다른 S3 경로를 사용하는 App, 또는 실패 상태인 App은 삭제한 뒤 현재 설정으로 다시 생성합니다.

## 배포 결과

배포가 완료되면 다음 값이 출력됩니다.

```text
Infrastructure ready.
Artifact bucket: sagemaker-xgboost-lab-<account-id>
Execution role: <training-role-arn>
MLflow App: <mlflow-app-arn>
Next: ./scripts/run-training.sh
```

`run-training.sh`가 이 값을 자동으로 조회하므로 직접 `export`할 필요가 없습니다.

## 필요한 실행 권한

bootstrap을 실행하는 identity에는 다음 리소스를 생성할 권한이 필요합니다.

- CloudFormation stack
- S3 버킷과 버킷 정책
- IAM 역할과 inline 정책
- SageMaker MLflow App

학습을 실행하는 identity에는 S3 업로드/다운로드, SageMaker Training Job 생성,
실행 역할을 전달하는 `iam:PassRole`, MLflow App 접근 권한이 필요합니다. Automatic
Model Tuning을 실행하려면 SageMaker Hyperparameter Tuning Job 생성 및 조회 권한도
필요합니다.

### Batch Transform 실행 권한

`run-batch-inference.sh`를 실행하는 사용자나 CI identity는 SageMaker 실행
역할과 별개이므로 다음 최소 권한을 별도로 부여해야 합니다.

| 권한 | 범위 |
|---|---|
| `cloudformation:DescribeStacks` | `sagemaker-xgboost-lab` stack |
| `sagemaker:ListModelPackageGroups`, `sagemaker:ListModelPackages`, `sagemaker:DescribeModelPackage` | 등록된 Model Package 조회 |
| `sagemaker:CreateModel`, `sagemaker:DeleteModel` | `xgboost-lab-batch-*` 임시 모델 |
| `sagemaker:CreateTransformJob`, `sagemaker:DescribeTransformJob` | `xgboost-lab-batch-*` Transform 작업 |
| `iam:PassRole` | CloudFormation output의 `SageMakerExecutionRoleArn` 하나 |
| `s3:PutObject`, `s3:GetObject` | `sagemaker-xgboost-lab/credit-card-default/batch/*` prefix |

`iam:PassRole`은 반드시 이 샘플의 SageMaker 실행 역할로 제한합니다. Batch
Transform이 S3 입력 데이터를 읽고 예측 결과를 쓰는 권한은 해당 실행 역할에 이미 포함되어
있습니다.

## 다시 실행

같은 명령은 반복해서 실행할 수 있으며, CloudFormation은 변경된 부분만 업데이트합니다. 기존 MLflow App의 IAM 역할과 artifact store가 현재 인프라 설정과 같으면 재사용하고, 다르면 App을 다시 생성합니다.

stack이 `ROLLBACK_COMPLETE` 상태이면 bootstrap이 실패한 stack을 삭제한 뒤 다시 배포합니다. 배포가 실패하면 마지막 CloudFormation 실패 이벤트를 터미널에 표시합니다.
