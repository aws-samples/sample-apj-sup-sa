# QUICKSTART — Resume After Compaction

**Date created:** 2026-06-12 · **Last updated:** 2026-06-12 (post live-test fixes)
**Branch:** `memory-and-voice-fixes-research` — rebased on latest `main`, all work
COMMITTED + PUSHED. MR #1 open: https://gitlab.aws.dev/diponego/voice-analytics-agent/-/merge_requests/1
**Audience:** Future Claude session resuming work, or teammate picking up.

## TL;DR — what's been done

User (chanlyw) wanted to fix three concerns: (1) AgentCore Memory not threading
context across voice turns, (2) markdown leaking into TTS so Aura-2 reads
asterisks aloud, (3) Deepgram credit anxiety. Built fixes, a teammate ran a
LIVE voice demo, and that surfaced two more bugs (UTF-8 decode crash + bot
going silent after a few turns) which are now also fixed.

**All fixes are committed + pushed on `memory-and-voice-fixes-research`.** The
branch is rebased on `main`. A teammate is deploying/testing on their own AWS
account via the new `scripts/deploy_backend.sh` (see below).

## The fixes (all committed on this branch)

Branch commits on top of `main`, newest first:

| Commit | What | Deploy needed? |
|--------|------|----------------|
| `e6456e3` | **Fix bot going silent after a few turns** (see below — the important one) | Agent-side: redeploy agent container. Processor-side: restart bot. |
| `650dad4` | Add `specs/TRIAGE-2026-06-12.md` (live-test triage notes) | No |
| `669006f` | **UTF-8 decode fix** — `analytics_processor.py` uses `codecs.IncrementalDecoder` so multibyte chars (em-dash etc.) at HTTP chunk boundaries don't crash the stream | No — restart bot |
| `56cec50` | Original 3 fixes: stable `runtimeSessionId` (memory threading), `MarkdownTextFilter` on TTS, + the now-superseded memory-blocks change | Mixed |

### Current state of each original concern

| # | Concern | Fix | Where | Deploy? |
|---|---------|-----|-------|---------|
| 1 | Memory write-only (no threading across turns) | `analytics_processor.py`: `+import secrets`; constructor stores `self._session_id = f"voice-{secrets.token_hex(20)}"` (40 chars; AWS requires ≥33); `invoke_agent_runtime` passes `runtimeSessionId=self._session_id` | `server/analytics_processor.py` | No — local Pipecat. **Verified working in live test** (Dec drill-down recalled prior turns). |
| 2 | Markdown leaks to Aura-2 TTS | `MarkdownTextFilter` on both Deepgram TTS services (`filter_code=True, filter_tables=True`) | `server/bot.py` | No. Verified: no markdown spoken in live test. |
| 3 | Memory replay re-ran tools (Bug B) | **ABANDONED.** We tried saving toolUse/toolResult blocks → caused the silent-hang bug. Reverted to plain-text-only persistence. Re-running a tool on follow-ups is acceptable for analytics. | both agent files | Agent redeploy |

### ⚠️ The silent-hang bug (`e6456e3`) — read this

Two compounding bugs made the bot go mute after ~5 turns:

1. **Crash:** our earlier "save full content blocks" change persisted
   `toolUse`/`toolResult` blocks. Replaying them orphans a tool pair when the
   `list_events` window (`max_results`) truncates through one. Bedrock Converse
   requires every `toolUse` be immediately followed by its `toolResult`; an
   orphan makes it reject the whole message list. ~5 tool turns fills the
   window → every later turn fails.
   **Fix:** reverted both agents' `MemoryHookProvider` to persist + replay
   PLAIN TEXT only (always Converse-valid). Loader flattens any JSON-encoded
   events left in memory from the prior build.
2. **Silence:** on error the agent yields `{"type":"text","content":...}` but
   `analytics_processor._extract_text` only read the `contentBlockDelta` shape
   → no audio. **Fix:** `_extract_text` now also recognizes the error shape, so
   agent failures are SPOKEN, not dropped.

**If the bot ever goes silent again:** the spoken error message will now tell
you why. Check AgentCore Runtime CloudWatch logs for the real exception.

## Why these specific fixes

### Bug A (fix #1) — write-only memory

`analytics_processor.py` calls `invoke_agent_runtime` once per voice turn.
Stock Pipecat `AWSAgentCoreProcessor` doesn't pass `runtimeSessionId`, so AWS
generates a fresh one each call. Agent's `MemoryHookProvider` keys
`list_events` / `create_event` on `context.session_id` — which equals the
caller-supplied `runtimeSessionId`. So writes always go to a fresh session,
reads always come back empty. Memory is effectively write-only.

Fix: one stable session ID per Pipecat connection (= one processor instance).
Confirmed against:
- `aws-knowledge` MCP → SDK docs for `InvokeAgentRuntimeRequest.runtimeSessionId`
- `bedrock-agentcore-mcp-server` MCP → `aws.github.io/bedrock-agentcore-starter-toolkit/examples/session-management.md` shows the `--session-id conv1` pattern, and `examples/memory_gateway_agent.md` uses 40-char session IDs in its LTM testing block.

### Bug B (memory replay re-runs tools) — ATTEMPTED THEN ABANDONED

`resources/agentic-analytics-workshop/dev/issues/memory-replay-re-executes-tools.md`
documents this. The hook stripped `toolUse`/`toolResult` blocks before saving,
so on reload the agent re-called tools on follow-ups. The issue file prescribes
"Option B — Save Full Content Blocks". **We tried that and it backfired** —
replaying tool blocks orphans a tool pair when the load window truncates, which
makes Bedrock Converse reject the message list and the bot goes silent after
~5 turns (see `e6456e3` / the silent-hang section above). We reverted to
plain-text-only. The re-running-tools behavior is back, but for analytics a
re-query on "break that down by month" is usually correct, and it's vastly
better than a mute bot. **Do not re-attempt the full-content-blocks approach
without solving tool-pair-safe truncation first.**

### Fix #4 — markdown filter

User reported "only the response summary is spoken." Voice SOP at
`server/unicorn_rental_voice.sop.md` already forbids markdown, but Claude
Opus 4.6 (the deployed model per `MILESTONE_3_README.md:123`) emits tables
anyway for tabular data. Aura-2 has no markdown stripping, so pipes/asterisks
go to the voice model raw → garbled audio.

Fix: defense-in-depth. Pipecat ships `MarkdownTextFilter` for this exact
purpose — strips `**bold**`, `*italic*`, headers, table pipes. Verified
constructor and kwarg name (`text_filters` plural, list) against the pipecat
package source in the local venv.

## What's NOT done

- ❌ **2-turn memory test not yet added.** Plan: extend the teammate's committed
  integration test `server/tests/test_agentcore_integration.py` (not a throwaway
  `/tmp/` script) with a 2-turn assertion that proves memory threading. The
  existing test is single-shot only. See "Memory test plan" below.
- ❌ Agent container not yet redeployed with `e6456e3` (the silent-hang fix is
  agent-side — needs `scripts/deploy_backend.sh --agent-only`). The
  processor-side half is local-only and live the moment you restart `bot.py`.
- ❌ Memory store may hold poisoned JSON-block events from earlier testing. The
  new loader flattens them, but if behavior is weird, clear the memory or just
  use a fresh `runtimeSessionId`.
- ❌ `specs/voice-integration.md` §7.5 still suggests session_id in the JSON
  payload — wrong, it goes on the boto3 call as `runtimeSessionId`. Minor doc nit.

## Key context the user has confirmed

- **Architecture:** cascaded Pipecat (Deepgram STT → AgentCore-as-LLM → Deepgram TTS), NOT speech-to-speech. See `specs/voice-integration.md` §2.
- **Deploy:** teammate deploys/tests on THEIR AWS account from MR #1. chanlyw's
  own from-scratch deploy attempt was abandoned (kept fighting shell paste
  issues; teammate already had a working stack).
- **Deploy tooling:** use `scripts/deploy_backend.sh` (added on main, commit
  `51cd9dd`). It packages, uploads the voice SOP, deploys/updates CFN, pulls
  demo creds from SSM, and writes `server/.env`. `--agent-only` is the ~5–8 min
  fast path for agent-code changes (CodeBuild rebuild → update-agent-runtime →
  wait READY → update endpoint). This SUPERSEDES the old manual deploy steps.
- **Don't switch to Deepgram Voice Agent API** — would throw away the Strands
  agent + analytics tools + Cognito/Cedar/RLS plumbing. Reaffirmed.
- **Deepgram credits:** $200 covers ~2,000+ full demo sessions. Don't ask for more.
- **Don't use SageMaker mode** to "save credits" — requires Deepgram enterprise contract. Stick with PAYG cloud.

## How to deploy (use `scripts/deploy_backend.sh`)

The old manual `package_and_upload → create-stack → docker → update-agent-runtime`
sequence is GONE — `deploy_backend.sh` does all of it. From the repo root:

```bash
# Ambient AWS creds must point at the target account (aws sts get-caller-identity).
# jq, aws CLI, npm, uv, pip3 must be installed.

# Full backend bring-up (~25-35 min: Aurora, VPC/NAT, Glue, KB, AgentCore, Cognito, Amplify):
scripts/deploy_backend.sh
#   Optional env overrides: BUCKET=... STACK=... REGION=... DEMO_ROLE=rental_admin
#   --recreate      force delete + recreate (DESTROYS Aurora data)
#   --skip-package  reuse artifacts already in S3 (faster re-deploy)

# Fast path for AGENT-CODE changes only (~5-8 min) — this is what our fixes need:
scripts/deploy_backend.sh --agent-only
#   re-zips agent code → CodeBuild rebuild+push → update-agent-runtime → wait READY → update endpoint

# The script writes server/.env for you (preserving DEEPGRAM_API_KEY / DAILY_API_KEY
# if already set). Then run the voice bot:
cd server && uv sync && uv run bot.py --transport daily
# open the printed http://localhost:7860 URL and talk
```

`.env` is generated by the script. You only hand-fill `DEEPGRAM_API_KEY`
(console.deepgram.com) and `DAILY_API_KEY` (dashboard.daily.co) — everything
else (AWS_AGENT_ARN, Cognito, demo user/pass from SSM) is auto-populated.

## Memory test plan (extend the committed integration test)

`server/tests/test_agentcore_integration.py` (added on main, `b5d212e`) already
does Cognito → invoke → SSE → no-markdown, single-shot. `conftest.py` auto-skips
when env vars are missing. **TODO:** add a second test in that file:

- `test_memory_threads_across_turns`: pick a fixed `runtimeSessionId`, call
  `invoke_agent_runtime` twice with it — turn 1 states a fact ("my favorite
  unicorn breed is the lunar moonhorn"), turn 2 asks for it back ("what's my
  favorite breed?"). Assert the breed appears in turn 2's spoken text. This is
  the live proof that fix #1 (`runtimeSessionId` threading) works end-to-end.
- Pass `runtimeSessionId=` on the boto3 call (NOT in the JSON payload).
- Run with: `cd server && uv run --group dev pytest tests/ -m integration -v`
  (auto-skips without a populated `.env`).

**Reference snippet** for the 2-turn logic (adapt into the pytest test above;
scenario A proves the OLD bug, scenario B proves fix #1):

```python
import json, os, secrets, sys, boto3
sys.path.insert(0, ".")  # Pipecat venv has auth.py
from dotenv import load_dotenv
load_dotenv()
from auth import get_gateway_token

token = get_gateway_token()
client = boto3.client("bedrock-agentcore", region_name=os.environ["AWS_REGION"])
ARN = os.environ["AWS_AGENT_ARN"]

def call(prompt, session_id=None):
    kwargs = dict(
        agentRuntimeArn=ARN,
        payload=json.dumps({
            "prompt": prompt, "gateway_token": token,
            "sop_s3_key": "sops/unicorn_rental_voice.sop.md",
        }).encode(),
    )
    if session_id:
        kwargs["runtimeSessionId"] = session_id
    resp = client.invoke_agent_runtime(**kwargs)
    raw = resp["response"].read().decode("utf-8")
    texts = []
    for line in raw.split("\n"):
        if line.startswith("data: "):
            try:
                d = json.loads(line[6:])
                t = d.get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
                if t: texts.append(t)
            except Exception: pass
    return "".join(texts)

# Scenario A — no session id (should NOT recall)
print("=== A: no runtimeSessionId ===")
print("Turn 1:", call("My favorite unicorn breed is the lunar moonhorn.")[:200])
print("Turn 2:", call("What is my favorite unicorn breed?")[:200])

# Scenario B — stable session id (should recall after fixes)
sid = f"voice-{secrets.token_hex(20)}"  # 40 chars
print("\n=== B: stable runtimeSessionId ===")
print("Turn 1:", call("My favorite unicorn breed is the silver starhoof.", sid)[:200])
print("Turn 2:", call("What is my favorite unicorn breed?", sid)[:200])
```

## File map — where to look for what

| Path | Purpose |
|------|---------|
| `CLAUDE.md` | Project guide. Read first. |
| `specs/voice-integration.md` | Original design doc. §7.5 is OUTDATED — see "Spec edits needed" below. |
| `specs/implementation-plan.md` | M1-M4 milestones. M4 task 1 is OUTDATED. |
| `specs/QUICKSTART.md` | This file. |
| `scripts/deploy_backend.sh` | **THE deploy script** (on main). Full bring-up + `--agent-only` fast path. Writes `server/.env`. |
| `server/bot.py` | Pipecat pipeline (modified — MarkdownTextFilter on TTS) |
| `server/analytics_processor.py` | Custom Pipecat processor (modified — `runtimeSessionId`, IncrementalDecoder, error-shape extraction) |
| `server/tests/test_agentcore_integration.py` | Integration test (on main). EXTEND with 2-turn memory test. |
| `server/tests/conftest.py` | Skip-guard: auto-skips integration tests if `.env` vars missing |
| `server/auth.py` | Cognito ROPC token helper |
| `server/unicorn_rental_voice.sop.md` | Voice SOP (text rules, already strict) |
| `.mcp.json` + `.claude/settings.json` | MCP servers (committed). `bedrock-agentcore-mcp-server`, `aws-knowledge`, `pipecat-docs`, `deepgram-docs`. |

> ⚠️ **Historical note:** this doc predates the repo restructure. Many paths it
> cites (`server/`, `resources/agentic-analytics-workshop/`) no longer exist —
> voice now lives under `app/voice/` and the agent under
> `app/agentcore_strands/`. Kept for the design rationale, not the file paths.

## Spec edits needed (not yet done)

**`specs/voice-integration.md` §7.5** — currently says thread session_id "into
the agent payload." That's wrong. Replace with:

> Threading happens at the boto3 layer, not in the JSON payload.
> `AnalyticsAgentCoreProcessor` generates a stable session ID at construction
> (one per Pipecat connection) and passes it as `runtimeSessionId=` to
> `invoke_agent_runtime`. The deployed agent's existing `context.session_id`
> plumbing then keys memory correctly without further changes. Stock
> `pipecat-ai[aws]` `AWSAgentCoreProcessor` does NOT support this — confirmed
> by reading the package source. We extend our existing subclass.

**§7.3** — add footnote: "SOP alone insufficient; Claude Opus 4.6 emits tables
anyway. Mitigation at the pipeline layer via Pipecat `MarkdownTextFilter` on
TTS service."

**§9** — close: "Can we pass `gateway_token` through the processor?" → resolved
yes via subclass; already done.

**`specs/implementation-plan.md` M4 task 1** — fix the example:

```python
# Wrong:
payload = {"prompt": user_text, "gateway_token": token, "session_id": daily_room_id}

# Right (in AnalyticsAgentCoreProcessor.__init__):
self._session_id = (daily_room_id or f"voice-{secrets.token_hex(20)}")[:128]
# In invoke_agent_runtime call:
runtimeSessionId=self._session_id,
```

## MCP servers — use these BEFORE answering AWS questions

| MCP | Use for |
|-----|---------|
| `bedrock-agentcore-mcp-server` (`search_agentcore_docs`, `fetch_agentcore_doc`) | Anything AgentCore (Runtime, Memory, Gateway, sessions) |
| `aws-knowledge` (`aws___search_documentation`, `aws___read_documentation`) | General AWS API/SDK references |
| `aws-documentation` (`search_documentation`, `read_documentation`) | AWS service docs |
| `pipecat-docs` (`searchDocs`) | Pipecat APIs, requires OAuth on first use |
| `deepgram-docs` (`searchDocs`) | STT/TTS questions, no auth |

Defaulting to WebFetch + memory was the wrong move; user called it out.

## Open questions still on the table

- Do we want voice path on Haiku 4.5 (faster) vs current Opus 4.6 (better,
  documented in `agentcore-stack.yaml:1454`)? Spec §7.6 recommends Haiku for
  voice; current deploy is Opus.
- Enable `USE_FLUX=true` for better turn-taking? Spec §7.6 recommends.
- Switch `DEEPGRAM_VOICE_ID` to `aura-2-odysseus-en`? User mentioned it.

## Recommended resume action

All code fixes are committed + pushed (MR #1). The teammate is testing on their
account. Pick up with whichever matches state:

1. **Default next task** → add the 2-turn `test_memory_threads_across_turns` to
   `server/tests/test_agentcore_integration.py` (see "Memory test plan" above).
   This is the one piece of planned work not yet done.
2. **If teammate reports a NEW bug** → triage from their chat/RTVI logs +
   AgentCore CloudWatch. The silent-hang fix means errors are now spoken, so
   the bot should tell you what's wrong.
3. **If teammate confirms the silent-hang is gone** → close out, consider the
   two TRIAGE items (canned-summary regression, guardrail false-positive on
   "booking summary") if there's time before the demo.
4. **If validating locally** → `scripts/deploy_backend.sh --agent-only` to push
   the agent fix, then `cd server && uv run bot.py --transport daily`.

## Known traps

- `uuid.uuid4().hex` is **32 chars — one short** of AWS minimum for
  `runtimeSessionId`. Use `secrets.token_hex(20)` (40) or prefix something.
- **Do NOT save toolUse/toolResult blocks to memory** and replay them — it
  orphans tool pairs on truncation → Converse rejects the message list → bot
  goes silent. Plain-text persistence only. (This is the whole point of `e6456e3`.)
- `MarkdownTextFilter` strips URL schemes (`https://` → ``). Fine for voice.
- Multibyte chars at HTTP chunk boundaries crash a naive `chunk.decode("utf-8")`
  — that's why `analytics_processor.py` uses `codecs.IncrementalDecoder`. Don't
  revert that.
- Agent-side changes (the two `*_agent.py` files) need a **container rebuild** —
  `scripts/deploy_backend.sh --agent-only`. Processor/bot changes are local;
  just restart `bot.py`.
- When pasting multi-line shell commands into the user's zsh, they sometimes
  split across lines and break. Prefer single-line commands or run one at a time.
- `gitlab.aws.dev` URLs — `ReadInternalWebsites` MCP doesn't support them.
- Remote is GitLab (`gitlab.aws.dev`), not GitHub. `glab` isn't auth'd for that
  host, so update MR descriptions manually in the web UI.
