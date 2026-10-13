"""
Deploy whisper-large-v3-turbo to a SageMaker endpoint from a CUSTOM ECR image that
already contains the model weights + serving code. Works for BOTH images in this
repo (huggingface/ and vllm/).

No model_data is passed: the image already holds everything, so SageMaker only
pulls from ECR. Every instance autoscaling launches uses this same image -- no
Hugging Face download, no pip install at startup.

Build/push an image first (huggingface/build_and_push.sh or vllm/build_and_push.sh),
then run this with the printed image URI.

Usage:
    python common/deploy_from_ecr.py \
        --image-uri <account>.dkr.ecr.<region>.amazonaws.com/whisper-vllm:latest \
        --role arn:aws:iam::<account-id>:role/<SageMakerExecutionRole> \
        --region ap-south-1 \
        --instance-type ml.g5.xlarge \
        --endpoint-name whisper-vllm \
        [--update-endpoint]
"""

import argparse

import boto3
import sagemaker
from sagemaker.model import Model


def parse_args():
    p = argparse.ArgumentParser(description="Deploy Whisper from a custom ECR image.")
    p.add_argument("--image-uri", required=True, help="Full ECR image URI (repo:tag).")
    p.add_argument("--role", default=None, help="IAM role ARN with SageMaker permissions.")
    p.add_argument("--region", default=None)
    p.add_argument("--instance-type", default="ml.g5.xlarge")
    p.add_argument("--instance-count", type=int, default=1)
    p.add_argument("--endpoint-name", default="whisper-large-v3-turbo")
    p.add_argument("--update-endpoint", action="store_true",
                   help="Update an existing endpoint in place instead of creating a new one.")
    return p.parse_args()


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
    print(f"Image:         {args.image_uri}")
    print(f"Role:          {role}")
    print(f"Instance type: {args.instance_type} x {args.instance_count}")
    print(f"Endpoint name: {args.endpoint_name}")

    # Custom image with the model baked in -> no model_data.
    model = Model(
        image_uri=args.image_uri,
        role=role,
        sagemaker_session=session,
        env={
            "SAGEMAKER_PROGRAM": "inference.py",
            "SAGEMAKER_SUBMIT_DIRECTORY": "/opt/ml/model/code",
            "HF_TASK": "automatic-speech-recognition",
            "SAGEMAKER_MODEL_SERVER_TIMEOUT": "600",
            "SAGEMAKER_TS_RESPONSE_TIMEOUT": "600",
        },
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
