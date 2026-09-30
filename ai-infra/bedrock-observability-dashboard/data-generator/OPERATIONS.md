# Operating the data generator

How to start, monitor, and stop the synthetic Bedrock workload generator. The generator runs detached from any shell (PPID = 1 / launchd) and survives Claude Code exit, terminal close, and shell logout.

All commands assume `cwd = data-generator/`.

## Files at runtime

| File | Purpose | Tracked in git? |
|---|---|---|
| `generate.py` | The generator script | yes |
| `generator.pid` | PID of the running process (one line) | no (gitignored) |
| `generator.log` | All stdout/stderr — startup, per-minute progress, errors, final stats | no (gitignored) |

## Start (detached, survives session exit)

```bash
cd data-generator
pip install -q -r requirements.txt
nohup python3 -u generate.py > generator.log 2>&1 < /dev/null &
disown
echo $! > generator.pid
```

The `-u` flag forces unbuffered output so `tail -f` shows lines as they're written. `< /dev/null` detaches stdin so the process doesn't block on terminal close.

Confirm it's truly detached (parent PID should be `1`):
```bash
ps -p $(cat generator.pid) -o pid=,ppid=,stat=,command=
# expected: <pid>  1  S<n>  python3 -u generate.py
```

If `ppid` is anything other than `1`, the process is still tied to your shell and will die when you close the terminal.

## Monitor

### Live log tail (Ctrl-C just stops the tail; generator keeps running)
```bash
tail -f generator.log
```

### Quick one-shot health check
```bash
ps -p $(cat generator.pid) -o pid=,stat=,etime=,command=    # running? for how long?
tail -3 generator.log                                        # most recent line
```

### Per-minute throughput summary
```bash
grep "phase=" generator.log | tail -20
```
Each `phase=...` line is the once-a-minute progress logger. Format:
```
phase=lunch-burst target_cpm=60 | last_60s {'ok': 54, 'stream_ok': 23, 'err4xx': 3, 'err5xx': 1, 'throttle': 0, 'cache_used': 10} | totals ...
```

| Counter | Means |
|---|---|
| `ok` | successful Converse / ConverseStream calls |
| `stream_ok` | subset of `ok` that used streaming |
| `err4xx` | client-side validation or 4xx (intentional bad model id / bad params) |
| `err5xx` | server 5xx |
| `throttle` | account-level TPM/RPM throttles |
| `cache_used` | calls that included a `cachePoint` (Anthropic models only) |

### Aggregate totals so far
```bash
grep "totals" generator.log | tail -1
```

### Watch the dashboard fill
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=Bedrock-FM-Dashboard

| Section | First data lands |
|---|---|
| 1. Overview tiles | ~2 min |
| 2. Performance (per-model latency p50/p90/p99) | ~3 min |
| 3. Errors / throttles / log-delivery | ~3 min |
| 4. Tokens (incl. cache hit ratio) | ~3 min |
| 5. Quota headroom | ~3 min |
| 6. Logs Insights (top teams, stop-reasons) | ~5–10 min (Bedrock log delivery latency) |

## Stop

```bash
kill -TERM $(cat generator.pid)
```

`SIGTERM` triggers a graceful shutdown — the generator finishes any in-flight call, prints `FINAL stats: {...}` to `generator.log`, and exits.

To force-kill (only if SIGTERM hangs):
```bash
kill -9 $(cat generator.pid)
```

## Restart (e.g. after editing tunables)

```bash
kill -TERM $(cat generator.pid)
sleep 4
nohup python3 -u generate.py >> generator.log 2>&1 < /dev/null &
disown
echo $! > generator.pid
```

`>>` instead of `>` appends rather than overwrites the log.

## What if the laptop sleeps?

The process pauses on sleep, resumes on wake — no state is lost. But CloudWatch will show a flat-line gap on every widget for the sleep duration, and the diurnal `SCHEDULE` continues to advance by wall-clock so you may resume in a different phase.

For unattended 24h+ runs, prefer EC2 t3.micro (free tier) — same `nohup` command, no sleep risk.

## Find the generator from a fresh shell / Claude session

```bash
cat data-generator/generator.pid
ps -p $(cat data-generator/generator.pid) -o pid=,stat=,etime=,command=
# alternative: ps -ef | grep generate.py | grep -v grep
```

## Tuning without restart-loops

Edit constants near the top of `generate.py` then restart:
- `MODELS` — id, weight, caching support, label
- `STREAMING_RATIO` / `CACHING_RATIO` / `ERROR_RATIO`
- `SCHEDULE` — `(start_utc_hour, end_utc_hour, calls_per_minute, label)`
- `TEAMS_APPS` — `requestMetadata.team` / `app` mix

Verify the model id list with:
```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?status==`ACTIVE`].inferenceProfileId' \
  --output text | tr '\t' '\n'
```

## Cost watch

Default config produces ~$15–25 in Bedrock invocation charges over 24h at us-east-1 list prices. To reduce spend: lower the `SCHEDULE` calls-per-minute, or shift weight in `MODELS` away from Sonnet/Opus and toward Haiku/Nova-micro.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `generator.pid` is stale (process gone) | Crash, OOM, or kill | Check `tail -50 generator.log`; restart |
| `nohup: ignoring input and appending output` then process exits immediately | `python3` not on PATH | Use full path or activate venv before nohup |
| All calls are `err4xx` | Wrong model ids in `MODELS` | Run the `aws bedrock list-inference-profiles` command above |
| `AccessDeniedException` | Model access not enabled | Bedrock console → Model access → enable |
| Sustained `throttle` counts > 0 | Hitting account TPM/RPM | Expected during burst windows; lower `SCHEDULE` cpm if persistent |
| Process keeps running but no log lines | Buffered stdout | Confirm you started with `python3 -u` |

## Don't

- Don't commit `generator.log` or `generator.pid` (already gitignored).
- Don't put real customer data in `TEAMS_APPS` — values land in CloudWatch Logs and CloudTrail in plain text.
- Don't `kill -9` first — `SIGTERM` lets the script flush final stats.
