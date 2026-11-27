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

새로 생성하는 버킷에는 Block Public Access와 HTTP 접근을 거부하는 정책이 적용됩니다. 재사용 버킷의 기존 정책은 이 stack이 관리하거나 변경하지 않습니다. Training 역할의 S3 권한은 프로젝트 prefix로 제한되며, ECR 이미지 조회, 학습 로그와 지표 기록, autolog 예제의 MLflow App API 호출에 필요한 권한도 포함합니다. SageMaker SDK v3의 역할 사전 검사를 통과하기 위한 network interface 권한도 포함하지만, 이 샘플 자체는 VPC 설정을 사용하지 않습니다.

신규 배포의 버킷 이름은 `sagemaker-xgboost-lab-<account-id>-<region>`입니다. 기존 stack은 output에 있는 버킷 이름을 유지합니다. 재사용 전에는 버킷 소유 계정과 리전을 확인합니다. IAM 역할 이름은 CloudFormation이 생성하고, launcher는 stack output의 ARN을 사용합니다. 코드와 정책의 S3 artifact prefix는 모두 `sagemaker-xgboost-lab`으로 고정합니다.

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

기존 App의 역할·artifact store가 stack output과 다르거나 실패 상태이면 App을 삭제하지 않고 중단합니다. 출력된 값을 검토한 뒤 해당 App을 삭제하고 다시 만들려는 경우에만 `./scripts/bootstrap-infra.sh --replace-mlflow-app`을 사용합니다. 이 작업은 기존 App의 추적 이력을 이전하는 작업이 아닙니다.

## 배포 결과

배포가 완료되면 다음 값이 출력됩니다.

```text
Infrastructure ready.
Artifact bucket: sagemaker-xgboost-lab-<account-id>-<region>
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

같은 명령은 반복해서 실행할 수 있으며, CloudFormation은 변경된 부분만 업데이트합니다. 기존 MLflow App의 IAM 역할과 artifact store가 현재 인프라 설정과 같으면 재사용하고, 다르면 중단하며, 재생성하려면 `--replace-mlflow-app`이 필요합니다.

stack이 `ROLLBACK_COMPLETE` 상태이면 bootstrap이 실패한 stack을 삭제한 뒤 다시 배포합니다. 배포가 실패하면 마지막 CloudFormation 실패 이벤트를 터미널에 표시합니다.

## 이전 배포 갱신

이전 stack에서 bootstrap을 재실행하기 전에 CloudFormation 변경 세트를 확인하세요.
고정 역할 이름 제거는 역할 교체를, 버킷 정책 조건 추가는 재사용 버킷에서 이전에 관리하던
정책 제거를 유발할 수 있습니다. 현재 버킷 정책과 사용자 지정 artifact prefix 여부도 확인합니다.

`sm-xgboost-lab`에서 다음 명령으로 실행하지 않은 변경 세트를 생성합니다.

```bash
BUCKET_NAME="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue | [0]" --output text)"
CREATE_BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Stacks[0].Parameters[?ParameterKey=='CreateArtifactBucket'].ParameterValue | [0]" --output text)"
aws cloudformation deploy --template-file infra/main.yaml \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --parameter-overrides "BucketName=${BUCKET_NAME}" "CreateArtifactBucket=${CREATE_BUCKET}" \
  --capabilities CAPABILITY_NAMED_IAM --no-execute-changeset
```

CloudFormation에서 변경 세트를 확인한 뒤 실행합니다. 기존 버킷 이름과 생성 여부를 유지합니다.
별도 지정하지 않은 기존 파라미터(permissions boundary 포함)는 이전 값을 유지합니다.
실행 역할이 바뀌면 의존하는 정책을 갱신하고 Pipeline setup·등록을 다시 실행합니다.
MLflow 역할이 바뀌면 App 교체를 명시적으로 계획합니다. 일반 bootstrap은 불일치 시 중단합니다.

stack이 소유한 버킷을 수동으로 삭제한 경우 bootstrap은 다른 버킷으로 자동 전환하지 않고 중단합니다.
해당 버킷을 복구하거나 이전 stack을 의도적으로 정리한 뒤 새로 배포하세요.
