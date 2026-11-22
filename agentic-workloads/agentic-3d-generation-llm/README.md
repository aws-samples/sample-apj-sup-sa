# 3D Generation with LLMs

Compare how well frontier LLMs generate 3D models, side by side, on the same prompt.

**The problem.** Frontier LLMs improving at 3D generation, but they can differ in quality, speed and cost. This project runs several LLM models via Amazon Bedrock on the same prompt and puts their results next to each other, so you can see when to use which model. It is useful for Physical AI teams that need to generate 3D objects to build scenes for their simulation worlds, and for anyone choosing a model for 3D content.

**How it works.** Type a description of an object, and four LLMs each turn it into a 3D model in parallel. Every model writes a Blender Python script on Amazon Bedrock AgentCore Runtime. Headless Blender runs the script and exports a GLB file. If a script fails, the model sees the error and tries again, up to 3 attempts.

**What you get after deploying.** A public web page, served by CloudFront, with three pages:

- **Generate:** four 3D previews side by side that you can rotate and zoom, and download as GLB. Each model's card shows:
  - **Cost and speed:** time taken, total tokens, token cost in USD and the number of attempts. The fastest and cheapest models are tagged.
  - **Mesh quality**, measured in Blender: whether the model is watertight, plus triangle, vertex and part counts. It also counts holes, flipped faces, loose vertices, zero-area faces and duplicate vertices.
- **Gallery:** every past generation with its metrics, so you can compare models across many prompts.
- **How it Works:** the architecture of the app.

| Layer | Tech |
|---|---|
| Frontend | React + Vite, `@google/model-viewer` for GLB previews, on S3 + CloudFront |
| Access | Public, no sign-in. CloudFront is the only entry point (AWS WAF on CloudFront and on the API) |
| API | API Gateway (REST) + AWS Lambda (Python 3.14, arm64), with a daily generation cap |
| 3D Generation | Amazon Bedrock AgentCore Runtime (arm64 container with Blender 4.0) |
| Models | Amazon Bedrock eg. GPT-6 Astra, Claude Opus 5.5, etc |
| Data | DynamoDB (jobs and metrics), S3 (GLB files and scripts, 30-day expiry) |
| Infrastructure | AWS CDK (TypeScript) |

## Models

Defined in [`config/models.json`](config/models.json), with prices per 1M tokens (us-east-1 on-demand, from the AWS Price List API on 2026-11-11):

| Model | Bedrock ID | Endpoint | Input | Output |
|---|---|---|---|---|
| GPT-6 Astra | `us.openai.gpt-6-astra` | Converse | $11.00 | $55.00 |
| GPT-6.1 Sol | `us.openai.gpt-6.1-sol` | Converse | $2.20 | $11.00 |
| Claude Opus 5.5 (default) | `us.anthropic.claude-opus-5-5` | Converse | $4.40 | $22.00 |
| ↳ or Claude Sonnet 5.5 | `us.anthropic.claude-sonnet-5-5` | Converse | $2.20 | $11.00 |
| ↳ or Claude Haiku 5.5 | `us.anthropic.claude-haiku-5-5` | Converse | $0.11 | $0.55 |
| GPT-6 Luna | `us.openai.gpt-6-luna` | Converse | $0.11 | $0.55 |

**Choosing the Claude model.** On the Generate page the Claude card's title is a dropdown: Claude Opus 5.5 (default), Claude Sonnet 5.5 or Claude Haiku 5.5. It comes from the Opus entry's `options` list in `config/models.json`; any slot can have one. The page sends the pick as `"models": {"claude-opus-5-5": "claude-haiku-5-5"}` in `POST /api/generations`, and the API only accepts a slot's own default or one of its options.

To compare different models, edit `config/models.json` and redeploy. IAM permissions follow the file. Move a replaced model to `retired` rather than deleting it, so past generations keep their labels. The agent still supports `"api": "mantle"` (bedrock-mantle Chat Completions with SigV4) for models offered only there.

## Architecture

![Reference architecture](docs/architecture.png)

Source: `docs/diagram/architecture.html` (re-render with the aws-reference-architecture-diagram skill). The web page shows the same diagram at the bottom.

## Security

- **Public, no sign-in.** Anyone with the URL can generate models and see the shared history. To restrict viewers, add a login eg. Cognito to only allow sign-in users.
- **CloudFront is the only way in.** The site and artifacts buckets are private and read only through CloudFront Origin Access Control. CloudFront adds a secret `X-Origin-Verify` header (Secrets Manager) to `/api/*`, and a regional AWS WAF on the API stage blocks every request without it, so calls straight to the `execute-api` URL get `403`.
- **Cost limits.** At most 100 generations per UTC day (`DAILY_GENERATION_LIMIT` in `infra/lib/stack.ts`); after that the API returns `429`. Each generation runs four paid models (about $0.25-0.30 in tests).
- **Model-written code is untrusted.** AI-generated Blender code run in isolated secure Agentcore Runtime environments. Before it runs, an AST check allows only `bpy`, `bmesh`, `mathutils` and maths imports, and blocks `open`, `eval`/`exec`, dunder attributes and Blender's file and script operators. Blender then runs as the unprivileged `sandbox` user with an empty environment (no AWS credentials) and a time limit. The runtime role can only invoke the configured models; S3 writes are done by the worker, not the runtime.
- WAF on CloudFront: WAF rules in place eg. Amazon IP reputation, common rule set, known bad inputs, 10 generation POSTs limits per IP per 5 minutes, 2,000 requests per IP per 5 minutes. Optional IP allowlist: `-c allowedCidrs=1.2.3.0/24,5.6.7.8/32`.
- Private S3 buckets (Block Public Access, TLS only, SSE-S3, no CORS).

## Deploy

Prerequisites: Node.js 22, Docker with arm64 emulation (QEMU/binfmt) on x86 hosts, `uv` (for the Python tests), and an AWS profile for the target account. The account must be CDK-bootstrapped in us-east-1 and have access to the four models.

```bash
make install
make test

make deploy
```

Then open `SiteUrl`.

### Remove

`make destroy`.

## Project layout

```
agent/          AgentCore Runtime container: app.py (agent loop), llm.py (Converse + mantle),
                blender_runner.py, blender_harness.py (runs inside Blender), script_guard.py
backend/api     REST API Lambda
backend/worker  Per-model worker Lambda
frontend/       React app
infra/          CDK stack and tests
config/         models.json (models and prices)
```
