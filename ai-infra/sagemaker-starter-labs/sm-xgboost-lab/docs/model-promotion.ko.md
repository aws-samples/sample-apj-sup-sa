[English](model-promotion.md) | 한국어

# 모델 등록과 배치 추론

이 단계에서는 MLflow에서 비교한 결과를 바탕으로 모델을 선택하고 실제 예측까지 실행합니다.

```text
MLflow 비교
  -> MLflow UI에서 model 선택
  -> MLflow와 SageMaker Model Registry에 version 등록
  -> SageMaker Studio에서 version 승인
  -> 최신 Approved version으로 Batch Transform
```

모든 experiment를 Registry에 등록하지는 않습니다. MLflow에는 시도한 모든 실행을 보관하고, Registry에는 실제 추론 후보로 선택한 모델만 등록합니다.

## 1. MLflow UI에서 모델 등록

1. MLflow의 **Model training → Runs**에서 같은 run ID를 가진 `default`와 `tuned`를 비교합니다.
2. 사용할 실행의 이름을 눌러 상세 화면으로 이동합니다.
3. **Artifacts** 탭에서 `model` Logged Model을 선택합니다.
4. **Register model**을 누릅니다.
5. 처음 등록할 때는 **Create New Model**을 선택하고 이름에 `sagemaker-xgboost-lab`을 입력합니다.
6. 이후 모델은 같은 이름을 선택해 새 버전으로 등록합니다.

등록하면 MLflow Model Registry와 SageMaker Model Registry에 같은 버전이 자동으로 생성됩니다. UI에서 선택하지 않은 실행은 SageMaker Registry에 등록되지 않습니다.

일반 Training과 Automatic Model Tuning이 끝난 뒤 실행되는 `log_model()`은 UI에서 선택할
수 있는 Logged Model을 만듭니다. 이 호출만으로는 SageMaker Model Registry에
등록되지 않습니다. MLflow App의 automatic model registration 설정이 UI의
**Register model** 작업을 SageMaker Model Registry에 동기화합니다.

등록된 Model Package 버전에는 SageMaker가 저장한 `model.tar.gz`의 S3 URI, XGBoost 추론 컨테이너 이미지, 지원 인스턴스 유형, `text/csv` 입력·출력 형식이 연결됩니다. 모델 선택의 근거가 된 파라미터와 지표는 MLflow 실행에 남아 있습니다.

학습 코드는 MLflow 화면에서 등록할 수 있도록 Logged Model을 남기고, SageMaker의 원본 `model.tar.gz`, 추론 이미지, CSV 형식을 inference specification으로 연결합니다.

등록 결과는 MLflow App이 아니라 SageMaker Studio에서 확인합니다. 현재 Studio에서는 왼쪽 탐색 메뉴의 **JumpStart / Models → My models → Logged**로 이동합니다. `sagemaker-xgboost-lab-`로 시작하는 모델 그룹에서 **View all versions**를 누르면 등록된 버전을 볼 수 있습니다.

Studio의 **Home** 화면에서 **Workflows and tasks → Build, train, tune models → Catalog models with model registry**를 눌러 같은 화면으로 이동할 수도 있습니다. AWS 공식 문서에는 이 경로가 아직 **Models → Registered models → Model Groups**로 표시되어 있을 수 있습니다. 현재 Studio UI에서는 `Models`가 **JumpStart / Models**로 바뀌었고, Registry는 **My models → Logged** 아래로 옮겨졌습니다. **Deployments** 메뉴는 엔드포인트와 Batch Transform 같은 추론 작업을 보는 곳이므로, Registry를 찾을 때 사용하는 경로가 아닙니다.

MLflow App이 SageMaker 그룹 이름에 접미사를 붙일 수 있으므로 MLflow에 등록한 모델 이름과 그룹 이름이 정확히 일치하지 않을 수 있습니다. 이 샘플에서 `sagemaker-xgboost-lab`을 등록하면 `sagemaker-xgboost-lab-4663e9`와 같은 이름으로 표시됩니다. Studio Classic을 사용한다면 **Home → Models → Model registry** 경로를 사용합니다.

## 2. SageMaker Registry에서 버전 승인

Registry 등록은 추론 후보를 보관하는 작업이고, 승인은 그중 실제 추론에 사용할 버전을 사람이 확인하는 단계입니다. 이 샘플에서는 복잡한 CI/CD 없이 수동 승인 한 번만 사용합니다.

1. SageMaker Studio 왼쪽 탐색 메뉴에서 **JumpStart / Models**를 선택합니다.
2. **My models → Logged**로 이동합니다.
3. `sagemaker-xgboost-lab-`으로 시작하는 모델 그룹을 선택합니다.
4. **Version 1**처럼 승인할 버전을 선택합니다.
5. **Overview** 상단의 **Deploy** 상태를 확인합니다.
6. **Pending Approval** 드롭다운에서 **Approved**를 선택하고, 확인 창이 나타나면 변경을 승인합니다.

승인이 완료되면 버전 목록의 **Deployment status**가 **Approved**로 표시됩니다. `Approved` 상태가 되어도 모델이 엔드포인트에 자동으로 배포되지는 않습니다. 이 프로젝트에서 `Approved` 상태는 안전장치 역할을 하며, Batch Transform 스크립트는 최신 `Approved` 버전 중 실행할 인스턴스와 호환되는 버전만 선택합니다.

지표나 아티팩트가 기대와 다르면 **Rejected**를 선택합니다. `Rejected`와 **Pending Approval** 상태의 버전은 Batch Transform에서 사용하지 않습니다.

## 3. Batch Transform 실행

Studio에서 확인한 정확한 Model Package Group 이름을 지정해 실행합니다.

```bash
MODEL_PACKAGE_GROUP_NAME=sagemaker-xgboost-lab-4663e9 \
  ./scripts/run-batch-inference.sh
```

이 샘플의 Batch Transform 인스턴스는 작은 XGBoost 추론에 충분한
`ml.c5.xlarge` 한 종류로 고정합니다. Registry에 등록하는 모델 버전도 이 인스턴스만
지원하도록 선언합니다.

Training 할당량과 Transform 할당량은 별도로 관리되며, 계정마다 기본값이 다를 수 있습니다. Service Quotas에서 사용할 인스턴스의 **for transform job usage** 값이 `1` 이상인지 확인합니다.

이 명령은 행마다 예측 API를 호출하지 않습니다. 로컬에서는 Batch Transform Job 하나만 요청하고, 이후 입력 분할과 모델 호출은 SageMaker가 처리합니다.

이 샘플은 CSV 파일 하나를 사용합니다. `SplitType=Line`으로 각 줄을 하나의 레코드로
구분하고, `BatchStrategy=MultiRecord`로 여러 줄을 하나의 요청에 묶습니다.
`ml.c5.xlarge` 인스턴스 한 대에서 최대 4개의 요청을 동시에 처리합니다.

| 옵션 | 이 샘플의 값 | 의미 |
|---|---|---|
| `InstanceCount` | `1` | 추론에 사용할 인스턴스 수 |
| `SplitType` | `Line` | CSV 한 줄을 하나의 레코드로 구분 |
| `BatchStrategy` | `MultiRecord` | 여러 레코드를 하나의 요청 payload로 묶음 |
| `MaxConcurrentTransforms` | `4` | 인스턴스 하나에 동시에 보내는 최대 요청 수 |
| `AssembleWith` | `Line` | 예측 결과를 줄 단위로 이어서 저장 |

`InstanceCount=1`이므로 인스턴스는 한 대만 사용합니다. `MaxConcurrentTransforms=4`는
그 인스턴스에 요청을 최대 4개까지 동시에 보낼 수 있다는 뜻입니다. payload 크기는
따로 지정하지 않고 SageMaker 기본값을 사용합니다.

데이터가 커져도 기본 설정에서는 인스턴스 한 대 안에서 최대 4개의 요청이 병렬로 처리됩니다. 처리량이 더 필요하면 입력을 여러 S3 파일로 나누고 `InstanceCount`를 늘려 파일을 여러 인스턴스에 분산할 수 있습니다. 입력 파일이 하나뿐인 상태에서 인스턴스 수만 늘리면 나머지 인스턴스가 유휴 상태로 남을 수 있으므로 두 값을 함께 검토해야 합니다.

스크립트는 다음 작업을 수행합니다.

1. MLflow가 만든 SageMaker Model Package Group을 자동으로 찾습니다. 같은 prefix의 그룹이 여러 개라면 `MODEL_PACKAGE_GROUP_NAME`에 Studio에 표시된 정확한 이름을 지정합니다.
2. 해당 그룹의 최신 `Approved` 버전부터 확인해 `ml.c5.xlarge`를 지원하는 첫 번째 버전을 선택합니다. 조건에 맞는 버전이 없으면 여기서 중단합니다.
3. validation feature에서 레이블을 제외한 추론용 CSV를 만듭니다.
4. 입력을 S3에 업로드합니다.
5. Model Package로 임시 SageMaker Model을 만듭니다.
6. Batch Transform이 각 행의 확률을 계산합니다.
7. 결과를 로컬로 다운로드하고 임시 SageMaker Model을 삭제합니다.

```text
[1/4] Find the latest approved model version
[2/4] Prepare credit default inference rows and upload them to S3
[3/4] Run Batch Transform (ml.c5.xlarge)
[4/4] Download predictions
```

결과는 다음 위치에 저장됩니다.

```text
artifacts/batch/<run-id>/predictions.csv.out
```

터미널에는 결과 파일의 처음 5개 확률만 표시됩니다.

```text
Predictions: artifacts/batch/<run-id>/predictions.csv.out
First predicted probabilities of default next month:
- 0.16083382070064545
- 0.1926967203617096
- 0.12307227402925491
- 0.15535558760166168
- 0.03271584212779999
```

전체 파일에는 validation 입력과 같은 순서로 6,000개의 확률이 저장됩니다. 각 줄은 입력 CSV에서 같은 위치에 있는 고객이 다음 달 연체할 확률이며, 값은 실행한 모델에 따라 달라집니다.

이 숫자는 모델 전체의 성능 점수가 아닙니다. 예를 들어 `0.6731`은 모델이 해당 고객의
다음 달 연체에 대해 `0.6731`이라는 확률 점수를 냈다는 뜻입니다. 운영 환경에서
이 값을 정확한 PD(Probability of Default) 67.31%로 해석하려면 별도의 test 데이터로
calibration을 검증해야 합니다.

임계값을 `0.5`로 정하면 확률이 `0.5` 이상일 때 연체 `1`, 그보다 작을 때
비연체 `0`으로 분류할 수 있습니다. 다만 이 샘플은 확률만 출력하며,
임계값 결정은 자동화하지 않습니다.

이 샘플은 validation feature를 Batch Transform 입력으로 재사용하지만, 실제
추론 데이터에는 정답이 없습니다. 상환 결과가 확정된 뒤 예측값과 레이블을
결합해 성능을 평가합니다. 실제 업무의 임계값이 `0.5`일 필요는 없으며,
승인율, 연체 탐지율, 그리고 잘못된 거절이나 놓친 연체로 인한 비즈니스 비용을 비교해 임계값을
정합니다.

S3에서는 입력과 출력이 다음 prefix에 저장됩니다.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/batch/<run-id>/
```

이전의 4-feature 데이터셋으로 등록·승인한 모델 버전이 같은 그룹에 남아 있다면, 새
신용카드 연체 모델을 등록·승인한 뒤 이전 버전을 `Rejected`로 변경합니다.
23개 feature 입력을 과거의 4-feature 모델에 보내면 스키마가 맞지 않기 때문입니다.

## 엔드포인트 대신 Batch Transform을 먼저 사용하는 이유

Batch Transform은 요청이 있을 때만 인스턴스를 사용하고, 작업이 끝나면 인스턴스가 종료됩니다. S3 파일 단위 예측, 정기 scoring, backfill에 적합하며 엔드포인트 유지 비용이 들지 않습니다.

실시간 API가 필요해지면 같은 Model Package로 엔드포인트를 추가할 수 있습니다. 스타트업 샘플에서는 먼저 Batch Transform으로 모델 선택부터 추론까지의 흐름을 검증하고, 지연 시간 요구 사항이 생겼을 때 엔드포인트를 추가하는 방식이 더 단순합니다.
