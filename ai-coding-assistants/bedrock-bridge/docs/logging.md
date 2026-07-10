# Logging

The bridge writes its logs to `/tmp/bedrock-bridge-<port>.log` (the path is
printed at startup). Verbosity has three tiers, set by `--log-level` or the
`BEDROCK_BRIDGE_LOG_LEVEL` environment variable. The CLI flag wins; the env var
is the fallback; the default is `default`.

```bash
bedrock-bridge -m moonshotai.kimi-k2.5 --log-level verbose
BEDROCK_BRIDGE_LOG_LEVEL=verbose bedrock-bridge -m moonshotai.kimi-k2.5
```

`--log-level` is a bridge flag, so it goes before `--claude`. A `--log-level`
placed after `--claude` is forwarded to the `claude` command instead (see the
boundary rule in the README).

## Tiers

| Tier | Server level | uvicorn level | What it logs |
|------|--------------|---------------|--------------|
| `default` | `INFO` | `warning` | One access line per request (routed model, stream flag, tool count), plus warnings and errors. |
| `verbose` | `DEBUG` | `info` | Adds internal adaptation detail: vision adaptation (images stashed or stripped), history-recall fixups, `describe_image` round counts and loop detection. |
| `debug` | `TRACE` | `debug` | Adds request and response content: the full request body, the outgoing Converse kwargs, the JSON response, and each `describe_image` prompt the main model authored. |

Each tier is a superset of the one above it.

## The debug tier and PII

`debug` logs prompt text verbatim. That includes anything in the conversation:
source code, file contents, names, secrets a tool happened to surface. Images
are replaced with a `<redacted: N bytes>` or `<redacted: N base64 chars>` marker
(incoming Anthropic requests carry base64-encoded images, outgoing Converse
kwargs carry raw bytes, so both forms can appear; the count is kept because
request-body-size limits are a common thing to debug), but text is not truncated
or masked.

Because of that, `debug` is gated:

- The CLI prints the log path and asks `Proceed? [y/N]` before starting. Only
  `y` / `yes` continues; anything else aborts.
- On a non-TTY (CI, or `--print`), `debug` hard-fails rather than logging
  content unprompted. There is deliberately no `--yes` / `-y` bypass.

Use `debug` to capture a self-contained reproduction for a bug report: run the
failing request once at `debug`, then attach the relevant slice of
`/tmp/bedrock-bridge-<port>.log`. Review it for sensitive content before
sharing.

## Note on errors

AWS error strings can quote parts of a request and are logged at `ERROR`, which
is visible at every tier including `default`. This predates the tiers and is
independent of `--log-level`.
