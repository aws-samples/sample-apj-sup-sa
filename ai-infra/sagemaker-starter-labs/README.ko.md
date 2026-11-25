[English](README.md) | 한국어

# SageMaker 학습 예제 모음

Amazon SageMaker에서 모델을 학습하고 운영하는 방법을 여러 방식으로 보여주는 예제 저장소입니다.
MNIST 분류를 통해 SageMaker Training Job의 기본 구조를 비교하는 예제와, 공개 신용 연체
dataset으로 XGBoost, managed MLflow, Model Registry, Pipeline 흐름을 다루는 실습이 있습니다.

이 예제들은 내 로컬 PC에서 IDE로 학습 코드를 작성하고, 터미널에서 명령을 실행해 SageMaker에
학습 Job을 제출하는 흐름을 기준으로 작성했습니다. SageMaker Studio나 노트북 인스턴스 안에서
실행하는 상황은 다루지 않습니다.

여기서 중요한 건 환경이 두 개로 나뉜다는 점입니다.

- **로컬 환경(로컬 PC)** — 학습 코드를 작성하고, Job을 제출하고, 상태를 확인하는 곳입니다.
  여기에는 `sagemaker`, `boto3` 같은 제어용 패키지만 있으면 됩니다.
- **학습 컨테이너(AWS)** — 실제 학습이 GPU/CPU 위에서 도는 곳입니다. `torch` 같은 학습
  프레임워크와 CUDA는 여기에 들어갑니다.

그래서 `torch`를 로컬에 설치하지 않습니다. 학습 코드는 로컬 PC가 아니라 AWS가 띄운
컨테이너 안에서 돌기 때문입니다. 로컬 환경이 가벼워지고, "무엇이 어디서 도는지"가 분명해집니다.

## 프로젝트별 uv 환경

각 예제는 독립된 프로젝트이고, 환경은 [uv](https://docs.astral.sh/uv/)로 관리합니다.
프로젝트마다 `pyproject.toml`, `uv.lock`, `.python-version`을 두어 서로 간섭 없이 정확히
같은 의존성을 재현할 수 있게 했습니다.

사용법은 프로젝트 폴더로 들어가 `uv sync`로 환경을 만든 뒤, `uv run <스크립트>`로 실행하는
식입니다. `uv run`이 그 프로젝트의 가상환경에서 실행하므로 `activate` 과정은 필요 없습니다.

```bash
cd sm-training-job/hello-mnist
uv sync
uv run launch.py --dry-run
```

로컬 의존성은 `sagemaker`(v3)와 `boto3`뿐입니다. 학습 프레임워크는 각 예제의 컨테이너
정의(스크립트 모드는 관리형 DLC, BYOC는 `container/Dockerfile`) 안에 들어갑니다.

## 예제 목록

- **[hello-mnist](sm-training-job/hello-mnist/README.ko.md) — 스크립트 모드**
  가장 쉬운 방식입니다. AWS가 관리하는 PyTorch 컨테이너(DLC)를 그대로 쓰고, 내 학습 코드
  (`train.py`)만 넘기면 SageMaker가 나머지를 처리합니다. Docker를 다룰 필요가 없어서,
  SageMaker 학습을 처음 본다면 여기서 시작하는 게 좋습니다.

- **[byoc-mnist](sm-training-job/byoc-mnist/README.ko.md) — BYOC (커스텀 컨테이너)**
  Docker 이미지를 직접 만들어 ECR에 올리고 그 이미지로 학습시키는 방식(Bring Your Own
  Container)입니다. 컨테이너 규약(`/opt/ml` 경로, 실행 방식 등)을 직접 지켜야 하는 대신
  런타임을 완전히 통제할 수 있습니다. hello-mnist와 비교해 보면 스크립트 모드에서 SageMaker가
  뒤에서 무엇을 대신 해주는지 드러납니다.

- **[sm-xgboost-lab](sm-xgboost-lab/README.ko.md) — XGBoost, MLflow, Model Registry**
  UCI 신용 연체 공개 dataset으로 built-in XGBoost Training Job을 실행하고, managed MLflow에서
  실험 결과를 비교한 뒤 Model Registry 승인과 Batch Transform까지 이어지는 실습입니다.
  CloudFormation으로 필요한 인프라를 만들며, SageMaker Studio가 필요합니다.

- **[sm-pipeline-quick](sm-pipeline-quick/README.ko.md) — SageMaker Pipeline 흐름 참고**
  `sm-xgboost-lab`의 인프라를 바탕으로 Redshift snapshot, Processing, Automatic Model Tuning,
  managed MLflow, Model Registry quality gate를 연결한 Pipeline DAG를 살펴보는 참고 예제입니다.
  실행 전 `sm-xgboost-lab`에서 `./scripts/bootstrap-infra.sh`로 bootstrap 인프라를 먼저
  만들어야 합니다. 기본 launcher는 Pipeline 정의만 등록하므로 실제 Redshift query나 Training
  Job을 실행하지 않습니다.

## 사전 준비 (공통)

- **uv** — 파이썬 환경/패키지 관리
- **AWS CLI v2** + 자격증명과 기본 리전 (`aws configure`, `aws sts get-caller-identity`로 확인)
- **SageMaker 실행 역할** — MNIST 예제의 `launch.py`는 계정에서 자동 탐지합니다.
  XGBoost 실습은 `scripts/bootstrap-infra.sh`가 CloudFormation으로 필요한 역할을 만듭니다.
  자세한 내용은 각 프로젝트 README를 참고하세요.
- **Docker** — BYOC 예제(byoc-mnist)에서 이미지 빌드에 필요합니다. XGBoost Pipeline 참고 예제를
  실제 Processing까지 확장할 때도 필요합니다.
- **SageMaker Studio** — `sm-xgboost-lab`에서 managed MLflow와 Model Registry를 확인할 때
  필요합니다.

각 예제의 구체적인 실행 방법과 설정은 해당 프로젝트의 README에 있습니다.
