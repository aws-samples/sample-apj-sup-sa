[English](README.md) | 한국어

# SageMaker 학습 예제 모음

Amazon SageMaker에서 모델을 학습하고 운영하는 방법을 여러 방식으로 보여주는 예제 저장소입니다.
MNIST 분류로 SageMaker Training Job의 기본 구조를 비교하는 예제와, 공개 신용 연체
데이터셋으로 XGBoost, managed MLflow, Model Registry, Pipeline 흐름을 다루는 실습이 있습니다.

이 예제들은 로컬 PC의 IDE에서 학습 코드를 작성하고, 로컬 터미널에서 SageMaker Training Job을
제출하는 흐름을 기준으로 합니다. 실제 학습은 SageMaker의 학습 컨테이너에서 실행됩니다.
SageMaker Studio는 Training Job, MLflow 실험, 등록된 모델과 Pipeline DAG를 확인하는 데 사용합니다.

이 예제들의 실행 환경은 다음 두 가지로 나뉩니다.

- **로컬 환경(로컬 PC)** — 학습 코드를 작성하고, Job을 제출하고, 상태를 확인하는 곳입니다.
  이 환경에는 `sagemaker`, `boto3` 같은 제어용 패키지만 필요합니다.
- **학습 컨테이너(AWS)** — 실제 학습이 GPU/CPU에서 실행되는 곳입니다. `torch` 같은 학습
  프레임워크와 CUDA는 여기에 들어갑니다.

학습 코드는 로컬 PC가 아니라 AWS가 실행하는 컨테이너 안에서 실행되므로 `torch`를 로컬에
설치하지 않습니다. 이 구조에서는 로컬 환경이 가벼워지고, 각 구성 요소의 실행 위치가 명확해집니다.

## 프로젝트별 uv 환경

각 예제는 독립된 프로젝트이고, 환경은 [uv](https://docs.astral.sh/uv/)로 관리합니다.
프로젝트마다 `pyproject.toml`, `uv.lock`, `.python-version`을 두어 서로 간섭 없이 정확히
같은 의존성을 재현할 수 있게 했습니다.

프로젝트 폴더에서 `uv sync`로 환경을 만든 뒤 `uv run <스크립트>`로 실행합니다.
`uv run`이 해당 프로젝트의 가상환경에서 실행하므로 `activate` 과정은 필요 없습니다.

```bash
cd sm-training-job/hello-mnist
uv sync
uv run launch.py --dry-run
```

로컬 의존성은 `sagemaker`(v3)와 `boto3`뿐입니다. 학습 프레임워크는 각 예제의 컨테이너
정의(스크립트 모드는 관리형 DLC, BYOC는 `container/Dockerfile`) 안에 들어갑니다.

## 예제 목록

- **[hello-mnist](sm-training-job/hello-mnist/README.ko.md) — 스크립트 모드**
  가장 간단한 방식입니다. AWS가 관리하는 PyTorch 컨테이너(DLC)를 그대로 사용하고, 학습 코드
  (`train.py`)를 전달하면 SageMaker가 나머지를 처리합니다. Docker를 다룰 필요가 없으므로
  SageMaker 학습을 처음 접한다면 이 예제부터 시작할 것을 권장합니다.

- **[byoc-mnist](sm-training-job/byoc-mnist/README.ko.md) — BYOC (커스텀 컨테이너)**
  Docker 이미지를 직접 만들어 ECR에 올리고 그 이미지로 학습하는 방식(Bring Your Own
  Container)입니다. 컨테이너 규약(`/opt/ml` 경로, 실행 방식 등)을 직접 지켜야 하지만, 그 대신
  런타임을 완전히 통제할 수 있습니다. hello-mnist와 비교하면 스크립트 모드에서 SageMaker가
  내부적으로 대신 처리하는 작업을 파악할 수 있습니다.

- **[sm-xgboost-lab](sm-xgboost-lab/README.ko.md) — XGBoost, MLflow, Model Registry**
  UCI 신용 연체 공개 데이터셋으로 built-in XGBoost Training Job을 실행하고, managed MLflow에서
  실험 결과를 비교한 뒤 Model Registry 승인과 Batch Transform까지 이어지는 실습입니다.
  CloudFormation으로 필요한 인프라를 만들며, SageMaker Studio가 필요합니다.

- **[sm-pipeline-quick](sm-pipeline-quick/README.ko.md) — SageMaker Pipeline**
  앞 예제의 UCI 데이터 로더와 인프라를 재사용해 전처리, XGBoost Training Job 한 번,
  별도 test 데이터 평가, AUC 기준 모델 등록을 Pipeline으로 실행합니다.
  setup 스크립트가 입력 데이터, Processing 이미지, 권한을 준비하고 Studio에서 실행할 Pipeline 정의를 등록합니다.

## 사전 준비 (공통)

- **uv** — 파이썬 환경/패키지 관리
- **AWS CLI v2** + 자격증명과 기본 리전 (`aws configure`, `aws sts get-caller-identity`로 확인)
- **SageMaker 실행 역할** — MNIST 예제의 `launch.py`는 계정에서 자동 탐지합니다.
  XGBoost 실습은 `scripts/bootstrap-infra.sh`가 CloudFormation으로 필요한 역할을 만듭니다.
  자세한 내용은 각 프로젝트 README를 참고하세요.
- **Docker** — BYOC 예제(byoc-mnist)와 XGBoost Pipeline의 Processing 이미지 빌드에 필요합니다.
- **SageMaker Studio** — `sm-xgboost-lab`에서 managed MLflow와 Model Registry를 확인할 때
  필요합니다.

각 예제의 구체적인 실행 방법과 설정은 해당 프로젝트의 README에 있습니다.
