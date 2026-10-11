# Health Insurance Voice Intake Agent (reference sample)

A reference implementation of a voice agent that completes a health-insurance
medical-history intake call on AWS. A customer who has bought a policy is called
(or connects over the web), the agent walks them through the insurer's proposal
questionnaire in English, Hindi or code-mixed Hinglish, screens disclosed
conditions against a clinical knowledge graph, and writes a structured,
auditable record back to the CRM.

This repository is a sample for learning and experimentation. It is not a
production system and ships no real customer data, account identifiers, or
telephony credentials — every such value is a placeholder.

## Architecture

The deploy is modelled as an AWS CDK app (`infra/cdk/`) composed of four core
stacks plus an optional telephony edge:

- **Data** — DynamoDB tables (call state, sessions, CRM cache, form schema,
  responses, consent ledger, audit) and the per-call S3 transcript/recording
  archive, with versioning, SSE, TLS-only and public-access-block.
- **Knowledge** — an OpenSearch k-NN index that interprets what a caller meant,
  and (opt-in) a Neptune graph that deterministically enforces known
  condition/synonym relationships.
- **Inference** — the Whisper speech-to-text SageMaker endpoint (always), and
  the opt-in OmniVoice text-to-speech endpoint.
- **Agent** — the reasoning agent (Amazon Bedrock) packaged as a Lambda
  container image (or an AgentCore custom resource), plus the Cognito-protected
  web surface and the CRM sync/writeback handlers.
- **Telephony (opt-in)** — a Chime SDK Voice Connector + Asterisk edge for PSTN
  calls, kept behind a flag because it is carrier-specific.

The numbered scripts under `scripts/` are a shell reference implementation of the
same deploy; the CDK app is additive.

### Two regions

Data and reasoning live in one region (default `ap-south-1`) to keep health data
in-region. The Chime telephony edge lives in `us-east-1`, because Chime SDK Voice
Connector is not offered in every region. The split only appears when telephony
is enabled.

## Prerequisites

- An AWS account and credentials with permission to deploy the stacks.
- [AWS CDK v2](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html).
- Python 3.12+.
- Node.js (jsii needs it at synth time).

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -r infra/cdk/requirements.txt
```

## Deploy

Deployment is driven by CDK context flags (`-c hvi:flag=value`); defaults live in
`infra/cdk/cdk.json`. From the repository root:

```bash
# Set your target account/region first (placeholders ship in cdk.json).
cd infra/cdk
cdk synth   -c hvi:account=<your-account-id>
cdk deploy --all -c hvi:account=<your-account-id>
```

### Opt-in flags

| Flag | Default | What it does |
|---|---|---|
| `hvi:tts` | `polly` | TTS engine. Polly is the managed default (no GPU). `omnivoice` opts into the OmniVoice endpoint. |
| `hvi:enableNeptune` | `false` | Provision the Neptune knowledge graph. Required for a clinically functional condition turn; off by default because an idle cluster carries cost. |
| `hvi:enableWebMedia` | `false` | Provision the browser/WebRTC media edge + CloudFront. |
| `hvi:enableTelephony` | `false` | Provision the Chime/Asterisk PSTN edge (carrier-specific). |

### Text-to-speech and licensing

Amazon **Polly** is the managed default: no GPU, no extra artifact, nothing to
build. **OmniVoice** (`k2-fsa/OmniVoice`) is **opt-in** via
`-c hvi:tts=omnivoice`. OmniVoice is licensed **CC-BY-NC (non-commercial)**, so
it is deliberately **not** the default.

Engine selection is a **deploy-time** choice. There is no silent runtime
fallback between engines: a misconfigured TTS fails loudly at startup rather than
substituting a different voice mid-call.

## Cost and cleanup

- Neptune (`hvi:enableNeptune=true`) runs a cluster that costs money while idle;
  leave it off unless you need the condition graph, and tear it down when done.
- The OmniVoice path provisions a GPU SageMaker endpoint; the default Polly path
  does not.
- To remove everything you deployed:

  ```bash
  cd infra/cdk
  cdk destroy --all -c hvi:account=<your-account-id>
  ```

  The S3 archive bucket and DynamoDB tables are retained by design (they may hold
  records you want to keep); delete them manually if you are sure.

## Tests

The offline verification entry point runs the CDK synthesis tests and the
deterministic Python suites, and also scans the tree for stray identifiers:

```bash
python tools/publish_scrub_check.py
```

Many suites under `tests/` are integration tests that require deployed AWS
resources or a Salesforce org and cannot run offline.

## Security

See [CONTRIBUTING.md](CONTRIBUTING.md) for security issue reporting. This sample
creates network-exposed resources (e.g. the Cognito-protected web surface) only
behind opt-in flags; review the IAM, networking and data-retention settings
before any non-sample use.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
