---
title: "[Optional] Talk to Your Data — Voice"
weight: 78
---

## Learning Objectives

By the end of this optional step, you will:
- Deploy a **voice** front-end for the same analytics agent — speak a question, hear the answer
- Understand how a second AgentCore Runtime (a Pipecat pipeline) reuses the agent you already built
- See an AWS-native real-time voice stack: WebRTC with **Amazon Kinesis Video Streams (KVS) managed TURN** — no third-party media vendor

## Why Voice?

Your analytics assistant answers typed questions. But a depot manager walking the floor, or a staffer with their hands full, would rather just **ask** — "what's my revenue this week?" — and hear the answer. Voice removes the keyboard.

The key idea: **you don't rebuild the agent.** Voice is a new *front-end* that calls the exact same Strands analytics agent you deployed in Step 2, with the same per-user JWT — so RBAC, RLS, the SOP, and the conversation memory all work identically. A spoken question and a typed question reach the same Runtime.

::alert[**This lab is optional and a separate stack.** Unlike Steps 4–8 (which uncomment sections of the one analytics top-up), voice is its own CloudFormation stack: a **second AgentCore Runtime** running a :link[Pipecat]{href="https://www.pipecat.ai/" external=true} pipeline (Deepgram speech-to-text → your analytics agent → Deepgram text-to-speech) plus a tiny JWT-gated WebRTC signaling proxy. It reuses the analytics Runtime you already built; it does not replace it.]{type="info"}

## Architecture

```
Browser  ──WebRTC signaling (SDP/ICE) over HTTPS, Authorization: Bearer <Cognito token>──►
         JWT signaling proxy ─► Voice AgentCore Runtime  (Pipecat: STT → agent → TTS)   [this stack]
              │  media: browser ↔ KVS managed TURN relay ↔ runtime (UDP)
              │  the pipeline's "LLM stage" calls ↓ over HTTPS, forwarding the user's JWT
              ▼
         Analytics AgentCore Runtime  (the Strands agent from Step 2 — unchanged)
```

- The browser's microphone audio rides **WebRTC**; the media relay is **Amazon KVS managed TURN** (AWS-native — no Daily, Twilio, or other SFU vendor). The runtime fetches the TURN credentials itself, so the browser never holds AWS keys.
- Signaling (the initial SDP offer / ICE exchange) is **JWT-authorized** by the same Cognito client as the rest of the app — the voice runtime only talks to a signed-in user.
- The voice pipeline forwards that **same JWT** when it calls the analytics agent, so tenant isolation and role-based access are identical to the text path.

## Lab Procedures

### Step V.1: Get a Deepgram API key

The voice pipeline uses :link[Deepgram]{href="https://deepgram.com/" external=true} for speech-to-text and text-to-speech. Sign up for a free key (the free tier is ample for this lab) and copy it.

::alert[Deepgram is the one third-party dependency in this optional lab, and only for STT/TTS — the media transport and the agent are entirely AWS-native. You could swap in another provider supported by Pipecat.]{type="info"}

### Step V.2: Deploy the voice stack

From the agent folder, run `make voice-deploy` with your Deepgram key. This zips and uploads the voice bot code, then deploys `voice-agentcore-stack.yaml`:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make voice-deploy DEEPGRAM_API_KEY=<your-deepgram-key>
```

`make voice-deploy` automatically:
- reads the **analytics** Runtime ARN from your top-up stack's outputs (so the voice pipeline knows which agent to call),
- pulls the VPC, subnets, and Cognito ids from `config.env`,
- builds the voice container image (the first deploy runs the CodeBuild build — voice pulls heavier native deps, so allow ~5–10 minutes), and
- stands up the voice Runtime, the KVS signaling, and the JWT signaling proxy.

::alert[**Why a separate `make voice-deploy` (not `make deploy`)?** The voice runtime is an independent stack with its own image and lifecycle. Keeping it separate means the optional voice lab never affects the analytics stack you built in Steps 2–8, and you can tear it down on its own.]{type="info"}

### Step V.3: Point the UI at the signaling proxy

When the deploy finishes, grab the signaling URL:

```bash
aws cloudformation describe-stacks --stack-name agentic-analytics-voice --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='VoiceSignalingUrl'].OutputValue" --output text
```

Open :code[/workshop/agentic-analytics/app/ui/public/config.js]{showCopyAction=true} and set the URL so the UI shows the voice button:

::::expand{header="💡 What config.js should look like"}
:::code{language=javascript showCopyAction=true}
window.__APP_CONFIG__ = { VOICE_SIGNALING_URL: "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com" };
:::
(Use the exact `VoiceSignalingUrl` value from the command above.)
::::

`config.js` is served statically, so just **reload** the chat UI tab — no rebuild. A microphone / voice button now appears.

::::expand{header="Using the Amplify-hosted UI instead? (if you did the optional Step 3.6)"}
If you deployed the UI to **AWS Amplify** in [Step 3.6](../01-agent-and-infrastructure/03-connect-ui/) and have been using the `https://main.xxxxxxxxxxxxxx.amplifyapp.com` URL, editing the local `config.js` won't affect it — the Amplify build has its own bundled config. The voice client reads the same value from a **build-time** variable (`REACT_APP_VOICE_SIGNALING_URL`), so you just rebuild + redeploy the Amplify UI with that variable set.

From the agent folder, write an env file with the **full** set of build variables (the same ones Step 3.6 used **plus** the voice URL) and re-run the same Amplify deploy script. Pass the complete set — the build bakes exactly what's in this file into the bundle, so the agent runtime ARN and Cognito values must be present alongside the new voice URL, or the redeployed UI would lose its agent config:

:::code{language=bash showCopyAction=true}
cd /workshop/agentic-analytics/app/agentcore_strands

REGION="us-east-1"
AG_CONFIG="config.env"
STACK="agentic-analytics-agentcore"   # the top-up stack from Step 2

AGENT_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)
IDENTITY_POOL_ID=$(aws cloudformation describe-stacks --stack-name main-stack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text)
VOICE_URL=$(aws cloudformation describe-stacks --stack-name agentic-analytics-voice --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='VoiceSignalingUrl'].OutputValue" --output text)

cat > /tmp/amplify.env << EOF
REACT_APP_AWS_REGION=$REGION
REACT_APP_AGENT_RUNTIME_ARN=$AGENT_ARN
REACT_APP_COGNITO_USER_POOL_ID=$(grep COGNITO_USER_POOL_ID $AG_CONFIG | cut -d= -f2)
REACT_APP_COGNITO_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
REACT_APP_COGNITO_USER_CLIENT_ID=$(grep COGNITO_USER_LOGIN_CLIENT_ID $AG_CONFIG | cut -d= -f2)
REACT_APP_COGNITO_DOMAIN=$(grep COGNITO_DOMAIN $AG_CONFIG | cut -d= -f2)
REACT_APP_VOICE_SIGNALING_URL=$VOICE_URL
EOF

cat /tmp/amplify.env   # sanity check: all 7 lines populated (no empty values)
python3 ui/deploy_amplify_hosting.py --env-file /tmp/amplify.env
:::

The script rebuilds the React app with `REACT_APP_VOICE_SIGNALING_URL` (and the agent/Cognito config) baked in and redeploys to the **same** Amplify app, so your existing Amplify URL now shows the voice button. Open that URL (not localhost) and reload. Everything else in this step works identically — voice is UI-host-agnostic.

::alert[**CORS note:** the signaling proxy ships with `AllowedOrigin: '*'`, so it accepts requests from the Amplify origin out of the box. If you (or a later hardening step) restrict `AllowedOrigin`, set it to your Amplify URL and `make voice-deploy` again.]{type="info"}
::::

### Step V.4: Talk to your data

1. Make sure you're logged in (voice uses your JWT, same as text).
2. Click the voice button and allow microphone access when the browser asks.
3. Ask out loud: **"Who are my top three customers this month?"**

The agent answers **by voice**, and the on-screen panel shows the same written answer (and chart, if the question calls for one). Because voice and text share one AgentCore Memory thread, you can follow up by **typing** "and what about last month?" — and it remembers the spoken question.

::alert[**Same agent, same security.** The spoken question went: browser → WebRTC/KVS → voice Runtime → (your JWT) → the **analytics Runtime from Step 2** → Gateway → tools → Aurora with RLS. Every security layer you built applies to voice unchanged.]{type="info"}

### Step V.5 (optional): Change the voice

The text-to-speech voice is a parameter. Redeploy with a different Deepgram voice id:

```bash
make voice-deploy DEEPGRAM_API_KEY=<your-key> DEEPGRAM_VOICE_ID=aura-2-thalia-en
```

## Verification

- `make voice-deploy` finishes; `agentic-analytics-voice` reaches `CREATE_COMPLETE`
- The signaling URL is wired into the UI (local: `app/ui/public/config.js`; Amplify: `REACT_APP_VOICE_SIGNALING_URL` rebuilt via `deploy_amplify_hosting.py`) and the voice button appears after a reload
- A spoken question returns spoken audio plus the on-screen answer
- A typed follow-up remembers the spoken turn (shared memory)

## Troubleshooting

**No voice button after editing config.js**
- Hard-reload the UI tab (the browser may cache `config.js`). Confirm `VOICE_SIGNALING_URL` is a full `https://...execute-api...` URL.

**Voice connects but no audio / long initial pause**
- The voice microVM cold-starts on the first call (a few seconds). Try once more after it warms up.
- Confirm the deploy reached `CREATE_COMPLETE` and the voice Runtime is `READY`.

**`make voice-deploy` fails reading the analytics ARN**
- Deploy the analytics top-up first (`make deploy` / `make outputs` should show `AgentRuntimeArn`). The voice stack reuses that runtime.

**Build times out**
- The voice image has heavier native dependencies (aiortc etc.). The stack allows a longer CodeBuild timeout; if it still times out, re-run `make voice-deploy` — the layers cache.

## Summary

You added a voice front-end to the same analytics agent — a second AgentCore Runtime running a Pipecat pipeline over AWS-native WebRTC + KVS TURN, calling the Strands agent you built with the same JWT. Text and voice now share one agent, one security model, and one memory thread.

Next → [Summary & Next Steps](../summary/)

## Reference Materials

- :link[Amazon Bedrock AgentCore Runtime]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html"}
- :link[Pipecat — open-source voice AI]{href="https://www.pipecat.ai/" external=true}
- :link[Amazon Kinesis Video Streams — WebRTC]{href="https://docs.aws.amazon.com/kinesisvideostreams-webrtc-dg/latest/devguide/what-is-kvswebrtc.html"}
- :link[Deepgram — STT & TTS]{href="https://developers.deepgram.com/" external=true}
