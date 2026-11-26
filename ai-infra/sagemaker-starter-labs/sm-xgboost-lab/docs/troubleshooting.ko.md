[English](troubleshooting.md) | 한국어

# Troubleshooting

## AWS credential을 찾지 못함

다음 command가 account 정보를 반환하는지 확인합니다.

```bash
aws sts get-caller-identity
```

## `AccessDenied`

현재 identity에 CloudFormation, S3, SageMaker 권한이 있는지 확인합니다. Training에는
SageMaker Training Job 생성 권한이 필요하고, AMT에는 Hyperparameter Tuning Job 생성
권한이 추가로 필요합니다. 두 경우 모두 CloudFormation이 만든 Training role에 대한
`iam:PassRole`이 필요합니다.

## MLflow App을 찾지 못함

Infrastructure를 먼저 배포합니다.

```bash
./scripts/bootstrap-infra.sh
```

현재 status는 다음처럼 확인합니다.

```bash
aws sagemaker list-mlflow-apps \
  --region ap-northeast-2 \
  --query "Summaries[?Name=='sagemaker-xgboost-lab'].[Name,Status,MlflowVersion,Arn]" \
  --output table
```

## MLflow endpoint에서 HTTP 502 발생

MLflow App의 status가 `Created`여도 App이 삭제된 예전 IAM role을 참조하면 endpoint가 `502`를 반환할 수 있습니다. 다음 command를 다시 실행하면 현재 CloudFormation output과 App의 role 및 artifact store를 비교하고, 값이 다르면 App을 삭제한 뒤 올바른 설정으로 다시 생성합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-training.sh --instance-type ml.g5.xlarge
```

Training code도 비용이 발생하는 Training Job을 만들기 전에 MLflow 연결을 확인하고, 연결할 수 없으면 짧은 안내와 함께 종료합니다.

## MLflow artifact upload `AccessDenied`

autolog Training Job은 `data-summary.json`, model, feature importance를 MLflow artifact
prefix에 직접 upload합니다. 따라서 MLflow App role뿐 아니라 **SageMaker Training
role**에도 `${ArtifactPrefix}/mlflow/*`의 `s3:PutObject` 권한이 필요합니다.

다음처럼 `sagemaker-xgboost-lab-training` role의 `s3:PutObject` 오류가 보이면
Infrastructure를 다시 배포합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-autolog-training.sh
```

## GPU instance quota error

`ml.g4dn.xlarge` 또는 `ml.g5.xlarge` Training quota가 account에 없으면 Job 생성이 거부됩니다. Service Quotas에서 해당 SageMaker Training instance quota를 확인하거나 기본 `ml.c6i.xlarge`로 실행합니다.

Batch Transform은 Training과 다른 quota를 사용합니다. 이 sample은 `ml.c5.xlarge` 한 종류로
고정하므로 Service Quotas에서 이 instance의 **for transform job usage**가 `1` 이상인지
확인합니다.

```bash
./scripts/run-training.sh
```

## Automatic Model Tuning이 시작되지 않음

AMT의 `max-parallel-jobs`는 해당 instance의 동시 Training quota를 넘을 수 없습니다.
기본값 `1`로 다시 실행하거나 Service Quotas에서 Training quota를 확인합니다.

```bash
./scripts/run-tuning.sh --max-jobs 4 --max-parallel-jobs 1
```

AMT는 `max-jobs`만큼 Training Job을 실행할 수 있습니다. 일반 Training보다 시간과
비용이 더 필요하며, 중단한 Tuning Job에서도 이미 실행된 Training 비용은 발생합니다.

## AMT에서 CSV를 LIBSVM으로 해석함

Training Job의 `InputDataConfig`에 `ContentType=text/csv`가 없으면 built-in XGBoost가
CSV를 기본 LIBSVM 형식으로 해석해 실패합니다. 이 프로젝트의 `train_tunner.py`는 SDK v3의
간단한 `InputData` 변환 대신 `Channel`을 사용해 content type을 명시합니다.

## Batch Transform에서 Approved model을 찾지 못함

MLflow UI에서 model을 등록해도 SageMaker Model Registry의 deployment status는 처음에 **Pending Approval**로 보일 수 있습니다. SageMaker Studio에서 **JumpStart / Models → My models → Logged**로 이동하고, model group과 version을 차례로 연 뒤 **Overview → Deploy** 상태를 **Approved**로 변경합니다.

`run-batch-inference.sh`는 MLflow가 만든 Model Package Group을 자동으로 찾습니다. 같은 prefix의 group이 여러 개라면 `MODEL_PACKAGE_GROUP_NAME`에 Studio에 표시된 정확한 이름을 넣습니다. script는 이 group의 최신 `Approved` version 중 `ml.c5.xlarge`를 지원하는 version만 사용합니다. **Pending Approval**, **Rejected** 또는 호환되지 않는 version만 있으면 inference 비용이 발생하기 전에 중단합니다.

```bash
./scripts/run-batch-inference.sh
```

## UCI dataset download 실패

local machine에서 UCI repository에 접근할 수 있는지 확인합니다. dataset download는 SageMaker가 아니라 command를 실행한 local Python process가 수행합니다.

## Training Job 실패

terminal에 출력된 SageMaker log에서 마지막 error를 확인합니다. SageMaker console의 Training jobs 화면에서도 같은 Job name으로 log와 failure reason을 볼 수 있습니다.

## `RoleValidationError`

`ModelTrainer`는 Training Job을 만들기 전에 execution role의 S3, ECR, CloudWatch, EC2 network 권한을 확인합니다. 이 repository가 만든 role이 오래된 설정이라면 Infrastructure를 다시 배포해 policy를 update합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-training.sh --instance-type ml.g5.xlarge
```

이 검사는 Job 생성 전에 실행되므로 error가 이 단계에서 발생했다면 Training instance 비용은 발생하지 않습니다.

## MLflow에 run은 있지만 metric이 없음

SageMaker Training Job이 완료됐고 `validation:auc`가 기록됐는지 확인합니다. MLflow metric은 완료된 Job의 이 값을 읽어 `validation-auc`로 저장합니다.
