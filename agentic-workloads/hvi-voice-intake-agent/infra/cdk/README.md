# HVI — CDK app (`infra/cdk/`)

Python AWS CDK (`aws-cdk-lib` v2) definition of the **web-only** subset of the
Health Voice Intake deploy as four stacks — Data, Knowledge, Inference, Agent —
in one app, one account, one region (defaults: placeholder account
`111122223333`, `ap-south-1`). The numbered shell scripts in `scripts/` remain
the reference implementation; this app is strictly additive and does not modify
them.

> **A functional intake demo requires `-c hvi:enableNeptune=true`. The default
> deploy provisions the backbone but _cannot complete a condition turn_** —
> `classify_condition` calls the graph (Neptune) first and re-raises on failure,
> so with Neptune off every condition utterance fails the turn. This is correct
> fail-loud behaviour by design, not a bug; it is called out here because it is
> surprising. Turn Neptune on for a clinically functional demo.

## Prerequisites

- Python 3.12+ with this app's dependencies installed into a virtualenv:

  ```bash
  python -m venv .venv && . .venv/bin/activate
  python -m pip install -r infra/cdk/requirements.txt
  ```

- Node.js on `PATH` (jsii needs it at synth time; v22.x confirmed).

## Synthesise and test offline (no AWS, no credentials)

All verification is in-process synthesis via pytest. Run from the repository
root so `stacks` and the asset source packages resolve:

```bash
PYTHONPATH=$PWD python -m pytest infra/cdk/tests -q
```

Each test builds `App(context={...})`, instantiates stacks, and asserts on
`Template.from_stack(...)`. No `cdk deploy`/`bootstrap`/`import`, no `cdk` CLI
subprocess, no AWS calls.

## Context flags (`hvi:*`)

Defaults live in `cdk.json` and are validated (fail-closed) in
`stacks/context.py`. Override with `-c hvi:flag=value`.

| Flag | Type | Default | Effect |
|---|---|---|---|
| `hvi:enableNeptune` | bool | `false` | Provision Neptune + graph seed (clinical core). |
| `hvi:tts` | `polly`\|`omnivoice` | `polly` | TTS engine. `polly` (default) is managed and needs no GPU artifact. `omnivoice` gates the whole OmniVoice subtree (endpoint, artifact, bucket, prompt cache) and is opt-in / CC-BY-NC. |
| `hvi:enableWebMedia` | bool | `false` | Provision the `hvi-turn`/`hvi-web` EC2 media edge + CloudFront. |
| `hvi:skipNat` | bool | `false` | Drop the NAT gateway (pre-baked inference containers). |
| `hvi:agentCompute` | `lambda`\|`agentcore-cr` | `lambda` | Agent compute: Lambda container image (default) or an AgentCore custom resource. |
| `hvi:bedrockModelId` | str | `mistral.mistral-large-3-675b-instruct` | Reasoning model; must be non-empty. |
| `hvi:opensearchInstanceType` | str | `r6g.large.search` | OpenSearch data node type. |
| `hvi:opensearchEngine` | str | `OpenSearch_2.17` | Pinned engine version (no synth-time version lookup). |
| `hvi:webCallbackUrl` | str | `http://localhost:8095` | Cognito SPA callback/logout URL. |
| `hvi:cloudfrontOriginPrefixListId` | str | _(unset)_ | CloudFront origin-facing prefix list id for the `hvi-web-sg` ingress. |
| `hvi:account` | str | `111122223333` | Target account. |
| `hvi:region` | str | `ap-south-1` | Target region. |
| `hvi:enableTelephony` | bool | `false` | Provision the Chime/Asterisk telephony edge (carrier-specific; opt-in). |

> **TTS licensing:** Amazon Polly is the managed default and the published
> default. OmniVoice (`-c hvi:tts=omnivoice`) is opt-in and licensed CC-BY-NC
> (non-commercial), so it is **not** the default. Engine selection is a
> deploy-time choice with no silent runtime fallback: a misconfigured engine
> fails loudly at startup.

## Post-deploy verification checklist (live AWS — run by an operator)

These require credentials and are **out of scope** for the offline synthesis
tests (which deliberately make no AWS calls). Run them after a real deploy:

- **GPU preflight** actually read live service quota and the Whisper/OmniVoice
  endpoint ladders were filtered to pools with headroom (no `NO_USABLE_GPU`).
- **Whisper** endpoint `hvi-whisper` reached `InService`; and under
  `hvi:tts=omnivoice`, **OmniVoice** `hvi-omnivoice` reached `InService`.
- **Graph seed** (`hvi:enableNeptune=true`) populated Neptune — `{"action":"stats"}`
  on `hvi-graph` returns a non-empty graph.
- **Vector seed** created `hvi-icd-concepts` with the distractor null class
  present, without dropping `hvi-call-transcripts`.
- **Form + CRM seed** materialised into `hvi-form-schema` / `hvi-crm-cache`.
- **Agent** answers `GET /ping` (Lambda) and a `POST /invocations` round-trips.
- Archiving works: a completed call writes a transcript under
  `s3://hvi-archive-<account>-ap-south-1/calls/<callId>/`.

> Safety: the archive bucket and all DynamoDB tables are `RETAIN`; versioning,
> SSE, public-access-block and TLS-only are never disabled. Treat any deployment
> that stores real health data accordingly. Never run
> `cdk deploy`/`bootstrap`/`import` as part of testing this app.
