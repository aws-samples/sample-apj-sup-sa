[English](troubleshooting.md) | 한국어

# Troubleshooting

## Bootstrap에서 `Unsupported MLflow App version` 발생

이전 bootstrap 스크립트는 MLflow App 버전을 `3.10.x`로 제한했습니다. 현재 스크립트는 App 버전을
표시만 하며, 로컬 환경과 autolog 학습 환경의 MLflow 클라이언트는 `3.16.0`으로 고정합니다.
수정된 코드로 의존성을 동기화한 뒤 다시 실행합니다.

```bash
uv sync --frozen --extra dev
./scripts/bootstrap-infra.sh
```

이 버전 오류 때문에 App을 삭제하거나 다시 만들 필요는 없습니다. Pipeline도 사용한다면
`sm-pipeline-quick`에서 `uv sync --frozen --extra dev`를 실행한 뒤 `./scripts/setup.sh`를 실행합니다.
서버 버전이 표시되더라도 연결이 검증된 것은 아니므로, 실제 연결 오류는 별도로 확인해야 합니다.

## AWS 자격 증명을 찾지 못함

다음 명령이 계정 정보를 반환하는지 확인합니다.

```bash
aws sts get-caller-identity
```

## `AccessDenied`

현재 자격 증명에 CloudFormation, S3, SageMaker 권한이 있는지 확인합니다. Training에는
SageMaker Training Job 생성 권한이 필요하고, AMT에는 Hyperparameter Tuning Job 생성
권한이 추가로 필요합니다. 두 경우 모두 CloudFormation이 만든 Training 역할에 대한
`iam:PassRole` 권한이 필요합니다.

## MLflow App을 찾지 못함

먼저 인프라를 배포합니다.

```bash
./scripts/bootstrap-infra.sh
```

현재 상태는 다음과 같이 확인합니다.

```bash
aws sagemaker list-mlflow-apps \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Summaries[?Name=='sagemaker-xgboost-lab'].[Name,Status,MlflowVersion,Arn]" \
  --output table
```

## MLflow 엔드포인트에서 HTTP 502 발생

MLflow App의 상태가 `Created`여도 App이 이미 삭제된 예전 IAM 역할을 참조하고 있으면 엔드포인트가 `502`를 반환할 수 있습니다. bootstrap은 현재 CloudFormation 출력과 App의 역할 및 artifact store를 비교하고, 값이 다르면 App을 삭제하지 않고 중단합니다. 해당 App을 삭제하고 다시 만들려는 경우에만 아래 교체 명령을 실행하세요.

```bash
./scripts/bootstrap-infra.sh --replace-mlflow-app
./scripts/run-training.sh --instance-type ml.g5.xlarge
```

학습 코드도 비용이 발생하는 Training Job을 만들기 전에 MLflow 연결을 확인하고, 연결할 수 없으면 짧은 안내 메시지를 출력하고 종료합니다.

## MLflow 아티팩트 업로드 시 `AccessDenied`

autolog Training Job은 `data-summary.json`, 모델, feature importance를 MLflow 아티팩트
prefix에 직접 업로드합니다. 따라서 MLflow App 역할뿐 아니라 **SageMaker Training
역할**에도 `sagemaker-xgboost-lab/mlflow/*`에 대한 `s3:PutObject` 권한이 필요합니다.

stack의 `SageMakerExecutionRoleArn`에 해당하는 역할에서 `s3:PutObject` 오류가 발생하면 다음과 같이
인프라를 다시 배포합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-autolog-training.sh
```

## GPU 인스턴스 할당량 오류

계정에 `ml.g4dn.xlarge` 또는 `ml.g5.xlarge` Training 할당량이 없으면 작업 생성이 거부됩니다. Service Quotas에서 해당 SageMaker Training 인스턴스 할당량을 확인하거나, 기본값인 `ml.c6i.xlarge`로 실행합니다.

Batch Transform은 Training과 별도의 할당량을 사용합니다. 이 샘플은 `ml.c5.xlarge` 한 종류로
고정되어 있으므로 Service Quotas에서 이 인스턴스의 **for transform job usage** 값이 `1` 이상인지
확인합니다.

```bash
./scripts/run-training.sh
```

## Automatic Model Tuning이 시작되지 않음

AMT의 `max-parallel-jobs`는 해당 인스턴스의 동시 Training 할당량을 초과할 수 없습니다.
기본값 `1`로 다시 실행하거나 Service Quotas에서 Training 할당량을 확인합니다.

```bash
./scripts/run-tuning.sh --max-jobs 4 --max-parallel-jobs 1
```

AMT는 `max-jobs`만큼 Training Job을 실행할 수 있습니다. 일반 Training보다 시간과
비용이 더 들며, Tuning Job을 중단하더라도 이미 실행된 Training의 비용은 발생합니다.

## AMT에서 CSV를 LIBSVM으로 해석함

Training Job의 `InputDataConfig`에 `ContentType=text/csv`가 없으면 built-in XGBoost가
CSV를 기본 형식인 LIBSVM으로 해석해 실패합니다. 이 프로젝트의 `train_tuner.py`는 SDK v3의
간단한 `InputData` 변환 대신 `Channel`을 사용해 content type을 명시합니다.

## Batch Transform에서 Approved 모델을 찾지 못함

MLflow UI에서 모델을 등록해도 SageMaker Model Registry의 배포 상태는 처음에 **Pending Approval**로 표시될 수 있습니다. SageMaker Studio에서 **JumpStart / Models → My models → Logged**로 이동해 모델 그룹과 버전을 차례로 연 뒤, **Overview → Deploy** 상태를 **Approved**로 변경합니다.

`run-batch-inference.sh`는 MLflow가 만든 Model Package Group을 자동으로 찾습니다. 같은 prefix의 그룹이 여러 개라면 `MODEL_PACKAGE_GROUP_NAME`에 Studio에 표시된 정확한 이름을 지정합니다. 스크립트는 이 그룹의 최신 `Approved` 버전 중 `ml.c5.xlarge`를 지원하는 버전만 사용합니다. **Pending Approval**, **Rejected** 또는 호환되지 않는 버전만 있으면 추론 비용이 발생하기 전에 중단합니다.

```bash
./scripts/run-batch-inference.sh
```

## UCI 데이터셋 다운로드 실패

로컬 머신에서 UCI 리포지토리에 접근할 수 있는지 확인합니다. 데이터셋 다운로드는 SageMaker가 아니라 명령을 실행한 로컬 Python 프로세스가 수행합니다.

## Training Job 실패

터미널에 출력된 SageMaker 로그에서 마지막 오류를 확인합니다. SageMaker 콘솔의 Training jobs 화면에서도 같은 작업 이름으로 로그와 실패 원인을 볼 수 있습니다.

## `RoleValidationError`

`ModelTrainer`는 Training Job을 만들기 전에 실행 역할의 S3, ECR, CloudWatch, EC2 네트워크 권한을 확인합니다. 이 리포지토리가 만든 역할의 설정이 오래되었다면 인프라를 다시 배포해 정책을 업데이트합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-training.sh --instance-type ml.g5.xlarge
```

이 검사는 작업 생성 전에 실행되므로, 이 단계에서 발생한 오류로는 Training 인스턴스 비용이 발생하지 않습니다.

## MLflow에 실행은 있지만 지표가 없음

SageMaker Training Job이 완료되었고 `validation:auc`가 기록되었는지 확인합니다. MLflow 지표는 완료된 작업의 이 값을 읽어 `validation-auc`로 저장합니다.
