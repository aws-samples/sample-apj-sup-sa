[English](README.md) | 한국어

# SageMaker Credit Default XGBoost + MLflow

[UCI Default of Credit Card Clients](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients) 공개 dataset으로 SageMaker XGBoost를 학습하고, managed MLflow에서 결과를 비교한 뒤 SageMaker Model Registry 승인과 Batch Transform까지 경험하는 실습입니다.

이 repository의 핵심 경로는 다음과 같습니다.

```text
clone
  -> Infrastructure 생성
  -> Built-in XGBoost 단일 학습
  -> MLflow에 두 설정을 기록하고 비교
  -> 선택한 model을 SageMaker Model Registry에 등록하고 Approved로 변경
  -> Batch Transform으로 probability 예측
```

모델 Training부터 inference까지의 가장 작은 운영 흐름을 이해하는 것이 목적입니다.

## 비용

이 실습은 사용한 만큼 비용이 발생합니다. SageMaker Training Job, managed MLflow App, Batch Transform 실행 시간과 S3 artifact 저장량이 과금 대상입니다. 먼저 핵심 흐름만 실행하고, Automatic Model Tuning은 필요할 때만 추가로 실행하세요. 실습 후에는 [리소스 정리](#리소스-정리) 절차로 MLflow App과 artifact를 삭제합니다.

예상 비용은 해당 Region의 [AWS Pricing Calculator](https://calculator.aws/)에서 다음처럼 계산합니다.

- Training: instance 시간당 요금 × Job 실행 시간. 기본 흐름은 simple 1개와 MLflow Training 2개입니다.
- MLflow: Tracking Server 시간당 요금 × App을 유지한 시간입니다.
- Batch Transform: `ml.c5.xlarge` 시간당 요금 × Job 실행 시간입니다.
- S3: artifact 저장 용량(GB-month) × 보관 기간입니다.

Automatic Model Tuning을 실행하면 완료된 trial 수만큼 Training 비용이 추가됩니다.

## 이 예제에서 만드는 것

- SageMaker SDK v3 `ModelTrainer`가 시작하는 XGBoost Training Job
- UCI Default of Credit Card Clients의 다음 달 연체 여부 예측 model
- managed MLflow의 experiment, run, metric, parameter, dataset lineage
- MLflow에서 선택해 SageMaker Model Registry에 등록한 model version
- 최신 `Approved` model로 실행하는 Batch Transform Job

dataset은 30,000행의 숫자형 table입니다. 신용 한도, 최근 6개월의 상환 상태·청구 금액·납부 금액, 인구통계 정보 등 23개 항목으로 다음 달 연체 여부를 예측합니다. `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`는 학습 경험을 위해 포함하지만, 실제 신용 의사결정에 사용하려면 규제·공정성 검토와 집단별 성능 평가가 선행되어야 합니다.

## 시작 전 준비

- Python 3.10 이상
- [uv](https://docs.astral.sh/uv/)
- AWS CLI와 설정된 AWS credential
- CloudFormation, IAM, S3, SageMaker AI를 사용할 권한
- SageMaker Studio access와 Studio 생성·실행 권한

AWS account와 Region을 먼저 확인합니다. 이 예제의 기본 Region은 서울 `ap-northeast-2`입니다.

```bash
aws sts get-caller-identity
aws configure get region
```

권한과 생성되는 AWS resource는 [Infrastructure 배포](infra/README.ko.md)를 참고하세요. Training Job, MLflow App, S3 저장소, Batch Transform에는 비용이 발생할 수 있습니다.

## 1. Clone과 local 환경 준비

GitHub repository 주소를 사용해 clone합니다.

```bash
git clone <repository-url>
cd sm-xgboost-lab
```

`uv`가 project dependency와 개발 도구를 설치합니다. unit test는 AWS resource를 생성하지 않습니다.

```bash
uv sync --extra dev
uv run python -m pytest
```

## 2. Infrastructure 생성

한 번만 실행합니다. S3 artifact bucket, SageMaker Training execution role, managed MLflow App에 필요한 role을 생성하고 MLflow App이 준비될 때까지 기다립니다.

```bash
./scripts/bootstrap-infra.sh
```

bucket 이름은 `sagemaker-xgboost-lab-<account-id>` 형식으로 자동 생성됩니다. script가 CloudFormation output을 이후 실행에 전달하므로 bucket 이름이나 role ARN을 직접 `export`할 필요가 없습니다.

## 3. MLflow 없이 Built-in XGBoost를 한 번 실행

먼저 아래 예제로 SageMaker Training Job의 가장 작은 흐름을 확인합니다.

```bash
./scripts/run-simple-training.sh
```

이 command는 public dataset을 local에서 준비해 S3에 upload하고, 하나의 SageMaker built-in XGBoost Training Job을 실행합니다. 완료되면 terminal에 `Validation AUC`, Training Job 이름, model artifact의 S3 URI가 출력됩니다.

이 단계는 MLflow App ARN을 읽지 않고 MLflow run이나 Registry version도 만들지 않습니다. 코드와 `train`/`validation` channel의 의미는 [간단하게 Built-in XGBoost 학습하기](docs/simple-training.ko.md)에서 확인하세요.

## 4. MLflow에 기록되는 Training 실행

이제 두 개의 built-in XGBoost 설정을 같은 validation dataset으로 실행하고, 결과를 managed MLflow에 기록합니다.

```bash
./scripts/run-training.sh
```

기본 CPU instance는 `ml.c6i.xlarge`입니다. 이 작은 공개 dataset에는 충분합니다. 실행이 끝날 때까지 기다리면 `default`와 `tuned` Training Job, 각각의 `validation-auc`, model S3 URI가 출력됩니다.

```text
[1/4] Connect to the MLflow App
[2/4] Prepare the public dataset and upload it to S3
[3/4] SageMaker Training Job 1/2: default
[3/4] SageMaker Training Job 2/2: tuned
[4/4] Complete MLflow logging and result comparison
```

이 예제에서 `default`는 개선 효과를 쉽게 보기 위해 의도적으로 작게 만든 baseline입니다. 실제 문제에서는 AUC뿐 아니라 별도 test 또는 out-of-time dataset과 business metric으로 model을 선택해야 합니다. Code 흐름은 [Training 실행 흐름](docs/training.ko.md)에 설명되어 있습니다.

## 5. MLflow에서 결과 비교

1. AWS Console에서 Region을 `ap-northeast-2`로 선택합니다.
2. **Amazon SageMaker AI → SageMaker Studio**로 이동합니다. Studio가 없으면 **Create SageMaker Studio**를 완료합니다.
3. 생성된 Studio에서 **Open Studio**를 선택합니다.
4. Studio 왼쪽 navigation에서 **MLflow** icon을 선택하고 `sagemaker-xgboost-lab` App을 엽니다.
5. **Open MLflow**를 선택한 뒤 왼쪽 위에서 **Model training**을 선택합니다.
6. `sagemaker-xgboost-lab` experiment를 열고, 같은 run ID suffix를 가진 완료된 `default`와 `tuned` run을 선택해 **Compare**를 누릅니다.
7. `validation-auc`, XGBoost parameter, Training Job name, model S3 URI를 비교합니다.

`validation-auc`는 validation data에서 연체와 비연체를 구분하는 순위 성능입니다. `1.0`에 가까울수록 좋고, `0.5`는 무작위 추측 수준입니다. 상세 화면 안내와 metric 해석은 [MLflow 결과 확인 가이드](docs/mlflow-guide.ko.md)를 참고하세요.

## 6. 선택한 model을 Registry에 등록하고 승인

MLflow는 모든 실험 결과를 보관하고, SageMaker Model Registry에는 실제 inference 후보만 넣습니다.

1. MLflow에서 선택한 run을 열고 **Artifacts → model**로 이동합니다.
2. **Register model**을 선택합니다.
3. model name에 `sagemaker-xgboost-lab`을 입력하고 등록합니다.
4. SageMaker Studio에서 **JumpStart / Models → My models → Logged**로 이동합니다.
5. `sagemaker-xgboost-lab-`로 시작하는 model group과 새 version을 엽니다.
6. **Pending Approval** dropdown을 **Approved**로 변경합니다.

MLflow에서 **Register model**을 실행하면 MLflow Model Registry와 SageMaker Model Registry에 같은 version이 생성됩니다. `Approved`는 endpoint를 자동 생성하지 않습니다. 이 예제에서는 Batch Transform이 사용해도 되는 model version을 고르는 명시적인 사람의 승인 단계입니다.

Studio 화면과 승인 상태의 의미는 [Model Registration and Batch Inference](docs/model-promotion.ko.md)를 참고하세요.

## 7. Batch Transform으로 예측 확인

최소 한 개의 model version을 `Approved`로 변경한 뒤 실행합니다.

```bash
./scripts/run-batch-inference.sh
```

script는 MLflow가 만든 Model Package Group에서 Batch Transform instance와 호환되는 최신 `Approved` version을 찾습니다. label을 제외한 sample CSV를 S3에 올리고, `ml.c5.xlarge` Batch Transform Job을 실행한 뒤 probability 예측을 다음 local file에 내려받습니다.

```text
artifacts/batch/<run-id>/predictions.csv.out
```

terminal에는 결과의 처음 5개만 preview로 출력됩니다. 값은 실행한 model에 따라 달라집니다.

```text
Predictions: artifacts/batch/<run-id>/predictions.csv.out
First predicted probabilities of default next month:
- 0.16083382070064545
- 0.1926967203617096
- 0.12307227402925491
- 0.15535558760166168
- 0.03271584212779999
```

전체 결과 파일에는 validation input과 같은 순서의 6,000개 probability가 저장됩니다. 각 줄의 `0.0`부터 `1.0` 사이 숫자는 해당 고객이 다음 달 연체할 확률입니다. 예를 들어 `0.99`는 높은 연체 위험 예측이고, `0.01`은 낮은 위험 예측입니다. 실제 운영에서는 business policy와 calibration 검토를 거쳐 threshold를 정해야 하며, 이 숫자만으로 자동 의사결정을 하면 안 됩니다.

Batch Transform의 model 선택, 병렬 처리와 결과 file 형식은 [Model Registration and Batch Inference](docs/model-promotion.ko.md)에 설명되어 있습니다.

## 선택 실습

아래는 핵심 흐름을 완료한 뒤 필요할 때만 실행하면 됩니다. 각 문서에는 목적, 비용, 실행 command와 결과 확인 방법이 있습니다.

| 실습 | 언제 실행하는가 | 상세 가이드 |
|---|---|---|
| MLflow autolog | SageMaker container 안의 `xgboost.train()`과 자동 logging을 보고 싶을 때 | [MLflow autolog 가이드](docs/mlflow-autolog.ko.md) |
| Automatic Model Tuning | 여러 hyperparameter 조합을 SageMaker가 탐색하도록 하고 싶을 때 | [Automatic Model Tuning 가이드](docs/automatic-model-tuning.ko.md) |
| Data schema와 CSV | feature, label-first CSV, `train`/`validation` channel을 자세히 볼 때 | [Data 준비 가이드](docs/data-preparation.ko.md) |
| Architecture | local process, SageMaker, MLflow, Registry의 역할을 한 장으로 볼 때 | [Architecture](docs/architecture.ko.md) |
| 문제 해결 | MLflow App, quota, Registry approval, Batch Transform 오류가 있을 때 | [Troubleshooting](docs/troubleshooting.ko.md) |

## Source 구성

| File | 역할 |
|---|---|
| `data_prep.py` | public dataset download, train/validation split, SageMaker CSV 생성 |
| `train_simple.py` | MLflow 없이 한 개의 built-in XGBoost Training Job 실행 |
| `train.py` | 두 built-in XGBoost Training Job 실행과 MLflow 기록 |
| `train_autolog.py` / `autolog.py` | script mode XGBoost Training과 MLflow autolog |
| `train_tunner.py` | SageMaker Automatic Model Tuning 실행과 최적 결과 기록 |
| `batch_transform.py` | 최신 Approved version으로 Batch Transform 실행 |

## 리소스 정리

local에는 input data와 Batch Transform prediction이, S3에는 Training input과 model artifact가 남습니다.

```text
artifacts/<run-id>/
artifacts/simple/<run-id>/
artifacts/batch/<run-id>/predictions.csv.out

s3://sagemaker-xgboost-lab-<account-id>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/
```

실습이 끝나면 먼저 MLflow App을 삭제합니다.

```bash
aws sagemaker list-mlflow-apps \
  --region ap-northeast-2 \
  --query "Summaries[?Name=='sagemaker-xgboost-lab'].[Arn,Status]" \
  --output table

aws sagemaker delete-mlflow-app \
  --arn '<mlflow-app-arn>' \
  --region ap-northeast-2
```

S3 artifact가 더 이상 필요하지 않으면 bucket을 비운 뒤 CloudFormation stack을 삭제합니다. 현재 stack이 만든 빈 artifact bucket은 stack과 함께 삭제됩니다. 이전 `Retain` 정책에서 남아 bootstrap이 재사용한 bucket은 별도로 삭제합니다.

```bash
aws s3 rm "s3://sagemaker-xgboost-lab-<account-id>/" --recursive
aws cloudformation delete-stack \
  --stack-name sagemaker-xgboost-lab \
  --region ap-northeast-2
```
