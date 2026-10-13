# Whisper Large V3 Turbo on Amazon SageMaker

Deploy [`openai/whisper-large-v3-turbo`](https://huggingface.co/openai/whisper-large-v3-turbo)
as a real-time speech-to-text endpoint on Amazon SageMaker — with two serving
backends, autoscaling, load-test tooling, and published benchmarks.

Built for **turn-based voice agents** (VAD-segmented utterances → transcript), but
works for any batch transcription workload.

---

## Two serving options

| | **Hugging Face** (`huggingface/`) | **vLLM** (`vllm/`) |
|---|---|---|
| Engine | Transformers ASR pipeline (AWS DLC) | vLLM OpenAI server + continuous batching |
| Throughput (24s clip, 1× g5.xlarge) | ~2.1 rps | **~6.4 rps (≈3×)** |
| Latency under load | degrades past ~2 concurrent | scales smoothly |
| Setup | simplest; **no Docker option** available | requires building an image |
| Best for | low volume, quick start | high concurrency / many calls |

Both bake the model weights into the deployment artifact, so **new instances
(including autoscaled ones) start with no Hugging Face download and no pip install**.
Full numbers: [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

---

## Repository layout

```
.
├── common/                 # shared tooling (used by both options)
│   ├── download_model.py   #   fetch model weights -> model_artifacts/snapshot
│   ├── deploy_from_ecr.py  #   deploy a baked ECR image to an endpoint
│   ├── invoke.py           #   send audio, print transcript
│   ├── benchmark.py        #   load test (throughput / latency / RTF)
│   ├── autoscale.py        #   attach target-tracking autoscaling
│   └── cleanup.py          #   delete endpoint + config + model + autoscaling
├── huggingface/            # Option A: Transformers pipeline
│   ├── inference.py        #   custom SageMaker handler
│   ├── Dockerfile          #   bakes model + handler into the HF DLC
│   ├── build_and_push.sh   #   build & push the image to ECR
│   ├── deploy_s3.py        #   no-Docker alternative (DLC + S3 weights)
│   └── README.md
├── vllm/                   # Option B: vLLM high-throughput
│   ├── proxy.py            #   SageMaker /ping + /invocations -> vLLM transcription
│   ├── launch.py           #   container entrypoint (starts vLLM + proxy)
│   ├── Dockerfile          #   bakes model + proxy into the vLLM image
│   ├── build_and_push.sh
│   └── README.md
├── docs/BENCHMARKS.md      # full benchmark tables + methodology
├── samples/                # example audio
├── requirements.txt        # local tooling deps
└── LICENSE
```

---

## Prerequisites

- AWS account with SageMaker, ECR, S3, and IAM pass-role access; credentials configured.
- A **SageMaker execution role ARN** with the following permissions:
  - `sagemaker:CreateModel`, `sagemaker:CreateEndpointConfig`, `sagemaker:CreateEndpoint`,
    `sagemaker:InvokeEndpoint`, `sagemaker:DeleteModel`, `sagemaker:DeleteEndpoint`,
    `sagemaker:DeleteEndpointConfig`
  - `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`
  - `s3:GetObject`, `s3:PutObject` on your SageMaker bucket
  - `iam:PassRole` on the execution role itself
  - `application-autoscaling:*` (only if using `common/autoscale.py`)

  `AmazonSageMakerFullAccess` works for quick experimentation but is overly broad
  for production — scope down to the actions above.
- **GPU endpoint quota** for your instance type (e.g. `ml.g5.xlarge for endpoint usage`).
- Python 3.10+ and the local deps:
  ```bash
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```
- For the image-based options: **Docker** with buildx (Docker Desktop is fine).

---

## Quickstart

### Option A — Hugging Face, no Docker (simplest)

```bash
python huggingface/deploy_s3.py \
  --role arn:aws:iam::<account-id>:role/<SageMakerExecutionRole> \
  --region <region> --instance-type ml.g5.xlarge --endpoint-name whisper-hf

# wait until InService, then:
python common/invoke.py --endpoint-name whisper-hf --region <region> --audio samples/sample.wav
```

### Option B — vLLM (high throughput, image-based)

```bash
# Build & push the image (downloads model, bakes it in, pushes to ECR)
vllm/build_and_push.sh <region> whisper-vllm latest

# Deploy from the pushed image
python common/deploy_from_ecr.py \
  --image-uri <account-id>.dkr.ecr.<region>.amazonaws.com/whisper-vllm:latest \
  --role arn:aws:iam::<account-id>:role/<SageMakerExecutionRole> \
  --region <region> --instance-type ml.g5.xlarge --endpoint-name whisper-vllm

python common/invoke.py --endpoint-name whisper-vllm --region <region> --audio samples/sample.wav
```

The Hugging Face option can also be deployed as an image — see
[`huggingface/README.md`](huggingface/README.md).

---

## Invoke

Both endpoints accept the same request formats and return `{"text": "..."}`.

```bash
# raw audio bytes
python common/invoke.py --endpoint-name <ep> --region <region> --audio samples/sample.wav

# JSON with options (force language, translate, segment timestamps)
python common/invoke.py --endpoint-name <ep> --region <region> --audio clip.mp3 \
  --language hi --task transcribe --timestamps
```

Hindi (`hi`) and other languages are supported by the model; pass `--language` or
let it auto-detect.

## Benchmark

```bash
python common/benchmark.py --endpoint-name <ep> --region <region> \
  --audio samples/sample.wav --audio-duration 4.59 \
  --concurrency 1 2 4 8 16 --requests-per-level 32
```

## Autoscaling

```bash
python common/autoscale.py --endpoint-name <ep> --region <region> \
  --min-capacity 1 --max-capacity 4 --target-invocations-per-instance 250
```
Set the target from your benchmark (see BENCHMARKS.md — ~250 invocations/instance/min
is a latency-safe starting point for the vLLM endpoint; the single-worker HF endpoint
needs a much lower value). With `min-capacity >= 2`, SageMaker spreads instances across
AZs and load-balances requests across them, giving both high availability and automatic
failover. Autoscaling is configured **per endpoint**, so attach it to whichever endpoint
you run (e.g. `whisper-vllm`).

**Pre-warm for known peaks (scheduled scaling).** A new instance takes ~6–12 min to
provision + pull the image + start, which is too slow to absorb a sudden surge. Raise
the floor *before* the peak instead:

```bash
# 08:30 local: pre-warm the floor to 4 instances
python common/autoscale.py --endpoint-name <ep> --region <region> \
  --schedule-name prewarm --schedule "cron(30 8 * * ? *)" \
  --schedule-min 4 --schedule-max 8 --timezone Asia/Kolkata

# 20:00 local: drop the floor back to 1
python common/autoscale.py --endpoint-name <ep> --region <region> \
  --schedule-name off-peak --schedule "cron(0 20 * * ? *)" \
  --schedule-min 1 --schedule-max 8 --timezone Asia/Kolkata
```
Scheduled actions set the min/max bounds; target-tracking still adjusts within them.
For a voice agent, keep a warm baseline that covers your expected concurrent-call peak
rather than relying on reactive scale-out.

## Clean up (GPU endpoints bill per hour)

```bash
python common/cleanup.py --endpoint-name <ep> --region <region>
```

---

## Notes & lessons learned

- **Turn-based vs real-time streaming.** This sample uses the standard SageMaker
  request-response contract (`/invocations`) — ideal for VAD-segmented utterances where
  you send a complete audio chunk and receive the transcript back. For **continuous
  live transcription** (audio streaming in and tokens streaming out simultaneously),
  see [SageMaker bidirectional streaming with vLLM](https://aws.amazon.com/blogs/machine-learning/build-real-time-voice-applications-with-amazon-sagemaker-ai-and-vllm/)
  which uses WebSocket-based `/invocations-bidirectional-stream` and the vLLM Realtime API.

- **No Hugging Face download at boot.** Weights are baked into the artifact/image, so
  autoscaled instances start fast and don't depend on the Hub at runtime.
- **CUDA vs SageMaker host driver (vLLM).** SageMaker's GPU hosting AMI driver caps the
  container CUDA version you can run (~12.4 today). Newer vLLM images (CUDA 12.8/12.9)
  fail with `CannotStartContainerError` and **no logs** because the NVIDIA runtime
  rejects them before startup. This repo pins **vLLM v0.8.5.post1 (CUDA 12.4)**.
- **Build images for `linux/amd64`** (SageMaker hosts are x86_64) and with
  `--provenance=false --sbom=false` — buildx attestation manifests can make SageMaker
  fail to pull the image.
- **No custom AMI on SageMaker hosting.** The customizable layers are the model
  artifact (S3) and the container image (ECR); both are reused by every autoscaled
  instance — that's the "bake once, reuse everywhere" mechanism.
- **Security.** Endpoints have no built-in auth; access is controlled by IAM on
  `sagemaker:InvokeEndpoint`. Scope that permission tightly.
- **Cost.** Real-time GPU endpoints bill per hour while running (min capacity ≥ 1 stays
  warm). Tear down with `common/cleanup.py` when idle.

## License

MIT-0 (MIT No Attribution) — see [LICENSE](LICENSE), consistent with the repository.
The model and third-party dependencies (vLLM, AWS DLCs, transformers) are governed by
their own licenses.
