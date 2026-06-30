# Amazon Bedrock TPM/RPM Ramp Testing

Test and validate your Amazon Bedrock throughput limits using the recommended
ramp-up procedure from the
[Scaling throughput best practices](https://docs.aws.amazon.com/bedrock/latest/userguide/scaling-throughput-best-practices.html)
documentation.

## Why ramp testing?

Amazon Bedrock provisions generous default TPM (Tokens Per Minute) and RPM
(Requests Per Minute) quotas for on-demand models. These high limits reflect the
platform's capacity to scale — but like any large-scale inference system, the
underlying compute benefits from a gradual warm-up to reach peak throughput
reliably. Ramp testing helps you:

1. **Discover your effective steady-state throughput** before going to production.
2. **Avoid transient 503 (ServiceUnavailable) errors** during sudden traffic spikes.
3. **Build confidence** that your workload can sustain target volume over time.

## How it works

The script implements the recommended ramp-up procedure automatically:

```
Target RPM (e.g. 2000)
       │
       ▼
 Start at target ──► 503 errors? ──► Reduce by 50%
                         │                  │
                         No                 ▼
                         │           Still 503? ──► Reduce again
                         ▼                  │
                   Hold 15 min              No
                         │                  ▼
                         ▼            Hold 15 min
                  Increase 50%              │
                         │                  ▼
                         ▼           Increase 50%
                   Repeat until             │
                   target reached     ... repeat ...
```

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with defaults (moonshotai.kimi-k2.5, target 500 RPM)
python ramp_test.py --region us-east-1

# Custom target
python ramp_test.py --region us-west-2 --target-rpm 2000 --model-id us.moonshotai.kimi-k2.5

# Dry run (shows the plan without making API calls)
python ramp_test.py --dry-run --target-rpm 2000
```

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--model-id` | `us.moonshotai.kimi-k2.5` | Bedrock model ID to test |
| `--region` | `us-east-1` | AWS region |
| `--target-rpm` | `500` | Target requests per minute |
| `--hold-minutes` | `15` | Minutes to hold at each steady state |
| `--reduction-factor` | `0.5` | Factor to reduce by on 503 errors |
| `--increase-factor` | `1.5` | Factor to increase by after steady state |
| `--error-threshold` | `0.05` | Error rate threshold to trigger reduction (5%) |
| `--max-tokens` | `100` | Max tokens per request |
| `--dry-run` | `false` | Show plan without calling Bedrock |
| `--output` | `results.json` | Output file for results |

## Output

The script produces:
- **Console progress** with live RPM, success rate, and current phase
- **`results.json`** with full ramp history, latency percentiles, and error breakdown

## License

MIT-0 — see [LICENSE](../../LICENSE).
