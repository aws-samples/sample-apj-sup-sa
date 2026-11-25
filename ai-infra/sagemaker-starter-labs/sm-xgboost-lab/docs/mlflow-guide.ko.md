[English](mlflow-guide.md) | 한국어

# MLflow 결과 확인 가이드

이 문서는 Training이 끝난 뒤 managed MLflow를 열고, `default`와 `tuned` run을 찾아 결과를 비교하는 과정을 순서대로 설명합니다.

이 예제에서 MLflow는 model을 직접 학습하거나 배포하는 도구가 아닙니다. 어떤 설정으로 학습했고, 결과가 어땠으며, SageMaker의 어느 Training Job과 model artifact에 연결되는지를 한곳에서 확인하는 experiment tracking 도구입니다.

## 먼저 알아둘 구조

MLflow 화면은 다음 세 단계로 구성됩니다.

```text
MLflow App
  -> Experiment
      -> Run
```

| 단계 | 이 예제의 값 | 의미 |
|---|---|---|
| MLflow App | `sagemaker-xgboost-lab` | MLflow UI와 metadata를 제공하는 managed App |
| Experiment | `sagemaker-xgboost-lab` | 같은 목적의 Training 결과를 모아 두는 공간 |
| Run | `default-<run-id>`, `tuned-<run-id>` | 한 가지 hyperparameter 설정으로 실행한 결과 |
| AMT Run | `amt-<run-id>` | Automatic Model Tuning에서 선택된 최적 trial |

`./scripts/run-training.sh`를 한 번 실행하면 같은 `<run-id>`를 가진 `default`와 `tuned` run이 하나씩 만들어집니다. 두 설정을 비교할 때는 이름 뒤의 `<run-id>`가 같은 run을 선택해야 합니다.

`./scripts/run-tuning.sh`는 여러 SageMaker Training Job을 실행한 뒤 최적 trial을
`amt-<run-id>` run으로 기록합니다. 전체 trial은 SageMaker Hyperparameter tuning jobs
화면에서 보고, MLflow의 `tuning/trials.json` artifact에서도 요약을 확인할 수 있습니다.

예를 들면 다음 두 run이 한 쌍입니다.

```text
default-20260803-055035-9139f1
tuned-20260803-055035-9139f1
```

## 1. MLflow App 열기

1. AWS Console에서 Region을 `ap-northeast-2`로 선택합니다.
2. **Amazon SageMaker AI → SageMaker Studio**로 이동합니다. Studio가 없으면 **Create SageMaker Studio**를 완료합니다.
3. 생성된 Studio에서 **Open Studio**를 선택합니다.
4. Studio 왼쪽 navigation에서 **MLflow** icon을 선택합니다.
5. `sagemaker-xgboost-lab` App을 선택하고 **Open MLflow**를 누릅니다.
6. MLflow가 열리면 왼쪽 위에서 **Model training**을 선택합니다.

MLflow 3 화면은 처음에 **GenAI** mode로 열릴 수 있습니다. 이 예제는 일반적인 model Training 결과를 기록하므로 반드시 **Model training**으로 전환합니다.

Home 화면의 **Recent Experiments**에서 `sagemaker-xgboost-lab`을 선택하거나, 왼쪽의 **Experiments**에서 같은 이름을 선택합니다.

## 2. Runs 목록 읽기

Experiment를 열면 **Runs** 화면에 지금까지 실행한 결과가 시간순으로 표시됩니다.

먼저 다음 항목을 확인합니다.

| 화면 항목 | 확인할 내용 |
|---|---|
| Run Name | 같은 `<run-id>`를 가진 `default`, `tuned`인지 확인 |
| 상태 icon | 녹색이면 완료, 빨간색이면 실패 |
| Created | 언제 시작한 run인지 확인 |
| Duration | Training과 평가에 걸린 전체 시간 |
| Source | 이 예제에서는 `sagemaker-xgboost-lab` |

이름 뒤의 `<run-id>`가 다른 run을 비교하면 서로 다른 시점의 실행을 섞게 됩니다. 최신 결과를 볼 때는 가장 위에 있는 `default`와 `tuned`의 suffix가 같은지 먼저 확인합니다.

이전 실행에서 실패한 run이 남아 있을 수 있습니다. 실패한 run은 원인을 추적할 때는 유용하지만, 최종 성능 비교에서는 완료된 run만 선택합니다.

## 3. 개별 run 확인하기

Run Name을 선택하면 해당 run의 **Overview**가 열립니다.

처음에는 다음 세 영역을 순서대로 봅니다.

### Metrics

Metric은 model이 validation dataset에서 낸 결과입니다.

| Metric | 의미 | 읽는 방법 |
|---|---|---|
| `validation-auc` | 양성과 음성을 구분하는 전반적인 순위 성능 | `1.0`에 가까울수록 좋고, `0.5`는 무작위 추측 수준 |

`validation-auc`는 XGBoost container가 Training 중 계산한 SageMaker `validation:auc`를 완료된 Training Job에서 읽어 MLflow에 기록한 값입니다. 같은 값이므로 SageMaker와 MLflow 사이에서 metric 계산 결과가 달라지지 않습니다. Autolog run도 같은 이름을 사용하므로 한 Compare 화면에서 함께 선택할 수 있습니다.

### Parameters

Parameter는 model을 어떤 설정으로 학습했는지 보여줍니다.

| Parameter | 의미 |
|---|---|
| `xgboost.objective` | 해결하려는 문제와 예측 출력 형식. `binary:logistic`은 binary classification probability를 출력 |
| `xgboost.eval_metric` | Training 중 관찰할 metric. 이 예제는 `auc` 사용 |
| `xgboost.tree_method` | tree를 만드는 방식. GPU instance에서는 `gpu_hist` 사용 |
| `xgboost.seed` | 같은 조건의 결과를 재현하기 위한 random seed |
| `xgboost.num_round` | 순서대로 추가하는 tree 수 |
| `xgboost.max_depth` | 각 tree가 복잡해질 수 있는 최대 depth |
| `xgboost.eta` | 각 tree의 영향을 조절하는 learning rate |
| `xgboost.subsample` | 각 tree가 학습에 사용하는 row 비율 |
| `xgboost.colsample_bytree` | 각 tree가 사용하는 feature 비율 |
| `sagemaker.instance_type` | Training에 사용한 SageMaker instance |
| `sagemaker.image_uri` | 사용한 XGBoost container image와 version |
| `dataset.uci_id` | 이 예제에서 사용한 public dataset ID |
| `dataset.name` | UCI Default of Credit Card Clients |
| `dataset.positive_label` | `1 = default payment next month` |

`eta`가 작으면 한 번에 덜 학습하므로 보통 더 많은 `num_round`가 필요합니다. 이 예제의 `tuned`는 `eta=0.08`, `num_round=250`을 사용하고, `default`는 `eta=0.1`, `num_round=10`을 사용합니다.

`0.9`는 `eta`가 아니라 `subsample`과 `colsample_bytree` 값입니다.

### Tags와 Artifacts

화면 아래쪽의 Tags는 MLflow run과 SageMaker resource를 연결합니다.

| Tag | 의미 |
|---|---|
| `candidate` | `default` 또는 `tuned` |
| `run_id` | 한 번의 script 실행을 구분하는 project run ID |
| `sagemaker.instance_type` | 사용한 Training instance |
| `sagemaker.training_job_name` | SageMaker console에서 log와 metric을 찾을 때 사용할 Job name |
| `sagemaker.model_s3_uri` | 학습된 `model.tar.gz`가 저장된 S3 URI |
| `mlflow_run_id` | MLflow 내부에서 해당 run을 구분하는 ID |

**Datasets**에는 실제 Training과 validation DataFrame의 schema, digest와 S3 source가
표시됩니다. **Artifacts** tab의 `data-summary.json`에는 dataset 이름, feature 순서,
target 의미, split별 row와 class 수, random seed가 있습니다.

**Artifacts** tab의 `model`은 MLflow Logged Model입니다. 비교 후 사용할 run을 선택했다면 `model`을 열고 **Register model**을 눌러 `sagemaker-xgboost-lab` 이름으로 등록합니다. 이 작업이 SageMaker Model Registry에도 같은 version을 만듭니다.

SageMaker가 만든 원본 model은 `sagemaker.model_s3_uri`에 있으며, Logged Model에 포함된 inference specification이 이 URI와 XGBoost image를 연결합니다.

## 4. default와 tuned 비교하기

Runs 화면으로 돌아가 다음 순서로 비교합니다.

1. 이름 뒤의 `<run-id>`가 같은 `default`와 `tuned`를 찾습니다.
2. 두 row의 checkbox를 선택합니다.
3. 위쪽의 **Compare**를 누릅니다.
4. **Parameters**와 **Metrics**에서 **Show diff only**를 켜면 다른 값만 빠르게 볼 수 있습니다.

### 시각화 읽기

Compare 화면 위쪽의 **Parallel Coordinates Plot**은 parameter와 metric의 관계를 선으로 연결합니다.

왼쪽 **Parameters**에서 비교할 parameter를 고르고, **Metrics**에서 결과 metric을 고릅니다. 이 예제처럼 run이 두 개뿐일 때는 전체 경향을 분석하기보다 어떤 설정이 어떤 결과로 이어졌는지 확인하는 용도로 사용합니다.

run이 많이 쌓이면 Scatter Plot으로 `eta`와 ROC-AUC 관계를 보거나, 여러 parameter 조합 중 좋은 영역을 찾는 데 활용할 수 있습니다.

### 표로 결과 해석하기

아래로 내려가면 parameter와 metric을 column별로 직접 비교할 수 있습니다.

| 항목 | tuned | default | 해석 |
|---|---:|---:|---|
| `xgboost.max_depth` | 5 | 1 | tuned가 더 복잡한 관계를 학습할 수 있음 |
| `xgboost.num_round` | 250 | 10 | tuned가 더 많은 tree를 순서대로 학습 |
| `xgboost.eta` | 0.08 | 0.1 | tuned가 더 작은 step으로 학습 |
| `validation-auc` | 실행 결과 확인 | 실행 결과 확인 | 높을수록 연체/비연체 순위 구분이 좋음 |

같은 split을 사용한 local sanity check에서는 baseline 약 `0.70`, tuned 약 `0.76`을
확인했지만 SageMaker 실행 결과는 container version과 실행 환경에 따라 달라질 수
있습니다. 실제 MLflow 값을 기준으로 비교하세요. tuned의 AUC가 높더라도 더 많은
tree와 시간이 필요하므로 개선 폭과 복잡성을 함께 봅니다.

## 5. 실제로 model을 선택할 때 확인할 순서

MLflow에서 가장 높은 숫자만 찾기보다 다음 순서로 확인합니다.

1. 비교하는 run이 같은 dataset과 split을 사용했는지 `run_id`, dataset ID, seed로 확인합니다.
2. validation AUC가 실제로 개선됐는지 확인합니다.
3. train metric만 높고 validation metric이 낮아지는 overfitting 징후가 없는지 Training log 또는 autolog learning curve에서 확인합니다.
4. instance type, Duration, tree 수를 보고 추가 비용과 복잡도를 확인합니다.
5. 선택한 run의 Training Job name과 model S3 URI를 Tags에서 확인합니다.

이 예제의 `default`는 개선 효과를 쉽게 확인하도록 의도적으로 작게 만든 demo 설정입니다. 따라서 tuned가 좋다는 결과를 XGBoost의 일반적인 결론으로 해석하면 안 됩니다. 실제 data에서는 여러 실험을 반복하고, 별도의 test dataset과 business metric으로 최종 판단해야 합니다.

AMT run도 같은 validation set을 사용해 최적 trial을 선택합니다. 따라서 최종 성능을
판단할 때는 AMT가 보지 않은 test 또는 out-of-time dataset이 별도로 필요합니다.

## 6. Python code가 MLflow에 기록하는 순서

화면의 값은 `train.py`에서 다음 순서로 기록합니다.

먼저 managed MLflow App과 experiment를 선택합니다.

```python
mlflow.set_tracking_uri(mlflow_app_arn)
mlflow.set_experiment("sagemaker-xgboost-lab")
```

각 candidate마다 독립된 run을 엽니다. 이 이름이 Runs 화면의 Run Name이 됩니다.

```python
with mlflow.start_run(run_name=f"{candidate}-{run_id}"):
    ...
```

Training 전에 재현에 필요한 설정을 Parameters와 Tags에 기록합니다.

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

Training이 끝나면 SageMaker가 계산한 validation AUC를 기록합니다.

```python
mlflow.log_metric("validation-auc", validation_auc)
```

마지막으로 SageMaker Training Job과 model 위치를 Tags에 연결하고, dataset 요약을 Artifact로 남깁니다.

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
| `mlflow.start_run()` | Run Name과 run 상태 |
| `mlflow.log_params()` | Parameters |
| `mlflow.log_metrics()` | Metrics |
| `mlflow.set_tags()` | Tags |
| `mlflow.log_artifact()` | Artifacts |

실제 구현은 [`train.py`](../src/sagemaker_xgboost_lab/train.py)의 `run_experiment()`에서 확인할 수 있습니다.

