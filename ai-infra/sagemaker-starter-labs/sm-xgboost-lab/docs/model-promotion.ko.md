[English](model-promotion.md) | 한국어

# Model Registration and Batch Inference

이 단계는 MLflow에서 비교한 결과를 실제 예측으로 이어줍니다.

```text
MLflow 비교
  -> MLflow UI에서 model 선택
  -> MLflow와 SageMaker Model Registry에 version 등록
  -> SageMaker Studio에서 version 승인
  -> 최신 Approved version으로 Batch Transform
```

모든 experiment를 Registry에 넣지는 않습니다. MLflow는 시도한 모든 run을 보관하고, Registry에는 실제 inference 후보로 선택한 model만 등록합니다.

## 1. MLflow UI에서 model 등록

1. MLflow의 **Model training → Runs**에서 같은 run ID를 가진 `default`와 `tuned`를 비교합니다.
2. 사용할 run 이름을 눌러 상세 화면으로 이동합니다.
3. **Artifacts** tab에서 `model` Logged Model을 선택합니다.
4. **Register model**을 누릅니다.
5. 처음 등록할 때 **Create New Model**을 선택하고 이름을 `sagemaker-xgboost-lab`으로 입력합니다.
6. 이후 model은 같은 이름을 선택해 새 version으로 등록합니다.

등록하면 MLflow Model Registry와 SageMaker Model Registry에 같은 version이 자동으로 생성됩니다. UI에서 선택하지 않은 run은 SageMaker Registry에 등록되지 않습니다.

일반 Training과 Automatic Model Tuning 완료 후 실행되는 `log_model()`은 UI에서 선택할
수 있는 Logged Model을 만드는 작업입니다. 이 호출만으로 SageMaker Model Registry에
등록되지는 않습니다. MLflow App의 automatic model registration 설정은 UI의
**Register model** 작업을 SageMaker Model Registry에 동기화합니다.

등록된 Model Package version에는 SageMaker가 저장한 `model.tar.gz` S3 URI, XGBoost inference container image, 지원하는 instance type, `text/csv` input과 output 형식이 연결됩니다. 어떤 parameter와 metric을 보고 이 model을 선택했는지는 MLflow run에서 계속 확인합니다.

Training code는 MLflow가 화면에서 등록할 수 있도록 Logged Model을 남기고, SageMaker의 원본 `model.tar.gz`, inference image, CSV 형식을 inference specification으로 연결합니다.

등록 결과는 MLflow App이 아니라 SageMaker Studio에서 확인합니다. 현재 Studio에서는 왼쪽 navigation의 **JumpStart / Models → My models → Logged**로 이동합니다. `sagemaker-xgboost-lab-`로 시작하는 model group에서 **View all versions**를 누르면 등록된 version을 볼 수 있습니다.

Studio의 **Home** 화면에서는 **Workflows and tasks → Build, train, tune models → Catalog models with model registry**를 눌러 같은 화면으로 이동할 수도 있습니다. AWS 공식 문서에는 아직 이 경로가 **Models → Registered models → Model Groups**로 표시될 수 있지만, 현재 Studio UI에서 `Models`가 **JumpStart / Models**로 바뀌고 Registry가 **My models → Logged**에 배치된 것입니다. **Deployments** 메뉴는 endpoint와 Batch Transform 같은 inference 작업을 보는 곳이므로 Registry를 찾는 경로가 아닙니다.

MLflow App이 SageMaker group 이름에 suffix를 붙일 수 있으므로, MLflow registered model 이름과 group 이름이 완전히 같지 않을 수 있습니다. 이 sample에서는 `sagemaker-xgboost-lab`을 등록하면 `sagemaker-xgboost-lab-4663e9`처럼 보입니다. Studio Classic을 사용한다면 **Home → Models → Model registry** 경로를 사용합니다.

## 2. SageMaker Registry에서 version 승인

Registry 등록은 inference 후보를 보관하는 작업이고, approval은 그중 실제 inference에 사용할 version을 사람이 확인하는 단계입니다. 이 sample에서는 복잡한 CI/CD 없이 한 번의 manual approval만 사용합니다.

1. SageMaker Studio 왼쪽 navigation에서 **JumpStart / Models**를 선택합니다.
2. **My models → Logged**로 이동합니다.
3. `sagemaker-xgboost-lab-`으로 시작하는 model group을 선택합니다.
4. **Version 1**처럼 승인할 version을 선택합니다.
5. **Overview** 상단의 **Deploy** 상태를 확인합니다.
6. **Pending Approval** dropdown에서 **Approved**를 선택하고, 확인 창이 표시되면 변경을 승인합니다.

version 목록의 **Deployment status**가 **Approved**로 표시되면 완료된 것입니다. `Approved`는 model을 endpoint에 자동 배포하지 않습니다. 이 project에서는 Batch Transform script가 최신 `Approved` version 중 실행할 instance와 호환되는 version만 선택하도록 만드는 안전장치로 사용합니다.

metric이나 artifact가 기대와 다르면 **Rejected**를 선택합니다. `Rejected`와 **Pending Approval** version은 Batch Transform에서 사용하지 않습니다.

## 3. Batch Transform 실행

Studio에서 확인한 정확한 Model Package Group 이름을 지정해 실행합니다.

```bash
MODEL_PACKAGE_GROUP_NAME=sagemaker-xgboost-lab-4663e9 \
  ./scripts/run-batch-inference.sh
```

이 sample의 Batch Transform instance는 작은 XGBoost inference에 충분한
`ml.c5.xlarge` 한 종류로 고정합니다. Registry에 등록하는 model version도 이 instance만
지원하도록 선언합니다.

Training quota와 Transform quota는 별도로 관리되며 account마다 기본값이 다를 수 있습니다. Service Quotas에서 사용할 instance의 **for transform job usage** 값이 `1` 이상인지 확인합니다.

이 command가 prediction API를 row마다 여러 번 호출하는 것은 아닙니다. local에서는 Batch Transform Job 하나만 요청하고, 이후의 input 분할과 model 호출은 SageMaker가 맡습니다.

이 sample은 하나의 CSV file을 사용합니다. `SplitType=Line`으로 각 줄을 하나의 record로
구분하고, `BatchStrategy=MultiRecord`로 여러 줄을 하나의 request에 묶습니다.
`ml.c5.xlarge` instance 한 대에서 최대 4개의 request를 동시에 처리합니다.

| 옵션 | 이 sample의 값 | 의미 |
|---|---|---|
| `InstanceCount` | `1` | inference에 사용할 instance 수 |
| `SplitType` | `Line` | CSV 한 줄을 하나의 record로 구분 |
| `BatchStrategy` | `MultiRecord` | 여러 record를 하나의 request payload로 묶음 |
| `MaxConcurrentTransforms` | `4` | instance 하나에 동시에 보내는 최대 request 수 |
| `AssembleWith` | `Line` | prediction 결과를 줄 단위로 이어서 저장 |

`InstanceCount=1`이므로 사용하는 instance는 한 대입니다. `MaxConcurrentTransforms=4`는
그 instance에 최대 4개의 request를 동시에 보낼 수 있다는 의미입니다. payload 크기는
명시하지 않고 SageMaker 기본값을 사용합니다.

data가 커지면 먼저 한 instance 안에서 최대 4개의 request가 병렬로 처리됩니다. 처리량이 더 필요하면 input을 여러 S3 file로 나누고 `InstanceCount`를 늘려 file을 여러 instance에 분산할 수 있습니다. input file이 하나뿐인 상태에서 instance만 늘리면 나머지 instance가 쉬게 될 수 있으므로, 두 값을 함께 검토해야 합니다.

script는 다음 작업을 수행합니다.

1. MLflow가 만든 SageMaker Model Package Group을 자동으로 찾습니다. 같은 prefix의 group이 여러 개면 `MODEL_PACKAGE_GROUP_NAME`으로 Studio에 표시된 정확한 이름을 지정합니다.
2. 해당 group에서 최신 `Approved` version부터 확인하고, `ml.c5.xlarge`를 지원하는 첫 version을 선택합니다. 조건에 맞는 version이 없으면 여기서 중단합니다.
3. validation feature에서 label을 제외한 inference CSV를 만듭니다.
4. input을 S3에 upload합니다.
5. model package로 임시 SageMaker Model을 만듭니다.
6. Batch Transform이 각 row의 probability를 계산합니다.
7. 결과를 local로 download하고 임시 SageMaker Model을 삭제합니다.

```text
[1/4] Find the latest approved model version
[2/4] Prepare credit default inference rows and upload them to S3
[3/4] Run Batch Transform (ml.c5.xlarge)
[4/4] Download predictions
```

결과는 다음 위치에 남습니다.

```text
artifacts/batch/<run-id>/predictions.csv.out
```

terminal은 결과 파일의 처음 5개 probability만 보여 줍니다.

```text
Predictions: artifacts/batch/<run-id>/predictions.csv.out
First predicted probabilities of default next month:
- 0.16083382070064545
- 0.1926967203617096
- 0.12307227402925491
- 0.15535558760166168
- 0.03271584212779999
```

전체 파일에는 validation input과 같은 순서로 6,000개 probability가 저장됩니다. 각 줄은 input CSV의 같은 고객이 다음 달 연체할 probability이며, 값은 실행한 model에 따라 달라집니다.

이 숫자는 model 전체의 성능 점수가 아닙니다. 예를 들어 `0.6731`은 해당 고객의 다음
달 연체에 대해 model이 `0.6731`의 probability score를 냈다는 뜻입니다. 운영에서
이를 정확한 PD(Probability of Default) 67.31%로 해석하려면 별도 test data에서
calibration을 검증해야 합니다.

threshold를 `0.5`로 정하면 probability가 `0.5` 이상일 때 연체 `1`, 그보다 작을 때
비연체 `0`으로 분류할 수 있습니다. 하지만 이 sample은 probability만 출력하며,
threshold 결정은 자동화하지 않습니다.

이 sample은 validation feature를 Batch Transform input으로 재사용하지만, 실제
inference data에는 정답이 없습니다. 상환 결과가 확정된 뒤 prediction과 label을
결합해 성능을 평가합니다. 실제 업무에서는 반드시 `0.5`를 사용할 필요도 없습니다.
승인율, 연체 탐지율과 잘못 거절하거나 놓친 연체의 business cost를 비교해 threshold를
정합니다.

S3에는 input과 output이 다음 prefix에 남습니다.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/batch/<run-id>/
```

이전 4-feature dataset으로 등록·승인한 model version이 같은 group에 남아 있다면 새
credit default model을 등록하고 승인한 뒤 이전 version을 `Rejected`로 바꾸세요.
23개 feature input을 과거 4개 feature model에 보내면 schema가 맞지 않습니다.

## 왜 endpoint부터 만들지 않나요

Batch Transform은 요청이 있을 때만 instance를 사용하고 작업이 끝나면 종료됩니다. S3 file 단위 예측, 정기 scoring, backfill에 적합하며 endpoint 유지 비용이 없습니다.

실시간 API가 필요해지면 같은 model package로 endpoint를 추가할 수 있습니다. startup sample에서는 먼저 Batch Transform으로 model selection부터 inference까지의 연결을 검증한 뒤, latency 요구가 생길 때 endpoint를 추가하는 편이 단순합니다.
