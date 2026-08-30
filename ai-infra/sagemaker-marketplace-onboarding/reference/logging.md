# Logging & Monitoring — Reference

SageMaker captures container `stdout` and `stderr` automatically and ships them to CloudWatch Logs. You do not need to install a CloudWatch agent or configure log shipping.

## Log group

```
/aws/sagemaker/Endpoints/<endpoint-name>
```

For Batch Transform jobs:
```
/aws/sagemaker/TransformJobs/<transform-job-name>
```

Log stream names are per-container-per-variant.

## Emit structured JSON

Plain-text logs work but are painful to query in CloudWatch Logs Insights. Emit structured JSON so you can filter by `msg`, `level`, `inference_id`, etc.:

```python
import json, logging, sys

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    json.dumps({
        "time":  "%(asctime)s",
        "level": "%(levelname)s",
        "msg":   "%(message)s",
    })
))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
```

## Key events to log

At minimum, log these — they are the events you'll need when a customer opens a support ticket:

- **Startup complete** — model loaded successfully, time taken, weights source path
- **Startup failure** — full stack trace; useful when /ping times out during CreateEndpoint
- **Request received** — `X-Amzn-SageMaker-Inference-Id`, content-type, body size
- **Request completed** — inference-id, latency in ms, output size
- **Error** — inference-id, full stack trace, request metadata (not the full request body — could be PII)
- **Ping check failed** — inference path is broken; SageMaker will start replacing the instance shortly

Optional but useful:

- **GPU memory** — periodic (every ~30s) allocated + reserved MB
- **Batch mode signals** — log the `SAGEMAKER_BATCH`, `SAGEMAKER_MAX_PAYLOAD_IN_MB` env vars once at startup

## Multi-process containers

If you use supervisord, every program in `supervisord.conf` must pipe its own stdout/stderr:

```ini
[program:model-server]
command=python3 model_server.py
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
```

Without `stdout_logfile=/dev/stdout` on each program, supervisord captures to disk and SageMaker never sees those logs.

## What to NOT log

- **Full inference bodies** — customer inputs may be PII (audio recordings, text with personal info, images). Log the size and hash if you need to correlate, not the content.
- **Model weights or internal state**
- **Credentials** — you shouldn't have any anyway (no outbound network), but scrub environment variables before logging them.
- **High-frequency success events** — logging every token in a streaming response floods CloudWatch and is expensive.

## Querying

CloudWatch Logs Insights:

```
fields @timestamp, level, msg, inference_id, latency_ms
| filter level = "ERROR"
| sort @timestamp desc
| limit 20
```

## No CloudWatch Metrics API

Your container **cannot** call `cloudwatch:PutMetricData` at runtime — no outbound network. If you need custom metrics, log them as structured JSON and use CloudWatch Metric Filters to promote them into metrics.

## Retention

CloudWatch log retention is set by the endpoint owner (the customer), not by you. Customers can configure retention on `/aws/sagemaker/Endpoints/<their-endpoint-name>`. Do not assume logs will exist a week later — write anything you need to persist to your own system via the customer's log retention.
