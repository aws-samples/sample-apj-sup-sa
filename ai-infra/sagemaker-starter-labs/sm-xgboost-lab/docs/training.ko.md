[English](training.md) | 한국어

# Training 실행 흐름

다음 command 하나가 전체 Python 흐름을 시작합니다.

```bash
./scripts/run-training.sh
```

`run-training.sh`는 CloudFormation output에서 S3 bucket과 Training role을 읽고, 준비된 MLflow App ARN을 찾습니다. 이 값을 Python process에 전달한 뒤 `sagemaker-xgboost-lab` CLI를 실행합니다.

## 1. MLflow App 연결 확인

`train.py`는 SageMaker Training 비용이 발생하기 전에 MLflow App을 tracking URI로
설정하고 experiment를 준비합니다. 연결에 실패하면 Training Job을 만들기 전에
종료됩니다. Infrastructure 설정이 맞지 않으면 다음 command로 App을 다시 준비합니다.

```bash
./scripts/bootstrap-infra.sh
```

## 2. Public dataset 준비 및 S3 upload

`data_prep.py`가 UCI Default of Credit Card Clients dataset을 내려받습니다. 23개
feature의 schema와 다음 달 연체 label을 확인하고 train 80%, validation 20%로
나눕니다. class별로 sampling하므로 두 split의 연체 비율은 약 22.1%로 유지되며,
같은 random seed를 사용하면 split도 같습니다.

SageMaker built-in XGBoost의 CSV input은 label이 첫 column에 있어야 하고 header가 없어야 합니다. Python code가 이 형식으로 `train.csv`와 `validation.csv`를 만든 뒤 run별 S3 prefix에 올립니다.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/data/
```

SageMaker Training Job은 이 S3 URI를 `train`과 `validation` channel로 읽습니다.

dataset schema, split 전략, Training과 Batch Transform CSV의 차이는 [Data 준비 가이드](data-preparation.ko.md)에 설명되어 있습니다.

## 3. Training Job 두 개 실행

`train.py`에는 비교하기 쉬운 두 설정이 code로 들어 있습니다.

| Run | 차이 |
|---|---|
| `default` | 차이를 쉽게 확인하도록 depth와 boosting round를 의도적으로 작게 제한한 모델 |
| `tuned` | 더 깊은 tree, 작은 learning rate, 더 많은 boosting round |

각 설정마다 SDK v3 `ModelTrainer`가 별도 SageMaker Training Job을 만듭니다. `wait=True`, `logs=True`이므로 Job이 끝날 때까지 기다리고 Training log를 terminal에 표시합니다.

`default`는 일반적인 XGBoost 기본값이나 권장 설정을 의미하지 않습니다. 개선 전후의
차이를 확인하기 위해 의도적으로 학습량을 제한한 demo baseline입니다.

CPU instance에서는 `tree_method=hist`를 사용합니다. `ml.g4dn.*` 또는 `ml.g5.*`를 선택하면 `tree_method=gpu_hist`로 바뀝니다.

## 4. 평가와 MLflow 기록

Training이 끝나면 XGBoost container가 기록한 `validation:auc`를 완료된 Training
Job에서 읽어 MLflow의 `validation-auc`로 기록합니다. SageMaker가 만든
`model.tar.gz`는 MLflow Logged Model을 만들기 위해 local XGBoost `Booster`로 엽니다.

각 candidate는 MLflow에서 독립된 run입니다.

```text
default-<run-id>
tuned-<run-id>
```

각 run에는 다음 정보가 남습니다.

- XGBoost hyperparameter
- SageMaker image URI와 instance type
- SageMaker Training Job의 validation AUC
- SageMaker Training Job name
- model artifact S3 URI
- dataset summary JSON

마지막으로 terminal에 두 run의 결과와 validation AUC가 가장 높은 run을 출력합니다. 이 선택은 학습을 위한 단순 비교이며 production model 승인이나 배포를 의미하지 않습니다.

여러 hyperparameter 조합을 SageMaker가 자동 탐색하는 흐름은
[Automatic Model Tuning 가이드](automatic-model-tuning.ko.md)를 참고하세요.
