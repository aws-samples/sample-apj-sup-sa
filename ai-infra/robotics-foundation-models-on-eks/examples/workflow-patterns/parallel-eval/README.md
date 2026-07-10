# Parallel Eval

Small OSMO `groups` example that fans out four CPU evaluation shards and aggregates their metrics.

No GPU prewarm is required.

Run it through the repo wrapper:

```bash
cd ai-infra/robotics-foundation-models-on-eks
WORKFLOW_FILE=examples/workflow-patterns/parallel-eval/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=120 \
  examples/run-workflow.sh
```

Expected result:

- Four evaluation shards complete and aggregate into one summary dataset.
- Expected completion time is `2-3 min` on a warm platform.

This is a workflow-shape example. It uses synthetic metrics so it can validate OSMO fan-out/fan-in behavior without external data.
