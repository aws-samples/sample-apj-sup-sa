"""
Deploy openai/whisper-large-v3-turbo to SageMaker WITHOUT building a container.

This is the no-Docker alternative to the BYOC image. It uses the stock Hugging
Face DLC and ships the model weights + handler as an UNCOMPRESSED S3 artifact:

  1. Download the model snapshot once into <repo>/model_artifacts/snapshot.
  2. Assemble an artifact dir = weights + code/inference.py.
  3. Upload it uncompressed to S3 (S3Prefix; parallel download, no untar).
  4. Create/update the endpoint. The handler loads weights from the local artifact
     dir, so every instance (including autoscaled ones) reuses the same weights
     with no Hugging Face download at runtime.

Usage:
    python huggingface/deploy_s3.py \
        --role arn:aws:iam::<account-id>:role/<SageMakerExecutionRole> \
        --region ap-south-1 \
        --instance-type ml.g5.xlarge \
        --endpoint-name whisper-hf \
        [--update-endpoint]
"""

import argparse
import shutil
from pathlib import Path

import boto3
import sagemaker
from huggingface_hub import snapshot_download
from sagemaker.huggingface import HuggingFaceModel
from sagemaker.s3 import S3Uploader

MODEL_ID = "openai/whisper-large-v3-turbo"
REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = REPO_ROOT / "model_artifacts" / "snapshot"
ARTIFACT_DIR = REPO_ROOT / "model_artifacts" / "artifact_hf"
HANDLER = Path(__file__).resolve().parent / "inference.py"

ALLOW_PATTERNS = [
    "*.json", "*.txt", "*.safetensors", "merges.txt", "vocab.json",
    "tokenizer*", "preprocessor_config.json", "generation_config.json",
    "normalizer.json", "added_tokens.json", "special_tokens_map.json",
]
IGNORE_PATTERNS = ["*.bin", "*.pt", "*.pth", "*.h5", "*.onnx", "*.msgpack", "*.tflite"]


def parse_args():
    p = argparse.ArgumentParser(description="Deploy Whisper to SageMaker via an S3 artifact.")
    p.add_argument("--role", default=None, help="IAM role ARN with SageMaker permissions.")
    p.add_argument("--region", default=None)
    p.add_argument("--instance-type", default="ml.g5.xlarge")
    p.add_argument("--instance-count", type=int, default=1)
    p.add_argument("--endpoint-name", default="whisper-hf")
    p.add_argument("--update-endpoint", action="store_true",
                   help="Update an existing endpoint in place instead of creating a new one.")
    return p.parse_args()


def build_artifact():
    """Assemble model_artifacts/artifact_hf = weights + code/inference.py."""
    print(f"Downloading {MODEL_ID} -> {SNAPSHOT_DIR} (cached after first run)...")
    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=str(SNAPSHOT_DIR),
        allow_patterns=ALLOW_PATTERNS,
        ignore_patterns=IGNORE_PATTERNS,
    )

    if ARTIFACT_DIR.exists():
        shutil.rmtree(ARTIFACT_DIR)
    shutil.copytree(SNAPSHOT_DIR, ARTIFACT_DIR,
                    ignore=shutil.ignore_patterns(".cache", ".huggingface"))

    code_dst = ARTIFACT_DIR / "code"
    code_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(HANDLER, code_dst / "inference.py")

    if not (ARTIFACT_DIR / "config.json").exists():
        raise SystemExit("Model config.json missing from artifact; download may have failed.")
    return str(ARTIFACT_DIR)


def main():
    args = parse_args()

    boto_session = boto3.Session(region_name=args.region) if args.region else boto3.Session()
    session = sagemaker.Session(boto_session=boto_session)
    region = boto_session.region_name

    role = args.role
    if role is None:
        try:
            role = sagemaker.get_execution_role()
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                "Could not auto-detect an IAM role. Pass one explicitly with --role.\n"
                f"Original error: {exc}"
            )

    print(f"Region:        {region}")
    print(f"Role:          {role}")
    print(f"Instance type: {args.instance_type} x {args.instance_count}")
    print(f"Endpoint name: {args.endpoint_name}")

    artifact_dir = build_artifact()

    bucket = session.default_bucket()
    s3_prefix = f"s3://{bucket}/{args.endpoint_name}/model"
    print(f"Uploading uncompressed artifact to {s3_prefix}/ ...")
    S3Uploader.upload(artifact_dir, s3_prefix, sagemaker_session=session)

    model_data = {
        "S3DataSource": {
            "S3Uri": f"{s3_prefix}/",
            "S3DataType": "S3Prefix",
            "CompressionType": "None",
        }
    }

    env = {
        "HF_TASK": "automatic-speech-recognition",
        "SAGEMAKER_PROGRAM": "inference.py",
        "SAGEMAKER_SUBMIT_DIRECTORY": "/opt/ml/model/code",
        "SAGEMAKER_MODEL_SERVER_TIMEOUT": "600",
        "SAGEMAKER_TS_RESPONSE_TIMEOUT": "600",
    }

    model = HuggingFaceModel(
        model_data=model_data,
        role=role,
        sagemaker_session=session,
        env=env,
        transformers_version="4.37.0",
        pytorch_version="2.1.0",
        py_version="py310",
    )

    model.deploy(
        initial_instance_count=args.instance_count,
        instance_type=args.instance_type,
        endpoint_name=args.endpoint_name,
        container_startup_health_check_timeout=900,
        update_endpoint=args.update_endpoint,
        wait=False,
    )

    print("\nEndpoint creation/update started (not waiting for InService).")
    print(f"Check status:  aws sagemaker describe-endpoint --region {region} "
          f"--endpoint-name {args.endpoint_name} --query EndpointStatus --output text")
    print(f"Test it with:  python common/invoke.py --endpoint-name {args.endpoint_name} "
          f"--region {region} --audio samples/sample.wav")


if __name__ == "__main__":
    main()
