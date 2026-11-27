[English](simple-training.md) | 한국어

# 간단하게 Built-in XGBoost 학습하기

`train_simple.py`는 SageMaker를 처음 도입할 때 가장 먼저 살펴볼 학습 예제입니다.
MLflow, Model Registry, 모델 다운로드, Batch Transform 연결을 모두 제외하고 다음 세
단계만 수행합니다.

```text
public dataset 준비와 S3 upload
  -> SageMaker built-in XGBoost Training Job
  -> validation AUC, Training Job name, model S3 URI 출력
```

## 실행

CloudFormation stack을 한 번 배포해 두었다면 MLflow App 상태와 관계없이 실행할 수
있습니다.

```bash
./scripts/run-simple-training.sh
```

기본 인스턴스는 `ml.c6i.xlarge`입니다.

```bash
./scripts/run-simple-training.sh --instance-type ml.m6i.xlarge
```

## 코드 읽는 순서

[`train_simple.py`](../src/sagemaker_xgboost_lab/train_simple.py)의 `run_training()`을
위에서 아래 순서로 읽습니다.

1. `prepare_training_data()`가 UCI 신용카드 연체 데이터를 SageMaker CSV 형식으로
   만들고 로컬에서 S3로 업로드합니다.
2. `image_uris.retrieve()`가 리전과 XGBoost 버전에 맞는 AWS built-in 이미지를
   선택합니다.
3. `ModelTrainer`가 학습 이미지, 실행 역할, 인스턴스, 하이퍼파라미터, 모델
   출력 S3 경로를 묶어 Training Job을 정의합니다.
4. `trainer.train()`이 `train`, `validation` S3 채널을 전달해 Job을 시작합니다.
5. Job이 끝나면 SageMaker가 계산한 `validation:auc`와 모델 아티팩트 위치를
   터미널에 출력합니다.

`train`과 `validation`은 S3 데이터를 구분하는 채널 이름입니다. 컨테이너 안에서는
각각 `/opt/ml/input/data/train/`과 `/opt/ml/input/data/validation/`에 마운트됩니다.
자세한 CSV 스키마는 [데이터 준비 가이드](data-preparation.ko.md)를 참고하세요.

## 고정 하이퍼파라미터

이 예제는 읽기 쉬운 XGBoost 설정 하나만 사용합니다.

| 하이퍼파라미터 | 값 |
|---|---:|
| `objective` | `binary:logistic` |
| `eval_metric` | `auc` |
| `tree_method` | `hist` |
| `num_round` | `300` |
| `max_depth` | `5` |
| `eta` | `0.08` |
| `subsample` | `0.9` |
| `colsample_bytree` | `0.9` |

이 값은 이 공개 데이터셋에 대한 운영 권장값이 아닙니다. built-in Training Job 한 번의
구조를 이해하기 위한 출발점입니다.

## 포함하지 않는 것

이 예제는 MLflow 실행, 데이터셋 lineage, Logged Model, Model Registry 등록, Batch
Transform을 만들지 않습니다. 따라서 SageMaker Training Job과 S3 모델 아티팩트를
먼저 확인하려는 경우에 적합합니다.

실험 비교에는 `train.py`, 반복별 자동 기록에는 `train_autolog.py`, 여러 하이퍼파라미터
조합 탐색에는 `train_tunner.py`를 사용합니다.
