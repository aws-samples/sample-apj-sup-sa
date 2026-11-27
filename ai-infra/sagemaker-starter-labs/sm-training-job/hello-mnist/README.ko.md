[English](README.md) | 한국어

# hello-mnist — SageMaker Training Job 예제

관리형 PyTorch 컨테이너에서 MNIST 분류 모델을 학습하는 SageMaker Training Job 예제입니다.

로컬 환경은 **uv**로 격리해 관리하고, 실제 학습은 **AWS Deep Learning Container(DLC)** 위에서 실행됩니다.

---

## 핵심 개념: 두 개의 환경

이 예제의 개발 환경과 학습 환경은 완전히 분리되어 있습니다.

| | ① 로컬 PC | ② 학습 컨테이너 (SageMaker 위) |
|---|---|---|
| 하는 일 | `launch.py` 로 Job 제출 | `src/train.py` 로 실제 학습 |
| 관리 도구 | **uv** (`pyproject.toml`) | **AWS DLC** (torch 등 내장) |
| 필요한 패키지 | `sagemaker-core`, `sagemaker-train`, `boto3` | `torch`, `torchvision` (DLC에 이미 포함) |
| GPU/CUDA | ❌ 불필요 | ✅ DLC에 포함 |

> **torch를 로컬에 설치하지 않습니다.** 학습 코드(`train.py`)는 로컬이 아니라
> AWS가 실행하는 컨테이너 안에서 실행되기 때문입니다. 이 때문에 로컬 환경이 가볍고 빠릅니다.

---

## 파일 구조

```
hello-mnist/
├─ pyproject.toml       # 로컬 의존성 (sagemaker, boto3) — uv가 관리
├─ .python-version      # 로컬 파이썬 버전 고정 (3.11)
├─ uv.lock              # 재현용 잠금 파일 (uv sync가 생성)
├─ launch.py            # ① 로컬에서 실행: Training Job 제출
├─ jobstatus.py         # ① 로컬에서 실행: Job 상태/결과 확인
└─ src/                 # ② 통째로 컨테이너에 업로드되는 코드
   └─ train.py          #    컨테이너 안에서 실행되는 학습 스크립트
```

---

## 사전 준비

### 1. 도구 (이미 설치돼 있음)

- [uv](https://docs.astral.sh/uv/) — 파이썬 환경/패키지 관리
- AWS CLI v2 — 자격증명 설정용

### 2. AWS 자격증명

```bash
aws configure          # 또는 SSO / 환경변수 / EC2 인스턴스 역할 등
aws sts get-caller-identity   # 자격증명 확인
```

리전은 CONFIG `REGION`, `AWS_REGION`, `AWS_DEFAULT_REGION`, 기본 AWS 설정 순서로 선택합니다.

### 3. IAM 실행 역할 (SageMaker Execution Role)

Training Job은 **SageMaker 실행 역할**로 실행됩니다. `launch.py` CONFIG의 `ROLE`에
ARN을 직접 지정하세요. 계정에서 역할을 자동으로 선택하지 않습니다. 적합한 역할이 없으면 만듭니다.

- 콘솔: **IAM → Roles → Create role → Use case: SageMaker** 선택 →
  `AmazonSageMakerFullAccess` 정책 부여 (학습용으로는 이것으로 충분)
- 이 실습에 사용할 역할을 선택하고, Job 제출 전에 ARN을 `ROLE`에 지정합니다.

역할에 필요한 최소 권한은 SageMaker 학습, 산출물 S3 버킷 읽기/쓰기, ECR pull(DLC), CloudWatch Logs입니다.
`AmazonSageMakerFullAccess`가 이를 모두 포함합니다.

---

학습 컨테이너에서 MNIST를 내려받으므로 인터넷 접근이 필요합니다. network isolation을 사용하거나
VPC에 NAT 등의 인터넷 경로가 없으면 다운로드가 실패합니다. 로컬에서 준비한 데이터를 S3 입력으로
전달하는 방식은 [byoc-mnist](../byoc-mnist/README.ko.md)를 참고하세요.

## 실행 방법

모든 명령은 이 폴더(`hello-mnist/`)에서 실행합니다. `uv run`이 프로젝트 전용
가상환경(`.venv`)을 자동으로 만들고 그 안에서 실행하므로 `activate`는 필요 없습니다.

### Step 0. 환경 준비 (최초 1회)

```bash
uv sync
```

`pyproject.toml`을 읽어 `.venv`를 만들고 `sagemaker-core`, `sagemaker-train`, `boto3`를 설치합니다.

### Step 1. 설정 검증 (권장, AWS 과금 없음)

```bash
uv run launch.py --dry-run
```

Training Job을 **제출하지 않고** 리전·DLC 이미지 URI·인스턴스 설정만 출력합니다.
출력 예:

```
region   : ap-northeast-2
image    : 763104351884.dkr.ecr.ap-northeast-2.amazonaws.com/pytorch-training:2.7.1-cpu-py312
instance : ml.c6i.2xlarge
epochs   : 3
[dry-run] Not submitted. Configuration check complete.
```

### Step 2. Job 제출

```bash
uv run launch.py
```

- 기본값은 **CPU 인스턴스(`ml.c6i.2xlarge`, 8 vCPU)** 입니다.
  신규 계정은 GPU 쿼터가 0인 경우가 많으므로 처음 실행할 때는 CPU 인스턴스를 권장합니다.
- 기본적으로 **완료될 때까지 대기**하며 로그를 실시간으로 출력합니다(수 분 내 완료).
- 제출된 Job 이름이 `.last_job.json`에 기록됩니다.

### Step 3. 상태 / 결과 확인

```bash
uv run jobstatus.py                         # .last_job.json 의 마지막 Job 확인
uv run jobstatus.py hello-mnist-2026-...    # 특정 Job 이름으로 확인
```

`Completed`가 되면 모델 아티팩트 S3 경로(`.../output/model.tar.gz`)를 출력합니다.

---

## 설정 바꾸기

모든 설정은 `launch.py` 상단의 **CONFIG 블록** 한 곳에 있습니다. 값을 수정한 뒤 다시 실행합니다.

```python
EPOCHS = 3
INSTANCE_TYPE = "ml.c6i.2xlarge"   # GPU로 학습하려면 예: "ml.g4dn.xlarge" (계정 쿼터 필요)
FRAMEWORK_VERSION = "2.7.1"        # PyTorch DLC 버전 (예: "2.7.1", "2.8.0")
MAX_RUN_SECONDS = 3600             # 최대 실행 시간(안전장치). 예: 86400(24h)
WAIT = True                        # False면 제출 즉시 리턴(비동기)
ROLE = "arn:aws:iam::<account-id>:role/<role-name>"  # 필수 실행 역할
REGION = None                      # AWS 리전. None이면 기본 세션 리전
```

예를 들어 GPU로 10 epoch 학습하려면 `INSTANCE_TYPE = "ml.g4dn.xlarge"`, `EPOCHS = 10`으로
변경한 뒤 `uv run launch.py`를 실행합니다. 먼저 `--dry-run`으로 확인하는 것을 권장합니다.

---

## 콘솔에서 모니터링

Job을 제출한 뒤 [SageMaker 콘솔](https://console.aws.amazon.com/sagemaker/)에서 다음을 확인할 수 있습니다.

1. **Training → Training jobs** 목록에서 상태 확인 (`InProgress → Completed`)
2. Job 클릭 → **View logs** (CloudWatch)에서 실시간 로그 확인
3. **View algorithm metrics**에서 `test:accuracy` 그래프 확인
   (이 지표는 `launch.py`의 정규식이 `train.py`의 `test_accuracy=...`
   출력을 파싱해 만듭니다)

---

## 동작 원리 (요약)

1. `launch.py`가 `src/` 전체를 S3로 업로드하고, 관리형 PyTorch DLC를 학습 이미지로 지정해
   `ModelTrainer.train()`으로 Training Job을 제출합니다(기본은 완료까지 대기).
2. SageMaker가 인스턴스를 시작하고 DLC 컨테이너 안에서 `train.py`를
   `python train.py --epochs 3` 형태로 실행합니다(하이퍼파라미터는 CLI 인자로 전달됨).
3. `train.py`는 MNIST를 내려받아 CNN을 학습하고, `SM_MODEL_DIR`(`/opt/ml/model`)에
   모델을 저장합니다.
4. Job이 끝나면 SageMaker가 그 폴더를 `model.tar.gz`로 묶어 S3 출력 경로에 업로드합니다.
5. 인스턴스는 자동 종료됩니다(과금은 학습 시간만큼만).

---

## 자주 겪는 문제

| 증상 | 원인 / 해결 |
|---|---|
| `Set ROLE in launch.py CONFIG` | 실행 역할이 없음. 위 **IAM 실행 역할** 섹션 참고 후 CONFIG의 `ROLE`에 ARN 지정 |
| `Could not find a region` | `aws configure`로 기본 리전 설정하거나 CONFIG의 `REGION` 지정 |
| `ResourceLimitExceeded` (GPU) | 해당 인스턴스 쿼터가 0. CPU로 실행하거나 Service Quotas에서 증설 요청 |
| Job이 `Failed` | `uv run jobstatus.py`로 `FailureReason` 확인 → CloudWatch 로그에서 상세 스택트레이스 |
| 데이터 다운로드 실패 | 학습 인스턴스가 VPC-only(인터넷 차단)로 설정된 경우. MNIST를 S3에 올려 입력 채널로 전달해야 함 |


## 개발 검사

이 프로젝트 폴더에서 실행합니다. 공통 테스트는 AWS 호출과 데이터 다운로드를 모킹합니다.

```bash
uv sync --frozen --group dev
uv run --group dev ruff check launch.py jobstatus.py ../tests
uv run --group dev pytest ../tests
```
