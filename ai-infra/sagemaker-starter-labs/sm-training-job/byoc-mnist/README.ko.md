[English](README.md) | 한국어

# byoc-mnist — SageMaker BYOC(커스텀 컨테이너) 예제

같은 MNIST 데이터셋을 직접 만든 Docker 이미지로 학습하는 SageMaker Training Job 예제입니다.
옆 폴더 `hello-mnist`(관리형 컨테이너 + 스크립트 모드)와 짝을 이루는 BYOC(Bring Your Own
Container) 버전입니다. AWS가 제공하는 컨테이너 대신 처음부터 직접 만든 컨테이너를 SageMaker에
전달해 학습합니다.

---

## hello-mnist(스크립트 모드) vs byoc-mnist(BYOC)

| | hello-mnist (스크립트 모드) | byoc-mnist (BYOC) |
|---|---|---|
| 컨테이너 | AWS 관리형 PyTorch DLC | **직접 만든 이미지 (ECR)** |
| 제공하는 것 | `train.py` 코드만 | **Docker 이미지 통째로** |
| Docker 빌드 | 불필요 | 직접 빌드 & ECR 푸시 |
| torch 설치 | DLC에 이미 있음 | 직접 작성한 Dockerfile에서 설치 |
| SageMaker 실행 | SDK가 넣은 래퍼가 `train.py` 실행 | `docker run <image> train` 그대로 |
| 사용 시점 | 지원 프레임워크로 충분할 때 (대부분) | 특수 런타임/OS/의존성이 필요할 때 |

스크립트 모드에서는 AWS가 컨테이너와 실행 방식을 관리해 주고, BYOC에서는 컨테이너 규약을 직접
지켜야 합니다. BYOC 이미지를 직접 만들어 보면 스크립트 모드에서 SageMaker가 내부적으로 대신
처리하던 작업을 파악할 수 있습니다.

실무에서 BYOC가 필요한 경우는 많지 않습니다. 컨테이너를 다루는 방법은 세 가지이고 대부분 앞의 두
가지로 해결되는데, 이 내용은 다음 섹션에서 다룹니다. 이 예제는 학습 목적으로 가장 낮은 수준의 방식인
BYOC를 직접 구현합니다.

---

## 세 가지 방식: 스크립트 모드 · DLC 확장 · BYOC

SageMaker에서 학습 컨테이너를 다루는 방법은 세 가지이며, 뒤로 갈수록 관리 부담과 장애
가능성이 커집니다. BYOC를 만들기 전에 앞의 두 방법으로 요구 사항을 충족할 수 있는지 먼저 검토할 것을 권장합니다.

| | 스크립트 모드 | DLC 확장 | BYOC (이 예제) |
|---|---|---|---|
| 하는 일 | 코드만 전달 | prebuilt에 패키지 추가 | 이미지를 처음부터 작성 |
| Dockerfile | 없음 | `FROM <prebuilt DLC>` + 몇 줄 | 전부 직접 |
| CUDA·드라이버·서빙스택 | AWS 관리 | AWS 관리 (그대로 물려받음) | 직접 관리 |
| 난이도 / 위험 | 가장 낮음 | 낮음 | 높음 |

버전 불일치 문제는 대부분 중간 단계인 DLC 확장으로 해결됩니다. DLC 확장이 무엇이고
prebuilt 이미지를 어떻게 찾는지는 [부록 A](#부록-a-dlc-확장)와 [부록 B](#부록-b-prebuilt-이미지-확인하기)에,
BYOC 컨테이너가 지켜야 하는 규약은 [부록 C](#부록-c-byoc-컨테이너-규약)에 정리했습니다.

---

## 파일 구조

```
byoc-mnist/
├─ pyproject.toml        # 로컬 의존성 (sagemaker, boto3) — uv가 관리
├─ build_and_push.sh     # 이미지 빌드(linux/amd64) → ECR 푸시
├─ launch.py             # MNIST 준비 + 커스텀 이미지로 Training Job 제출 (CONFIG 블록)
├─ jobstatus.py          # Job 상태/결과 확인
├─ data/                 # launch.py 실행 시 MNIST가 받아지는 곳 (git 무시)
└─ container/            # Docker 이미지 안에 들어가는 것들
   ├─ Dockerfile         #    python:3.11-slim + torch, train을 PATH에
   └─ train              #    SageMaker가 실행하는 학습 프로그램 (파일명 그대로 'train')
```

---

## 사전 준비

- **Docker Desktop** 실행 중 (이미지 빌드용)
- **AWS 자격증명** + 기본 리전 (`aws configure`, `aws sts get-caller-identity`로 확인)
- **IAM 실행 역할** — `launch.py`가 계정에서 `AmazonSageMaker-ExecutionRole-*`를 자동으로 찾습니다.
  역할이 없으면 IAM에서 SageMaker 실행 역할을 만들고(`AmazonSageMakerFullAccess`면 충분)
  `launch.py`의 CONFIG `ROLE`에 ARN을 지정하세요.
- 역할이 ECR에서 이미지를 pull할 수 있어야 합니다(`AmazonSageMakerFullAccess`에 포함됨).

---

## 실행 방법

모든 명령은 이 폴더(`byoc-mnist/`)에서 실행합니다.

### Step 0. 로컬 환경 준비 (최초 1회)

```bash
uv sync
```

### Step 1. 이미지 빌드 & ECR 푸시

```bash
./build_and_push.sh
```

- `container/`의 Dockerfile로 이미지를 빌드하고 ECR 리포지토리(`byoc-mnist`)에 올립니다.
- SageMaker 학습 인스턴스는 x86_64(amd64)이므로 `--platform linux/amd64`로 빌드합니다
  (빌드 스크립트에 이미 반영됨).
- 완료되면 이미지 URI를 출력합니다:
  `<account>.dkr.ecr.<region>.amazonaws.com/byoc-mnist:latest`

### Step 2. 설정 확인 (권장, AWS 과금 없음)

```bash
uv run launch.py --dry-run
```

이미지 URI·인스턴스·리전만 출력하고 제출하지 않습니다.

### Step 3. Training Job 제출

```bash
uv run launch.py
```

먼저 MNIST를 로컬 `data/`에 준비해 `training` 채널로 전달합니다(S3 업로드는 SDK가 수행합니다).
`ModelTrainer`에는 커스텀 이미지(`training_image`)만 전달하고 `source_code`는 지정하지 않으므로,
SageMaker가 `docker run <image> train`으로 컨테이너를 실행하고 컨테이너의 `train`이 그대로 실행됩니다.
기본적으로 학습이 끝날 때까지 대기하면서 CloudWatch 로그를 실시간으로 출력합니다.

### Step 4. 상태 / 결과 확인

```bash
uv run jobstatus.py                    # 마지막 Job 확인
uv run jobstatus.py <job-name>         # 특정 Job 확인
```

`Completed`가 되면 모델 아티팩트 S3 경로(`.../output/model.tar.gz`)를 출력합니다.

---

## 설정 바꾸기

모든 설정은 `launch.py` 상단 CONFIG 블록 한 곳에 있습니다.

```python
EPOCHS = 3
INSTANCE_TYPE = "ml.c6i.2xlarge"   # 이미지가 CPU 빌드라 CPU 인스턴스
MAX_RUN_SECONDS = 3600
WAIT = True                        # False면 제출 즉시 리턴(비동기)
ECR_REPO = "byoc-mnist"            # build_and_push.sh 의 리포명과 일치해야 함
IMAGE_TAG = "latest"
IMAGE_URI = None                   # 직접 지정 가능. None이면 계정/리전+ECR_REPO로 자동 조합
ROLE = None                        # None이면 자동 탐지
REGION = None                      # None이면 기본 세션 리전
```

---

## 동작 원리

1. `build_and_push.sh`가 `container/`를 linux/amd64 이미지로 빌드해 ECR에 올립니다.
2. `launch.py`가 그 이미지 URI로 `ModelTrainer.train()`을 호출합니다. `source_code`를 지정하지
   않으므로 순수 BYOC로 동작합니다.
3. `launch.py`가 MNIST를 로컬에 준비해 `training` 채널로 전달하면, SDK가 S3에 업로드하고
   SageMaker가 인스턴스를 시작해 `docker run <image> train`을 실행합니다. 이때 `container/train`이
   실행됩니다.
4. `train`은 `/opt/ml/input/config/hyperparameters.json`을 읽고,
   `/opt/ml/input/data/training/`에 마운트된 MNIST로 학습한 뒤 모델을 `/opt/ml/model/`에 저장합니다.
5. SageMaker가 `/opt/ml/model`을 `model.tar.gz`로 묶어 S3에 업로드하고, 인스턴스는
   자동 종료됩니다(과금은 학습 시간만큼).

---

## 데이터 흐름 (준비, S3 업로드, Job 제출)

`uv run launch.py`를 실행하면 아래 단계가 순서대로 진행됩니다. 앞의 두 단계는 로컬 PC에서,
세 번째 단계부터는 AWS에서 실행됩니다.

```
 [로컬 PC]                                                  [AWS]

 1) MNIST 준비      2) S3 업로드         3) Job 제출        4) 채널 마운트 + 학습
 ┌──────────────┐   ┌────────────────┐   ┌──────────────┐   ┌────────────────────────┐
 │ data/MNIST/  │   │ s3://<기본     │   │ create_      │   │ /opt/ml/input/data/    │
 │   raw/*.ubyte│──▶│  버킷>/        │──▶│ training_job │──▶│   training/ 에 마운트  │
 │              │   │ byoc-mnist/    │   │ 호출         │   │ → train 이 여기서 읽음 │
 │              │   │ input/training │   │              │   │                        │
 └──────────────┘   └────────────────┘   └──────────────┘   └────────────────────────┘
 내가 짠 코드       SDK가 자동 처리      SDK가 자동 처리    SageMaker가 처리
```

### 1) MNIST 준비 (`prepare_mnist_data`)

`launch.py`가 MNIST 원본 파일 4개를 로컬 `data/MNIST/raw/`에 다운로드하고 압축을 풉니다.
이 디렉터리 구조는 컨테이너 안의 torchvision이 `download=False`로 읽을 때 기대하는
레이아웃(`<root>/MNIST/raw/`)과 정확히 일치합니다. 함수는 준비된 로컬 경로 문자열을 반환합니다.

### 2)~3) 채널 지정과 제출

```python
training_channel = InputData(channel_name="training", data_source=data_dir)
trainer.train(input_data_config=[training_channel], ...)
```

`data_source`에는 S3 URI가 아니라 로컬 경로를 전달합니다. SageMaker는 AWS의 학습 인스턴스에서
실행되어 로컬 PC의 파일에 접근할 수 없으므로, SDK가 `train()` 호출 시점에 S3 업로드를
자동으로 수행합니다.

### S3 업로드는 SDK가 대신 합니다

SDK는 `data_source` 값을 보고 분기합니다. `s3://...` URI면 그대로 사용하고, 로컬 경로면 S3에
업로드한 뒤 그 S3 URI로 채널을 구성합니다. 이 예제는 후자입니다.

로컬 경로일 때 업로드 위치는 규칙으로 자동 결정됩니다.

```
s3://<계정 기본 SageMaker 버킷>/<base_job_name>/input/<channel_name>/
  예) s3://sagemaker-ap-northeast-2-<account>/byoc-mnist/input/training/
```

버킷을 별도로 만들 필요는 없습니다. SDK는 계정·리전별 기본 버킷 `sagemaker-<region>-<account>`를
사용하고, 버킷이 없으면 생성합니다. 경로는 `base_job_name`(`"byoc-mnist"`)에 `input/`과 채널명(`training`)을
붙여 정합니다. 실제 업로드는 SDK 내부에서 boto3로 수행됩니다. 따라서 예제 코드에는
버킷 이름이나 `upload` 호출이 없으며, `data_source`에 로컬 경로를 전달하는 것만으로 업로드가 처리됩니다.

업로드가 끝나면 SDK는 채널의 위치를 방금 만든 S3 URI로 바꿔 `create_training_job`을
호출합니다. 이 요청의 `InputDataConfig`에 이 채널이 담깁니다(채널이 하나도 없으면 이 값이
비어 API 검증에 실패하므로, BYOC라도 입력 채널이 최소 하나 필요합니다).

### 4) 컨테이너가 채널을 받음

SageMaker가 인스턴스를 시작하고, S3의 `training` 채널 데이터를 컨테이너의
`/opt/ml/input/data/training/`으로 다운로드해 마운트합니다. 그다음 `docker run <image> train`이
실행되고, `train`은 그 경로에서 MNIST를 읽어 학습합니다.

`InputData`에 로컬 경로를 전달하면 SDK가 그 폴더를 기본 SageMaker 버킷의
`<job이름>/input/<채널명>/`에 업로드하고 해당 S3 위치를 Job에 전달합니다. SageMaker는 학습 시점에
이 데이터를 컨테이너의 `/opt/ml/input/data/<채널명>/`으로 다운로드합니다.

---

## 다음 단계: 추론 컨테이너

이 예제는 학습만 다룹니다. 같은 이미지에 추론 기능을 넣으려면 `serve` 실행 파일을 추가합니다.
SageMaker가 `docker run <image> serve`로 호출하면 8080 포트에서 웹 서버를 실행해 두 엔드포인트를
제공해야 합니다. `GET /ping`은 모델이 로드됐으면 HTTP 200을 반환하는 헬스체크(요청당 타임아웃
2초)이고, `POST /invocations`는 요청 본문을 받아 추론 결과를 돌려줍니다(기본 응답은 60초 이내).
모델은 SageMaker가 `/opt/ml/model`에 미리 풀어 두므로 해당 경로에서 모델을 읽습니다(읽기 전용).

MNIST 규모라면 Flask 앱 하나를 8080 포트에서 실행해 위 두 엔드포인트만 구현해도 규약을 충족합니다
(nginx/gunicorn/MMS는 필요 없습니다). 배포는
`sagemaker.model.Model(image_uri=..., model_data=<S3 model.tar.gz>).deploy(...)`로 합니다.
규약 상세는 [추론 컨테이너 문서](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html)를
참고하세요.

---

## 자주 겪는 문제

| 증상 | 원인 / 해결 |
|---|---|
| `exec format error` (Job 즉시 실패) | 이미지 아키텍처 불일치. `--platform linux/amd64`로 빌드 (SageMaker는 x86_64) |
| `train: not found` / permission denied | `train`이 PATH에 없거나 실행권한 없음. Dockerfile의 `chmod 755` + `ENV PATH` 확인 |
| 모델이 S3에 올라가지 않음 | 모델을 `/opt/ml/model/`이 아닌 다른 곳에 저장함 |
| 하이퍼파라미터가 적용되지 않음 | JSON 값은 문자열이라 `int(hp["epochs"])`처럼 캐스팅해야 함 |
| Job이 `Failed` | `uv run jobstatus.py`로 `FailureReason` 확인 후 CloudWatch 로그에서 스택트레이스 확인 |
| ECR pull 권한 오류 | 실행 역할에 ECR 읽기 권한 필요(`AmazonSageMakerFullAccess`에 포함) |

---

## 참고 문서

- BYOC 학습 컨테이너 규약: https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html
- `/opt/ml` 디렉터리 구조: https://docs.aws.amazon.com/sagemaker/latest/dg/amazon-sagemaker-toolkits.html
- 추론 컨테이너 규약(`serve`, `/ping`, `/invocations`): https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html
- BYOC를 쓰는 경우: https://docs.aws.amazon.com/sagemaker/latest/dg/docker-containers-create.html

---

## 부록 A. DLC 확장

DLC(Deep Learning Container)는 AWS가 미리 만들어 둔 학습용 Docker 이미지입니다. 특정 버전의
PyTorch와 그에 맞는 CUDA·cuDNN·GPU 드라이버 설정, SageMaker 컨테이너 규약(입력/출력 경로
처리, 학습 실행 래퍼), 추론용 서빙 스택이 이미 들어 있습니다.

DLC 확장은 이 이미지를 `FROM`으로 베이스 삼아 부족한 패키지만 추가하는 방식입니다. AWS가
검증한 기반은 그대로 유지하고 그 위에 얇은 레이어 하나만 추가합니다.

```dockerfile
# 예: AWS의 PyTorch 2.7.1 GPU 이미지를 그대로 베이스로 사용
FROM 763104351884.dkr.ecr.ap-northeast-2.amazonaws.com/pytorch-training:2.7.1-gpu-py312

# 필요한 패키지만 추가로 설치 (CUDA·드라이버·PyTorch는 이미 있음)
RUN pip install --no-cache-dir transformers==4.44 accelerate
```

이렇게 만든 이미지는 ECR에 올린 뒤 스크립트 모드처럼 사용하거나(SDK가 `SAGEMAKER_PROGRAM`으로
지정한 코드를 실행) BYOC처럼 사용할 수 있습니다. AWS가 맞춰 둔 CUDA·드라이버 조합을 그대로
사용하므로 GPU에서 버전이 맞지 않아 실행되지 않는 문제를 피할 수 있고, 베이스 이미지가 갱신되면 `FROM`
태그만 변경해도 보안 패치가 함께 적용됩니다. 직접 수정하는 부분이 pip 패키지 몇 줄에 불과하므로 장애 요인이 적습니다.

버전이 맞지 않는 경우는 대부분 이 방식으로 해결됩니다. 예를 들어 prebuilt의 최신이 PyTorch
2.7인데 2.7.2가 필요하다면, `FROM` 한 뒤 `pip install torch==2.7.2`로 그 버전만
덮어씁니다. `FROM`으로 시작해 `pip install`로 끝나는 일이면 DLC 확장으로 충분합니다. DLC 확장으로 해결되지 않는 경우
— 애초에 베이스가 될 prebuilt가 없거나, CUDA·OS 자체를 바꿔야 하거나, 이미 있는 프로덕션
이미지를 그대로 가져와야 하는 경우 — 에만 BYOC를 선택합니다.

---

## 부록 B. prebuilt 이미지 확인하기

`FROM`에 쓸 이미지 URI는 세 가지 경로로 찾을 수 있습니다.

전체 목록을 한눈에 보려면 AWS가 관리하는
[available_images.md](https://github.com/aws/deep-learning-containers/blob/master/available_images.md)를
확인합니다. 프레임워크·버전·CPU/GPU·Python·CUDA·용도(training/inference)별 태그가 표로 정리되어
있습니다.

리전마다 이미지 계정과 URI가 다르므로, 사용 중인 리전의 정확한 값은 공식 문서의
[Docker Registry Paths 목록](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-algo-docker-registry-paths.html)에서
리전을 골라 확인합니다.

SDK로 직접 조회하는 방법이 가장 빠릅니다(SDK는 이 프로젝트에 이미 설치되어 있습니다).
`image_uris.retrieve`가 현재 리전에 맞는 URI를 계산하므로 원하는 버전의 제공 여부를 바로
확인할 수 있습니다.

```python
from sagemaker.core import image_uris

# 특정 버전의 정확한 URI 얻기
print(image_uris.retrieve(
    framework="pytorch", region="ap-northeast-2", version="2.7.1",
    image_scope="training", instance_type="ml.g5.xlarge",  # gpu 인스턴스 → gpu 이미지
))
# → 763104351884.dkr.ecr.ap-northeast-2.amazonaws.com/pytorch-training:2.7.1-gpu-py312

# 학습용으로 지원되는 PyTorch 버전 전체 목록
cfg = image_uris.config_for_framework("pytorch")
print(sorted(cfg["training"]["versions"]))
```

`763104351884`은 AWS 공식 DLC 이미지 계정입니다(대부분의 리전에서 같지만 일부 리전은 다르므로 위
문서에서 확인하세요). `instance_type`이 CPU 계열이면 `-cpu-`, GPU 계열이면 `-gpu-` 이미지가
반환됩니다.

---

## 부록 C. BYOC 컨테이너 규약

SageMaker는 학습을 시작할 때 다음과 같이 실행합니다
([문서](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html)):

```
docker run <이미지> train
```

이 명령은 `train`이라는 실행 파일(PATH에 있고 실행 권한이 있어야 함)을 실행합니다. `train`은
아래 경로를 규약에 맞게 처리해야 합니다. 각 경로의 파일 자체는 SageMaker가 컨테이너 안에 만듭니다.
각 경로의 근거 문서는 오른쪽 열에 링크해 두었습니다.

| 경로 | 용도 | 공식 문서 |
|---|---|---|
| `/opt/ml/input/config/hyperparameters.json` | 하이퍼파라미터 (값이 전부 문자열이라 캐스팅 필요) | [입력 데이터·하이퍼파라미터](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-running-container.html) |
| `/opt/ml/input/data/training/` | `training` 입력 채널 — `launch.py`가 MNIST를 S3에 올려 여기 마운트 | [입력 데이터 채널](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-running-container.html) · [디렉터리 구조](https://docs.aws.amazon.com/sagemaker/latest/dg/amazon-sagemaker-toolkits.html) |
| `/opt/ml/model/` | 여기 저장하면 SageMaker가 `model.tar.gz`로 묶어 S3 업로드 | [모델·출력 경로](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html) |
| `/opt/ml/output/failure` | 실패 시 이유를 적으면 콘솔 `FailureReason`에 표시 (앞 1024자) | [실패 파일](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html) |

성공 여부는 종료 코드로 전달됩니다. `exit 0`이면 성공(Completed), 0이 아니면 실패(Failed)입니다
([성공/실패 신호 문서](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-signal-success-failure.html)).

추론용 규약은 별도로 정의되어 있습니다. `docker run <이미지> serve`로 실행되어 8080 포트에서
`GET /ping`(헬스체크)과 `POST /invocations`(추론)를 제공하는 방식이며, 자세한 내용은
위 "다음 단계: 추론 컨테이너"에 정리했습니다.
