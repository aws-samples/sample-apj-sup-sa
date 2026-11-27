[English](README.md) | 한국어

# SageMaker Credit Default XGBoost + MLflow

[UCI Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients) 공개 데이터셋으로 SageMaker XGBoost 모델을 학습하고, managed MLflow에서 결과를 비교한 뒤 SageMaker Model Registry 승인과 Batch Transform까지 경험하는 실습입니다.

이 저장소의 핵심 흐름은 다음과 같습니다.

```text
clone
  -> Infrastructure 생성
  -> Built-in XGBoost 단일 학습
  -> MLflow에 두 설정을 기록하고 비교
  -> 선택한 model을 SageMaker Model Registry에 등록하고 Approved로 변경
  -> Batch Transform으로 probability 예측
```

모델 학습부터 추론까지 이어지는 가장 작은 운영 흐름을 이해하는 것이 목적입니다.

## 비용

이 실습은 사용한 만큼 비용이 발생합니다. SageMaker Training Job, managed MLflow App, Batch Transform 실행 시간과 S3 아티팩트 저장량이 과금 대상입니다. 먼저 핵심 흐름만 실행하고, Automatic Model Tuning은 필요할 때만 추가로 실행하세요. 실습이 끝나면 [리소스 정리](#리소스-정리) 절차에 따라 MLflow App과 아티팩트를 삭제합니다.

예상 비용은 사용하는 Region을 선택한 [AWS Pricing Calculator](https://calculator.aws/)에서 다음과 같이 계산합니다.

- 학습: 인스턴스 시간당 요금 × 작업 실행 시간. 기본 흐름에는 simple 학습 1개와 MLflow 학습 2개가 포함됩니다.
- MLflow: Tracking Server 시간당 요금 × App 유지 시간
- Batch Transform: `ml.c5.xlarge` 시간당 요금 × 작업 실행 시간
- S3: 아티팩트 저장 용량(GB-month) × 보관 기간

Automatic Model Tuning을 실행하면 완료된 trial 수만큼 학습 비용이 추가됩니다.

## 이 예제에서 만드는 것

- SageMaker SDK v3 `ModelTrainer`가 시작하는 XGBoost Training Job
- UCI Default of Credit Card Clients 데이터로 다음 달 연체 여부를 예측하는 모델
- managed MLflow의 experiment, 실행(run), 지표, 파라미터, 데이터셋 lineage
- MLflow에서 선택해 SageMaker Model Registry에 등록한 모델 버전
- 최신 `Approved` 모델로 실행하는 Batch Transform 작업

데이터셋은 30,000행의 숫자형 테이블입니다. 신용 한도, 최근 6개월의 상환 상태·청구 금액·납부 금액, 인구통계 정보 등 23개 항목으로 다음 달 연체 여부를 예측합니다. `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`는 학습 경험을 위해 포함했지만, 실제 신용 의사결정에 사용하려면 먼저 규제·공정성 검토와 집단별 성능 평가를 거쳐야 합니다.

## 시작 전 준비

- Python 3.10 이상
- [uv](https://docs.astral.sh/uv/)
- AWS CLI와 설정된 AWS 자격 증명
- CloudFormation, IAM, S3, SageMaker AI를 사용할 권한
- SageMaker Studio 접근 권한과 Studio 생성·실행 권한

먼저 AWS 계정과 Region을 확인합니다. 이 예제의 기본 Region은 서울 `ap-northeast-2`입니다. 다른 리전을 사용하려면 bootstrap과 학습을 실행할 터미널에서 먼저 설정하세요.

```bash
export AWS_REGION=ap-northeast-1
```

SageMaker Studio와 이후 명령에서도 같은 리전을 사용합니다.

```bash
aws sts get-caller-identity
printf '%s\n' "${AWS_REGION:-ap-northeast-2}"
```

필요한 권한과 생성되는 AWS 리소스는 [인프라 배포](infra/README.ko.md)를 참고하세요. Training Job, MLflow App, S3 저장소, Batch Transform에는 비용이 발생할 수 있습니다.

## 1. Clone과 로컬 환경 준비

GitHub 저장소 주소로 clone합니다.

```bash
git clone <repository-url>
cd sm-xgboost-lab
```

`uv`가 프로젝트 의존성과 개발 도구를 설치합니다. 단위 테스트는 AWS 리소스를 생성하지 않습니다.

```bash
uv sync --extra dev
uv run ruff check src tests
uv run python -m pytest
```

실습 코드를 수정한다면 `sm-xgboost-lab` 폴더에서 pre-commit hook을 선택적으로 설치하고
실행할 수 있습니다. hook은 이 실습의 파일만 검사하며, Ruff 자동 수정을 적용할 수 있습니다.

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

고객 데이터나 자격 증명을 저장소에 추가하지 마세요. 실제 데이터에 적용하기
전에는 암호화, 네트워크 접근, 데이터 보관 정책을 검토합니다.

## 2. 인프라 생성

이 스크립트는 한 번만 실행합니다. S3 아티팩트 버킷, SageMaker Training 실행 역할, managed MLflow App에 필요한 역할을 생성하고 MLflow App이 준비될 때까지 기다립니다.

```bash
./scripts/bootstrap-infra.sh
```

버킷 이름은 `sagemaker-xgboost-lab-<account-id>-<region>` 형식으로 자동 생성됩니다. 기존 stack은 현재 버킷 이름을 유지합니다. 스크립트가 CloudFormation output을 이후 실행에 전달하므로 버킷 이름이나 역할 ARN을 직접 `export`하지 않아도 됩니다.

기존 배포를 갱신한다면 bootstrap 재실행 전에 [이전 배포 갱신](infra/README.ko.md#이전-배포-갱신)을 확인하세요. IAM 역할이 교체될 수 있습니다.

## 3. MLflow 없이 Built-in XGBoost를 한 번 실행

먼저 다음 예제로 SageMaker Training Job의 가장 작은 흐름을 확인합니다.

```bash
./scripts/run-simple-training.sh
```

이 명령은 공개 데이터셋을 로컬에서 준비해 S3에 업로드하고, SageMaker built-in XGBoost Training Job 하나를 실행합니다. 완료되면 터미널에 `Validation AUC`, Training Job 이름, 모델 아티팩트의 S3 URI가 출력됩니다.

이 단계는 MLflow App ARN을 읽지 않으며, MLflow 실행이나 Registry 버전도 만들지 않습니다. 코드와 `train`/`validation` channel의 의미는 [간단하게 Built-in XGBoost 학습하기](docs/simple-training.ko.md)에서 확인하세요.

## 4. MLflow에 기록되는 Training 실행

다음 명령은 두 가지 built-in XGBoost 설정을 같은 validation 데이터셋으로 실행하고, 결과를 managed MLflow에 기록합니다.

```bash
./scripts/run-training.sh
```

기본 CPU 인스턴스는 `ml.c6i.xlarge`이며, 이 작은 공개 데이터셋에는 충분합니다. 실행이 끝나면 `default`와 `tuned` Training Job, 각각의 `validation-auc`, 모델 S3 URI가 출력됩니다.

```text
[1/4] Connect to the MLflow App
[2/4] Prepare the public dataset and upload it to S3
[3/4] SageMaker Training Job 1/2: default
[3/4] SageMaker Training Job 2/2: tuned
[4/4] Complete MLflow logging and result comparison
```

이 예제의 `default`는 개선 효과를 쉽게 확인하도록 의도적으로 작게 만든 baseline입니다. 실제 문제에서는 AUC뿐 아니라 별도의 test 또는 out-of-time 데이터셋과 비즈니스 지표로 모델을 선택해야 합니다. 코드 흐름은 [Training 실행 흐름](docs/training.ko.md)에서 설명합니다.

## 5. MLflow에서 결과 비교

1. AWS Console에서 bootstrap과 학습에 사용한 리전(기본값: `ap-northeast-2`)을 선택합니다.
2. **Amazon SageMaker AI → SageMaker Studio**로 이동합니다. Studio가 없으면 **Create SageMaker Studio**를 완료합니다.
3. 생성된 Studio에서 **Open Studio**를 선택합니다.
4. Studio 왼쪽 탐색 창에서 **MLflow** 아이콘을 선택하고 `sagemaker-xgboost-lab` App을 엽니다.
5. **Open MLflow**를 선택한 뒤 왼쪽 위에서 **Model training**을 선택합니다.
6. `sagemaker-xgboost-lab` experiment를 열고, run ID suffix가 같은 완료된 `default`와 `tuned` 실행을 선택한 뒤 **Compare**를 누릅니다.
7. `validation-auc`, XGBoost 파라미터, Training Job 이름, 모델 S3 URI를 비교합니다.

`validation-auc`는 validation 데이터에서 연체와 비연체를 얼마나 잘 구분해 순위를 매기는지 나타냅니다. `1.0`에 가까울수록 좋고, `0.5`는 무작위 추측 수준입니다. 화면별 상세 안내와 지표 해석은 [MLflow 결과 확인 가이드](docs/mlflow-guide.ko.md)를 참고하세요.

## 6. 선택한 모델을 Registry에 등록하고 승인

MLflow에는 모든 실험 결과를 보관하고, SageMaker Model Registry에는 실제 추론에 사용할 후보만 등록합니다.

1. MLflow에서 선택한 실행을 열고 **Artifacts → model**로 이동합니다.
2. **Register model**을 선택합니다.
3. 모델 이름에 `sagemaker-xgboost-lab`을 입력하고 등록합니다.
4. SageMaker Studio에서 **JumpStart / Models → My models → Logged**로 이동합니다.
5. `sagemaker-xgboost-lab-`로 시작하는 모델 그룹과 새 버전을 엽니다.
6. **Pending Approval** 드롭다운을 **Approved**로 변경합니다.

MLflow에서 **Register model**을 실행하면 MLflow Model Registry와 SageMaker Model Registry에 같은 버전이 생성됩니다. `Approved`로 바꿔도 엔드포인트가 자동으로 생성되지는 않습니다. 이 예제에서 승인은 Batch Transform에 사용할 모델 버전을 사람이 명시적으로 지정하는 단계입니다.

Studio 화면과 승인 상태의 의미는 [Model Registration and Batch Inference](docs/model-promotion.ko.md)를 참고하세요.

## 7. Batch Transform으로 예측 확인

모델 버전을 하나 이상 `Approved`로 변경한 뒤 실행합니다.

```bash
./scripts/run-batch-inference.sh
```

스크립트는 MLflow가 만든 Model Package Group에서 Batch Transform 인스턴스와 호환되는 최신 `Approved` 버전을 찾습니다. 레이블을 제외한 샘플 CSV를 S3에 업로드하고 `ml.c5.xlarge` Batch Transform 작업을 실행한 뒤, 확률 예측 결과를 다음 로컬 파일로 다운로드합니다.

```text
artifacts/batch/<run-id>/predictions.csv.out
```

터미널에는 결과 중 처음 5개만 미리 보기로 출력됩니다. 값은 실행한 모델에 따라 달라집니다.

```text
Predictions: artifacts/batch/<run-id>/predictions.csv.out
First predicted probabilities of default next month:
- 0.16083382070064545
- 0.1926967203617096
- 0.12307227402925491
- 0.15535558760166168
- 0.03271584212779999
```

전체 결과 파일에는 validation 입력과 같은 순서로 6,000개의 확률 값이 저장됩니다. 각 줄의 `0.0`~`1.0` 사이 숫자는 해당 고객이 다음 달에 연체할 확률입니다. 예를 들어 `0.99`는 연체 위험이 높다는 예측이고, `0.01`은 위험이 낮다는 예측입니다. 실제 운영에서는 비즈니스 정책과 calibration 검토를 거쳐 threshold를 정해야 하며, 이 숫자만으로 자동 의사결정을 해서는 안 됩니다.

Batch Transform의 모델 선택, 병렬 처리, 결과 파일 형식은 [Model Registration and Batch Inference](docs/model-promotion.ko.md)에서 설명합니다.

## 선택 실습

다음 실습은 핵심 흐름을 완료한 뒤 필요한 경우에만 실행합니다. 각 문서에서 목적, 비용, 실행 명령, 결과 확인 방법을 안내합니다.

| 실습 | 언제 실행하는가 | 상세 가이드 |
|---|---|---|
| MLflow autolog | SageMaker 컨테이너 안의 `xgboost.train()`과 자동 로깅을 보고 싶을 때 | [MLflow autolog 가이드](docs/mlflow-autolog.ko.md) |
| Automatic Model Tuning | 여러 하이퍼파라미터 조합을 SageMaker가 탐색하게 하고 싶을 때 | [Automatic Model Tuning 가이드](docs/automatic-model-tuning.ko.md) |
| 데이터 스키마와 CSV | feature, label-first CSV, `train`/`validation` channel을 자세히 볼 때 | [데이터 준비 가이드](docs/data-preparation.ko.md) |
| 아키텍처 | 로컬 프로세스, SageMaker, MLflow, Registry의 역할을 한눈에 볼 때 | [Architecture](docs/architecture.ko.md) |
| 문제 해결 | MLflow App, quota, Registry 승인, Batch Transform 오류가 있을 때 | [Troubleshooting](docs/troubleshooting.ko.md) |

## 소스 구성

| 파일 | 역할 |
|---|---|
| `data_prep.py` | 공개 데이터셋 다운로드, train/validation 분할, SageMaker용 CSV 생성 |
| `train_simple.py` | MLflow 없이 built-in XGBoost Training Job 1개 실행 |
| `train.py` | built-in XGBoost Training Job 2개 실행과 MLflow 기록 |
| `train_autolog.py` / `autolog.py` | script mode XGBoost 학습과 MLflow autolog |
| `train_tuner.py` | SageMaker Automatic Model Tuning 실행과 최적 결과 기록 |
| `batch_transform.py` | 최신 Approved 버전으로 Batch Transform 실행 |

## 리소스 정리

로컬에는 입력 데이터와 Batch Transform 예측 결과가, S3에는 학습 입력 데이터와 모델 아티팩트가 남습니다.

```text
artifacts/<run-id>/
artifacts/simple/<run-id>/
artifacts/batch/<run-id>/predictions.csv.out

s3://sagemaker-xgboost-lab-<account-id>-<region>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/
```

실습이 끝나면 먼저 MLflow App을 삭제합니다.

```bash
aws sagemaker list-mlflow-apps \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Summaries[?Name=='sagemaker-xgboost-lab'].[Arn,Status]" \
  --output table

aws sagemaker delete-mlflow-app \
  --arn '<mlflow-app-arn>' \
  --region "${AWS_REGION:-ap-northeast-2}"
```

S3 아티팩트가 더 이상 필요하지 않으면 버킷을 비운 뒤 CloudFormation stack을 삭제합니다. 현재 stack이 만든 빈 아티팩트 버킷은 stack과 함께 삭제됩니다. 이전 `Retain` 정책 때문에 남아 있다가 bootstrap이 재사용한 버킷은 별도로 삭제해야 합니다.

```bash
BUCKET_NAME="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue | [0]" \
  --output text)"
aws s3 rm "s3://${BUCKET_NAME}/" --recursive
aws cloudformation delete-stack \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}"
```
