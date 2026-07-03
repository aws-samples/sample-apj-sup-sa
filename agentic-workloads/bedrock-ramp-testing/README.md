# Amazon Bedrock TPM/RPM Ramp Testing

Test and validate your Amazon Bedrock throughput limits using the recommended
ramp-up procedure from the
[Scaling throughput best practices](https://docs.aws.amazon.com/bedrock/latest/userguide/scaling-throughput-best-practices.html)
documentation.

## ⚠️ Cost Warning

**This tool makes real API calls to Amazon Bedrock that incur charges.**

Before running:
- Use `--dry-run` first to preview the plan without any API calls
- Set `--max-requests N` to enforce a hard budget cap (the test aborts after N total requests)
- A confirmation prompt shows estimated request/token counts before proceeding (bypass with `--yes`)
- Check [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) for your model's per-token cost

Example: A ramp test to 2,000 RPM with 15-minute holds could generate ~50,000+ requests.
At 130 tokens/request on a typical model, that's ~6.5M tokens. **Always start with
`--dry-run` and set `--max-requests` to a budget you're comfortable with.**

---

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

# --- Option 1: bedrock-runtime (Converse API, default) ---
# Uses IAM credentials (boto3). No API key needed.
python ramp_test.py --region us-east-1 --target-rpm 500

# --- Option 2: bedrock-mantle (OpenAI-compatible API) ---
# Uses a Bedrock API key. Separate quotas from bedrock-runtime.
# Create a key: https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html
export BEDROCK_API_KEY="your-key-here"
python ramp_test.py --region us-east-1 --target-rpm 500 --endpoint mantle

# Custom model and target
python ramp_test.py --region us-west-2 --target-rpm 2000 --model-id us.moonshotai.kimi-k2.5 --endpoint mantle --api-key <key>

# Dry run (shows the plan without making API calls)
python ramp_test.py --dry-run --target-rpm 2000
```

### Which endpoint should I test?

| | **bedrock-runtime** | **bedrock-mantle** |
|---|---|---|
| API | Converse / InvokeModel (boto3) | Chat Completions / Responses (OpenAI SDK) |
| Auth | IAM credentials | Bedrock API key |
| Quotas | bedrock-runtime quotas | Separate bedrock-mantle quotas |
| Best for | Native AWS apps, VPC endpoints | OpenAI SDK migration, Mantle engine testing |
| Flag | `--endpoint runtime` (default) | `--endpoint mantle` |

> **Note:** bedrock-mantle has its own independent quotas. If you plan to use both
> endpoints in production, ramp test each one separately.
> See: https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html

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
| `--endpoint` | `runtime` | Bedrock endpoint: `runtime` or `mantle` |
| `--api-key` | (env var) | Bedrock API key for mantle endpoint |
| `--max-tokens` | `100` | Max tokens per request |
| `--max-requests` | unlimited | Hard cap on total requests (budget safety) |
| `--yes` / `-y` | `false` | Skip confirmation prompt (for CI) |
| `--dry-run` | `false` | Show plan without calling Bedrock |
| `--output` | `results.json` | Output file for results |

## Output

The script produces:
- **Console progress** with live RPM, success rate, and current phase
- **`results.json`** with full ramp history, latency percentiles, and error breakdown

## License

MIT-0 — see [LICENSE](../../LICENSE).
