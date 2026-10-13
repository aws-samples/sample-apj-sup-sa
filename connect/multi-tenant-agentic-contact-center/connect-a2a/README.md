# Hand a chat to an external AI agent (A2A) — opt-in

Amazon Connect **agent-to-agent (A2A) collaboration** lets a Connect AI agent hand a live
conversation to a **third-party AI agent** over a WebSocket, with no PSTN transfer. This module
shows both sides on chat:

- **Customer side** (what any Connect customer configures for a partner agent): API key storage
  Connect can read, the `A2A_SERVER` application, the security-profile allow-list, an orchestrator
  AI agent with an immediate handoff, and a flow that routes the partner's outcome.
- **Partner side**: a mock "**Level3**" agent that implements the Connect A2A protocol contract
  (text session) on API Gateway WebSocket + Lambda, so the demo is self-contained.

It is additive. It has its own stack, Lex bot, security profile, prompt, AI agent and chat flow. The
self-service AI agent, its bot, the existing flows and the merchant chat wiring are not changed.

## Deploy

Prereqs: `AnyCompanyPayConnectStack` and `AnyCompanyPayLexStack` (for the Q in Connect assistant)
in the same account and Region. The instance must be on the **Connect Customer** tier (A2A is not
available on Customer Basic).

```
cd connect-a2a
bash deploy.sh
```

`deploy.sh` runs three steps, and is safe to re-run:

| Step | What | How |
|---|---|---|
| 1 | KMS key (`connect.amazonaws.com` may decrypt), API-key secret (resource policy for Connect), mock partner endpoint, Lex bot | CDK (`AnyCompanyPayConnectA2aStack`) |
| 2 | `A2A_SERVER` application (`AccessUrl` = the `wss://` endpoint, `API_KEY` auth), instance association, `anycompany-pay-a2a-agent` security profile (application allowed as `THIRD_PARTY`), prompt + `anycompany-pay-a2a-orchestrator` AI agent, a published version with the profile on the agent **and** the version | `provision-a2a.sh` (API-only: CloudFormation does not support `A2A_SERVER` applications yet) |
| 3 | The `anycompany-pay-chat-a2a` flow, pointed at that AI agent version | CDK (`-c a2aAgentArn`) |

## Try it

In the Connect admin website open **Test chat**, choose the flow **anycompany-pay-chat-a2a**, and
chat. The mock partner is rule-based so results are predictable:

| You type | Level3 (mock) does | Then |
|---|---|---|
| a question, e.g. "What is the payout time?" | answers; the conversation continues | — |
| "agent", "human", "person"… | writes `l3_intent`, `l3_summary`, `l3_outcome`, `l3_escalation_reason` to the contact, then `FINISH ESCALATE` | "Connecting you to a support agent…" → support queue; the agent sees the `l3_*` attributes |
| "bye", "no thanks"… | `FINISH COMPLETE` | "Thanks for chatting with us." → end |
| "simulate failure" | `TASK_STATE_FAILED` (structured error) | fallback → support queue |

## How it works

```
Customer chat ──► Connect flow ──► Lex bot (AMAZON.QInConnectIntent)
                    │                 session attr x-amz-lex:q-in-connect:ai-agent-arn
                    │                 = anycompany-pay-a2a-orchestrator:<version>
                    │                        │ immediate handoff (handoffAgentConfiguration,
                    │                        │ audioStreamingEnabled=false)
                    │                        ▼
                    │        wss://…/a2a  (Authorization: Bearer <API key from Secrets Manager>)
                    │        Level3 mock: INIT_SESSION → CHANNEL_STATE READY → text turns →
                    │                     TEXT_RESPONSE_CHUNK + TRACING_SPAN → FINISH
                    ▼
   Tool = Escalate / l3_outcome = ESCALATE  → support queue
   Tool = Complete / l3_outcome = COMPLETE  → thanks, end
   anything else (partner failed, no outcome) → support queue
```

What the mock partner implements (A2A protocol contract, Connect A2A extension v1, text session):

- **Auth and extension**: `Bearer` key checked by a `$connect` authorizer; the `A2A-Extensions`
  header is echoed on the upgrade.
- **Session**: `INIT_SESSION` → `INIT_SESSION_RESPONSE` (200). On chat, Connect opens a new
  connection for every customer message with the same `contextId` and re-sends the history, so
  session state is kept in DynamoDB.
- **Turns**: `task` (working) → `TEXT_RESPONSE_CHUNK` → `TRACING_SPAN` → terminal `statusUpdate`,
  with a `FINISH` part when the conversation ends. Only finish types listed in
  `supportedFinishTypes` are sent.
- **Tracing**: one OTLP/JSON `invoke_agent` span per turn, with the turn's input and output as
  `aws.connect.span.message` events. It adopts the trace id and parent span from the `traceparent`
  upgrade header when present, otherwise it uses its own trace id. Connect requires this trace data
  and disables endpoints that don't send it. The spans show up in the Q in Connect session
  (`aws qconnect list-spans`). Observed on chat: Connect sent `A2A-Extensions` but **no
  `traceparent`**, and the spans were still accepted.
- **Also handled**: `DTMF_INPUT_COMPLETE` (used as the turn text), `FRAGMENT` reassembly, and
  informational `ERROR` / `INTERRUPTION` events (logged).

The orchestrator has **Escalate / Complete** return-to-control tools. When the partner finishes,
Connect hands control back to the orchestrator, which calls the matching tool, and the flow reads it
as `$.Lex.SessionAttributes.Tool`.

> **Observed behaviour (chat with message streaming):** when the partner finishes on its
> **first** turn, the flow receives `Tool = Escalate / Complete` as documented. When it finishes on
> a **later** turn, Connect processes the `FINISH` (the session spans show `return_to_agent
> ESCALATE / COMPLETE`), but the flow's Lex block takes its **error** branch. The flow therefore
> also routes on the partner's own `l3_outcome` contact attribute. The design already has the
> partner write intent, summary and outcome to the contact, so this costs nothing extra. Worth
> raising with AWS before production.

## Production notes

- **Partner hosting**: a real partner hosts its own `wss://` endpoint. API Gateway WebSocket + Lambda
  is fine for chat, but **voice streaming** (`audioStreamingEnabled=true`, PCM audio, barge-in)
  needs a persistent, low-latency server such as a container. AWS asks you to validate latency and
  audio quality with the account team before production. Voice also needs a Lex bot with
  speech-to-speech enabled and immediate handoff.
- **Contact attributes**: here the mock writes contact attributes with its own role in the same
  account. A partner would use a cross-account role limited to `connect:UpdateContactAttributes` /
  `GetContactAttributes` on the customer's instance, with an external ID.
- **Data shared with the partner**: Connect passes the contact ARN, conversation history and system
  attributes (plus custom attributes if permitted). Selective attribute sharing is not available
  yet, so keep sensitive data out of custom attributes before the handoff.
- **Key rotation**: rotate the API key in Secrets Manager. The authorizer re-reads it every 5 minutes.

## Remove

The full `cleanup.sh` deletes this stack first. Its AI agent, prompt and AppIntegrations
application are removed with the other Q in Connect and AppIntegrations objects, and the security
profile goes with the instance.
