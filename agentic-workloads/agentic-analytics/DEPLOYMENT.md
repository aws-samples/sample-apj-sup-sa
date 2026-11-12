# Deployment

Everything needed to deploy lives in the repo: the agent (`app/agentcore_strands/`),
the React UI (`app/ui/`), the voice bot (`app/voice/`), CFN + scripts
(`infrastructure/`), and data (`dataset/`, `common/`).

## Two layers

1. **Backend** (the analytics agent + dashboard), always deployed.
2. **Voice** (the Pipecat pipeline), **optional** and chosen by a CFN parameter.

## Deploy experience

### Step 1: package artifacts to S3 (local; needs aws-cli, npm, zip, pip3, NOT Docker)
```bash
cd infrastructure/scripts
aws s3 mb s3://<your-artifacts-bucket> --region us-west-2
./package_and_upload.sh <your-artifacts-bucket>
```
This builds the UI (from `app/ui/`), zips the agent code (`app/agentcore_strands/`)
and the voice bot context (`app/voice/`, hash-versioned), uploads templates +
Lambdas + data, and prints the exact `create-stack` command with all the resolved
S3 keys.

### Step 2: create the stack (CloudFormation builds the rest IN-CLOUD)
Run the printed command. Docker images are built by **CodeBuild inside the deploy**
(agent image always; voice bot image when voice is on), so you need no local Docker.

**Voice is a parameter.** The printed command deploys backend-only by default
(`EnableVoice=false`). To deploy **with voice**, add:

```
ParameterKey=EnableVoice,ParameterValue=true
ParameterKey=VoiceMode,ParameterValue=agentcore        # or pipecat-cloud
ParameterKey=DeepgramApiKey,ParameterValue=<key>
ParameterKey=DeepgramVoiceId,ParameterValue=aura-2-apollo-en
```

No demo credentials: hosted voice forwards each signed-in user's own Cognito token,
so RBAC/RLS is per-user (identical to the text path). No Daily key for agentcore mode
(WebRTC media relays via Amazon KVS managed TURN, with no 3rd-party SFU).

## The voice modes

| Mode | Where the Pipecat pipeline runs | How it's deployed |
|------|----------------------------------|-------------------|
| **laptop** | your laptop + UI on `localhost:3001` | `infrastructure/scripts/deploy_voice.sh laptop` (local dev; not a CFN value) |
| **agentcore** | its OWN AgentCore Runtime (WebRTC + KVS TURN) in your AWS account | **Fully CFN**: `EnableVoice=true VoiceMode=agentcore` → main stack deploys `voice-agentcore-stack.yaml` (CodeBuild builds the ARM64 image; the Pipecat pipeline runs as a second AgentCore Runtime in VPC mode in its own AgentCore-supported-AZ subnets attached to the Aurora NAT route table — see the AZ note below; a tiny API-Gateway+Cognito-JWT+Lambda signaling proxy translates the browser's SDP offer/ICE to the runtime and unwraps its SSE answer). One deploy. Fast iteration: `deploy_backend.sh --voice-only`. |
| **pipecat-cloud** | Daily's Pipecat Cloud (SaaS) | **CFN + post-deploy script**: deploy the main stack with `EnableVoice=true VoiceMode=pipecat-cloud` (this leaves the UI's voice URL empty — main CFN does NOT deploy PCC infra), then run `infrastructure/scripts/deploy_voice_pcc.sh`. |

### Why agentcore is one-step but pipecat-cloud needs a script
Pipecat Cloud is **external SaaS**. Its agent + secret set can't be modeled in
CloudFormation (no AWS resource types for them), so `infrastructure/scripts/deploy_voice_pcc.sh`
finishes the job after the main CFN deploy. It:
1. creates the **PCC secret set** (Deepgram/Daily keys on Pipecat's side, CLI-only);
2. deploys the **PCC agent** (`infrastructure/voice-pcc-cr/`, drives PCC's REST API);
3. deploys the **JWT start proxy** (`infrastructure/voice-proxy/`), needed because the
   browser can't call PCC's `/start` directly (the PCC key must stay server-side);
4. fills the proxy's **Secrets Manager placeholder** with the PCC key (the key
   never touches a CFN template/param);
5. points the UI's `VOICE_START_URL` at the proxy and **redeploys the UI** so the
   Voice button appears.

Required env for the script: `PCC_PAT`, `PCC_PUBLIC_KEY`, `DEEPGRAM_API_KEY`,
`DAILY_API_KEY`.

(agentcore needs no SaaS finisher: the pipeline runs as a second AgentCore Runtime
in your account, and the in-stack signaling proxy is plain CFN, so it is one deploy.)

When voice is off, the UI's `VOICE_SIGNALING_URL` is empty → the **Voice button is
hidden** (the dashboard is pure text chat).

## Voice off removes the voice client

`app/ui/src/services/voiceClient.js` has **no localhost fallback**. `VOICE_SIGNALING_URL`
is set only when voice is deployed (CFN injects it into `config.js`, or
`app/ui/.env.local` sets it for laptop dev). Absent → `voiceConfigured()` is false.

## Schema change pipeline

`deploy_backend.sh` packages `app/schema_sync/` and nests `infrastructure/stacks/schema-sync-stack.yaml`
(`SchemaSyncStack`) in demo mode. The stack output `SchemaSyncStateMachineArn` is the
`<env>-schema-sync` Step Functions state machine.

- **When it runs:** after every successful Glue crawler run (EventBridge rule), once a day at
  18:00 UTC, or when you start an execution yourself. The first run publishes the whole schema.
- **What it does:** snapshot the Postgres catalog, diff it with `schema/current.json` in the
  artifacts bucket, classify the change (none, additive, breaking, blocked), describe new or
  changed objects with the agent's model, then write Glue table descriptions and one KB card
  per table or view (`docs/schema/<name>.md` in the KB docs bucket) and start a KB sync.
- **Approval:** a breaking change (rename, retype, drop) waits at `WaitForApproval`. The task
  token is in `s3://<artifacts>/schema/pending/<run_id>.json`; answer with
  `aws stepfunctions send-task-success --task-token <token> --task-output '{"approved":true}'`
  (or `send-task-failure` to reject). The workshop Makefile wraps this as `make approve` /
  `make reject`.
- **Blocked:** a table with an `account_id` column but no row-level security policy fails the run
  with `TenantTableWithoutRowLevelSecurity`; nothing is published until RLS is on.
- **Rebuild:** start an execution with input `{"rebuild": true}` to republish every card and
  description, for example after cards were edited or deleted by hand.
- **Model:** `DescribeModelId` defaults to `global.anthropic.claude-sonnet-5-5`. Workshop
  Studio sandbox accounts deny Haiku 5.5 through the private marketplace.

## Operational notes

- **CodeBuild builds all images in-cloud.** Custom-resource Lambdas trigger the
  builds and block dependent resources until images are in ECR. ECR repos use
  `EmptyOnDelete: true` so the image repos delete cleanly.
- **agentcore voice runs as a second AgentCore Runtime in VPC mode**
  (`voice-agentcore-stack.yaml`). VPC NetworkMode is required for UDP TURN. The stack
  creates **its own two subnets pinned by `AvailabilityZoneId` to AgentCore-supported
  AZs** and attaches them to the **Aurora VPC's private route table** (so the runtime
  ENIs reach Deepgram/Bedrock/KVS/STS via the existing NAT, with no new VPC or second NAT).
  It does **not** reuse the Aurora private subnets directly: AgentCore Runtime only
  operates in a subset of AZs (us-east-1: `use1-az1`/`use1-az2`/`use1-az4`, fixed by
  AZ-ID), and `PrivateSubnet1` lands in `us-east-1a` = `use1-az6` in many accounts,
  which would fail Runtime creation with *"subnets are in unsupported availability
  zones."* The supported AZ-IDs are overridable params (`VoiceSubnet1AzId`/
  `VoiceSubnet2AzId`); change them for other regions. WebRTC
  media relays through **Amazon Kinesis Video Streams (KVS) managed TURN** (one signaling
  channel; see [KVS pricing](https://aws.amazon.com/kinesis/video-streams/pricing/) for
  the per-channel and TURN rates in your region). The runtime fetches TURN creds for itself, and the browser
  fetches the **same** creds from the signaling proxy's `GET /api/ice` (JWT-gated) so
  it can gather relay candidates. Without this the browser only has host candidates
  and ICE can't traverse NAT to the VPC-only runtime. No Daily, no ALB, no VPC Link.
- **Signaling proxy** (in the same stack): API Gateway HTTP API + Cognito JWT
  authorizer + a stdlib-only Lambda. It is authenticated at the edge (NOT a public
  Function URL): the browser POSTs its SDP offer (PATCHes ICE) with its Cognito Bearer
  token; the Lambda forwards that same Bearer to the JWT-only voice runtime
  `/invocations` and unwraps the SSE answer into the JSON the SmallWebRTC transport
  expects. It also serves `GET /api/ice` (the KVS TURN servers). Only signaling flows
  through it, not media.
- **Teardown of agentcore voice is NOT instant.** Deleting the voice Runtime leaves
  AWS-managed VPC ENIs (`interfaceType: agentic_ai`) attached to the runtime security
  group; AWS reclaims them asynchronously (~20–60 min), and the SG (hence a
  `delete-stack` or a `VoiceMode` switch away from agentcore) blocks until then. This
  is expected: CloudFormation retries and completes once the ENIs clear; you cannot
  detach `ela-attach` ENIs manually.
- **Cost:** agentcore voice is microVM-per-session (no always-on task/ALB); it
  cold-starts on the first connection (about 5-10 s in our us-east-1 tests, October
  2026). pipecat-cloud scales to zero (`min_agents=0`) and also cold-starts (about 10 s
  in the same tests). Expect different times in other regions.
