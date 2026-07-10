# Claude Code on non-Claude Bedrock models (bedrock-bridge)

Run Claude Code (and any Anthropic-API client) against any non-Claude Bedrock model: Kimi, Llama, DeepSeek, Qwen, GLM, MiniMax, Mistral. Local proxy that translates the Anthropic Messages API to the Bedrock Converse API.

## Install

With [uv](https://docs.astral.sh/uv/), from source:

```bash
uv tool install git+https://github.com/prog893/bedrock-bridge.git
```

Or run it without installing:

```bash
uvx --from git+https://github.com/prog893/bedrock-bridge.git bedrock-bridge -m moonshotai.kimi-k2.5 --claude
```

Prerequisites: macOS, AWS credentials, Bedrock model access enabled, IAM permissions ([docs/iam.md](./docs/iam.md)). For `--claude`: `claude` CLI on PATH (`brew install claude-code`).

## Quickstart

```bash
# Run Claude Code through Kimi K2.5
bedrock-bridge --model moonshotai.kimi-k2.5 --claude

# Two-model setup: main + light
bedrock-bridge -m moonshotai.kimi-k2.5 --model-light minimax.minimax-m2.5 --claude

# Just run the proxy; wire your own client
bedrock-bridge --model moonshotai.kimi-k2.5

# Text-only main model, with a vision model for images
bedrock-bridge -m deepseek.v3.2 --vision-model qwen.qwen3-vl-235b-a22b --claude
```

| Slot | Env var | CLI flag |
|------|---------|----------|
| Main (required) | `BEDROCK_BRIDGE_MODEL` | `--model` / `-m` |
| Light (optional) | `BEDROCK_BRIDGE_MODEL_LIGHT` | `--model-light` |
| Vision (optional) | `BEDROCK_BRIDGE_MODEL_VISION` | `--vision-model` |
| Log verbosity | `BEDROCK_BRIDGE_LOG_LEVEL` | `--log-level` |

The light slot is for background tasks Claude Code dispatches to a smaller model. If no light model is configured, all requests route to the main model.

The vision slot lets a text-only main model work with images. When set, the bridge inspects each attached image with this image-capable model and feeds the result back through a `describe_image` tool the main model can call (see [Images](#images)). If the main model is itself image-capable, setting `--vision-model` routes images to the vision model anyway and treats main as text-only.

Claude Code's auto-mode safety classifier works through the bridge. With a light slot configured it runs there; without one it falls through to the main model.

Pass any Bedrock foundation ID (`moonshotai.kimi-k2.5`) or inference-profile ID (`us.meta.llama4-...`) directly. CLI flags override env vars.

`--claude` is a hard boundary: every token after it is forwarded to the `claude` command verbatim, even one that matches a bridge flag. So bridge flags go before `--claude`, and Claude Code flags go after: `bedrock-bridge -m moonshotai.kimi-k2.5 --log-level verbose --claude --verbose` runs the bridge at verbose and passes `--verbose` to `claude`.

### Logging

`--log-level` (or `$BEDROCK_BRIDGE_LOG_LEVEL`) sets bridge verbosity. Logs go to `/tmp/bedrock-bridge-<port>.log`.

| Tier | Contents |
|------|----------|
| `default` | One access line per request, plus warnings and errors. |
| `verbose` | Adds internal adaptation detail (model routing, vision adaptation, history-recall fixups, `describe_image` rounds). |
| `debug` | Adds request and response content (prompt text, full request body and outgoing Converse kwargs; image bytes redacted). |

`debug` writes prompt content to the log file, so it asks for interactive confirmation before starting and refuses to run on a non-TTY (there is no bypass flag). Use it to capture self-contained evidence for a bug report. See [docs/logging.md](./docs/logging.md).

### Resuming sessions

Claude Code's `--continue` and `--resume` work normally through the bridge:

```bash
# Continue the most recent session in the current directory
bedrock-bridge -m moonshotai.kimi-k2.5 --claude --continue

# Pick a session interactively
bedrock-bridge -m moonshotai.kimi-k2.5 --claude --resume

# Resume a specific session by id
bedrock-bridge -m moonshotai.kimi-k2.5 --claude --resume <session-id>
```

### Aliases

Alias the bridge to a short command for frequent use. Add to `~/.zshrc` or `~/.bashrc`:

```bash
# Dedicated command per model; leaves `claude` untouched
alias claude-kimi='bedrock-bridge -m moonshotai.kimi-k2.5 --model-light minimax.minimax-m2.5 --claude'
alias claude-glm='bedrock-bridge -m zai.glm-5 --model-light zai.glm-4.7-flash --claude'  # text-only; image turns intercepted

# Or override `claude` so every invocation routes through the bridge
alias claude='bedrock-bridge -m moonshotai.kimi-k2.5 --model-light minimax.minimax-m2.5 --claude'
```

All forms accept the full `claude` flag set, including `--continue` and `--resume`.

## Privacy

Under `--claude`, the bridge sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` on the spawned Claude Code process. That umbrella opt-out disables Anthropic operational telemetry, Sentry error reporting, the `/feedback` command, the autoupdater, and session quality surveys. Local state (session transcripts, `/cost`, auto-memory) is unaffected. Override by exporting `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=0` before launch.

The bridge itself makes no outbound calls except to AWS Bedrock and STS, tagged with `User-Agent: bedrock-bridge/<version>`.

## Running Claude on Bedrock

bedrock-bridge does not serve Claude models. Use Claude Code's native Bedrock mode (`CLAUDE_CODE_USE_BEDROCK=1`); see Anthropic's [setup guide](https://code.claude.com/docs/en/amazon-bedrock).

## Known limitations

- macOS only.
- Bedrock models have a request body size cap, limiting the amount of data sendable in one request. When the cap is hit, Claude Code's TUI shows "Context limit reached · /compact or /clear to continue" and the session pauses. Run `/compact` to summarize old turns and continue, or `/clear` to start fresh. Common trigger: many large tool_result blocks (parallel screenshots, big file reads) accumulated across turns.
- Image handling on a text-only main model depends on whether `--vision-model` is set; see [Images](#images).
- Claude Code's `/model` command is not supported. Every request routes to the model configured at bridge startup; in-session model swaps have no effect. Restart the bridge with a different `--model` to switch.

## Images

A text-only Bedrock model cannot accept image input. How the bridge handles an attached image depends on whether a vision slot is configured.

**With `--vision-model` set.** Each image is held by the bridge and replaced, in the text the main model sees, with a short marker noting that the image is inspectable. The bridge injects a `describe_image` tool (visible only to the main model, never to Claude Code). When the main model wants to see an image it calls `describe_image` with a handle and a prompt stating what it needs to know; the bridge runs the vision model on the real image bytes with that prompt and returns the description as the tool result. The description is a second-hand text rendering produced by another model for a specific question, not the image loaded into the main model's context. The result is framed to make that explicit, so the main model does not treat it as if it had seen the image directly.

**Without a vision slot.** Each image is replaced with a text marker telling the model to inform the user that images need a vision model, and that they can restart the bridge with `--vision-model <image-capable-model-id>` (or set `$BEDROCK_BRIDGE_MODEL_VISION`) to enable image support. The request still goes through so the session continues normally.

In both cases the bridge forwards the turn rather than rejecting it: a rejection would leave the image in Claude Code's transcript, which then re-sends it on every following turn.

## Docs

- [docs/architecture.md](./docs/architecture.md): request flow, translation, preflight, routing.
- [docs/iam.md](./docs/iam.md): minimum policy template.
- [docs/compatibility.md](./docs/compatibility.md): end-to-end matrix across providers.

## Development

```bash
git clone https://github.com/prog893/bedrock-bridge.git
cd bedrock-bridge && uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'
pytest
```

See [CONTRIBUTING.md](https://github.com/prog893/bedrock-bridge/blob/main/CONTRIBUTING.md) for the test layers, the pre-commit
hook, and PR expectations.

## License

Licensed under MIT-0, inherited from the repository [LICENSE](../../LICENSE).
