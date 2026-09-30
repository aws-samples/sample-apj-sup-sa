# Synthetic data generator

Drives 24h of realistic Bedrock traffic against the deployed dashboard.

## What it does

Single Python loop. Each iteration:
- picks a model from a 7-model catalog (Claude / Nova / Llama, weighted toward cheap)
- picks a team (`platform`/`search`/`support`/...) + app + env, tags via `requestMetadata`
- 50% streaming (`ConverseStream`) so `TimeToFirstToken` populates
- 40% prompt-cache use (caching-capable models) so cache widgets populate
- 5% intentional errors (bad model id / invalid params) so 4xx widgets populate
- varied `maxTokens` (50–1500) so stop-reasons mix between `end_turn` and `max_tokens`
- diurnal traffic shape: ~2 cpm overnight, ~10 cpm steady, 60 cpm bursts at 8/12/17 UTC

Result: every widget on `Bedrock-FM-Dashboard` shows real data within ~10 minutes.

## Run / monitor / stop

See **[OPERATIONS.md](./OPERATIONS.md)** — start command, live log tail, health checks, restart, troubleshooting.

Quick start:
```bash
cd data-generator
pip install -r requirements.txt
nohup python3 -u generate.py > generator.log 2>&1 < /dev/null &
disown
echo $! > generator.pid
tail -f generator.log
```

## Cost estimate

~$15–25 USD over 24h at us-east-1 list prices, dominated by Sonnet/Opus calls. Edit `MODELS` weights in `generate.py` to skew cheaper/pricier. Weights are integers; relative ratios are what matter.

## Tuning

All knobs are constants near the top of `generate.py`:
- `MODELS` — model id, weight, caching support (or override via the BEDROCK_MODELS env var — no source edit needed)
- `TEAMS_APPS` — `requestMetadata.team` / `app` values
- `STREAMING_RATIO`, `CACHING_RATIO`, `ERROR_RATIO`
- `SCHEDULE` — diurnal `(start_utc, end_utc, calls_per_minute, label)`

## Required IAM

The credentials running the script need:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:Converse", "bedrock:ConverseStream", "bedrock:InvokeModel"],
    "Resource": "*"
  }]
}
```

Plus model access enabled for each id in `MODELS` via the Bedrock console (Model access).

## What it cannot populate

- **CloudTrail / Tier-3 widgets** — the generator's calls appear in CloudTrail under the *generator's own* IAM identity. The Section-7 identity, error-reason, and cross-region-routing widgets reflect your real callers and real routing, so they populate from actual workload, not from this tool. (CloudTrail management-event delivery also lags a few minutes.)
- **Non-us regions / custom model sets** — the default catalog uses `us.` inference-profile IDs. To drive a different region or model mix without editing source, set `AWS_REGION` and `BEDROCK_MODELS` (semicolon-separated `modelId,weight,cache,label` entries — see the header of `generate.py`). If you deployed the dashboard with a custom `TenantMetadataKey`, set the matching `TENANT_METADATA_KEY` env var so the generator tags the same key (default `team`).

## Caveats

- Stops if your laptop sleeps. For unattended 24h runs, prefer EC2/Lambda.
- Rate is bounded by `SCHEDULE` calls-per-minute, but bursts can still hit account TPM limits (which is the intended behavior — the throttle widget needs throttles).
- `requestMetadata` values land in CloudWatch Logs and CloudTrail in plain text. Don't add real customer data.
- `ERROR_RATIO=0.05` produces ~140 4xx errors/day at default rates — enough to populate widgets, low enough to ignore.
