"""Submit a SageMaker Training Job (run this on your local PC).

It boils down to just two steps:
  1) Define with ModelTrainer "what (train.py) / where (instance) / with which image" to run
  2) Submit with trainer.train() → SageMaker launches an instance and runs training

To change settings, just edit the CONFIG values below.
  uv run launch.py            # submit with the current settings and wait for completion
  uv run launch.py --dry-run  # only check the image URI/settings, without submitting to AWS
"""

import json
import pathlib
import sys

import boto3
from sagemaker.core import image_uris
from sagemaker.core.helper.session_helper import Session, get_execution_role
from sagemaker.core.shapes.shapes import MetricDefinition, StoppingCondition
from sagemaker.core.training.configs import Compute, SourceCode
from sagemaker.train import ModelTrainer

HERE = pathlib.Path(__file__).parent

# ============================================================
# CONFIG — these are the only values you need to change.
# ============================================================
EPOCHS = 3
INSTANCE_TYPE = "ml.c6i.2xlarge"   # For GPU training, e.g. "ml.g4dn.xlarge" (requires account quota)
FRAMEWORK_VERSION = "2.7.1"        # PyTorch DLC version (e.g. "2.7.1", "2.8.0")
MAX_RUN_SECONDS = 3600             # Max run time (safety limit). e.g. 86400 (24h)
WAIT = True                        # If False, return right after submitting (async)
ROLE = None                        # SageMaker execution role ARN. None = auto-detect
REGION = None                      # AWS Region. None = default session Region
# ============================================================


def resolve_role(session):
    """Resolve the execution role ARN: CONFIG ROLE > Studio auto > a SageMaker role in the account."""
    if ROLE:
        return ROLE
    try:
        return get_execution_role(session)  # works as-is inside a SageMaker notebook/Studio
    except Exception:
        pass
    for r in boto3.client("iam").list_roles()["Roles"]:  # local PC: pick one from the account
        if "SageMaker" in r["RoleName"] and "Execution" in r["RoleName"]:
            return r["Arn"]
    raise SystemExit(
        "Could not find a SageMaker execution role. Set its ARN in ROLE in CONFIG.\n"
        "(See the 'IAM execution role' section in the README.)"
    )


def main():
    dry_run = "--dry-run" in sys.argv[1:]

    region = REGION or boto3.session.Session().region_name
    if not region:
        raise SystemExit("Could not find a region. Set REGION in CONFIG or run 'aws configure'.")

    # Resolve the managed PyTorch DLC (Deep Learning Container) image URI — torch is preinstalled
    image_uri = image_uris.retrieve(
        framework="pytorch",
        region=region,
        version=FRAMEWORK_VERSION,
        image_scope="training",
        instance_type=INSTANCE_TYPE,
    )

    print(f"region   : {region}")
    print(f"image    : {image_uri}")
    print(f"instance : {INSTANCE_TYPE}")
    print(f"epochs   : {EPOCHS}")

    if dry_run:
        print("[dry-run] Not submitted. Configuration check complete.")
        return

    session = Session(boto_session=boto3.Session(region_name=region))

    # 1) Define the Training Job
    trainer = ModelTrainer(
        training_image=image_uri,
        source_code=SourceCode(source_dir=str(HERE / "src"), entry_script="train.py"),
        compute=Compute(instance_type=INSTANCE_TYPE, instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=MAX_RUN_SECONDS),
        hyperparameters={"epochs": EPOCHS},  # passed to train.py as "--epochs 3"
        base_job_name="hello-mnist",
        role=resolve_role(session),
        sagemaker_session=session,
    ).with_metric_definitions(
        # Parse train.py's "test_accuracy=..." output and plot it as a console metric
        [MetricDefinition(name="test:accuracy", regex=r"test_accuracy=([0-9\.]+)")]
    )

    # 2) Submit (with WAIT=True, stream logs until completion)
    print(f"\nSubmitting... (wait={WAIT})")
    trainer.train(wait=WAIT, logs=WAIT)

    job = trainer._latest_training_job
    print(f"\n✅ Training Job: {job.training_job_name}")

    # Save the job name so jobstatus.py can check it later
    (HERE / ".last_job.json").write_text(
        json.dumps({"job_name": job.training_job_name, "region": region})
    )

    if WAIT:
        job.refresh()
        if job.model_artifacts:
            print(f"Model: {job.model_artifacts.s3_model_artifacts}")
    else:
        print("Check status: uv run jobstatus.py")


if __name__ == "__main__":
    main()
