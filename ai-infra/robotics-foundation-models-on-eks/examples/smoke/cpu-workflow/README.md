# OSMO Smoke

CPU-only smoke workflow for the OSMO control plane and backend.

No GPU prewarm is required.

Run it through the repo wrapper:

```bash
cd ai-infra/robotics-foundation-models-on-eks
examples/run-workflow.sh
```

The wrapper submits [workflow.yaml](workflow.yaml), waits for completion, prints logs, and fails fast if the workflow does not complete.

Expected result: the workflow completes and uploads the smoke output dataset.
