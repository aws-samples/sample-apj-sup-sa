[English](mlflow-autolog.md) | 한국어

# MLflow autolog로 XGBoost 학습하기

이 예제는 SageMaker XGBoost container 안에서 사용자 Python script를 실행하고,
`mlflow.xgboost.autolog()`가 학습 정보를 자동으로 기록하는 흐름을 보여줍니다.

```bash
./scripts/run-autolog-training.sh
```

## built-in algorithm에서도 autolog를 쓸 수 있나?

SageMaker의 XGBoost container는 두 방식으로 사용할 수 있습니다.

| 방식 | 학습 code | MLflow autolog |
|---|---|---|
| built-in algorithm mode | AWS가 제공한 학습 code | 직접 호출할 위치가 없어 사용할 수 없음 |
| framework 또는 script mode | 사용자가 작성한 `xgboost.train()` | 학습 script에서 호출 가능 |

script mode도 AWS가 관리하는 SageMaker XGBoost image, Training Job, S3 input channel,
model output을 그대로 사용합니다. custom Docker image는 필요하지 않습니다. 달라지는 것은
container 안에서 AWS의 built-in 학습 code 대신 이 repository의
`autolog.py`를 실행한다는 점입니다.

기존 `train.py`는 built-in algorithm mode와 명시적 MLflow logging을 보여주므로
그대로 유지합니다. 두 예제를 함께 보면 다음 차이가 선명합니다.

- `train.py`: 학습은 built-in algorithm, MLflow 기록은 local code가 명시적으로 수행
- `train_autolog.py`: local code는 Job만 시작하고, cloud training script가 자동 기록

## 왜 XGBoost 3.0-5를 사용하나?

기존 built-in 예제는 XGBoost `1.7-1`입니다. 하지만 MLflow `3.10.1`의 XGBoost
autolog 공식 호환 범위는 XGBoost `2.1.0`부터 `3.2.0`까지입니다.

SageMaker가 framework mode로 공식 지원하는 버전 중 이 범위에 포함되는
`3.0-5`를 autolog 예제에만 사용합니다. 기존 예제의 결과나 model version을
갑자기 바꾸지 않기 위해 전체 프로젝트의 XGBoost version을 일괄 변경하지 않았습니다.

## 두 Python file의 역할

### `train_autolog.py`: local launcher

개인 PC에서 실행됩니다.

1. public dataset을 준비합니다.
2. train/validation CSV를 S3에 올립니다.
3. SDK v3 `SourceCode`로 training script와 requirements를 전달합니다.
4. SageMaker `ModelTrainer`로 Training Job을 실행합니다.

### `autolog.py`: cloud training script

SageMaker Training instance 안에서 실행됩니다.

1. `SM_CHANNEL_METADATA`에서 local data preparation이 만든 feature 순서를 읽습니다.
2. `SM_CHANNEL_TRAIN`, `SM_CHANNEL_VALIDATION`에서 CSV를 읽습니다.
3. managed MLflow App ARN을 tracking URI로 설정합니다.
4. `mlflow.xgboost.autolog()`를 켭니다.
5. `xgboost.train()`으로 학습합니다.
6. validation AUC가 가장 좋았던 model을 `SM_MODEL_DIR`에 저장합니다.

SageMaker가 `SM_CHANNEL_*`와 `SM_MODEL_DIR` 환경 변수를 자동으로 제공합니다.
`SM_MODEL_DIR` 아래 파일은 Job 종료 후 `model.tar.gz`로 압축되어 S3에 업로드됩니다.
input example과 signature를 수집할 수 있도록 autolog는 `DMatrix`를 만들기 전에
활성화합니다.

`data-summary.json`은 `data_prep.py`가 실제 feature 이름과 순서로 생성합니다.
따라서 cloud training script에 feature를 다시 하드코딩하지 않습니다.

## 자동으로 기록되는 항목

`xgboost.train()` 앞에서 autolog를 한 번 활성화하면 다음 항목이 MLflow run에
기록됩니다.

| MLflow 항목 | 예제에서 보이는 값 |
|---|---|
| Parameters | objective, eta, max depth, subsample 등 |
| Metrics | boosting round별 train AUC와 validation AUC |
| Artifacts | feature importance JSON과 plot |
| Logged Model | XGBoost model |
| Model metadata | input example과 inferred signature |

code에서 직접 추가하는 것은 SageMaker Training Job name, instance type, image URI,
dataset ID와 S3 URI 같은 SageMaker context tag입니다. model, XGBoost 학습 parameter,
반복별 metric은 autolog가 기록합니다. Early stopping의 최적 AUC와
`early_stopping_patience` 값만
run 간 비교가 쉽도록 최종 요약으로 추가합니다.

`validation-auc`는 기존 built-in Training과 AMT run에서도 동일한 이름을 사용합니다.
Early stopping이 끝나면 최적 AUC를 같은 metric의 마지막 point로 한 번 기록하므로
MLflow Compare에서 각 run의 최종 값을 바로 비교할 수 있습니다.

MLflow run의 tag에는 train CSV, validation CSV, data summary의 정확한 S3 URI도
남습니다. S3 versioning이나 별도 hash 없이도 SageMaker Training Job의 input channel과
MLflow run을 같은 URI로 연결할 수 있습니다.

## 실행

Infrastructure를 처음 만들었거나 이 예제를 추가한 뒤에는 Training role에
MLflow API 권한을 반영해야 합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-autolog-training.sh
```

다른 CPU Training instance를 사용하려면 다음처럼 실행합니다.

```bash
./scripts/run-autolog-training.sh --instance-type ml.m6i.xlarge
```

G4dn 또는 G5 instance를 지정하면 script가 SageMaker의 GPU 수를 확인하고 XGBoost
`device=cuda`를 사용합니다. 30,000행 sample은 CPU가 더 경제적입니다.

실행 중에는 다음 단계가 보입니다.

```text
[1/3] Prepare the public dataset and upload it to S3
[2/3] Run the script-mode SageMaker Training Job
[3/3] Training and MLflow autologging complete
```

완료 후 MLflow App에서 `sagemaker-xgboost-lab` experiment를 열고
`autolog-<run-id>` run을 선택합니다.

## startup에서 이 방식을 선택하는 기준

다음 조건이면 script mode와 autolog가 편합니다.

- 팀이 이미 open-source XGBoost code를 보유하고 있음
- parameter와 반복별 metric을 빠뜨리지 않고 기록하고 싶음
- 전처리나 custom evaluation을 training code 안에 넣어야 함
- custom image를 관리할 정도의 요구는 아직 없음

학습 code가 필요 없고 SageMaker 기본 CSV 학습만 빠르게 실행하려면 기존 built-in
algorithm mode가 더 짧습니다. autolog 하나만을 위해 모든 기존 Job을 script mode로
바꿀 필요는 없습니다.

## 알아둘 점

Training Job 시작 시 `autolog-requirements.txt`의 MLflow client와 AWS plugin을
설치합니다. 기본 인터넷 연결이 없는 private VPC에서는 private package repository를
사용하거나 dependency를 포함한 custom image가 필요합니다.

이 autolog 예제의 목적은 experiment tracking입니다. 기존 `train.py`와 `train_tunner.py`의
MLflow Logged Model에는 Batch Transform용 SageMaker inference specification도
명시적으로 기록합니다. 따라서 현재 repository의 Registry와 Batch Transform
실습은 기존 두 흐름을 계속 사용합니다.

## 공식 문서

- [SageMaker XGBoost를 framework로 사용](https://docs.aws.amazon.com/sagemaker/latest/dg/xgboost-how-to-use.html)
- [SageMaker XGBoost 지원 버전](https://docs.aws.amazon.com/sagemaker/latest/dg/xgboost.html)
- [managed MLflow client 연결](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-track-experiments.html)
- [MLflow App IAM 권한](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-app-setup-prerequisites-iam.html)
- [MLflow XGBoost autolog API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.xgboost.html#mlflow.xgboost.autolog)
