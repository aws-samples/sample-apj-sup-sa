English | [한국어](model-promotion.ko.md)

# Model Registration and Batch Inference

This step turns the results you compared in MLflow into actual predictions.

```text
Compare in MLflow
  -> Select a model in the MLflow UI
  -> Register a version in MLflow and SageMaker Model Registry
  -> Approve the version in SageMaker Studio
  -> Run Batch Transform with the latest Approved version
```

Not every experiment goes into the Registry. MLflow keeps every run you tried, while the Registry holds only the models you selected as actual inference candidates.

## 1. Register a model in the MLflow UI

1. In MLflow, go to **Model training → Runs** and compare `default` and `tuned` that share the same run ID.
2. Click the name of the run you want to use to open its detail page.
3. On the **Artifacts** tab, select the `model` Logged Model.
4. Click **Register model**.
5. For the first registration, select **Create New Model** and enter the name `sagemaker-xgboost-lab`.
6. For later models, select the same name to register them as new versions.

Once registered, the same version is automatically created in both the MLflow Model Registry and the SageMaker Model Registry. Runs that you do not select in the UI are not registered in the SageMaker Registry.

The `log_model()` call that runs after regular Training and Automatic Model Tuning
complete creates a Logged Model that you can select in the UI. This call alone does not
register anything in the SageMaker Model Registry. The MLflow App's automatic model
registration setting syncs the UI's **Register model** action to the SageMaker Model Registry.

A registered Model Package version is linked to the `model.tar.gz` S3 URI stored by SageMaker, the XGBoost inference container image, the supported instance types, and the `text/csv` input and output formats. Which parameters and metrics led you to choose this model remains visible in the MLflow run.

The Training code leaves a Logged Model so that MLflow can register it from the UI, and links SageMaker's original `model.tar.gz`, inference image, and CSV format as the inference specification.

You check the registration result in SageMaker Studio, not in the MLflow App. In the current Studio, go to **JumpStart / Models → My models → Logged** in the left navigation. In the model group whose name starts with `sagemaker-xgboost-lab-`, click **View all versions** to see the registered versions.

From the Studio **Home** page, you can also reach the same page via **Workflows and tasks → Build, train, tune models → Catalog models with model registry**. The official AWS documentation may still show this path as **Models → Registered models → Model Groups**, but in the current Studio UI, `Models` has been renamed to **JumpStart / Models** and the Registry has been placed under **My models → Logged**. The **Deployments** menu is for viewing inference jobs such as endpoints and Batch Transform, so it is not the path to the Registry.

Because the MLflow App may append a suffix to the SageMaker group name, the MLflow registered model name and the group name may not match exactly. In this sample, registering `sagemaker-xgboost-lab` shows up as something like `sagemaker-xgboost-lab-4663e9`. If you use Studio Classic, use the **Home → Models → Model registry** path.

## 2. Approve the version in SageMaker Registry

Registering in the Registry stores inference candidates, while approval is the step where a person confirms which of those versions to use for actual inference. This sample uses a single manual approval without complex CI/CD.

1. In the SageMaker Studio left navigation, select **JumpStart / Models**.
2. Go to **My models → Logged**.
3. Select the model group whose name starts with `sagemaker-xgboost-lab-`.
4. Select the version to approve, such as **Version 1**.
5. Check the **Deploy** status at the top of **Overview**.
6. In the **Pending Approval** dropdown, select **Approved**, and confirm the change if a confirmation dialog appears.

You are done when the **Deployment status** in the version list shows **Approved**. `Approved` does not automatically deploy the model to an endpoint. In this project, it serves as a safeguard so that the Batch Transform script selects only a version that is among the latest `Approved` versions and compatible with the instance it will run on.

If the metrics or artifacts are not what you expected, select **Rejected**. `Rejected` and **Pending Approval** versions are not used by Batch Transform.

## 3. Run Batch Transform

Run it with the exact Model Package Group name you confirmed in Studio.

```bash
MODEL_PACKAGE_GROUP_NAME=sagemaker-xgboost-lab-4663e9 \
  ./scripts/run-batch-inference.sh
```

This sample fixes the Batch Transform instance to a single type, `ml.c5.xlarge`, which
is sufficient for small XGBoost inference. The model versions registered in the Registry
also declare support for this instance only.

Training quotas and Transform quotas are managed separately, and the defaults may differ by account. In Service Quotas, check that the **for transform job usage** value for the instance you will use is `1` or higher.

This command does not call a prediction API many times, once per row. Locally, it requests only a single Batch Transform Job, and SageMaker handles splitting the input and invoking the model from there.

This sample uses a single CSV file. `SplitType=Line` treats each line as one record, and
`BatchStrategy=MultiRecord` bundles multiple lines into one request. A single
`ml.c5.xlarge` instance processes up to 4 requests concurrently.

| Option | Value in this sample | Meaning |
|---|---|---|
| `InstanceCount` | `1` | Number of instances used for inference |
| `SplitType` | `Line` | Treat each CSV line as one record |
| `BatchStrategy` | `MultiRecord` | Bundle multiple records into one request payload |
| `MaxConcurrentTransforms` | `4` | Maximum number of concurrent requests sent to one instance |
| `AssembleWith` | `Line` | Save prediction results joined line by line |

Because `InstanceCount=1`, a single instance is used. `MaxConcurrentTransforms=4` means
up to 4 requests can be sent to that instance concurrently. The payload size is not
specified, so the SageMaker default is used.

As data grows, up to 4 requests are first processed in parallel within one instance. If you need more throughput, split the input into multiple S3 files and increase `InstanceCount` to distribute the files across multiple instances. If you increase only the instance count while there is still just one input file, the other instances may sit idle, so review both values together.

The script performs the following steps.

1. Automatically finds the SageMaker Model Package Group created by MLflow. If there are multiple groups with the same prefix, specify the exact name shown in Studio with `MODEL_PACKAGE_GROUP_NAME`.
2. Checks versions in that group starting from the latest `Approved` version and selects the first version that supports `ml.c5.xlarge`. If no version meets the criteria, it stops here.
3. Creates an inference CSV from the validation features, excluding the label.
4. Uploads the input to S3.
5. Creates a temporary SageMaker Model from the model package.
6. Batch Transform computes the probability for each row.
7. Downloads the results locally and deletes the temporary SageMaker Model.

```text
[1/4] Find the latest approved model version
[2/4] Prepare credit default inference rows and upload them to S3
[3/4] Run Batch Transform (ml.c5.xlarge)
[4/4] Download predictions
```

The results are saved at the following location.

```text
artifacts/batch/<run-id>/predictions.csv.out
```

The terminal shows only the first 5 probabilities from the result file.

```text
Predictions: artifacts/batch/<run-id>/predictions.csv.out
First predicted probabilities of default next month:
- 0.16083382070064545
- 0.1926967203617096
- 0.12307227402925491
- 0.15535558760166168
- 0.03271584212779999
```

The full file stores 6,000 probabilities in the same order as the validation input. Each line is the probability that the customer on the same line of the input CSV will default next month, and the values vary depending on the model you ran.

These numbers are not a performance score for the model as a whole. For example, `0.6731`
means the model produced a probability score of `0.6731` for that customer defaulting next
month. To interpret this in production as an exact PD (Probability of Default) of 67.31%,
you must validate calibration on separate test data.

If you set the threshold to `0.5`, you can classify a probability of `0.5` or higher as
default `1` and anything lower as no default `0`. However, this sample only outputs
probabilities and does not automate the threshold decision.

This sample reuses the validation features as Batch Transform input, but real inference
data has no ground truth. Once repayment outcomes are confirmed, join the predictions with
the labels to evaluate performance. In practice, you also do not have to use `0.5`.
Choose the threshold by comparing approval rate, default detection rate, and the business
cost of wrongly rejected applicants and missed defaults.

In S3, the input and output are kept under the following prefix.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/batch/<run-id>/
```

If the same group still contains a model version registered and approved with the
previous 4-feature dataset, register and approve the new credit default model and then
change the previous version to `Rejected`. Sending 23-feature input to the old 4-feature
model causes a schema mismatch.

## Why not start with an endpoint?

Batch Transform uses instances only when there is a request and shuts them down when the job finishes. It suits per-S3-file prediction, scheduled scoring, and backfills, and has no endpoint upkeep cost.

When you need a real-time API, you can add an endpoint using the same model package. For a startup sample, it is simpler to first validate the end-to-end flow from model selection to inference with Batch Transform, and then add an endpoint once latency requirements arise.
