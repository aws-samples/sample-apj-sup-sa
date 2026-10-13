# Connect routing module (opt-in): case-owner replies + after-hours backlog

Implements *Amazon Connect: Case-Owner Reply Routing & After-Hours (OOH) Backlog* (design v0.1)
for this sample's **chat + task** channels. It is **additive**: new hours, queues, routing
profiles, Cases fields, two Lambdas and two **new** flows. The existing
`anycompany-pay-chat-inbound` / `anycompany-pay-chat-case` flows, support queue and routing
profile are not modified.

| Pattern | What happens |
|---|---|
| **A — case-owner reply routing** | A merchant chat on an **open case** (the "Chat about this case" button, `case_id` stamped server-side by `ChatApiFn`) is linked to the case and offered to the **case owner** (`assigned_user`) first: *Set routing criteria* step 1 = preferred agent with a 60 s expiry, then the longest-available agent in the tier queue. Contact priority **1**, ahead of new contacts (priority by tier). |
| **B — after-hours backlog** | The AI assistant answers **24/7**; only human agents follow business hours. When a merchant asks for a human out of hours (Escalate), the chat asks **"Our support agents are offline right now (Mon–Fri 08:00–18:00). Would you like me to log this as a support case…?"** with **Yes, log a case / No, thanks** buttons (read by a small Lex bot, `anycompany-pay-log-case`). **No** logs nothing and hands back to the AI assistant. **Yes** acknowledges ("logged your request as support case … an agent will follow up when we open (…)") and becomes a **new "After-hours request — <merchant>" case** — old unrelated open cases are never reused; a repeat request the same night joins tonight's case while its follow-up is still pending, and a chat started from a case uses that case — plus **one scheduled task** for the next opening (`GetEffectiveHoursOfOperations`, overrides applied, 6-day cap with a re-schedule loop). At opening the task lands in `ooh-followup` with tier priority (VIP 1, key 2, shared 5). |

New issues — and case chats out of hours — go through **agentic self-service** (Lex + Q in Connect,
24/7) when `AnyCompanyPayLexStack` exists. An *Escalate* is hours-gated: in hours it lands in the
merchant's tier queue; out of hours it becomes the case + follow-up task above. In hours, case chats
go straight to the case owner (Pattern A). Without the agentic bot, chats go straight to the
hours-gated human path.

## Deploy

```bash
cd connect-routing
bash deploy.sh                 # routing stack + switch ChatApiFn to the routed flow
bash provision-tiers.sh        # tier on Customer Profiles (Luxe=VIP, Nova=key, rest shared)
bash provision-agents.sh       # agent-live1/2 (RP-Live), agent-ooh1 (RP-OOH-Backlog)
```

| Option | Effect |
|---|---|
| `DEMO_MODE=1 bash deploy.sh` | The inbound flow checks an always-closed hours object (forces Pattern B during the day); tasks are scheduled ~2 min out against always-open hours. |
| `ALERT_EMAIL=you@x bash deploy.sh` | Subscribe to OOH scheduling-failure alerts (SNS). |
| `EXTRA_CTX="-c csTimeZone=Asia/Jakarta -c csOpen=08:00 -c csClose=17:00"` | Business hours (default Asia/Singapore, Mon–Fri 08:00–18:00). Also: `csDays`, `ownerPresenceCheck=false`, `ownerExpiryChatSeconds`, `liveOohDelaySeconds`, `openOffsetSeconds`, `tierDefaults`. |
| `UNWIRE=1 bash deploy.sh` | **Rollback**: merchant chats use the original flows again (routing stack stays). |

`deploy-lex.sh` keeps the routing flow wired when it redeploys the connect stack. Teardown:
`cleanup.sh` removes `AnyCompanyPayConnectRoutingStack` first.

## Validation

Verified on the live sample instance (us-west-2) on 2026-10-07 — API-level checks, a real browser
(merchant via CloudFront + Cognito Hosted UI, agents in the Connect CCP), and unit tests of the
scheduling rules. The test harness is kept outside this repo.

Things worth knowing when testing yourself:

- A chat's flow only starts once the **customer participant's WebSocket is connected** (ChatJS does
  this); a bare `StartChatContact` sits idle.
- Cases stores `assigned_user` as a **`stringValue` holding the Connect user ARN**.
- Pattern A needs **CS hours open**; outside them every chat takes the after-hours path (by design).
  `DEMO_MODE=1` forces Pattern B during the day; tasks then start ~2 min later.

### Coverage of the design's test catalogue

| Tests | Result |
|---|---|
| A-01, A-02, A-05, A-06, A-07, A-08, A-09 | Pass. Owner Available → offered to the owner while another Available agent is not; owner offline → skipped (presence check) or held until the 60 s step EXPIRES (`ownerPresenceCheck=false`); priority 1 reply offered before an older priority 5 contact. |
| A-03, A-04, A-12, A-13 | Not run (owner at capacity / custom status / RP without the queue / persistent chat). |
| A-11 | Not applicable: a case chat carries exactly one server-verified `case_id`. |
| A-10, A-14, B-16 | Not applicable (email channel out of scope; no SES identity on the instance). |
| B-01, B-02, B-03, B-08, B-09, B-12 | Pass. One case + one task per merchant; tasks offered VIP → key → shared (oldest was shared), one at a time; in hours no OOH case/task. |
| B-04, B-05, B-06, B-07 | Pass at the scheduling-rule level (weekend, holiday override, > 6-day cap + re-schedule, past-time guard). |
| B-10, B-11 | Not run (RP-Live delay; owner hand-off after an OOH task). |
| B-13, B-14 | Not built (Cases rules need event streams; optional in the design). |
| B-15, T-01..T-05 | Not built: no workload types; quick-connect transfer flows are platform behaviour, and *Change routing priority* is not supported in transfer flows. |

Tenant isolation was also re-verified across every layer: Cognito claims, Search/Cases/Chat APIs,
the AI tool, and the routing Lambdas (a forged cross-tenant `case_id` is never linked, owner-routed,
or given an OOH task).

## Design notes / deviations

- **`assigned_user` format (design §10.1):** Cases stores the owner as a `stringValue` holding the
  Connect **user ARN**; the Lambda normalises it to the user ID for the routing step.
- **Flow-language name:** the routing-criteria action is `UpdateContactRoutingCriteria`
  (the dev-guide page title `UpdateRoutingCriteria` is rejected by Connect).
- **Existing agents keep working:** when wired, the tier chat queues are also added to the existing
  `anycompany-pay-chat` routing profile (agent1 / admin), via `-c routingQueueArns`.
- **Encrypted profiles domain:** the routing Lambdas get `kms:Decrypt` on the Customer Profiles
  key (via `profile.<region>.amazonaws.com` only); `deploy.sh` discovers the key.

- **Lambdas instead of Cases flow blocks.** The OOH intake (find/create case, link, dedup,
  schedule task) runs in one Lambda (`OohSchedulerFn`) using the Cases + `StartTaskContact`
  APIs. This keeps the flow small, makes the dedup atomic-ish, and is directly testable.
- **Dedup is self-healing.** `ooh_task_pending=true` only blocks a new task while the task in
  `ooh_task_id` is still live (`DescribeContact`), so a forgotten flag never blocks follow-ups.
- **Priority is static per block** (flow-language rule), so the flows branch per tier.
- **Owner presence** (`GetCurrentUserData`): an offline owner is skipped (A-05) unless
  `ownerPresenceCheck=false`, which holds the reply for the owner until expiry (A-02).
- **Every agent who can own a case must have every tier queue** on the chat channel — both
  routing profiles include them (design §6.2 rule; test A-12).
