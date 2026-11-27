[English](mlflow-autolog.md) | 한국어

# MLflow autolog로 XGBoost 학습하기

이 예제는 SageMaker XGBoost 컨테이너 안에서 사용자 Python 스크립트를 실행하고,
`mlflow.xgboost.autolog()`로 학습 정보를 자동 기록하는 흐름을 보여 줍니다.

```bash
./scripts/run-autolog-training.sh
```

## built-in algorithm에서도 autolog를 사용할 수 있나요?

SageMaker의 XGBoost 컨테이너는 두 가지 방식으로 사용할 수 있습니다.

| 방식 | 학습 코드 | MLflow autolog |
|---|---|---|
| built-in algorithm mode | AWS가 제공한 학습 코드 | 직접 호출할 위치가 없어 사용 불가 |
| framework 또는 script mode | 사용자가 작성한 `xgboost.train()` | 학습 스크립트에서 호출 가능 |

script mode에서도 AWS가 관리하는 SageMaker XGBoost 이미지, Training Job, S3 입력 채널,
모델 출력을 그대로 사용합니다. 커스텀 Docker 이미지는 필요하지 않습니다. 컨테이너 안에서
AWS의 built-in 학습 코드 대신 이 리포지토리의 `autolog.py`를 실행한다는 점만 다릅니다.

기존 `train.py`는 built-in algorithm mode와 명시적인 MLflow 로깅을 보여 주는 예제이므로
그대로 유지합니다. 두 예제의 차이는 다음과 같습니다.

- `train.py`: 학습은 built-in algorithm이 수행하고, MLflow 기록은 로컬 코드가 명시적으로 수행
- `train_autolog.py`: 로컬 코드는 작업만 시작하고, 클라우드의 학습 스크립트가 자동으로 기록

## 왜 XGBoost 3.0-5를 사용하나요?

기존 built-in 예제는 XGBoost `1.7-1`을 사용합니다. 반면 MLflow `3.16.0`의 XGBoost
autolog가 공식 지원하는 범위는 XGBoost `2.1.2`부터 `3.4.1`까지입니다.

따라서 SageMaker가 framework mode로 공식 지원하는 버전 중 이 범위에 속하는
`3.0-5`를 autolog 예제에만 사용합니다. 기존 예제의 결과나 모델 버전이
바뀌지 않도록 프로젝트 전체의 XGBoost 버전은 일괄 변경하지 않았습니다.

## 두 Python 파일의 역할

### `train_autolog.py`: 로컬 실행기

개인 PC에서 실행됩니다.

1. 공개 데이터셋을 준비합니다.
2. train/validation CSV를 S3에 업로드합니다.
3. SDK v3 `SourceCode`로 학습 스크립트와 requirements를 전달합니다.
4. SageMaker `ModelTrainer`로 Training Job을 실행합니다.

### `autolog.py`: 클라우드 학습 스크립트

SageMaker Training 인스턴스 안에서 실행됩니다.

1. `SM_CHANNEL_METADATA`에서 로컬 데이터 준비 단계가 만든 feature 순서를 읽습니다.
2. `SM_CHANNEL_TRAIN`, `SM_CHANNEL_VALIDATION`에서 CSV를 읽습니다.
3. managed MLflow App ARN을 tracking URI로 설정합니다.
4. `mlflow.xgboost.autolog()`를 활성화합니다.
5. `xgboost.train()`으로 학습합니다.
6. validation AUC가 가장 좋았던 모델을 `SM_MODEL_DIR`에 저장합니다.

`SM_CHANNEL_*`와 `SM_MODEL_DIR` 환경 변수는 SageMaker가 자동으로 제공합니다.
`SM_MODEL_DIR` 아래의 파일은 작업이 끝난 뒤 `model.tar.gz`로 압축되어 S3에 업로드됩니다.
autolog가 input example과 signature를 수집할 수 있도록 `DMatrix`를 만들기 전에
autolog를 활성화합니다.

`data-summary.json`은 `data_prep.py`가 실제 feature 이름과 순서를 담아 생성합니다.
따라서 클라우드 학습 스크립트에 feature를 다시 하드코딩하지 않습니다.

## 자동으로 기록되는 항목

`xgboost.train()`을 호출하기 전에 autolog를 한 번 활성화하면 다음 항목이 MLflow 실행에
기록됩니다.

| MLflow 항목 | 예제에서 보이는 값 |
|---|---|
| Parameters | objective, eta, max depth, subsample 등 |
| Metrics | boosting round별 train AUC와 validation AUC |
| Artifacts | feature importance JSON과 plot |
| Logged Model | XGBoost 모델 |
| Model metadata | input example과 추론된 signature |

코드에서는 SageMaker Training Job 이름, 인스턴스 유형, 이미지 URI, 데이터셋 ID, S3 URI 같은
SageMaker 컨텍스트 태그를 직접 추가합니다. 모델, XGBoost 학습 파라미터, 반복별 지표는
autolog가 기록합니다. 실행 간 비교가 쉽도록 early stopping의 최적 AUC와
`early_stopping_patience` 값만 최종 요약으로 추가합니다.

`validation-auc`는 기존 built-in Training과 AMT 실행에서도 같은 이름을 사용합니다.
early stopping이 끝나면 최적 AUC를 같은 지표의 마지막 값으로 한 번 더 기록하므로
MLflow Compare에서 각 실행의 최종 값을 바로 비교할 수 있습니다.

MLflow 실행의 태그에는 train CSV, validation CSV, data summary의 정확한 S3 URI도
기록됩니다. 따라서 S3 버전 관리나 별도의 해시 없이도 SageMaker Training Job의 입력 채널과
MLflow 실행을 같은 URI로 연결할 수 있습니다.

## 실행

인프라를 처음 만들었거나 이 예제를 추가했다면 Training 역할에
MLflow API 권한을 반영해야 합니다.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-autolog-training.sh
```

다른 CPU Training 인스턴스를 사용하려면 다음과 같이 실행합니다.

```bash
./scripts/run-autolog-training.sh --instance-type ml.m6i.xlarge
```

G4dn 또는 G5 인스턴스를 지정하면 스크립트가 SageMaker의 GPU 수를 확인하고 XGBoost에서
`device=cuda`를 사용합니다. 30,000행 규모의 샘플에서는 CPU가 더 경제적입니다.

실행 중에는 다음 단계가 표시됩니다.

```text
[1/3] Prepare the public dataset and upload it to S3
[2/3] Run the script-mode SageMaker Training Job
[3/3] Training and MLflow autologging complete
```

완료되면 MLflow App에서 `sagemaker-xgboost-lab` experiment를 열고
`autolog-<run-id>` 실행을 선택합니다.

## 스타트업에서 이 방식을 선택하는 기준

다음 조건에 해당하면 script mode와 autolog가 편리합니다.

- 팀이 이미 오픈소스 XGBoost 코드를 보유하고 있음
- 파라미터와 반복별 지표를 빠짐없이 기록해야 함
- 전처리나 커스텀 평가를 학습 코드 안에 넣어야 함
- 커스텀 이미지를 관리해야 할 만큼의 요구 사항은 아직 없음

학습 코드를 작성할 필요 없이 SageMaker 기본 CSV 학습만 빠르게 실행하려면 기존 built-in
algorithm mode가 더 간단합니다. autolog만을 위해 기존 작업을 모두 script mode로
전환할 필요는 없습니다.

## 알아둘 점

Training Job이 시작될 때 `autolog-requirements.txt`에 있는 MLflow 클라이언트와 AWS 플러그인을
설치합니다. 기본 인터넷 연결이 없는 private VPC에서는 private 패키지 리포지토리를
사용하거나 의존성을 포함한 커스텀 이미지가 필요합니다.

이 autolog 예제의 목적은 experiment tracking입니다. 기존 `train.py`와 `train_tuner.py`는
MLflow Logged Model에 Batch Transform용 SageMaker inference specification도
명시적으로 기록합니다. 따라서 현재 리포지토리의 Registry와 Batch Transform
실습에서는 기존 두 흐름을 계속 사용합니다.

## 공식 문서

- [SageMaker XGBoost를 framework로 사용](https://docs.aws.amazon.com/sagemaker/latest/dg/xgboost-how-to-use.html)
- [SageMaker XGBoost 지원 버전](https://docs.aws.amazon.com/sagemaker/latest/dg/xgboost.html)
- [managed MLflow client 연결](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-track-experiments.html)
- [MLflow App IAM 권한](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-app-setup-prerequisites-iam.html)
- [MLflow XGBoost autolog API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.xgboost.html#mlflow.xgboost.autolog)
