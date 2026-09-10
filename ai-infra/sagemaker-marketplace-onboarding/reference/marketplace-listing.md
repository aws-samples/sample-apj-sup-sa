# CreateModelPackage — Reference

The skill's marketplace scope is deliberately narrow: help the user run `CreateModelPackage` with a validation job. Everything else (FDP, IAM, pricing, EULA, regions, notebook, publishing) is manual work the user handles via their AWS account team and the Marketplace Management Portal — not this skill.

## What the validation job does

`CreateModelPackage` with `CertifyForMarketplace=True` triggers a SageMaker Batch Transform job that runs the container against sample input the user provides in S3. It verifies: container starts, `/ping` returns 200 within 8 minutes, `/invocations` processes the sample and produces valid output, no security vulnerabilities in the image. This is a contract check, not an accuracy evaluation.

## Prerequisites (user's responsibility)

- ECR image pushed to the region where `CreateModelPackage` will run.
- `model.tar` uploaded to a seller-owned S3 bucket. Keep a local backup — after `CreateModelPackage`, weights transfer to a Marketplace-managed bucket and the seller loses direct access.
- Sample input for the validation job uploaded to S3 (e.g. `s3://<bucket>/validation-input/`). Format must match one of the `SupportedContentTypes` in the API call.
- An empty S3 prefix ready for validation output (`s3://<bucket>/validation-output/`).
- An IAM role with permission to run the transform job (typically the user's existing SageMaker execution role — this is not the separate marketplace assets role).

## API skeleton

```python
sm.create_model_package(
    ModelPackageName="<your-model>-v1",
    InferenceSpecification={
        "Containers": [{
            "Image": "<account>.dkr.ecr.<region>.amazonaws.com/<model>:v1",
            "ModelDataUrl": "s3://<seller-bucket>/<model>/model.tar",
        }],
        "SupportedRealtimeInferenceInstanceTypes": ["ml.g5.2xlarge"],
        "SupportedTransformInstanceTypes":         ["ml.g5.2xlarge"],
        "SupportedContentTypes":     ["application/json"],
        "SupportedResponseMIMETypes":["application/json"],
    },
    ValidationSpecification={
        "ValidationRole": "arn:aws:iam::<account>:role/SageMakerExecutionRole",
        "ValidationProfiles": [{
            "ProfileName": "validation-1",
            "TransformJobDefinition": {
                "TransformInput": {
                    "DataSource": {"S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": "s3://<bucket>/validation-input/",
                    }},
                    "ContentType": "application/json",
                },
                "TransformOutput": {"S3OutputPath": "s3://<bucket>/validation-output/"},
                "TransformResources": {"InstanceType": "ml.g5.xlarge", "InstanceCount": 1},
            },
        }],
    },
    CertifyForMarketplace=True,
)
```

## Field notes

- `Image` — the ECR URI from Phase 9.
- `ModelDataUrl` — the `model.tar` S3 path from Phase 7.
- `SupportedRealtimeInferenceInstanceTypes` / `SupportedTransformInstanceTypes` — the instance families the user tested during Phase 8. Do not list families that were not tested.
- `SupportedContentTypes` / `SupportedResponseMIMETypes` — must match what `/invocations` actually accepts and returns.
- `ValidationSpecification.TransformInput.ContentType` — must match one of the `SupportedContentTypes` above and must match the format of the sample files.
- `CertifyForMarketplace=True` — without this, you get a private ModelPackage instead of triggering the Marketplace review workflow.

## Everything else — user handles manually

For the listing steps this skill deliberately doesn't cover — FDP enrollment, IAM roles including the `assets.marketplace.amazonaws.com` trust role, pricing configuration, EULA selection, region setup, customer-facing notebook, publishing (Limited → Public) — refer the user to AWS docs:

- Machine Learning products overview: https://docs.aws.amazon.com/marketplace/latest/userguide/machine-learning-products.html
- ML publishing prerequisites: https://docs.aws.amazon.com/marketplace/latest/userguide/ml-publishing-prerequisites.html
- Marketplace Management Portal: https://aws.amazon.com/marketplace/management/
