# vLLM option (high throughput)

Serves `whisper-large-v3-turbo` with vLLM's OpenAI server, which does continuous
(in-flight) batching — concurrent transcription requests are merged into batched GPU
passes. ~3× the throughput of the HF pipeline on the same `ml.g5.xlarge`, with lower
latency under load. See [`../docs/BENCHMARKS.md`](../docs/BENCHMARKS.md).

## How it works

SageMaker requires a container that answers `GET /ping` and `POST /invocations` on
port 8080. vLLM speaks the OpenAI API (`/v1/audio/transcriptions`) on port 8000, so:

```
SageMaker ──/ping,/invocations──▶ proxy.py (:8080) ──/v1/audio/transcriptions──▶ vLLM (:8000)
```

- `launch.py` — container entrypoint (python3). Installs audio deps if missing, starts
  `proxy.py`, then runs the vLLM OpenAI server with the baked-in model.
- `proxy.py` — tiny stdlib HTTP server bridging the SageMaker contract to vLLM. Accepts
  the same request formats as the HF option and returns `{"text": "..."}`.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Bakes model + proxy + launcher into the vLLM image (context = repo root) |
| `build_and_push.sh` | Build & push the image to ECR |
| `proxy.py` | SageMaker `/ping` + `/invocations` adapter |
| `launch.py` | Container entrypoint: starts proxy + vLLM |

## Deploy

```bash
vllm/build_and_push.sh <region> whisper-vllm latest

python common/deploy_from_ecr.py \
  --image-uri <account-id>.dkr.ecr.<region>.amazonaws.com/whisper-vllm:latest \
  --role <role-arn> --region <region> --endpoint-name whisper-vllm
```

## ⚠️ CUDA / driver compatibility

SageMaker's GPU hosting AMI driver caps the container CUDA version. Newer vLLM images
(CUDA 12.8/12.9, driver ≥565/575) **fail with `CannotStartContainerError` and no logs**
— the NVIDIA runtime rejects them before startup. This option pins
**`vllm/vllm-openai:v0.8.5.post1` (CUDA 12.4, driver ≥550)**, which the SageMaker g5
host satisfies. Check the host driver before bumping the tag (compare the image's
`NVIDIA_REQUIRE_CUDA` against the driver AWS DLCs target).

Also build for `linux/amd64` with `--provenance=false --sbom=false` (already set in
`build_and_push.sh`) so SageMaker can pull the image.

## Request / response

Identical to the HF option: `audio/*` raw bytes or `application/json` with a base64
`audio` field (plus optional `language`, `return_timestamps`). Returns `{"text": "..."}`.
