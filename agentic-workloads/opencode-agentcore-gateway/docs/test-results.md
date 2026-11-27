# Test results

This page records the latest result of every test and scan. It is updated after each review round; the scenario
matrix itself is in [testing.md](testing.md).

## Unit tests

`uv run --no-project --with-requirements tests/requirements.txt python -m pytest -q tests/unit`

| Date | Commit | Result |
|---|---|---|
| 24 Nov 2026 | r197 | 5,300 passed, 0 failed |
| 24 Nov 2026 | r196 | 5,296 passed, 0 failed |
| 24 Nov 2026 | r195 | 5,280 passed, 0 failed |
| 24 Nov 2026 | r194 | 5,276 passed, 0 failed |
| 16 Nov 2026 | r171 fixes | 5,237 passed, 0 failed (also with no AWS credentials) |

## End-to-end matrix

`python3 tests/e2e/run.py --stack terraform` against a deployed stack, as real users of each identity provider.

| Date | Commit (reruns) | Scenarios | Subfeatures | Pass | Fail | No driver |
|---|---|---|---|---|---|---|
| 24 Nov 2026 (cognito) | r196 (no reruns; 1 retried in the run) | 368 of 1,048 | 129 of 129 | 295 | 0 | 20 |
| 24 Nov 2026 (cognito) | r195 (no reruns; 1 retried in the run) | 368 of 1,048 | 129 of 129 | 295 | 0 | 20 |
| 24 Nov 2026 (cognito) | r193 (no reruns; 2 retried in the run) | 367 of 1,047 | 128 of 128 | 293 | 1 | 20 |
| 18 Nov 2026 (cognito) | r191 (no reruns) | 367 of 1,047 | 128 of 128 | 294 | 0 | 20 |
| 16 Nov 2026 (cognito) | r187 (no reruns) | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r182, reruns on r183 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (jumpcloud) | r178 (reruns on r178) | 340 of 1,047 | 103 of 128 | 274 | 0 | 14 |
| 16 Nov 2026 (okta) | r178 | 340 of 1,047 | 103 of 128 | 274 | 0 | 14 |
| 16 Nov 2026 (cognito) | r178, reruns on r179 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r177, reruns on r178 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r176, reruns on r177 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r175, reruns on r176 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r174, reruns on r175 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r173, reruns on r174 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r172, reruns on r173 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r171, reruns on r172 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r170, reruns on r171 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 16 Nov 2026 (cognito) | r169, reruns on r170 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r168, reruns on r169 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r167, reruns on r168 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r166, reruns on r167 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r165, reruns on r166 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r164, reruns on r165 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r163, reruns on r164 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r162, reruns on r163 | 367 of 1,047 | 128 of 128 | 306 | 1 | 7 |
| 15 Nov 2026 (cognito) | r161, reruns on r162 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r160, reruns on r161 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r159, reruns on r160 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r158, reruns on r159 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r157, reruns on r158 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r156, reruns on r157 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r155, reruns on r156 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 15 Nov 2026 (cognito) | r154, reruns on r155 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r152, reruns on r153 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r151, reruns on r152 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r150, reruns on r151 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r149, reruns on r150 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r148, reruns on r149 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r147, reruns on r148 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r146, reruns on r147 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r145, reruns on r146 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r144, reruns on r145 | 367 of 1,047 | 128 of 128 | 307 | 0 | 7 |
| 14 Nov 2026 (cognito) | r143, reruns on r144 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r142, reruns on r143 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r141, reruns on r142 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r140, reruns on r141 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r139, reruns on r140 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r138, reruns on r139 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r137, reruns on r138 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r136, reruns on r137 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r135, reruns on r136 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r134, reruns on r135 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r133, reruns on r134 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r132, reruns on r133 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r131, reruns on r132 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r129, reruns on r130 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 14 Nov 2026 (cognito) | r128, reruns on r128 | 365 of 1,045 | 126 of 126 | 305 | 0 | 7 |
| 13 Nov 2026 (cognito) | b4c8cba | 365 of 1,045 | 126 of 126 | 296 | 1 | 16 |

r196 (on the r196 deploy): 295 pass, 0 fail, 0 error, 53 `n/a`, 20 without a driver, 1 passed on a retry (`F6.gateway_rpm-0798`). `F6.gateway_order` again measured `interceptor_before_rate_limit` with the same timing, so the data-plane caveat of r195 still applies.

r195 (on the r195 deploy): 295 pass, 0 fail, 0 error, 53 `n/a`, 20 without a driver, 1 passed on a retry. The new `F6.gateway_order` scenario measured `interceptor_before_rate_limit`: with carol's gateway requests limit at 0 (the first call 35 s after the update, then 20 s apart), all three calls for an unserved model got the interceptor's 404 and none the gateway's 429. This shows the interceptor answered while a 0 limit was set; it cannot fully rule out that the limit had not yet reached the data plane, since limits have taken longer than the documented 30 s before (see `drive_gateway_limit`).

r193 (on the r193 deploy): 293 pass, 1 fail, 0 error, 53 `n/a`, 20 without a driver, 2 passed on a retry. The one fail is `F12.plan_no_changes` (`terraform plan` exit code 2): the r194 code edits were made in the checkout while the run was in progress, so plan saw a change; it is to be re-run on the r194 deploy. All inference, fallback and web search scenarios pass, so the r193 `aws:SourceArn` trust conditions do not block AgentCore.

r191: 294 pass, 0 fail, 0 error, 53 `n/a`, no retries. In both runs the 20 without a driver are the 14 `F2.opencode.*` and `F2.task.*` scenarios (opencode is not installed on the runner), the six `F7.throttle`, `F7.5xx` and `F7.unreachable` cases (Mantle cannot be made to fail on demand) and `F12.destroy_clean` (the account owner's last step).

Okta and JumpCloud (16 Nov 2026, round 178): the stack was switched to each IdP in turn (`idp`, the OIDC settings and `identity_map` in an overlay over terraform.tfvars) and the matrix ran as test users signed in with `ocgw login` through the IdP's own sign-in page. Both passed 274 of their 340 scenarios with no failure. On JumpCloud, the `nogroup` web search scenarios and three `F2.client` token cases first errored because the runner had no signed-in home for them; they passed on the rerun once it did (and the token cases now expect the rotating refresh tokens Okta and JumpCloud issue). Okta passed in one run with no retry. Each set's 52 `n/a` are the `/v1/responses` route; its 14 without a driver are the seven `F1.groups` cases (they change a user's groups in the IdP, which needs its admin API), `F2.client.login_pkce` (the IdP's own sign-in page, done by hand for every test user), and the six upstream `F7` cases. The switch also found a defect, fixed in round 178: replacing the gateway's rate limit for a new user claim (Cognito `username` to `sub`) left the old limit in place, because Terraform ran no destroy step for the replaced instance; `ratelimit-ensure` now deletes this stack's limits on other keys, which the switch back to Cognito confirmed.

r187: no failures and no reruns. A first r187 run was stopped after it found every call as bob refused with a budget 429: his month's test spend had reached the $50 `default_budget_usd` (see testing.md); with the stack's default raised to 500 the run was started again and is the row above. Two scenarios passed on a retry, both of the kinds testing.md describes: `F6.gateway_rpm-0798` saw 429s in its first minute that did not name `requests` and got its own in the next, and `F2.task.multi_tool_edit` had a first session whose every reply was a 200 but whose model ended without editing; the fresh session did the edit.

r182: two failures. `F12.plan_no_changes` (round 183 Lambda code committed during the run, not yet applied) passed on round 183 after the apply. `F6.gateway_rpm-0798` sent two bursts of 7 at a limit of one a minute and got no 429; on round 183 the same scenario bit only in its third minute and the next one (limit 3, set right after) never bit in three, so an edit to the gateway's limit leaves it open for minutes, rule ACTIVE or not. `F6.gateway_rpm` now tests carol's standing limit as Terraform deploys it, with no edit; both scenarios passed with no retry on round 183, and `docs/dashboard.md` warns about the delay.

r178: two failures. `F12.plan_no_changes` (round 179 committed, not yet applied) passed after the apply. `F6.gateway_rpm-0798` sent 14 calls in two minutes 35 seconds after the gateway limit was lowered to one a minute and got no 429; the other two `F6.gateway_*` scenarios of the run were enforced, and on round 179 all four passed with no retry, so this is the gateway's documented fail-open while a change propagates, which ran longer than both minutes here. On round 179 (binary values rendered as base64 text, the runner restoring signed-in configs) all 39 `F11`, 24 `F3.kimi-k3.chat`, 27 `F5`, 9 `F6.rpm` and 4 `F6.gateway` scenarios passed with no retry.

r177: one failure, `F12.plan_no_changes` (round 178 committed, not yet applied); it passed after the apply. No retry.

r176: one failure, `F12.plan_no_changes` (round 177 changes three Lambdas, committed, not yet applied); it passed after the apply. `F6.gateway_rpm` passed after its documented repeat. On round 177 (rpm rounded where every reader cleans an item, the proxy's header checks before the sign-in refresh) all 39 `F11`, 24 `F3.kimi-k3.chat`, 27 `F5`, 9 `F6.rpm` and 23 `F2` scenarios passed with no retry.

r175: one failure, `F12.plan_no_changes` (round 176 changes three Lambdas, committed, not yet applied); it passed after the apply. `F6.gateway_tpm` passed after its documented repeat in the next minute. On round 176 (rpm rounding, the dashboard's group list, usage_sync's alias pass, the expired-session message) all 39 `F11`, 24 `F3.kimi-k3.chat`, 27 `F5`, 9 `F6.rpm` and 23 `F2` scenarios passed with no retry.

r174: no failure and no retry. On round 175 (the `ocgw` proxy forwards only an allow-list of request headers) all 23 `F2` scenarios, real opencode sessions included, and all 15 `F9` web search scenarios passed with no retry.

r173: no failure. `F2.task.multi_tool_edit` passed after its retry, in the full run and again on round 174: the recorder shows the same upstream reply both times, a 200 with `finish_reason: stop`, about 75 billed output tokens and no text or tool call (README Limitations). On round 174 (`ocgw` skips interim replies, drops headers its `Connection` header names and uses its own no-redirect opener) all 23 `F2` scenarios passed.

r172: no failure and no retry. On round 173 (the `ocgw` proxy refuses an interim reply and sends `Connection: close` when it closes) all 23 `F2` scenarios passed with no retry.

r171: no failure; `F12.plan_no_changes` passed (round 172 changes no infrastructure). `F3.kimi-k3.chat.once` passed after its retry: the first reply was a 200 with no text and no tool call (README Limitations). On round 172 (the `ocgw` proxy's reply headers and the recorder changed) all 23 `F2` scenarios passed with no retry, and the recorder saved every reply's outcome as `ok`.

r170: two failures. `F12.plan_no_changes` (round 171 committed, not yet applied) passed after the apply. `F2.opencode.glm-5.3` ran cleanly but the model answered "No, I'm sorry, but I can't do that." instead of PONG; the opencode scenarios now ask once more, visibly, as the chat scenarios do, and all 23 `F2` scenarios passed on round 171 with no retry. `F5.hourly.under` passed after its retry: usage_sync landed earlier spend in the window mid-call. On round 171 (the `ocgw` proxy's reply framing moved into one function) all 23 `F2`, 27 `F5`, 39 `F11` and 24 `F3.kimi-k3.chat` scenarios passed.

r169: one failure, `F12.plan_no_changes` (round 170 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 170 (the `ocgw` proxy changed again) all 23 `F2`, 27 `F5`, 39 `F11` and 24
`F3.kimi-k3.chat` scenarios passed; two `F2.task` scenarios passed after a retry, and the reply recorder caught the
first one's empty step at last. `multi_tool_edit`'s second model reply was a 200 of 75 chunks whose every delta was
empty (`{}` after the role), `finish_reason: stop`, and usage of 74 output tokens on 6,393 input: the edit call's
size, generated and billed but not delivered by Bedrock's endpoint (the raw stream is kept with the round's scans).
The gateway does not touch replies, so this is upstream; README Limitations says so. `switch_model_mid_session`'s
first attempt answered "OFF" for "ONE", a model miss.

r168: one failure, `F12.plan_no_changes` (round 169 committed, not yet applied); it passed after the apply. No
scenario needed a retry. Round 169 changed the `ocgw` proxy (a reply cut short is no longer passed on as whole), so
on round 169 all 23 `F2` client, proxy and opencode scenarios ran again with the 27 `F5`, 39 `F11` and 24
`F3.kimi-k3.chat` scenarios, and all passed.

r167: one failure, `F12.plan_no_changes` (round 168 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 168 all 27 `F5`, 39 `F11`, 24 `F3.kimi-k3.chat` and five `F2.task` scenarios
passed.

r166: one failure, `F12.plan_no_changes` (round 167 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 167 all 27 `F5`, 39 `F11`, 24 `F3.kimi-k3.chat` and five `F2.task` scenarios
passed, the `F2.task` ones through the reworked (chunked) reply recorder.

r165: one failure, `F12.plan_no_changes` (round 166 committed, not yet applied); it passed after the apply. No
scenario needed a retry; every `F2.task` session ran through the reply recorder. On round 166 all 27 `F5`, 39 `F11`,
24 `F3.kimi-k3.chat` and five `F2.task` scenarios passed.

r164: one failure, `F12.plan_no_changes` (round 165 committed, not yet applied); it passed after the apply.
`F2.task.multi_tool_edit` passed after a retry, with the same empty first step as on r156 to r162. From round 165 the
`F2.task` driver records every model reply of its sessions (`work/<task>-replies/`, through a hop on 127.0.0.1
between opencode and the user's ocgw proxy) and puts a summary of each in a failed attempt's result, so the next
empty step leaves its raw stream. On round 165 all 27 `F5`, 39 `F11`, 24 `F3.kimi-k3.chat` and five `F2.task`
scenarios passed, through the recorder.

r163: one failure, `F12.plan_no_changes` (round 164 committed, not yet applied); it passed after the apply. No
scenario needed a retry; `F2.task.multi_tool_edit` passed at the first attempt. On round 164 all 27 `F5`, 39 `F11`,
24 `F3.kimi-k3.chat` and five `F2.task` scenarios passed.

r162: two failures. `F12.plan_no_changes` (round 163 committed, not yet applied) passed after the apply.
`F2.task.multi_tool_edit` failed on both attempts, with the signature seen on r156, r158 and r160: one step that
finished with `stop` after about 70 output tokens and left no text and no tool call in opencode's session store. To
find where those tokens went, 24 more sessions of the same task ran through a capture proxy that recorded every
request and reply between opencode and the gateway: each first call to the model answered with a `read` tool call (40
tokens) and each second with an `edit` tool call (70 tokens), and the edit landed; one run edited a file left by an
earlier run (the probe's own folders, not the gateway). None reproduced the empty step, so its raw reply is not yet
recorded. The gateway does not change replies (the interceptor handles requests only). On round 163 the five
`F2.task` scenarios passed with no retry, with all 27 `F5`, 39 `F11` and 24 `F3.kimi-k3.chat` scenarios.

r161: one failure, `F12.plan_no_changes` (round 162 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 162 all 27 `F5`, 39 `F11` and 24 `F3.kimi-k3.chat` scenarios passed.

r160: one failure, `F12.plan_no_changes` (round 161 committed, not yet applied); it passed after the apply.
`F2.task.multi_tool_edit` passed after a retry, its third in rounds 156 to 160. In each first attempt opencode's
session store records one step that finished with `stop` after 73 or 74 output tokens and holds no text and no tool
call: the model's short answer never arrived as content or a parsed tool call. No other chat or tool scenario shows
this, so it is read as Kimi K3 on Bedrock, not the gateway (which handles requests, not replies). On round 161 all 27
`F5`, 39 `F11` and 24 `F3.kimi-k3.chat` scenarios passed.

r159: one failure, `F12.plan_no_changes` (round 160 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 160 all 27 `F5`, 39 `F11` and 24 `F3.kimi-k3.chat` scenarios passed.

r158: one failure, `F12.plan_no_changes` (round 159 committed, not yet applied); it passed after the apply.
`F2.task.multi_tool_edit` passed after a retry, as on r156: the interceptor log shows the first session's call
allowed, and opencode's log shows the model's reply ended the session within two seconds with no tool call. On round
159 all 27 `F5`, 39 `F11` and 24 `F3.kimi-k3.chat` scenarios passed.

r157: one failure, `F12.plan_no_changes` (round 158 committed, not yet applied); it passed after the apply. No
scenario needed a retry. Round 158 changed how the gateway reads a hand-edited boost, so on round 158 all 27 `F5`
budget scenarios ran again with the 39 `F11` and 24 `F3.kimi-k3.chat` scenarios, and all passed.

r156: one failure, `F12.plan_no_changes` (round 157 committed, not yet applied); it passed after the apply. One
scenario passed after a retry: `F2.task.multi_tool_edit`, whose first opencode session ended with no edit and no
error (the model stopped after its first step); the second made the edit. On round 157 all 39 `F11` scenarios and
all 24 `F3.kimi-k3.chat` scenarios passed.

r155: one failure, `F12.plan_no_changes` (round 156 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 156 all 39 `F11` scenarios and all 24 `F3.kimi-k3.chat` scenarios passed.

r154: one failure, `F12.plan_no_changes` (round 155 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 155 all 39 `F11` scenarios passed.

r153: the full run was cut off at scenario 916 of 1,047 (the agent's session was reset, which ended the runner);
its last result, `F2.client.mdm_overrides_env`, passed when run again alone on round 153, and no other scenario up
to that point failed. The round is not counted in the table; round 154 was run in full instead.

r152: two failures. `F12.plan_no_changes` (round 153 committed, not yet applied) passed after the apply.
`F3.kimi-k3.chat.once-0077` (tools offered) got a 200 whose message had no content and no tool call: the model put its
" PONG" in `reasoning`. The gateway answered correctly; the judge now asks such a reply once more, visibly, as it
already did for a wrong answer without tools, and the scenario passed when run again on round 153. One scenario
passed on its retry (`F2.task.multi_tool_edit`, the model's edit, as in r142).

r151: one failure, `F12.plan_no_changes` (round 152 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 152 all 39 `F11` scenarios passed.

r150: one failure, `F12.plan_no_changes` (round 151 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 151 `F11` (39) and `F5` (27) passed.

r149: one failure, `F12.plan_no_changes` (round 150 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 150 `F11` (39), `F5` (27) and `F4.model` (12) passed (the interceptor now reads
POLICY strongly consistent).

r148: one failure, `F12.plan_no_changes` (round 149 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 149 `F11` (39) and `F5` (27) passed.

r147: one failure, `F12.plan_no_changes` (round 148 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 148 `F11` (39), `F5` (27), `F9` (15), `F10` (4) and `F1.groups` (6, 1 n/a) passed.

r146: one failure, `F12.plan_no_changes` (round 147 committed, not yet applied); it passed after the apply. No
scenario needed a retry. Round 147's first deploy then failed 9 `F11` scenarios: the user detail read had moved to
BatchGetItem, which the dashboard role was not granted (the unit suite runs against moto, which checks no IAM).
The grant was added, a unit test now checks every DynamoDB call of the dashboard against its role, and all 39 `F11`
scenarios passed, with `F5` (27) and `F9` (15).

r145: one failure, `F12.plan_no_changes` (round 146 committed, not yet applied); it passed after the apply. No
scenario needed a retry. On round 146 `F11` (39) and `F5` (27) passed again.

r144: the first full run of 1,047 scenarios (367 Cognito, with the two `F11.action.repair_*`). One failure,
`F12.plan_no_changes` (round 145 committed, not yet applied); it passed after the apply. No scenario needed a retry.
On round 145 `F11` (39), `F5` (27) and `F9` (15) passed again.

r143: the full run had one failure, `F12.plan_no_changes` (round 144 committed, not yet applied); it passed after
the apply. No scenario needed a retry. Round 144 adds the two `F11.action.repair_*` scenarios (1,047 in all, 367
Cognito); on round 144 all 39 `F11` scenarios passed, with `F5` (27), `F9` (15) and `F1.groups` (6, 1 n/a).

r142: the full run had one failure, `F12.plan_no_changes` (round 143 committed, not yet applied); it passed after
the apply. One scenario passed on its retry: in `F2.task.multi_tool_edit` the model (Kimi K3) ended its first
session without making the edit, every step answered by the gateway, and made it in the fresh folder. On round 143
`F11` (37), `F5` (27), `F9` (15) and `F1.groups` (6, 1 n/a) passed again with no retry.

r141: the full run had one failure, `F12.plan_no_changes` (round 142 committed, not yet applied); it passed after
the apply. On round 142 one `F5.hourly.under` then failed because usage_sync landed the window's earlier model spend
during the call ($0.37 became $0.40 against a $0.39 cap): the gateway refused correctly. An "under" scenario now
keeps the whole cap as headroom and, if the sync still moves the window, re-reads it and retries once (counted). All
27 `F5` scenarios then passed with no retry. No other scenario needed one.

r140: the full run had one failure, `F12.plan_no_changes` (round 141 committed, not yet applied); it passed after
the apply. On round 141, five `F5.*.under` scenarios then failed: the test seeded spend below a window's real model
spend by adding a negative `spend_usd`, which round 141 rightly refuses as an unreadable record. The budget driver
now sets the cap and the seeded spend on top of what the window holds; all 27 `F5` scenarios passed, and the ledger
holds no negative `spend_usd`. No scenario needed a retry.

r139: the full run had one failure, `F12.plan_no_changes` (round 140 committed, not yet applied); it passed after
the apply. No scenario needed a retry.

r138: the full run had two failures. `F12.plan_no_changes` saw round 139 committed but not applied.
`F3.glm-5.3.chat.stream-0032` was a 200 stream with no answer at all: a reasoning model can spend a 256-token budget
thinking, so the chat scenarios now allow 1,024. After the round 139 apply all 72 served `F3` scenarios and the plan
check passed, and the row counts that rerun. No scenario needed a retry.

r137: the full run had one failure, `F12.plan_no_changes` (round 138 committed, not yet applied); it passed after
the apply. A check of the 72 served `F3` chat scenarios on round 138 then had DeepSeek answer "PING" to "say PONG":
the gateway had delivered the answer, so a chat scenario now passes on any answer or tool call and records whether
it said PONG (testing.md). The rerun passed 72 of 72, every one saying PONG. No scenario needed a retry.

r136: the full run had one failure, `F12.plan_no_changes` (round 137 committed, not yet applied); it passed after
the apply. No scenario needed a retry.

r135: the full run had two failures. `F12.plan_no_changes` saw round 136 committed but not applied, and
`F3.glm-5.3.chat.stream-0104` was a 200 stream the old substring check misjudged. Round 136 reads every reply by its
structure (testing.md, "How a reply is judged") and records the answer each chat scenario was judged on; after its apply all 72 served `F3`
chat scenarios passed, as did the plan check, and the row counts that rerun. No scenario needed a retry.

r134: the full run had one failure, `F12.plan_no_changes` (round 135 committed, not yet applied); it passed after
the apply. A check of the DeepSeek chat set on round 135 then caught a test that was too strict: with a tool offered,
the model answered in words instead of saying PONG. A tool scenario now passes on any answer or tool call; all 36
chat scenarios of both checked models passed. No scenario needed a retry.

r133: the full run had two failures. `F12.plan_no_changes` saw round 134 committed but not yet applied.
`F3.deepseek-v3.2.chat.stream-0130` was a 200 stream that the test misread: it looked for PONG in the raw server-sent
events, where a model may split a word across chunks; it now joins the chunks' text first. After the round 134 apply
both passed, with all 12 DeepSeek chat scenarios, and the row counts that rerun. No scenario needed a retry.

r132: the full run had one failure, `F12.plan_no_changes`, again because round 133 was committed but not yet
applied; it passed after the apply, and the row counts that rerun. No scenario needed a retry.

r131: the full run had two failures. `F12.plan_no_changes` saw round 132, committed but not yet applied, as
changes; it passed after the apply. `F2.task.multi_tool_edit` failed on the model, not the gateway: every opencode
step was answered, but the edit wrote `GREETING = \"OCGW-EDIT-OK\"` with the backslashes. Such a session now runs
once more in a fresh folder (testing.md); after the round 132 apply the five `F2.task` scenarios passed 5 of 5, and
the row counts that rerun.

r129: the full run had one failure, `F12.plan_no_changes-1039`: the round 130 interceptor and dashboard changes were
already in the working tree, so `terraform plan` showed both Lambda functions to update. After the round 130 apply
the scenario passed, as did `F1.groups` (6 pass, 1 n/a) and `F2.client.login_pkce`; the row counts that rerun.

r128: the full run had one failure, `F6.gateway_rpm-0798` (seven calls at a limit of one a minute all answered 200,
35 s after the change). The driver now repeats a 429-free burst once in the next minute; the four `F6.gateway_*`
scenarios then passed 4 of 4, and the row counts that rerun. The 7 `no_driver` are the six F7 faults Bedrock cannot
be made to produce and `F12.destroy_clean`, the account owner's last step (testing.md).

The matrix is 1,047 scenarios over 128 subfeatures, in three identity-provider sets: 367 Cognito, 340 Okta and 340
JumpCloud. The row above is the Cognito set, the only one a deployed stack can serve at a time: `idp` in
`terraform.tfvars` selects the provider, and this stack is deployed with `idp = "cognito"`. Covering the Okta and
JumpCloud sets needs a redeploy against each provider and one interactive sign-in per test user, so those 680
scenarios have not been run against a live gateway; the code paths they exercise are covered by the unit suite.

52 scenarios are reported `n/a`: they need the `/v1/responses` API, which no open-weight model on Bedrock serves
(every configured model answers "does not support the '/v1/responses' API. Try '/v1/chat/completions' instead."). They
are neither passes nor failures, and they start running by themselves if Bedrock adds the route. `no_driver` counts
scenarios whose kind has no driver yet, which are never reported as passes.

The full `tests/e2e/out/results.json` holds every scenario's status, timing and response body.

## Scans and reviews

| Round | Commit | Bandit (H / M / L) | Checkov (pass / fail) | ASH | Content scan | /security-review | /code-review |
|---|---|---|---|---|---|---|---|
| 1 | a279a3e | 0 / 0 / 218 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 2 | aedf4c5 to 9df8388 | 0 / 0 / 237 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 3 | c8631e2 | 0 / 0 / 254 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 4 | c095e5c | 0 / 0 / 266 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings: 9 fixed, 1 documented |
| 5 | d4361ae | 0 / 0 / 271 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 6 | 59b4b67 | 0 / 0 / 275 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 7 | 5a8c3fc | 0 / 0 / 280 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 8 | 9c17997 | 0 / 0 / 291 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 9 | e81e39e | 0 / 0 / 300 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 10 | ad0697c | 0 / 0 / 311 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 11 | 51ef36d | 0 / 0 / 324 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings: 9 fixed, 1 documented |
| 12 | 757df84 | 0 / 0 / 331 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings: 8 fixed, 2 documented |
| 13 | dec010f | 0 / 0 / 342 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 14 | 0f9a14c | 0 / 0 / 349 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings: 7 fixed, 3 documented |
| 15 | 95fd7b9 | 0 / 0 / 355 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 16 | 9d07b63 | 0 / 0 / 361 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 17 | a92eb8f | 0 / 0 / 366 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 18 | febd0a0 | 0 / 0 / 378 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 19 | 161f915 | 0 / 0 / 384 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, all fixed |
| 20 | 0be5c90 | 0 / 0 / 394 | 179 / 0 | 1 actionable (B102 in a test), fixed | not run | 0 findings | 10 findings, all fixed |
| 21 | e83e62a | 0 / 0 / 406 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 22 | b759dae | 0 / 0 / 427 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, 9 fixed, 1 kept by design |
| 23 | 9ef87c1 | 0 / 0 / 429 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 24 | 78e962c | 0 / 0 / 443 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 25 | 128a09c | 0 / 0 / 458 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, 9 fixed, 1 documented |
| 26 | ccbe82a | 0 / 0 / 457 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings, all fixed |
| 27 | c9b423b | 0 / 0 / 459 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings, all fixed |
| 28 | 4faf62c | 0 / 0 / 469 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, 7 fixed, 3 documented |
| 29 | 393731e | 0 / 0 / 471 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings, 6 fixed, 1 documented |
| 30 | fe871eb | 0 / 0 / 476 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, 8 fixed, 1 kept by design |
| 31 | 4ff4055 | 0 / 0 / 487 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, all fixed |
| 32 | 508cf7b | 0 / 0 / 489 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings, all fixed |
| 33 | 577d925 | 0 / 0 / 490 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, all fixed |
| 34 | f11a5ad | 0 / 0 / 498 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 35 | b19fbf5 | 0 / 0 / 504 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 36 | 1977c9c | 0 / 0 / 511 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 37 | 9d4743b | 0 / 0 / 515 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings, all fixed |
| 38 | d8bfa4b | 0 / 0 / 516 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 39 | 332d0cd | 0 / 0 / 520 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, 7 fixed, 1 kept by design |
| 40 | bdee514 | 0 / 0 / 525 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 41 | f1a6509 | 0 / 0 / 534 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, all fixed |
| 42 | 0bf14a8 | 0 / 0 / 537 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, all fixed |
| 43 | a38000b | 0 / 0 / 544 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 44 | bdf1b1d | 0 / 0 / 550 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings: 8 fixed, 2 documented |
| 45 | ba39834 | 0 / 0 / 565 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings, all fixed |
| 46 | c54568f | 0 / 0 / 567 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, 9 fixed, 1 kept |
| 47 | e460d56 | 0 / 0 / 574 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings, 6 fixed |
| 48 | a2f7c02 | 0 / 0 / 575 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings: 7 fixed, 1 false positive (pinned by a test) |
| 49 | ed3cea8 | 0 / 0 / 585 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings: 8 fixed, 1 kept |
| 50 | b27891a | 0 / 0 / 589 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings, all fixed |
| 51 | a168bbc | 0 / 0 / 593 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings: 8 fixed, 2 kept |
| 52 | 47c9758 | 0 / 0 / 601 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings, all fixed |
| 53 | f6aa299 | 0 / 0 / 606 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings, all fixed (2 were r52 regressions) |
| 54 | a40a8b6 | 0 / 0 / 610 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings, all fixed |
| 55 | 5808270 | 0 / 0 / 613 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed |
| 56 | d2655bc | 0 / 0 / 619 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 5 findings (none high), all fixed |
| 57 | 7574135 | 0 / 0 / 621 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings, all fixed (1 was an r56 regression, reproduced) |
| 58 | f7a9c95 | 0 / 0 / 620 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed |
| 59 | bd151b4 | 0 / 0 / 623 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed |
| 60 | 3c04242 | 0 / 0 / 628 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed |
| 61 | 03ae815 | 0 / 0 / 636 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed |
| 62 | 4f722ae | 0 / 0 / 642 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed |
| 63 | 12c4cea | 0 / 0 / 647 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed |
| 64 | d7992cf | 0 / 0 / 649 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), all fixed |
| 65 | 14d4a36 | 0 / 0 / 654 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed |
| 66 | c6056ef | 0 / 0 / 659 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed |
| 67 | 47eb174 | 0 / 0 / 653 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed |
| 68 | d2d1d5a | 0 / 0 / 660 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed |
| 69 | 58c6bb3 | 0 / 0 / 663 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), 6 fixed, 1 declined (explicit pool map kept); the deploy found the gateway allows one rate limit per dimension-key set, so per-user gateway limits were redesigned as entries of that limit and verified live (dashboard writes 13/13, Terraform keeps dashboard entries 5/5) |
| 70 | 3aa16b8 | 0 / 0 / 671 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), all fixed; live: dashboard writes 13/13, Terraform keeps dashboard entries 5/5, web search 15/15 |
| 71 | 98dce61 | 0 / 0 / 684 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed; live: dashboard writes 13/13, Terraform keeps dashboard entries 5/5, web search 15/15 |
| 72 | 6f9e541 | 0 / 0 / 686 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), all in the dashboard's per-user gateway-limit editing; resolved by removing that feature (gateway limits are Terraform's alone, shown read-only; per-user caps admins change are the interceptor's rpm); live read-only check 8/8, web search 15/15 |
| 73 | 539a0d0 | 0 / 0 / 633 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high): 2 fixes (rate comparison by number, a log of replaced entries) and 8 leftovers of the removed feature; live read-only check 8/8, web search 15/15 |
| 74 | e9f202d | 0 / 0 / 635 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), all fixed; deploy blocked (AWS sign-in expired 21:36 UTC) |
| 75 | 2a1b7bc | 0 / 0 / 636 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), all fixed; deploy blocked (AWS sign-in expired) |
| 76 | 3d75fc3 | 0 / 0 / 639 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), all fixed; deploy blocked (AWS sign-in expired) |
| 77 | ff7dde4 | 0 / 0 / 642 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), 9 fixed, 1 kept by design (legacy description forms needed to adopt stacks deployed before tags); deployed, live dashboard 8/8, web search 15/15, -replace keeps the limit |
| 78 | 616ef04 | 0 / 0 / 651 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), all fixed; deployed r77, web search 15/15, rate limits 12/13 (gateway_rpm: known service behaviour) |
| 79 | 327cf92 | 0 / 0 / 658 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed; deployed r78, dashboard 8/8 |
| 80 | 2785455 | 0 / 0 / 665 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), all fixed; deployed r79 (no change) |
| 81 | 9443bc6 | 0 / 0 / 678 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), all fixed |
| 82 | 1b8872c | 0 / 0 / 688 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed; deployed r81, dashboard 8/8 |
| 83 | 1984d28 | 0 / 0 / 701 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), all fixed |
| 84 | e7261cb | 0 / 0 / 705 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), 5 fixed, 1 kept by design (by_sk index projects every row; a sparse index needs a data back-fill) |
| 85 | e1fab54 | 0 / 0 / 714 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), 6 fixed, 1 kept by design (an update is not judged done by its content: the service can report a stale ACTIVE) |
| 86 | 9d33884 | 0 / 0 / 717 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed |
| 87 | 1aaa04a | 0 / 0 / 722 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed |
| 88 | e85d784 | 0 / 0 / 729 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed; deployed r87 |
| 89 | 4fb2973 | 0 / 0 / 733 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed |
| 90 | 34fd602 | 0 / 0 / 739 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), all fixed by one change: a single projects data source for every user (no cache, lock or overrides); live plan: no changes, same project ids |
| 91 | 07e368d | 0 / 0 / 727 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high): 4 fixed (retag on group change, one failed create no longer stops the others, one staleness rule, archive retries simplified); 6 not applicable per a live probe of the Projects API (create is active at once, list and GET never lag, archive is idempotent, status always present) |
| 92 | 7477812 | 0 / 0 / 729 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high): 4 fixed (retag moved to apply time, compared on Terraform's keys only; dead ids parameter), 1 kept by design (plan-time create, a documented limit), 2 ruled out by the r91 live probe; deployed, 3 tag steps added, no writes needed |
| 93 | 67e1bed | 0 / 0 / 730 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), 8 fixed (one batched retag step on the listing, id-change trigger, aws: keys dropped, empty replies, cursor loop guard); 1 reverted: a name trigger on project_archive replaced it on deploy and archived the test users' projects twice; recovered (new projects, plan clean), documented as a limit; dashboard 8/8, web search 15/15 after recovery |
| 94 | 8e207db | 0 / 0 / 733 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed after a live probe of tag writes (they merge, null removes, aws: refused); plan clean, live retag check: 3 projects, no writes |
| 95 | 2acfa7e | 0 / 0 / 736 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed: relative helper paths in state (destroy steps portable across checkouts), unlisted project read directly, only Terraform-set tag variables read, cursor URL-encoded, no-credentials message, archive failure as a message, tag-key consistency test; deployed: 5 in-place updates, plan clean |
| 96 | e4fb980 | 0 / 0 / 741 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high): 6 fixed (interpreter chosen at run time for every helper, retag and archive try every project, body-less MCP passes without a body, docstring), 1 kept by design (the retag environment's total-size ceiling, far above the DIRECTORY item's 400 KB user limit); deployed in place, web search 15/15, plan clean |
| 97 | e552421 | 0 / 0 / 744 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed by helpers/python.sh (run-time interpreter: .venv, else uv with boto3 and requests, else python3; quoted paths; no --isolated) and a retag that reports every failure; deployed in place, web search 15/15, plan clean |
| 98 | cf12723 | 0 / 0 / 749 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), all fixed: an MCP request with no readable body goes on with an empty body (fail closed); python.sh takes a .venv or python3 only with boto3 >= 1.43.118 and requests; helper errors are exceptions turned into one message, so every loop reports all failures; group seeding is one step for all users; the retag reads projects one by one when the listing fails; deployed in place, web search 15/15, plan clean |
| 99 | f89be08 | 0 / 0 / 753 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed: the helpers check boto3 >= 1.43.118 themselves (a hand-set python or rule_python included); network and boto3 errors reach Terraform as one message and per-user loops collect them; the layer zip path stays relative in state; python.sh prefers a fitting python3 to uv and reads versions without importing boto3; deployed (layer replaced once), dashboard 8/8, web search 15/15, plan clean |
| 100 | 01b2aec | 0 / 0 / 761 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), 8 fixed: layer.tf refuses a boto3_version below 1.43.118; the partial layer zip is per run and its digest is cached; the retag sends only the owned tag keys; python.sh reads the boto3 minimum from agentcore_wait.MIN_BOTO3; the interpreter-order comments match python.sh; one closed-month block in usage_sync. 1 declined: the dashboard Lambda keeps its own rate-limit listing (it cannot import the deploy helpers, which are not packaged with it). Deployed in place, dashboard 8/8, web search 15/15, plan clean |
| 101 | 81e4d73 | 0 / 0 / 765 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), 6 fixed: the retag stops at the first unreadable project once the listing has failed (no retry cycle per project); the layer zip is hashed every plan again (r100's cached digest could go stale, and its temp file could be left behind); one error contract for aggregated failures (_report raises HelperError, __main__ turns it into the message); an unknown command or a missing @env: variable is a message, not a traceback; a test ties layer.tf's boto3 minimum to MIN_BOTO3. 1 declined: group seeding stays one step for all users (each write is an idempotent SET of the same value, and one process is what r98 asked for). Plan clean (no changes), dashboard 8/8, web search 15/15 |
| 102 | 214fced | 0 / 0 / 769 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), 7 fixed: the boto3 layer is reproducible (dependencies resolved as of boto3_layer_resolved_before with uv --exclude-newer; bin/ scripts and RECORD files, which held the build's temp path, left out; two builds in different folders gave the same SHA-256); the retag stops early only when Mantle is out of reach and keeps the failures it has; a missing AWS profile, a missing OCGW_TAGS_i or malformed JSON is a message, not a traceback; leftover r100 digest files are removed on every run; opencode-config rejects a reply that is not an OpenAI model list with a message; layer.tf's minimum text is tied to MIN_BOTO3. 2 declined: the retag still lists before reading directly (about 2.5 minutes to report an outage is acceptable for a deploy step), and put_policy keeps writing lists (POLICY items are written only by Terraform and the dashboard, both as lists). Layer replaced once, dashboard 8/8, web search 15/15, plan clean |
| 103 | 6870f8b | 0 / 0 / 772 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), all fixed: the layer's source_code_hash is a hash of the files inside it (names and bytes), not of the zip, so another OS or zlib build gives the same value; members are sorted by posix path with create_system 3; cleanup keeps other zips for a week (another workspace's zip behind a saved plan stays); projects-ensure stdin and gateway_rule.py RULE_SPEC/argv errors are messages; a circuit breaker in mantle() makes every later call fail fast once one has spent its retries on an outage; a failed layer install names boto3_layer_resolved_before; usage_sync runs open and closed windows in one pool; policy.directory loses its unused fresh path; one pagination helper (agentcore_wait.list_all) for rules and rate limits. Layer replaced once, dashboard 8/8, web search 15/15, gateway 11/13 (the failures are the known service-side gateway RPM limit, see README Limitations; the interceptor rpm is the cap), plan clean |
| 104 | ed0c47c | 0 / 0 / 776 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), all fixed, 2 of them r103 regressions: the breaker's refusal is its own MantleOutage, which _outage() counts, so the retag's early stop fires again during an outage; the zip in use is touched every run, so the week-old cleanup never takes a zip another workspace still uses. Also: the breaker trips on a 5xx or 429 that outlasted the retries; the cleanup skips a zip another plan removed meanwhile; gateway_rule.py checks every RULE_SPEC key its command reads; list_all stops on a repeated nextToken. Plan clean (no changes), dashboard 8/8, web search 15/15 |
| 105 | 3d544c6 | 0 / 0 / 781 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), all fixed, 1 an r104 regression: a 5xx or 429 now trips the Mantle breaker only after two calls in a row exhaust their retries, so one bad project no longer fails the whole run (a network failure still trips it at once). Also: gateway_rule.py reports service and paging errors as messages; the dashboard's rate-limit listing and usage_sync's ListMetrics and GetMetricData loops stop on a repeated token and accept a null list; MCP calls other than tools/call (initialize, tools/list, streams) no longer need the user directory; an interrupted layer build's temp folder is removed after a day; one env-chunk reader (_env_chunks) names a missing chunk; the dashboard proxy keeps the link's other query parameters on its key redirect. Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, plan clean |
| 106 | 9b178f3 | 0 / 0 / 790 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), 9 fixed: the Mantle breaker trips at once only for a request never sent (a read timeout counts like a 5xx, two calls in a row), and the retag's own early stop is gone (the breaker is the one rule); gateway_rule.py reports a missing profile and a fallback entry without target or model as messages; list_all takes a null list as empty; the Lambdas' three token-paging loops share policy.paginate_token (null-safe, stops on a repeated token); a body-less MCP stream call needs no user directory; ocgw opencode-config reports a stalled or cut reply as a message; the FAILURES comment is back beside FAILURES. 1 declined: caching the layer's content digest (r101 removed that cache because it could go stale; hashing per plan is cheap and exact). Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, usage_sync synced after the deploy, plan clean |
| 107 | 165d69b | 0 / 0 / 794 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), 7 fixed: the unit tests no longer need an AWS sign-in (the project-helper fixture stubs credentials; dashboard/proxy.py treats an empty AWS_PROFILE as no profile); the suite now also runs on a shell with no AWS config at all (4,945 passed); the breaker trips at once for every network failure except a read timeout, which alone counts towards two in a row; paging takes the first non-empty list key (rateLimits empty, items filled); ocgw opencode-config tells a sign-in failure from a gateway one and shows the gateway's explanation (an identity_map hint, say); RULE_SPEC list fields must be lists; stale breaker comments and test names fixed, with a test that counts the 8 requests a retag sends during an outage. 1 declined: one paging module shared by the Lambdas and the Terraform helpers (they ship separately). Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, plan clean |
| 108 | eddc8ee | 0 / 0 / 799 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), 5 fixed: acgw.py drops an empty AWS_PROFILE or AWS_DEFAULT_PROFILE before the boto3 session (botocore looked up a profile named ""; a new test proved profile_name=None alone was not enough); a request no retry can fix (a bad URL, schema or header, a redirect loop) is a message after one try, not 8 retries reported as an outage; the breaker's count-and-trip rule is one helper (_spent) used by both branches; a non-string error.message from the gateway cannot crash ocgw opencode-config; the interceptor's allow list reads only the groups' model patterns (policy.group_models, shared with resolve) instead of a full limit resolution. 1 declined again: one paging module shared by the Lambdas and the Terraform helpers. Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, plan clean. Real opencode F2: 21/21 after two e2e-harness fixes (the per-user proxy port came from len(_HOMES), which shrinks when client homes are popped, so one user's proxy clashed with a live one and opencode reached the wrong proxy; a token-refresh check failed when two refreshes fell in the same second) |
| 109 | 970bca8 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), 8 fixed: the empty-profile cleanup is one function in agentcore_wait (drop_empty_profile) that acgw.py and gateway_rule.py both call (gateway_rule.py had missed it); a read timeout or a cut reply while the body streams (which requests raises as ConnectionError or ChunkedEncodingError) counts like a read timeout for the breaker, so one slow project no longer trips it; a missing or malformed CONFIG/DIRECTORY item takes the same backoff and recent-copy path as a failed read; the e2e harness re-checks the proxy is still running after it first answers; the client's /mcp forward keeps a query string configured in websearch_url; `ocgw claims` tolerates a null token; allow_list passes the already-cleaned policy items straight on. Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 110 | 1031eea | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), 5 fixed: a reset or abort before any reply (ConnectionError wrapping ProtocolError) trips the breaker at once again, since r109 had counted it as a slow reply (a cut mid-body is ChunkedEncodingError and still counts as slow); ocgw opencode-config refuses, with a message and the file untouched, an opencode.json that is not an object or whose provider is not one; the client proxy checks request framing before it refreshes the sign-in, so a request it will refuse never spends a refresh; the directory() docstring now says what the code does (a recent copy serves for up to 10 minutes); dashboard/proxy.py drops an empty profile before `terraform output`. 2 declined: a breaker that tracks whether a status line arrived instead of classifying exceptions (it would need stream=True or hooks on every call), and resolve() trusting already-cleaned items (it is the one entry point for the dashboard, the interceptor and the tests; cleaning is idempotent and cheap). Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 111 | 0aa3038 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), 6 fixed: save() retries the rename for about a second when Windows refuses it because a reader has tokens.json open, so a rotated refresh token is never dropped; directory() with no copy yet retries after FIRST_RETRY_SECONDS (2 s) instead of a whole CACHE_SECONDS, so a fresh container recovers from one transient error in seconds; `ocgw logout` takes token_lock, so a refresh in flight cannot write the session back after it; opencode-config explains a file it cannot parse (JSONC comments) and writes opencode.json through a temp file and a rename; the proxy allows an hour of silence on the MCP GET event stream instead of cutting it at 15 minutes. Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 112 | af4d084 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), 9 fixed: opencode-config rewrites through a symlink to the real file, keeps its permissions (0600 stays 0600), and removes its temp file if the write or rename fails; directory() also retries after FIRST_RETRY_SECONDS when its copy is past STALE_SECONDS, and counts valid JSON of the wrong shape (identity_map "[]") as malformed so the good copy keeps serving; `ocgw logout` retries an unlink Windows refuses; a refresh the IdP rejects with a 4xx (invalid_grant) drops the dead refresh token, so later requests say "run ocgw login" without asking the IdP again; dashboard/proxy.py calls agentcore_wait.drop_empty_profile (one copy of the rule); stale comments in policy.directory and agentcore_wait.list_all; the breaker's _spent takes a plain string. Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 113 | 113da90 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), 7 fixed: the token endpoint's 4xx is a typed TokenRefused carrying the OAuth error code, and only invalid_grant drops the refresh token (invalid_client, a WAF 403 or a bad endpoint keep it, so a config fault no longer signs everyone out); that rewrite keeps obtained_at, so an expired opaque token cannot look fresh again, and a failed write cannot hide the refusal; opencode-config makes its temp file with a random name, O_EXCL and O_NOFOLLOW, and explains a symlink into a read-only place (Nix, home-manager); dashboard/proxy.py takes its region default from AWS_REGION or AWS_DEFAULT_REGION; the breaker trips on an error with an empty message; directory() reuses one servable test. 1 declined again: one shared paging / not-found / parallel-get module across the Lambda zip and the Terraform helpers (they ship separately). Deployed in place (3 Lambdas), dashboard 8/8, web search 15/15, opencode F2 21/21 (F2.task.multi_tool_edit failed once with an empty model turn, rc 0 and no output, then passed 5/5 on a rerun of F2.task), plan clean |
| 114 | f4368f8 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 5 findings (none high), 5 fixed: post_form parses the whole 4xx body (capped at 64 KiB) and truncates only the message, so a long invalid_grant body from JumpCloud (Ory Hydra) is still recognised; a refresh that fails for any other reason (another 4xx, an IdP 5xx or network error) hands out the cached token while it is still valid; dashboard/proxy.py reads the region from the Function URL host unless --region is given, so a shell AWS_REGION for another region no longer breaks it (checked live with AWS_REGION=ap-southeast-2: / 200, /api/me 200, no key 401); opencode-config explains EROFS, EACCES and EPERM (a Nix store link) and its cleanup cannot raise over that message; O_NOFOLLOW dropped (O_EXCL already refuses any existing path). Client and local proxy only: plan showed no changes, nothing to deploy. Dashboard 8/8, web search 15/15, opencode F2 21/21 |
| 115 | 5e2e433 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 8 findings (none high), 8 fixed: get_token handles a failed early refresh in one place (any refusal, invalid_grant included, an IdP outage, a non-JSON or non-object reply, a cut reply): the cached token serves while still valid, and REFRESH_RETRY_S (30 s) spaces out further tries, so requests inside MARGIN no longer each wait on the IdP; ocgw installs a no-redirect opener, so a token or refresh token never follows a 3xx (http:// included); local_key falls back to an exclusive create where hard links are refused; the interceptor's /websearch answers an empty result list instead of a 502 when the search ran but its text is not the expected JSON (search_results); acgw.py sends SigV4 requests with allow_redirects=False; var.users groups use the dashboard's GROUP_NAME rule. Deployed in place (interceptor), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 116 | af7993f | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), 8 fixed: local_key's no-hard-link fallback can no longer leave or serve an empty key (a failed write removes the file, readers wait briefly for the content, and an empty key is refused rather than accepted as an empty bearer); a failed early refresh is one except clause, re-checks expiry after the attempt, and records refresh_failed_at in tokens.json so `ocgw token` and opencode-config share the proxy's backoff; an early refresh with no id_token keeps the still-valid one with the rotated refresh token; search_results tolerates a non-string hit text; var.users groups refuse leading or trailing spaces, and Cognito groups are checked against Cognito's rule at plan (precondition); dashboard/proxy.py answers a 3xx from the Function URL with a 502 and an explanation. 1 declined: a private urllib opener instead of install_opener (ocgw is a CLI entry point; every urlopen in its process carries a token, so the process-wide no-redirect opener is the guarantee wanted). Deployed in place (interceptor), dashboard 8/8, proxy live 200/200/401, web search 15/15, opencode F2 21/21, plan clean |
| 117 | 1c84248 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 7 findings (none high), 5 fixed: the early-refresh backoff is also kept in process memory, so a tokens.json that cannot be written no longer sends every request back to the IdP; refresh_failed_at is stored with sub-second precision; local_key's wait loop treats a key file removed by another process's failed write as empty and exits with a clear message instead of crashing; search_results turns a non-string hit title or url into a string; the Cognito group-name rule moved from a resource precondition into var.users' own validation, so plan explains every refused group in one place. 2 declined: passing a 304 through dashboard/proxy.py (the proxy sends no If-None-Match or If-Modified-Since, so the Function URL cannot answer 304); no backoff after a refresh that returned no id_token (without it every request inside MARGIN would ask the IdP again). Deployed in place (interceptor), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 118 | 357daaa | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 10 findings (none high), 4 fixed: search_results turns a non-string publishedDate into a string; an expired token whose refresh returned no id_token no longer keeps the old access_token under a fresh obtained_at; /websearch treats an explicit null count or maxResults as not given; dashboard/proxy.py percent-encodes / in query values as SigV4 canonicalises them (checked live: a query with / and a space is answered 200). 6 declined: the /websearch policy read (policy_items is cached per container); audit from/to (a date-only to is the whole day, so the example is not an inversion); closed-day request rollups (re-synced on every run for CLOSE_MINUTES); no backoff after a no-id_token refresh (r117 decision); ES256 doubling at y = 0 (no such point on P-256; alb_claims fails closed); acgw pool shutdown without waiting (the helper joins the threads at exit anyway). Deployed in place (interceptor), dashboard 8/8, proxy live 200/200/200/401, web search 15/15, opencode F2 21/21, plan clean |
| 119 | bd65b0d | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high; review run at medium effort after five high-effort runs ended on Bedrock 503s), 9 fixed: the 30 s refresh backoff also holds once the token has expired, so an unreachable IdP is a fast 502 rather than a ~45 s wait per request; an explicit null refresh_token from the IdP keeps the old one; the "cached token still works" rule is one helper; local_key uses a key only once it has the full format, so a half-written key on a no-hard-link home is never taken; the proxy's client sockets time out after 120 s; the MCP route treats maxResults: null as not given (as the HTTP route does) and drops it before forwarding; a JSON-RPC result that is not an object and a text nested too deep to parse give no hits rather than a 502 after the charge; dashboard/proxy.py reports a --region mismatch as such. Deployed in place (interceptor), dashboard 8/8, proxy live 200/200/200/401, web search 15/15, opencode F2 21/21, plan clean |
| 120 | b447717 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), 5 fixed: the proxy's 120 s client-socket timeout covers reading a request only and is lifted before the response, so a slow reader of a long MCP event stream is not cut off (a body shorter than its Content-Length is a 400); the fast-fail after a failed refresh says "try again in a moment" and the proxy's 502 carries the message; `ocgw token` reports a failed refresh in one line instead of a traceback; every null MCP tool argument is dropped as "not given"; local_key accepts a key of the generated length or longer. 1 declined: catching TypeError for {"results": null} (isinstance(hits, list) already gives no hits). Deployed in place (interceptor), dashboard 8/8, web search 15/15, opencode F2 21/21, plan clean |
| 121 | a1f2a06 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high; medium effort after Bedrock 503s), 4 fixed: the proxy writes a response under a 600 s send timeout instead of none, so a client that stops reading a stream frees its thread; a body read that fails (reset or timed out) closes the connection without a reply, and only a body cut short by a clean close gets the 400; local_key accepts 43-256 key characters and reads at most 512 bytes; the interceptor comment says null arguments are dropped in place because that dict is forwarded. 2 declined: settimeout() failing on a half-closed peer (it acts on our own open socket); the IdP's error text in the local 502 (the caller holds the local key and it is the user's own sign-in error, as `ocgw token` prints). Deployed in place (interceptor), dashboard 8/8, web search 15/15, opencode F2 21/21 on a rerun (the first run's /compact timed out after 300 s on Kimi K3, 20/21), plan clean |
| 122 | 6abb209 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | not completed (three Bedrock 503s before any code was read; r123 reviews the fixed tree) | 5 findings (medium effort after Bedrock 503s), all fixed: four came from edits the r121 E2E run made to the sample itself (the F2 `/compact` scenario ran opencode with --auto in a work folder inside this checkout, so opencode took the checkout as its project and the model refactored groups_of and wrote COMPACT_NOTES.md; never deployed). The edits are reverted, and each F2 work folder is now its own git root so an --auto session cannot reach the sample's files. The fifth: a test for the proxy closing quietly when the client resets mid-body. Plan clean (the live Lambdas already matched), dashboard 8/8, web search 15/15, opencode F2 21/21 on a rerun (the first run's multi_tool_edit got an empty Kimi K3 reply in 3.9 s, 20/21); the checkout was byte-identical before and after both E2E runs |
| 123 | r122 | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high; medium effort after three Bedrock 503s), 6 fixed: while one proxy thread refreshes the sign-in, the others use the still-valid cached token instead of queuing up to ~45 s; a failed tokens.json write after a refresh no longer fails a request holding a good token, and the "refresh returned no token" exit keeps its sign-in message; a malformed port in gateway_url or websearch_url is refused at start-up; dashboard/proxy.py does not forward a stale `k` from an old link; the streamed-reply read function is chosen once; /websearch echoes the stripped query it searched. 2 declined: merging the two refresh backoffs and a shared failure helper (the two paths store different things and are pinned by tests); 1 needed no change. Deployed in place (interceptor), dashboard 8/8, proxy live 200/200/200/401, web search 15/15, opencode F2 21/21, checkout unchanged across the E2E run, plan clean |
| 124 | 2a789bb | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 6 findings (none high), 5 fixed and 1 partly: tokens a refresh could not write to tokens.json are kept in memory and used by later calls (not the spent refresh token); during the 30 s backoff after a failed early refresh a valid token is returned without the lock; proxy_port must be an integer 0-65535 (one-line error, not a traceback); a Content-Length that is not ASCII digits, or a duplicate one, is a 400; an explicit null refresh_token means the session ends with the token; dashboard/proxy.py answers HEAD and OPTIONS with its JSON 405. Declined: not queueing while another process holds the refresh lock (a non-blocking flock path on two OSes for a wait only a concurrent `ocgw token` causes). Client-only change (plan: no changes), dashboard 8/8, proxy live 200/200/200/401, web search 15/15, opencode F2 21/21, checkout unchanged across the E2E run |
| 125 | 8b6f9ca | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | 0 findings | 9 findings (none high), 4 fixed: each open usage window's CloudWatch query now ends at the window's own end, so in the last minute of an hour, day or month the next period's first tokens are no longer priced into the window that is closing (they were, and the max() merge kept the over-count for the row's whole life, so an hourly cap was enforced against the next hour's usage); the policy view drops a boost that expires between its two clock reads instead of answering 500; the E2E rate-limit and rule listings reuse agentcore_wait.list_all/get_or_none, which guard against a repeated paging token; terraform/dashboard.tf no longer admits .map in the UI file regex and then excludes it. Declined: double-decoding of path segments in ALB mode (verified: the ALB hands over the path as sent, and only query values arrive encoded, which route() already decodes); MODEL# rollup rows splitting when a model leaves the price map mid-month (an unpriced model has no ow/ alias by design, and the interceptor refuses its name from then on); replacing the hand-written ES256 check with the cryptography wheel (the key comes from the fixed regional ALB key host over TLS and is checked on-curve; a native dependency in the layer for no behaviour change); cleaning ORG and GROUP policy once per /users request (resolve() stays the single entry point, as decided in r110); one needed no change. Dashboard and usage-sync Lambdas updated in place (2 changed, follow-up plan: no changes), dashboard 8/8, web search 15/15, opencode F2 21/21, checkout unchanged across the E2E run |
| 126 | b4c8cba | 0 / 0 / 803 | 179 / 0 | exit 0, 0 actionable | not run | **not completed** (two Bedrock 503s, then the third run stopped its own subagents at the background-wait ceiling with the dashboard-auth and interceptor areas unfinished; r127 reviews the fixed tree with that ceiling disabled) | 9 findings (none high), all 9 addressed: the F8 method-override scenario was vacuous (its driver accepted the exact status its scenario called a failure, and `status_not` was never read by the runner) and now checks the side effect; /logout is served before the dashboard's group check, so a signed-in user in neither group can clear the ALB session; on an Okta or JumpCloud gateway an empty identity_map now names nobody instead of falling back to the raw claim; a failed token write no longer leaves a temp file holding the refresh token, and `ocgw logout` removes one an interrupted write left behind; the proxy prints the port it bound (proxy_port 0 lets the OS choose) and `opencode-config` refuses port 0; a non-GET/HEAD request to / gets 405, not a 200 "UI is not built"; dashboard/proxy.py answers HEAD without a body (RFC 9110) and applies its DNS-rebinding Host check to every method; overview() walks the month's day rows once. 27 new unit tests, including the first coverage of es256_verify's rejection paths - the dashboard's only authorization gate. Deployed in place (dashboard, interceptor; follow-up plan: no changes), dashboard 8/8, proxy live 200/200/200/401, first full Cognito matrix run (row above), checkout unchanged across the E2E run |
| 127 | 5e07f7a | 0 / 0 / 932 | 179 / 0 | first run 2 actionable (detect-secrets: two P-256 key headers in a test, now taken from a real key), rerun 0 actionable; Grype errors inside ASH (a plugin bug) and run directly finds no vulnerabilities | first scan 45 (25 high, all in tests); after fixes 0 high / 0 medium / 14 low (fresh project, CSR rubric + CSR baseline; one documented exclusion, the bundled React file) | 0 findings (completed; the background-wait ceiling that cut r126 short is off) | 6 findings (none high), all 6 fixed: the client has one writer and one reader for the session, so a copy kept in memory after a failed save is replaced by every later write and never wins over a tokens.json another process wrote (an `ocgw login` in another shell); HEAD on /api/v2 is 405, not a CSRF refusal logged as a write; the user page sums each day row once; price_key's cache lost a test-only identity check. Deployed in place (dashboard, usage_sync), dashboard 8/8, gateway rate-limit slice 4/4 with the larger burst, client and opencode slices pass |
| 192 | aaf0b7f | 0 / 0 / 1,140 | 179 / 0 | all scanners passed; Grype errors inside ASH | 0 critical / 0 high / 0 medium / 14 low | not recorded in the round 192 scan artefacts | not recorded in the round 192 scan artefacts |

Round 192 also ran a static security analysis scan (0 critical; 27 high and 6 medium, each triaged: 22 license notes on generated bundles, 2 secret false positives, the picomatch advisory, and IAM and dependency items kept in the [security.md](security.md) r189 to r191 rows), detect-secrets (0 findings), `npm audit` (0 in production dependencies; over all dependencies one advisory, picomatch GHSA-fc5q-39xc-vjpf in dev dependencies, accepted in the security.md r191 row) and `pip-audit` (no known vulnerabilities). Rounds 128 and 129 have rows only in [security.md](security.md); rounds 130 to 191, 193 and 194 have no scan row on either page.

Run ASH from the repository root with `ash --source-dir . --mode local`. With an absolute source path that differs
from the resolved one (for example through a symlinked home directory), ASH reports paths that the `ignore_paths` in
`.ash/.ash.yaml` do not match, and the minified dashboard bundle is reported as high-entropy strings.
