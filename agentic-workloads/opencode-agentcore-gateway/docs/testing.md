# Testing

## Unit tests (no AWS account needed)

```bash
uv run --no-project --with-requirements tests/requirements.txt \
  python -m pytest -q tests/unit
```

They cover the interceptor's decisions (routes, identity, allow-list, bodies, request normalisation, budgets, rpm, model
listing, web search gate, fail closed), the usage sync's pricing and rollups, the policy resolver, the dashboard API
and the `ocgw` client and proxy.

## End-to-end tests (a deployed stack)

`tests/e2e/scenarios.py` defines the scenario matrix; `tests/e2e/run.py` runs it as real users against the deployed
gateway, the web search gateway, the local `ocgw` proxy, the dashboard and the opencode CLI, and writes
`tests/e2e/out/results.json`.

```bash
python3 tests/e2e/run.py --dry-run                 # how many scenarios are selected
python3 tests/e2e/run.py --idp cognito --only F3.  # one area, one IdP
python3 tests/e2e/run.py                           # everything
```

The suite spends real money as its test users, and the spend counts against their monthly budget. On a stack you
test often, raise `default_budget_usd` in your `terraform.tfvars` (the round 187 stack set 500): after about 180
rounds in one month bob reached the $50 default, and every later call as bob was refused with a budget 429.

For Okta and JumpCloud, switch the stack to that IdP first (`idp`, the OIDC settings and `identity_map`, as in
`terraform/okta.tfvars.example`), then sign each test user in once with `ocgw login` in a home of its own and point
the runner at it: `OCGW_HOME_<USER>_<IDP>=/path/to/that/home/.config/ocgw` for alice, bob and nogroup (for example
`OCGW_HOME_ALICE_OKTA`), and `OCGW_HOME_ALICE_<IDP>_CLIENT` for a second sign-in of alice, which the token refresh
and logout cases spend. The runner refreshes each home with `ocgw token`, so a run longer than the ID token's hour
keeps working, and never copies a refresh token (both IdPs rotate it). The `F1.groups` cases change a user's groups in
the IdP and are driven on Cognito only; on Okta and JumpCloud they are reported `no_driver`. Budget and rate-limit
scenarios write to the ledger table and restore it afterwards, so run them against a test stack, not production.

A scenario whose kind has no driver yet is reported as `no_driver`, never as a pass.

Some scenarios cannot be driven against a live stack at all, and say why in their result:

- `F7.throttle`, `F7.5xx` and `F7.unreachable` need the primary model to throttle, fail or be unreachable on demand,
  which Bedrock does not offer. The gateway rule's `triggerOn` list and the unit tests cover those paths;
  `F7.first_byte_timeout` and `F7.model_missing` run live, by lowering the rule's timeout and naming a missing model.
- `F12.destroy_clean` tears the stack down, so the account owner runs it last, by hand, as the README's Clean up
  section describes.
- Scenarios on the `/v1/responses` route are reported `n/a`: no open-weight model on Bedrock serves that API (each
  answers "does not support the '/v1/responses' API"). They start running by themselves if Bedrock adds the route.
- The gateway's own rate limit is a refilling bucket enforced across its fleet. AgentCore documents that a change
  reaches it within 30 seconds, but after an edit the limit has let every call through for minutes with the rule
  ACTIVE (round 183: a limit of one a minute bit only in the third minute, and a limit of three set right after it never
  bit in three). So `F6.gateway_rpm` tests carol's standing limit as Terraform deployed it (`rpm_overrides`, two a
  minute) with bursts of 12 and 16, and edits nothing. The gateway's buckets are spread over its fleet, so a burst must be well
  past the limit (r186: 8 calls at two a minute all passed in one minute), and only a 429 naming the scenario's own
  metric (`requests` or `tokens`) counts. `F6.gateway_tpm` lowers carol's tokens per minute on the live
  rule, waits 35 seconds and sends the limit plus six; it has been enforced in the first minute every run. A burst with
  no 429 is repeated in each of the next two minutes, and a limit that never bites in three minutes still fails.
- The `F2.task.*` scenarios check what the model did with real opencode tools, so the model's exactness is in the
  result too. One run's edit wrote `GREETING = \"OCGW-EDIT-OK\"`, backslashes included, although the gateway
  answered every step. A session whose steps all succeeded but whose result is wrong therefore runs once more in a
  fresh folder, and both attempts are reported. A step the gateway refused or failed is never retried. Every model
  reply of these sessions is kept beside the session's folder, in `<folder>-replies/` (a recording hop on 127.0.0.1 between opencode
  and the user's `ocgw` proxy, set with `OPENCODE_CONFIG_CONTENT`), and a failed attempt's result lists how each
  reply finished and where its output went.
- Either retry is visible: the scenario's line reads `pass ... (after a retry)`, the summary line counts `retried`,
  and its entry in `results.json` has `"retried": true` with every attempt. A gateway defect that shows up only now
  and then therefore cannot pass silently; check any retried scenario before calling a round clean.

### How a reply is judged

A chat scenario passes on the reply's structure, never on a substring of the raw body. `reply_of` in
`tests/e2e/run.py` reads a whole JSON reply (chat `choices[].message` content and `tool_calls`; a Responses body's
`message` items' `output_text` parts and `function_call` items, never reasoning) or, failing that, a server-sent-event
stream wherever it starts, joining every chunk's text first (a model may split a word across chunks). With a tool
offered, an answer in words or a tool call passes (a model has answered a made-up time instead of PONG). Without one
the answer must say PONG, the sign that the prompt reached the model intact; a model that once answers otherwise
(one said "PING"), or that answers only in its reasoning with no content and no tool call (one put "PONG" there),
is asked once more, counted as retried, and the scenario fails if it misses again. An `F2.opencode.*` session
follows the same rule: one that exited cleanly but whose model answered without PONG (one said "No, I'm sorry, but
I can't do that.") runs once more. Only a 200 is retried: a refusal
or an error from the gateway fails at once. The result keeps the answer
it was judged on, so a failure shows what the model said. A row in test-results.md counts the full run plus the
reruns noted under it; "reruns on rN" names the round those reruns ran on.

## Scenario matrix

Regenerate this table with `python3 tests/e2e/make_docs.py` after changing `scenarios.py`.

<!-- matrix:start -->
| Area | Subfeature | Kind | Cognito | Okta | JumpCloud | Total |
|---|---|---|---:|---:|---:|---:|
| Identity and tokens | `F1.groups.group_removed_in_idp` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.groups.groups_claim_list` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.groups.groups_claim_string` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.groups.no_groups_claim` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.groups.two_groups_union` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.groups.unknown_group` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.groups.user_disabled_in_idp` | http | 1 | 1 | 1 | 3 |
| Identity and tokens | `F1.token.basic_auth` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.duplicate_auth_header` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.expired` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.garbage` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.none` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.other_pool_user` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.tampered_sig` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.wrong_aud` | http | 3 | 3 | 3 | 9 |
| Identity and tokens | `F1.token.wrong_iss` | http | 3 | 3 | 3 | 9 |
| Client, proxy and opencode | `F2.client.login_pkce` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.logout_then_token` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.mdm_overrides_env` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.opencode_config_models` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.proxy_path_denied` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.proxy_stream` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.proxy_wrong_key` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.rotated_refresh` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.token_refresh` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.client.two_proxies_same_port` | proxy | 1 | 1 | 1 | 3 |
| Client, proxy and opencode | `F2.opencode.deepseek-v3.2` | opencode | 2 | 2 | 2 | 6 |
| Client, proxy and opencode | `F2.opencode.glm-5` | opencode | 2 | 2 | 2 | 6 |
| Client, proxy and opencode | `F2.opencode.glm-5.3` | opencode | 2 | 2 | 2 | 6 |
| Client, proxy and opencode | `F2.opencode.kimi-k3` | opencode | 2 | 2 | 2 | 6 |
| Client, proxy and opencode | `F2.task.compact` | opencode | 1 | 0 | 0 | 1 |
| Client, proxy and opencode | `F2.task.multi_tool_edit` | opencode | 1 | 0 | 0 | 1 |
| Client, proxy and opencode | `F2.task.run_shell` | opencode | 1 | 0 | 0 | 1 |
| Client, proxy and opencode | `F2.task.switch_model_mid_session` | opencode | 1 | 0 | 0 | 1 |
| Client, proxy and opencode | `F2.task.write_file` | opencode | 1 | 0 | 0 | 1 |
| Model calls | `F3.deepseek-v3.2.chat.once` | http | 6 | 6 | 6 | 18 |
| Model calls | `F3.deepseek-v3.2.chat.stream` | http | 6 | 6 | 6 | 18 |
| Model calls | `F3.glm-5.3.chat.once` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.glm-5.3.chat.stream` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.glm-5.3.responses.once` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.glm-5.3.responses.stream` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.glm-5.chat.once` | http | 6 | 6 | 6 | 18 |
| Model calls | `F3.glm-5.chat.stream` | http | 6 | 6 | 6 | 18 |
| Model calls | `F3.kimi-k3.chat.once` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.kimi-k3.chat.stream` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.kimi-k3.responses.once` | http | 12 | 12 | 12 | 36 |
| Model calls | `F3.kimi-k3.responses.stream` | http | 12 | 12 | 12 | 36 |
| Model allow-list | `F4.deny.deepseek-v3.2` | http | 12 | 12 | 12 | 36 |
| Model allow-list | `F4.deny.glm-5` | http | 12 | 12 | 12 | 36 |
| Model allow-list | `F4.model.empty` | http | 2 | 2 | 2 | 6 |
| Model allow-list | `F4.model.gpt-4o` | http | 2 | 2 | 2 | 6 |
| Model allow-list | `F4.model.kimi` | http | 2 | 2 | 2 | 6 |
| Model allow-list | `F4.model.ow_kimi-k3` | http | 4 | 4 | 4 | 12 |
| Model allow-list | `F4.model.ow_unknown` | http | 2 | 2 | 2 | 6 |
| Budgets | `F5.daily.at` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.daily.over` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.daily.under` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.hourly.at` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.hourly.over` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.hourly.under` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.monthly.at` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.monthly.over` | http | 3 | 3 | 3 | 9 |
| Budgets | `F5.monthly.under` | http | 3 | 3 | 3 | 9 |
| Rate limits | `F6.gateway_rpm` | http | 2 | 0 | 0 | 2 |
| Rate limits | `F6.gateway_tpm` | http | 2 | 0 | 0 | 2 |
| Rate limits | `F6.rpm.1` | http | 3 | 3 | 3 | 9 |
| Rate limits | `F6.rpm.2` | http | 3 | 3 | 3 | 9 |
| Rate limits | `F6.rpm.5` | http | 3 | 3 | 3 | 9 |
| Fallback | `F7.5xx` | http | 2 | 2 | 2 | 6 |
| Fallback | `F7.first_byte_timeout` | http | 2 | 2 | 2 | 6 |
| Fallback | `F7.model_missing` | http | 2 | 2 | 2 | 6 |
| Fallback | `F7.throttle` | http | 2 | 2 | 2 | 6 |
| Fallback | `F7.unreachable` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.1e999` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.deep_nesting` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.dot_segments` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.double_encoded_path` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.dup_model_key` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.encoded_path` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.get_on_chat` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.huge_body_6mb` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.infinity` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.invoke_path` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.json_array` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.model_int` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.model_list` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.model_null` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.nan` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.not_json` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.previous_response_id_of_other_user` | http | 1 | 1 | 1 | 3 |
| Request hardening | `F8.spoof_openai_project` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.spoof_x_ocgw_user` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.unknown_route` | http | 2 | 2 | 2 | 6 |
| Request hardening | `F8.x_http_method_override_delete` | http | 1 | 1 | 1 | 3 |
| Web search | `F9.helper.empty` | websearch | 3 | 3 | 3 | 9 |
| Web search | `F9.helper.ok` | websearch | 3 | 3 | 3 | 9 |
| Web search | `F9.query.empty` | mcp | 3 | 3 | 3 | 9 |
| Web search | `F9.query.ok` | mcp | 3 | 3 | 3 | 9 |
| Web search | `F9.query.too_long` | mcp | 3 | 3 | 3 | 9 |
| Attribution | `F10.glm-5.3` | http | 2 | 2 | 2 | 6 |
| Attribution | `F10.kimi-k3` | http | 2 | 2 | 2 | 6 |
| Dashboard | `F11.action.allow_model` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.block_message` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.concurrent_edit` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.deny_model` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.double_click_save` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.group_with_space` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.invalid_number` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.repair_readable_record` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.repair_record` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.set_group_budget` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.set_rpm` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.action.set_user_budget` | dashboard | 1 | 0 | 0 | 1 |
| Dashboard | `F11.page.audit` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.budgets` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.groups` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.model_access` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.overview` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.rate_limits` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.usage` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.user_detail` | dashboard | 3 | 3 | 3 | 9 |
| Dashboard | `F11.page.users` | dashboard | 3 | 3 | 3 | 9 |
| Infrastructure | `F12.ddb_pitr` | terraform | 1 | 0 | 0 | 1 |
| Infrastructure | `F12.destroy_clean` | terraform | 1 | 0 | 0 | 1 |
| Infrastructure | `F12.iam_least_privilege` | terraform | 1 | 0 | 0 | 1 |
| Infrastructure | `F12.kms_on_logs` | terraform | 1 | 0 | 0 | 1 |
| Infrastructure | `F12.lambda_tracing` | terraform | 1 | 0 | 0 | 1 |
| Infrastructure | `F12.plan_no_changes` | terraform | 1 | 0 | 0 | 1 |
| | **128 subfeatures** | | | | | **1047** |
<!-- matrix:end -->

## Live results

Results of each full run are summarised in [docs/test-results.md](test-results.md) (pass, fail and no-driver counts
per area, the commit tested, the date and the IdP).
