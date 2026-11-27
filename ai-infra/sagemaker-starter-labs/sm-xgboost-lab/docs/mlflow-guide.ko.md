[English](mlflow-guide.md) | 한국어

# MLflow 결과 확인 가이드

이 문서는 학습이 끝난 뒤 managed MLflow를 열고 `default`와 `tuned` 실행을 찾아 결과를 비교하는 과정을 순서대로 설명합니다.

이 예제에서 MLflow는 모델을 직접 학습하거나 배포하지 않습니다. MLflow는 학습 설정과 결과, 그리고 각 실행에 연결된 SageMaker Training Job과 모델 아티팩트를 한곳에서 확인하는 experiment tracking 도구입니다.

## 먼저 알아둘 구조

MLflow 화면은 다음 세 계층으로 구성됩니다.

```text
MLflow App
  -> Experiment
      -> Run
```

| 단계 | 이 예제의 값 | 의미 |
|---|---|---|
| MLflow App | `sagemaker-xgboost-lab` | MLflow UI와 메타데이터를 제공하는 managed App |
| Experiment | `sagemaker-xgboost-lab` | 같은 목적의 학습 결과를 모아 두는 공간 |
| Run | `default-<run-id>`, `tuned-<run-id>` | 하이퍼파라미터 설정 하나로 실행한 결과 |
| AMT Run | `amt-<run-id>` | Automatic Model Tuning에서 선택한 최적 trial |

`./scripts/run-training.sh`를 한 번 실행하면 같은 `<run-id>`를 가진 `default`와 `tuned` 실행이 하나씩 만들어집니다. 두 설정을 비교할 때는 이름 뒤의 `<run-id>`가 같은 실행을 선택해야 합니다.

`./scripts/run-tuning.sh`는 여러 SageMaker Training Job을 실행한 뒤 최적 trial을
`amt-<run-id>` 실행으로 기록합니다. 전체 trial은 SageMaker Hyperparameter tuning jobs
화면에서 볼 수 있고, MLflow의 `tuning/trials.json` 아티팩트에서도 요약을 확인할 수 있습니다.

다음 두 실행이 한 쌍의 예입니다.

```text
default-20260803-055035-9139f1
tuned-20260803-055035-9139f1
```

## 1. MLflow App 열기

1. AWS Console에서 Region을 `ap-northeast-2`로 선택합니다.
2. **Amazon SageMaker AI → SageMaker Studio**로 이동합니다. Studio가 없으면 **Create SageMaker Studio**를 완료합니다.
3. 생성된 Studio에서 **Open Studio**를 선택합니다.
4. Studio 왼쪽 탐색 창에서 **MLflow** 아이콘을 선택합니다.
5. `sagemaker-xgboost-lab` App을 선택하고 **Open MLflow**를 누릅니다.
6. MLflow가 열리면 왼쪽 위에서 **Model training**을 선택합니다.

MLflow 3 화면은 처음에 **GenAI** 모드로 열릴 수 있습니다. 이 예제는 일반적인 모델 학습 결과를 기록하므로 반드시 **Model training**으로 전환합니다.

Home 화면의 **Recent Experiments**에서 `sagemaker-xgboost-lab`을 선택하거나, 왼쪽의 **Experiments**에서 같은 이름을 선택합니다.

## 2. Runs 목록 읽기

Experiment를 열면 **Runs** 화면에 지금까지 실행한 결과가 시간순으로 표시됩니다.

먼저 다음 항목을 확인합니다.

| 화면 항목 | 확인할 내용 |
|---|---|
| Run Name | 같은 `<run-id>`를 가진 `default`, `tuned`인지 확인 |
| 상태 아이콘 | 녹색이면 완료, 빨간색이면 실패 |
| Created | 언제 시작한 실행인지 확인 |
| Duration | 학습과 평가에 걸린 전체 시간 |
| Source | 이 예제에서는 `sagemaker-xgboost-lab` |

이름 뒤의 `<run-id>`가 다른 실행끼리 비교하면 서로 다른 시점의 결과가 섞입니다. 최신 결과를 볼 때는 맨 위에 있는 `default`와 `tuned`의 suffix가 같은지 먼저 확인합니다.

이전에 실패한 실행이 남아 있을 수 있습니다. 실패한 실행은 원인을 추적할 때는 유용하지만, 최종 성능 비교에는 완료된 실행만 선택합니다.

## 3. 개별 실행 확인하기

Run Name을 선택하면 해당 실행의 **Overview**가 열립니다.

먼저 다음 세 영역을 순서대로 확인합니다.

### Metrics

지표는 모델이 validation 데이터셋에서 낸 결과입니다.

| Metric | 의미 | 읽는 방법 |
|---|---|---|
| `validation-auc` | 양성과 음성을 얼마나 잘 구분해 순위를 매기는지 나타내는 전반적인 성능 | `1.0`에 가까울수록 좋고, `0.5`는 무작위 추측 수준 |

`validation-auc`는 XGBoost 컨테이너가 학습 중에 계산한 SageMaker `validation:auc`를 완료된 Training Job에서 읽어 MLflow에 기록한 값입니다. 두 서비스가 같은 값을 사용하므로 SageMaker와 MLflow의 지표 값은 일치합니다. Autolog 실행도 같은 이름을 사용하므로 하나의 Compare 화면에서 함께 선택할 수 있습니다.

### Parameters

파라미터는 모델을 어떤 설정으로 학습했는지 보여줍니다.

| Parameter | 의미 |
|---|---|
| `xgboost.objective` | 해결하려는 문제와 예측 출력 형식. `binary:logistic`은 이진 분류 확률을 출력 |
| `xgboost.eval_metric` | 학습 중 관찰할 지표. 이 예제는 `auc` 사용 |
| `xgboost.tree_method` | tree를 만드는 방식. GPU 인스턴스에서는 `gpu_hist` 사용 |
| `xgboost.seed` | 같은 조건의 결과를 재현하기 위한 random seed |
| `xgboost.num_round` | 순서대로 추가하는 tree 수 |
| `xgboost.max_depth` | 각 tree가 가질 수 있는 최대 깊이 |
| `xgboost.eta` | 각 tree의 영향을 조절하는 learning rate |
| `xgboost.subsample` | 각 tree가 학습에 사용하는 row 비율 |
| `xgboost.colsample_bytree` | 각 tree가 사용하는 feature 비율 |
| `sagemaker.instance_type` | 학습에 사용한 SageMaker 인스턴스 |
| `sagemaker.image_uri` | 사용한 XGBoost 컨테이너 이미지와 버전 |
| `dataset.uci_id` | 이 예제에서 사용한 공개 데이터셋 ID |
| `dataset.name` | UCI Default of Credit Card Clients |
| `dataset.positive_label` | `1 = default payment next month` |

`eta`가 작으면 한 번에 조금씩 학습하므로 보통 `num_round`가 더 많이 필요합니다. 이 예제의 `tuned`는 `eta=0.08`, `num_round=250`을, `default`는 `eta=0.1`, `num_round=10`을 사용합니다.

`0.9`는 `eta`가 아니라 `subsample`과 `colsample_bytree` 값입니다.

### Tags와 Artifacts

화면 아래쪽의 Tags는 MLflow 실행과 SageMaker 리소스를 연결합니다.

| Tag | 의미 |
|---|---|
| `candidate` | `default` 또는 `tuned` |
| `run_id` | 스크립트 실행 한 번을 구분하는 프로젝트 run ID |
| `sagemaker.instance_type` | 학습에 사용한 인스턴스 |
| `sagemaker.training_job_name` | SageMaker 콘솔에서 로그와 지표를 찾을 때 사용할 Job 이름 |
| `sagemaker.model_s3_uri` | 학습된 `model.tar.gz`가 저장된 S3 URI |
| `mlflow_run_id` | MLflow 내부에서 해당 실행을 구분하는 ID |

**Datasets**에는 실제 학습과 validation에 사용한 DataFrame의 스키마, digest, S3 source가
표시됩니다. **Artifacts** 탭의 `data-summary.json`에는 데이터셋 이름, feature 순서,
target의 의미, 분할별 row 수와 class 수, random seed가 들어 있습니다.

**Artifacts** 탭의 `model`은 MLflow Logged Model입니다. 비교 후 사용할 실행을 선택했다면 `model`을 열고 **Register model**을 눌러 `sagemaker-xgboost-lab` 이름으로 등록합니다. 등록하면 SageMaker Model Registry에도 같은 버전이 생성됩니다.

SageMaker가 만든 원본 모델은 `sagemaker.model_s3_uri`에 있으며, Logged Model에 포함된 inference specification이 이 URI와 XGBoost 이미지를 연결합니다.

## 4. default와 tuned 비교하기

Runs 화면으로 돌아가 다음 순서로 비교합니다.

1. 이름 뒤의 `<run-id>`가 같은 `default`와 `tuned`를 찾습니다.
2. 두 row의 체크박스를 선택합니다.
3. 위쪽의 **Compare**를 누릅니다.
4. **Parameters**와 **Metrics**에서 **Show diff only**를 켜면 값이 다른 항목만 빠르게 볼 수 있습니다.

### 시각화 읽기

Compare 화면 위쪽의 **Parallel Coordinates Plot**은 파라미터와 지표의 관계를 선으로 연결해 보여줍니다.

왼쪽 **Parameters**에서 비교할 파라미터를, **Metrics**에서 결과 지표를 고릅니다. 이 예제처럼 실행이 두 개뿐일 때는 전체 경향을 분석하기보다 어떤 설정이 어떤 결과로 이어졌는지 확인하는 용도로 사용합니다.

실행이 많이 쌓이면 Scatter Plot으로 `eta`와 ROC-AUC의 관계를 보거나, 여러 파라미터 조합 중 성능이 좋은 영역을 찾는 데 활용할 수 있습니다.

### 표로 결과 해석하기

Compare 화면 아래쪽에서는 파라미터와 지표를 column별로 직접 비교할 수 있습니다.

| 항목 | tuned | default | 해석 |
|---|---:|---:|---|
| `xgboost.max_depth` | 5 | 1 | tuned가 더 복잡한 관계를 학습할 수 있음 |
| `xgboost.num_round` | 250 | 10 | tuned가 더 많은 tree를 순서대로 학습 |
| `xgboost.eta` | 0.08 | 0.1 | tuned가 더 작은 step으로 학습 |
| `validation-auc` | 실행 결과 확인 | 실행 결과 확인 | 높을수록 연체/비연체 순위 구분이 좋음 |

같은 분할을 사용한 로컬 sanity check에서는 baseline이 약 `0.70`, tuned가 약 `0.76`이었지만, SageMaker 실행 결과는 컨테이너 버전과 실행 환경에 따라 달라질 수
있습니다. 실제 MLflow 값을 기준으로 비교하세요. tuned의 AUC가 더 높더라도 tree 수와
학습 시간이 더 많이 필요하므로 개선 폭과 복잡성을 함께 고려해야 합니다.

## 5. 실제로 모델을 선택할 때 확인할 순서

모델을 선택할 때는 MLflow에서 가장 높은 지표 값만 보지 말고 다음 순서로 검토합니다.

1. 비교하는 실행이 같은 데이터셋과 분할을 사용했는지 `run_id`, 데이터셋 ID, seed로 확인합니다.
2. validation AUC가 실제로 개선됐는지 확인합니다.
3. train 지표만 높고 validation 지표는 낮아지는 overfitting 징후가 없는지 학습 로그나 autolog learning curve에서 확인합니다.
4. 인스턴스 유형, Duration, tree 수로 추가 비용과 복잡도를 판단합니다.
5. 선택한 실행의 Training Job 이름과 모델 S3 URI를 Tags에서 확인합니다.

이 예제의 `default`는 개선 효과를 쉽게 확인하도록 의도적으로 작게 만든 데모 설정입니다. 따라서 tuned가 더 좋다는 결과를 XGBoost에 대한 일반적인 결론으로 해석해서는 안 됩니다. 실제 데이터에서는 여러 번 실험하고, 별도의 test 데이터셋과 비즈니스 지표로 최종 판단해야 합니다.

AMT 실행도 같은 validation set으로 최적 trial을 선택합니다. 따라서 최종 성능을
판단하려면 AMT가 보지 않은 test 또는 out-of-time 데이터셋이 별도로 필요합니다.

## 6. Python 코드가 MLflow에 기록하는 순서

화면에 표시되는 값은 `train.py`가 다음 순서로 기록합니다.

먼저 managed MLflow App과 experiment를 선택합니다.

```python
mlflow.set_tracking_uri(mlflow_app_arn)
mlflow.set_experiment("sagemaker-xgboost-lab")
```

candidate마다 별도의 실행을 시작합니다. 이 이름이 Runs 화면의 Run Name이 됩니다.

```python
with mlflow.start_run(run_name=f"{candidate}-{run_id}"):
    ...
```

학습 전에 재현에 필요한 설정을 Parameters와 Tags에 기록합니다.

```python
mlflow.log_params(hyperparameters)
mlflow.set_tags(
    {
        "candidate": candidate,
        "run_id": run_id,
        "sagemaker.instance_type": instance_type,
    }
)
```

학습이 끝나면 SageMaker가 계산한 validation AUC를 기록합니다.

```python
mlflow.log_metric("validation-auc", validation_auc)
```

마지막으로 SageMaker Training Job과 모델 위치를 Tags에 연결하고, 데이터셋 요약을 아티팩트로 남깁니다.

```python
mlflow.set_tags(
    {
        "sagemaker.training_job_name": training_job_name,
        "sagemaker.model_s3_uri": model_s3_uri,
    }
)
mlflow.log_artifact("data-summary.json", artifact_path="data")
```

| Python 호출 | MLflow 화면 |
|---|---|
| `mlflow.start_run()` | Run Name과 실행 상태 |
| `mlflow.log_params()` | Parameters |
| `mlflow.log_metrics()` | Metrics |
| `mlflow.set_tags()` | Tags |
| `mlflow.log_artifact()` | Artifacts |

실제 구현은 [`train.py`](../src/sagemaker_xgboost_lab/train.py)의 `run_experiment()`에서 확인할 수 있습니다.

