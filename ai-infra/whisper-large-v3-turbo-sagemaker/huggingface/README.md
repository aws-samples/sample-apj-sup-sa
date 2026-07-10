# Hugging Face option (Transformers ASR pipeline)

Serves `whisper-large-v3-turbo` on the AWS Hugging Face Deep Learning Container using
a custom inference handler (`inference.py`). Simplest to stand up. Throughput is
limited by a single model-server worker (~2.1 rps on a 24s clip per `ml.g5.xlarge`) —
for high concurrency use the [vLLM option](../vllm/README.md).

## Files

| File | Purpose |
|------|---------|
| `inference.py` | `model_fn`/`input_fn`/`predict_fn`/`output_fn`; loads baked-in weights |
| `Dockerfile` | Bakes model + handler into the HF DLC (build context = repo root) |
| `build_and_push.sh` | Build & push the image to ECR |
| `deploy_s3.py` | No-Docker deploy: stock DLC + weights as an uncompressed S3 artifact |

## Deploy — no Docker (recommended start)

```bash
python huggingface/deploy_s3.py \
  --role <role-arn> --region <region> \
  --instance-type ml.g5.xlarge --endpoint-name whisper-hf
```
Downloads the model once, uploads it (uncompressed) to your SageMaker S3 bucket, and
deploys the DLC. The handler loads weights from the local artifact — no Hub download
at runtime.

## Deploy — container image

```bash
huggingface/build_and_push.sh <region> whisper-hf latest

python common/deploy_from_ecr.py \
  --image-uri <account-id>.dkr.ecr.<region>.amazonaws.com/whisper-hf:latest \
  --role <role-arn> --region <region> --endpoint-name whisper-hf
```

## Request / response

- `audio/*` — raw audio bytes
- `application/json` — `{"audio": "<base64>", "language": "hi", "task": "transcribe", "return_timestamps": true}`
- Response: `{"text": "..."}` (plus `chunks` when timestamps are requested)

Long audio (>30s) is handled via 30s chunking in the handler.
