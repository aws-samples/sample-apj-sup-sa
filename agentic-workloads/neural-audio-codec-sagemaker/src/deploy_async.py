"""
Deploy the multi-codec handler to a SageMaker AI asynchronous inference endpoint.

Asynchronous inference exchanges input and output through Amazon S3, so it accepts
larger payloads and longer processing times than the real-time path. Use it for
batch tokenization of an audio corpus.

Configuration is read from environment variables (nothing account-specific is committed):

  SAGEMAKER_ROLE_ARN   IAM execution role ARN for the endpoint
  MODEL_S3_URI         s3://<bucket>/<prefix>/model.tar.gz
  ASYNC_OUTPUT_S3_URI  s3://<bucket>/<prefix>/  for async results
  AWS_REGION           Region to deploy in (default: us-east-1)
  ENDPOINT_NAME        Endpoint name (default: neural-codec-async)

Cost note: ml.c5.2xlarge is $0.408/hour in US East (N. Virginia) while processing.
With an Application Auto Scaling policy (minimum capacity 0), the endpoint scales to
zero when the queue is empty. Delete the endpoint when finished.
"""
import os

import boto3
import sagemaker
from sagemaker.huggingface import HuggingFaceModel
from sagemaker.async_inference import AsyncInferenceConfig

REGION = os.environ.get("AWS_REGION", "us-east-1")
ROLE = os.environ["SAGEMAKER_ROLE_ARN"]
MODEL_S3 = os.environ["MODEL_S3_URI"]
OUT_S3 = os.environ["ASYNC_OUTPUT_S3_URI"]
ENDPOINT = os.environ.get("ENDPOINT_NAME", "neural-codec-async")

sess = sagemaker.Session(boto_session=boto3.Session(region_name=REGION))

model = HuggingFaceModel(
    model_data=MODEL_S3,
    role=ROLE,
    transformers_version="4.49.0",
    pytorch_version="2.6.0",
    py_version="py312",
    sagemaker_session=sess,
    env={
        "HF_HOME": "/tmp/hf",
        "SAGEMAKER_MODEL_SERVER_WORKERS": "1",
        "OMP_NUM_THREADS": "4",
        # raise the Multi Model Server request/response ceiling (default ~6.25 MB)
        "MMS_MAX_REQUEST_SIZE": "1000000000",
        "MMS_MAX_RESPONSE_SIZE": "1000000000",
    },
)

predictor = model.deploy(
    initial_instance_count=1,
    instance_type="ml.c5.2xlarge",
    endpoint_name=ENDPOINT,
    async_inference_config=AsyncInferenceConfig(output_path=OUT_S3),
)
print("Async endpoint InService:", ENDPOINT)
print("Results written to:", OUT_S3)
