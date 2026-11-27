[English](training.md) | 한국어

# 학습 실행 흐름

다음 명령 하나로 전체 Python 흐름이 시작됩니다.

```bash
./scripts/run-training.sh
```

`run-training.sh`는 CloudFormation 출력에서 S3 버킷과 학습 역할을 읽고, 준비된 MLflow App ARN을 찾습니다. 이 값을 Python 프로세스에 전달한 뒤 `sagemaker-xgboost-lab` CLI를 실행합니다.

## 1. MLflow App 연결 확인

`train.py`는 SageMaker 학습 비용이 발생하기 전에 MLflow App을 tracking URI로
설정하고 experiment를 준비합니다. 연결에 실패하면 Training Job을 만들기 전에
종료됩니다. 인프라 설정이 맞지 않으면 다음 명령으로 App을 다시 준비합니다.

```bash
./scripts/bootstrap-infra.sh
```

## 2. 공개 데이터셋 준비 및 S3 업로드

`data_prep.py`가 UCI Default of Credit Card Clients 데이터셋을 다운로드합니다. 23개
feature의 스키마와 다음 달 연체 레이블을 확인하고 train 80%, validation 20%로
분할합니다. 클래스별로 샘플링하므로 두 분할의 연체 비율은 약 22.1%로 유지되며,
같은 random seed를 사용하면 분할 결과도 같습니다.

SageMaker built-in XGBoost의 CSV 입력은 레이블이 첫 번째 열에 있어야 하고 헤더가 없어야 합니다. Python 코드가 이 형식으로 `train.csv`와 `validation.csv`를 만든 뒤 실행별 S3 prefix에 올립니다.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/data/
```

SageMaker Training Job은 이 S3 URI를 `train`과 `validation` 채널로 읽습니다.

데이터셋 스키마, 분할 전략, 학습용 CSV와 Batch Transform용 CSV의 차이는 [데이터 준비 가이드](data-preparation.ko.md)에서 설명합니다.

## 3. Training Job 두 개 실행

`train.py`에는 비교하기 쉬운 두 설정이 코드로 들어 있습니다.

| Run | 차이 |
|---|---|
| `default` | 차이를 쉽게 확인하도록 depth와 boosting round를 의도적으로 작게 제한한 모델 |
| `tuned` | 더 깊은 tree, 작은 learning rate, 더 많은 boosting round |

설정마다 SDK v3 `ModelTrainer`가 별도의 SageMaker Training Job을 만듭니다. `wait=True`, `logs=True`이므로 Job이 끝날 때까지 기다리며 학습 로그를 터미널에 표시합니다.

`default`는 일반적인 XGBoost 기본값이나 권장 설정이 아닙니다. 개선 전후의
차이를 확인하기 위해 의도적으로 학습량을 제한한 데모용 baseline입니다.

CPU 인스턴스에서는 `tree_method=hist`를 사용합니다. `ml.g4dn.*` 또는 `ml.g5.*`를 선택하면 `tree_method=gpu_hist`로 바뀝니다.

## 4. 평가와 MLflow 기록

학습이 끝나면 XGBoost 컨테이너가 기록한 `validation:auc`를 완료된 Training
Job에서 읽어 MLflow의 `validation-auc`로 기록합니다. SageMaker가 만든
`model.tar.gz`는 MLflow Logged Model을 만들기 위해 로컬 XGBoost `Booster`로 엽니다.

각 후보는 MLflow에서 독립된 실행(run)입니다.

```text
default-<run-id>
tuned-<run-id>
```

각 실행에는 다음 정보가 남습니다.

- XGBoost 하이퍼파라미터
- SageMaker 이미지 URI와 인스턴스 유형
- SageMaker Training Job의 validation AUC
- SageMaker Training Job 이름
- 모델 아티팩트 S3 URI
- 데이터셋 요약 JSON

마지막으로 터미널에 두 실행의 결과와 validation AUC가 가장 높은 실행을 출력합니다. 이 선택은 학습 목적의 단순 비교이며, 프로덕션 모델 승인이나 배포를 의미하지 않습니다.

SageMaker가 여러 하이퍼파라미터 조합을 자동으로 탐색하는 흐름은
[Automatic Model Tuning 가이드](automatic-model-tuning.ko.md)를 참고하세요.
