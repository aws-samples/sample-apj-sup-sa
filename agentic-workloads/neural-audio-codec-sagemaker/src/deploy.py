"""
Deploy the multi-codec (Mimi + EnCodec) handler to a SageMaker AI real-time endpoint.

Configuration is read from environment variables so no account-specific values are
committed to source control:

  SAGEMAKER_ROLE_ARN   IAM execution role ARN for the endpoint
  MODEL_S3_URI         s3://<bucket>/<prefix>/model.tar.gz  (code/ packaged as tar.gz)
  AWS_REGION           Region to deploy in (default: us-east-1)
  ENDPOINT_NAME        Endpoint name (default: neural-codec-rt)

Cost note: billing starts when the endpoint reaches InService (ml.c5.2xlarge is
$0.408/hour in US East (N. Virginia)). Delete the endpoint when finished.
"""
import os

import boto3
import sagemaker
from sagemaker.huggingface import HuggingFaceModel

REGION = os.environ.get("AWS_REGION", "us-east-1")
ROLE = os.environ["SAGEMAKER_ROLE_ARN"]
MODEL_S3 = os.environ["MODEL_S3_URI"]
ENDPOINT = os.environ.get("ENDPOINT_NAME", "neural-codec-rt")

sess = sagemaker.Session(boto_session=boto3.Session(region_name=REGION))

model = HuggingFaceModel(
    model_data=MODEL_S3,
    role=ROLE,
    transformers_version="4.49.0",
    pytorch_version="2.6.0",
    py_version="py312",
    sagemaker_session=sess,
    env={
        "HF_HOME": "/tmp/hf",                    # writable cache dir
        "SAGEMAKER_MODEL_SERVER_WORKERS": "1",   # one worker: both codecs load once, avoids OOM
        "OMP_NUM_THREADS": "4",
    },
)

predictor = model.deploy(
    initial_instance_count=1,
    instance_type="ml.c5.2xlarge",
    endpoint_name=ENDPOINT,
)
print("Endpoint InService:", ENDPOINT)
