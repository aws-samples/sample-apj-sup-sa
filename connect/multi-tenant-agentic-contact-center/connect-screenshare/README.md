# Live screen sharing (opt-in)

A merchant starts a **web call** with support from a case and **shares their screen**; the agent
answers in the CCP (the app's Admin → Contact Center, or the Connect agent workspace) and sees the
shared screen live. Built on Amazon Connect's native in-app/web calling with screen sharing.

```
Merchant app (case) ─ "Share screen" ─▶ POST /screenshare/start (Cognito JWT)
   ScreenShareApiFn: tenant from the JWT, case ownership verified
   → StartWebRTCContact (Customer.ScreenShare = SEND) → { meeting, attendee }
Browser (Amazon Chime SDK): join the call with audio → "Share my screen" (content share)
Connect: anycompany-pay-screenshare-inbound flow → anycompany-pay-screenshare queue (VOICE)
Agent CCP (VideoContact.Access): accept the call → the shared screen opens in the
   CCP's screen-sharing session view
```

## How to use it

**Merchant** (Support → a case):
1. **Share screen** → **Start call**, and allow the microphone (it is a voice call).
2. "Connecting you to a support agent…" while the call waits in the queue.
3. When an agent answers: "You're connected to a support agent." and a **Share my screen** button.
4. **Share my screen** → the browser asks what to share (screen, window or tab).
5. **Stop sharing** (the call continues) or **Hang up**. The call reference is shown for support.

**Agent** (Admin → Contact Center, or the Connect agent workspace; Available):
1. The web call rings ("Luxe Living") → **Accept call**.
2. Talk as on a normal call.
3. When the merchant shares, the **Screen sharing session** view shows their screen live
   ("Start screen sharing session" is greyed out while a session is running).

## What it adds

| Resource | Purpose |
|---|---|
| `anycompany-pay-screenshare` queue (+ always-open hours) | Web calls with screen sharing, VOICE channel |
| `anycompany-pay-screenshare-inbound` flow | Greeting → queue → transfer |
| `anycompany-pay-video-agent` security profile | `VideoContact.Access` (+ `BasicAgentAccess`), added to agents on top of their existing profile |
| `ScreenShareApiFn` + HTTP API | `POST /screenshare/start`, `GET /screenshare/status/{contactId}` (JWT authorizer) |
| Runtime config `connect.screenShareApiUrl` | Turns on the **Share screen** button in the merchant app |

App changes (in the main SPA image): the **Share screen** button + session modal
(`src/components/screenshare/`, `src/connect/useScreenShare.ts`, Chime SDK loaded only when used),
the embedded CCP's `allowFramedVideoCall` / `allowFramedScreenSharing` / `allowFramedScreenSharingPopUp`
flags, and the CSP (`*.chime.aws`, `worker-src blob:`).

The connect stack adds the queue (VOICE) to the existing `anycompany-pay-chat` routing profile
(agent1 / admin) only when `-c screenShareQueueArn` is supplied; otherwise it is unchanged.

## Deploy

```bash
cd connect-screenshare
bash deploy.sh                 # stack + add the queue to anycompany-pay-chat (VOICE)
bash provision-video-agents.sh # VideoContact.Access for agent1, agent-live1/2, agent-ooh1
# then redeploy the app image so the merchant UI + CSP ship:
cd ../infra && npx cdk deploy AnyCompanyPayAppStack
```

| Option | Effect |
|---|---|
| `CUSTOMER_VIDEO=1 bash deploy.sh` | Merchants may also send camera video |
| `UNWIRE=1 bash deploy.sh` | Remove the queue from `anycompany-pay-chat` (stack stays) |
| `REMOVE=1 bash provision-video-agents.sh` | Take the video profile away again |

Every redeploy of `AnyCompanyPayConnectStack` goes through `infra/deploy-connect-stack.sh`, which keeps
all opt-in modules (agentic, routing, screen share) in their current state.

## Tenant isolation

- The contact's `merchant_id` / `merchant_name` / `email` come from the validated JWT; `case_id` is
  accepted only after the case is verified to belong to the caller (else 403).
- Only the `merchant` group may start a session; `GET /screenshare/status/{id}` answers only for the
  caller's own contact (404 otherwise).
- The meeting join token returned to the browser is scoped to that one contact.

## Validation (live, 2026-10-08)

Browser end to end, 10/10 with the agent in **both** the app's embedded CCP and the Connect agent
workspace: the merchant (CloudFront + Cognito) starts the call from a case; the contact is a
`VOICE` / `connect:WebRTC` contact stamped with the merchant's tenant and the case; the merchant is told
"connected" only once an agent has really accepted; the merchant shares the screen; the agent accepts
and the shared screen renders as live video (1920×1080); hang-up is clean.

API: another tenant's case → 403; admin token → 403; merchant token without a tenant → 403; another
tenant's call status → 404 (own call → 200). The existing chat / routing suites still pass with the
screen-share queue on agent1's routing profile.

## Notes

- **Agent view:** the standalone `ccp-v2` page has no screen-share view; use the app's embedded CCP
  (flags above) or the Connect agent workspace. The session opens as soon as the merchant shares.
- **"Connected" comes from Connect, not the meeting:** Connect's own media participant is present in
  the meeting before any agent answers, so the merchant UI polls `GET /screenshare/status/{id}`
  (`DescribeContact` → `ConnectedToAgentTimestamp`).
- **Hours:** the queue is always open. Point it at business hours, or route it through the routing
  module's checks, if after-hours web calls should not ring agents.
- **Not built:** agent → merchant screen sharing, recording of the shared screen, and calls outside a case.
