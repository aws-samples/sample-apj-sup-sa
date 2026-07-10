# bedrock-bridge: Bedrock model compatibility matrix

Measured end-to-end: `bedrock-bridge --model <id> --claude --print ...` drives a real Claude Code session against the bridge for each model. Two turns per model:

- **text+tool**: prompt forces a Bash tool call (`echo MATRIX_OK_*`).
- **image+tool**: Claude Code `Read`s a PNG from disk. The image ends up inside a `tool_result`, which exercises the hoist-image-out-of-toolResult transform.

Region: `ap-northeast-1`. bedrock-bridge serves only non-Claude models; Anthropic IDs are refused at preflight (use Claude Code's native `CLAUDE_CODE_USE_BEDROCK=1` mode for Claude).

`image+tool` is marked `N/A` for any model without an IMAGE input modality
(detected via Bedrock's model metadata); the bridge replaces image content
with an explicit text marker before the request leaves, so those models are
not asked to interpret pixels they cannot see.

## Matrix

| Model | text+tool | image+tool | notes |
|-------|-----------|------------|-------|
| `moonshotai.kimi-k2.5` | OK | OK | |
| `moonshot.kimi-k2-thinking` | OK | N/A | text-only |
| `minimax.minimax-m2.5` | OK | N/A | text-only |
| `deepseek.v3.2` | OK | N/A | text-only |
| `qwen.qwen3-235b-a22b-2507-v1:0` | OK | N/A | text-only |
| `qwen.qwen3-coder-480b-a35b-v1:0` | limit | N/A | context window reached on this run; bridge routes to compact |
| `qwen.qwen3-vl-235b-a22b` | OK | OK | |
| `qwen.qwen3-32b-v1:0` | limit | N/A | context window reached on this run; bridge routes to compact |
| `qwen.qwen3-next-80b-a3b` | OK | N/A | text-only |
| `qwen.qwen3-coder-30b-a3b-v1:0` | OK | N/A | text-only |
| `zai.glm-4.7` | OK | N/A | text-only |
| `zai.glm-4.7-flash` | OK | N/A | text-only |
| `zai.glm-5` | OK | N/A | text-only |
| `mistral.mistral-large-3-675b-instruct` | OK | OK | |
| `mistral.magistral-small-2509` | OK | OK | |
| `mistral.devstral-2-123b` | OK | N/A | text-only |
| `google.gemma-3-27b-it` | OK | OK | |
| `openai.gpt-oss-120b-1:0` | OK | N/A | text-only |
| `openai.gpt-oss-20b-1:0` | OK | N/A | text-only |
| `apac.amazon.nova-pro-v1:0` | limit | limit | output-token cap below client default; bridge surfaces a clear message |
| `amazon.nova-lite-v1:0` | limit | limit | output-token cap below client default; bridge surfaces a clear message |
| `nvidia.nemotron-nano-12b-v2` | flaky | flaky | intermittent Bedrock `internalServerException` on this account/region |
| `nvidia.nemotron-super-3-120b` | unavailable | unavailable | requests time out with no response in this account/region |

## Reading the results

- **OK**: tool-use turn completes end-to-end through the bridge.
- **N/A**: model has no vision modality; the image turn is not applicable.
- **limit**: a model limit was reached (context window or output-token cap).
  This is not a bridge failure; the bridge translates the limit into a clear
  message or Claude Code's compact path. See [error-mapping.md](./error-mapping.md).
- **flaky / unavailable**: the model did not respond reliably from this
  account and region during testing. Not a bridge behavior; may differ for you.

Tool-use translation and tool-name/ID normalization work across every model
that responded.

If you hit a failure with a model that should work, it is most likely a bridge-side translation gap (request/response shaping, streaming, tool or image handling), not a Bedrock or model-provider problem. These gaps are work in progress; please file an issue with the model ID and the bridge log. Treat a failure here as "the bridge does not handle this model's shape yet" rather than "Bedrock or the model is broken."

For how the bridge classifies and rewrites the known Bedrock errors (context window reached, output cap, per-image cap, body buffer cap), and the verbatim samples each pattern was built from, see [error-mapping.md](./error-mapping.md).

## Reproducing

```bash
./.venv/bin/python scripts/matrix_e2e.py
# or a subset
./.venv/bin/python scripts/matrix_e2e.py --only kimi qwen
```

The script writes `/tmp/bridge_matrix.md` and leaves per-run bridge logs in `/tmp/bedrock-bridge-<port>.log` for inspection.
