"""Check the status and results of a submitted Training Job (SageMaker SDK v3).

Usage:
  uv run jobstatus.py                     # check the last job recorded in .last_job.json
  uv run jobstatus.py <job-name>          # check a specific job
  uv run jobstatus.py --region us-west-2 <job-name>
"""

import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).parent


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("job_name", nargs="?", default=None, help="Training Job name (defaults to the last submitted job)")
    p.add_argument("--region", default=None)
    return p.parse_args()


def load_last_job():
    f = HERE / ".last_job.json"
    if not f.exists():
        raise SystemExit(
            "No record of the last job (.last_job.json). Pass a job name as an argument:\n"
            "  uv run jobstatus.py <job-name>"
        )
    return json.loads(f.read_text())


def main():
    args = parse_args()

    region = args.region
    job_name = args.job_name
    if job_name is None:
        rec = load_last_job()
        job_name = rec["job_name"]
        region = region or rec.get("region")

    from sagemaker.core.resources import TrainingJob

    job = TrainingJob.get(training_job_name=job_name, region=region)
    if job is None:
        raise SystemExit(f"Job not found: {job_name} (region={region})")

    status = job.training_job_status
    secondary = job.secondary_status or ""
    print(f"Job     : {job_name}")
    print(f"Status  : {status}  ({secondary})")

    if getattr(job, "training_time_in_seconds", None) is not None:
        print(f"Runtime : {job.training_time_in_seconds}s")

    if status == "Completed":
        if job.model_artifacts:
            print(f"Model   : {job.model_artifacts.s3_model_artifacts}")
        for m in job.final_metric_data_list or []:
            print(f"  metric {m.metric_name} = {m.value}")
    elif status == "Failed":
        print(f"Reason  : {job.failure_reason or '(none)'}")

    # Direct link to the CloudWatch logs
    import boto3

    log_region = region or boto3.session.Session().region_name
    print(
        "\nCloudWatch Logs:\n"
        f"  https://{log_region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={log_region}#logsV2:log-groups/log-group/$252Faws$252Fsagemaker$252FTrainingJobs"
    )


if __name__ == "__main__":
    main()
