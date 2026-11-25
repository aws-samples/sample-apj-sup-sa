[English](simple-training.md) | 한국어

# 간단하게 Built-in XGBoost 학습하기

`train_simple.py`는 SageMaker를 처음 적용할 때 가장 먼저 읽을 수 있는 학습 예제입니다.
MLflow, Model Registry, model download와 Batch Transform 연결을 모두 제외하고 다음 세
단계만 수행합니다.

```text
public dataset 준비와 S3 upload
  -> SageMaker built-in XGBoost Training Job
  -> validation AUC, Training Job name, model S3 URI 출력
```

## 실행

CloudFormation stack이 한 번 배포되어 있다면 MLflow App 상태와 관계없이 실행할 수
있습니다.

```bash
./scripts/run-simple-training.sh
```

기본 instance는 `ml.c6i.xlarge`입니다.

```bash
./scripts/run-simple-training.sh --instance-type ml.m6i.xlarge
```

## Code 읽는 순서

[`train_simple.py`](../src/sagemaker_xgboost_lab/train_simple.py)의 `run_training()`을
위에서 아래로 읽으면 됩니다.

1. `prepare_training_data()`가 UCI 신용카드 연체 데이터를 SageMaker CSV 형식으로
   만들고 local에서 S3로 upload합니다.
2. `image_uris.retrieve()`가 Region과 XGBoost version에 맞는 AWS built-in image를
   선택합니다.
3. `ModelTrainer`가 Training image, execution role, instance, hyperparameter와 model
   output S3 path를 묶어 Training Job을 정의합니다.
4. `trainer.train()`이 `train`, `validation` S3 channel을 전달해 Job을 시작합니다.
5. Job이 끝나면 SageMaker가 계산한 `validation:auc`와 model artifact location을
   terminal에 출력합니다.

`train`과 `validation`은 S3 data를 구분하는 channel 이름입니다. container 안에서는
각각 `/opt/ml/input/data/train/`과 `/opt/ml/input/data/validation/`에 mount됩니다.
자세한 CSV schema는 [Data 준비 가이드](data-preparation.ko.md)를 참고하세요.

## 고정 Hyperparameter

이 예제는 하나의 읽기 쉬운 XGBoost 설정만 사용합니다.

| Hyperparameter | 값 |
|---|---:|
| `objective` | `binary:logistic` |
| `eval_metric` | `auc` |
| `tree_method` | `hist` |
| `num_round` | `300` |
| `max_depth` | `5` |
| `eta` | `0.08` |
| `subsample` | `0.9` |
| `colsample_bytree` | `0.9` |

이 값은 이 공개 dataset을 위한 운영 권장값이 아닙니다. 한 번의 built-in Training Job
구조를 이해하기 위한 출발점입니다.

## 포함하지 않는 것

이 예제는 MLflow run, dataset lineage, Logged Model, Model Registry 등록과 Batch
Transform을 만들지 않습니다. 따라서 SageMaker Training Job과 S3 model artifact를
먼저 확인하려는 경우에 적합합니다.

실험 비교가 필요해지면 `train.py`, 반복별 자동 기록이 필요하면 `train_autolog.py`,
여러 hyperparameter 조합을 찾으려면 `train_tunner.py`로 넘어가면 됩니다.
