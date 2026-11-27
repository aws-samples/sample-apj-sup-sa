# Changelog

Every release has a **Changed permissions** and a **Replaced resources** section, because
[Upgrading](docs/implementation-guide.md#upgrading) step 1 tells operators to read them before they
run `deploy.sh`: a permission change is what a reviewer must re-approve, and a replaced resource is
what loses data or ends sessions.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are the
`SOLUTION_VERSION` in `infra/insights_radar_infra/config.py`.

## [1.1.0] - Unreleased

The stack description and the `DeployOptions` output (`solutionVersion`) say which release a stack
runs (it said v1.0.0 after every upgrade so far).

### Changed permissions

- The `Finalize` function gets read/write on the graph table (it had none), to remove the entities a
  run that ended early left unlinked; the `Sweep` function's graph grant goes from read to read/write,
  and it gets `s3:DeleteObject` on `drafts/*` (never an object version), to finish the deletes an
  administrator queued.
- The API function gets `states:StopExecution` on the run state machine's executions, for an
  administrator's **Cancel** of a module run.
- Viewers can no longer ask a product's documents (`POST /api/products/{id}/ask`) or list them: both
  need the new `product.read` action (editors and administrators). Chat and the MCP server's
  `ask_product` follow the same rule, because they call the API as the user.

- The API handler, the documentation crawler and the scheduled sweep are explicitly denied
  `s3:DeleteObjectVersion` and `s3:DeleteObjectVersionTagging` on the prefixes they write, so
  bucket versioning stays a recovery path for a deleted document.
- The sweep gets `s3:GetObject`/`s3:PutObject` on `radars/` instead of read/write, so it can add a
  radar version but not delete one.
- Both AgentCore runtime roles get `kms:Decrypt` and `kms:DescribeKey` on the data key, which
  applying a customer-managed-key-encrypted guardrail needs. The grant is its own policy
  (`GuardrailKeyAccess`), and the guardrail waits for it, so the runtimes can decrypt before the
  guardrail's key changes.
- The web-search gateway's service role gets encrypt/decrypt on the data key and
  `bedrock-agentcore:InvokeWebSearch`, in a policy of its own (`WebSearchGatewayAccess`) that the
  gateway and its target wait for, so CloudFormation deletes the gateway before the grant. With the
  grant in the role's default policy every gateway delete failed (rollback, `destroy.sh`, web
  search turned off), and the failed upgrades left an orphaned gateway behind (r3 P3-001). The
  data-key chat memory likewise waits for its role's key grant, on create and on delete.
- The `Seed` function gets read/write on the graph table (it was ops table only) and a 300-second
  timeout (was 30), for this release's one-time migrations of stored nodes and entity name keys.
- cdk-nag suppressions for COG8, CFR1, CFR4, APIG4 and SQS3 moved from the stack to the single
  resource each is about. No permission changed; a new unauthenticated route or a queue without a
  dead-letter queue is now a finding again.

- With a seed bundle (`AIR_SEED`), the `Seed` function gets read access to the bundle's asset
  object. With `authMode=federated`, a new pre-token-generation function (`AuthPreToken`) runs on
  every sign-in; its role may write its own log group and nothing else.
- Filtered and field-sorted list reads, and the new facets and export routes, count against the
  per-user graph rate limit like the other reads of up to 2,000 records.
- Only with the new `mcpIssuer`/`mcpAudience` options: an IAM OIDC provider (or the one
  `mcpProviderArn` names) and an MCP role assumable with that issuer's id tokens (`aud` must be
  `mcpAudience`, the session name the token's `sub`), allowed `execute-api:Invoke` on the API's
  `/iam/api/*` routes only. Off by default: no new permission without the options.

### Replaced resources

- **AgentCore Memory (chat history) is not replaced on an upgrade.** A new install encrypts it with
  the deployment's KMS data key (`chatMemoryKey=data-key`, memory `<prefix>_chat_k`). An existing
  deployment keeps its memory, name and AWS-owned key (`chatMemoryKey=aws-owned`): both
  `EncryptionKeyArn` and the name are create-only, and CloudFormation cannot replace a custom-named
  resource under the same name, so the earlier template failed every upgrade. To move an existing
  deployment's chat history to the data key, run `AIR_CHAT_MEMORY_KEY=data-key ./deployment/deploy.sh
  --diff --allow-data-loss`: CloudFormation creates `<prefix>_chat_k` and deletes `<prefix>_chat`,
  so **every user's chat history is deleted**. Insights, radars, evidence and documents are not
  affected. Tell users before you do it. Without `--allow-data-loss` deploy.sh refuses the change.
  A direct `cdk deploy` of an existing deployment must pass `-c chatMemoryKey=aws-owned`.
- **The AgentCore web-search gateway is replaced** (`<prefix>-web` becomes `<prefix>-web-k`, on the
  data key). CloudFormation lists the gateway's `KmsKeyArn` as updatable, but AgentCore refuses it
  ("KMS key cannot be updated for an existing gateway"), and the earlier template rolled back every
  upgrade of a deployment with web search at that resource. The gateway stores no data (its one
  target, the managed web-search connector, is in the template), so the new template gives it, and
  its target, new logical IDs: CloudFormation creates `<prefix>-web-k`, points the module runtime's
  `AIR_GATEWAY_URL` at it in the same update, and deletes `<prefix>-web` in the cleanup phase.
  No option is needed and nothing is lost; a module run still in progress loses web search when the
  old gateway goes, which is why [Upgrading](docs/implementation-guide.md#upgrading) step 3 turns on
  the kill switch first. A deployment without web search has no gateway and is not affected.
- **The Bedrock guardrail is updated in place**, not replaced: `KmsKeyArn` is an update without
  interruption in CloudFormation and Bedrock's `UpdateGuardrail` takes `kmsKeyId` (the failed
  upgrade's `UpdateGuardrail` call succeeded before the gateway rolled everything back). Its ID and
  published versions are kept.
- How each resource the data-key change touched behaves on an upgrade:

  | Resource | Key property | Changed in place? | What an existing deployment gets |
  |---|---|---|---|
  | AgentCore Memory (chat history) | `EncryptionKeyArn` | No: create-only | Kept as it is (`chatMemoryKey=aws-owned`); opt in with `AIR_CHAT_MEMORY_KEY=data-key --allow-data-loss` |
  | AgentCore Gateway (web search) | `KmsKeyArn` | No: AgentCore refuses it | Replaced by `<prefix>-web-k` on the data key; no data lost |
  | Bedrock guardrail | `KmsKeyArn` | Yes | Updated in place to the data key |
  | Step Functions state machine | `EncryptionConfiguration` | Yes (`UpdateStateMachine`) | Unchanged: stays on the AWS-owned key on purpose, because CDK's customer-managed path adds an unconditioned `delivery.logs` grant to the data key (P2-074) |
  | Step Functions activity | `EncryptionConfiguration` | No: an activity cannot be updated | None in this stack |
  | CloudWatch log groups | `KmsKeyId` | Yes (`AssociateKmsKey`) | Unchanged: already on the data key |

### Added

- MCP sign-in without a browser for federated deployments (deploy options `mcpIssuer`, `mcpAudience`,
  `mcpProviderArn`; MCP settings `AIR_MCP_WEB_IDENTITY_COMMAND`, `AIR_MCP_ROLE_ARN`): the MCP server
  exchanges an id token from the web app's identity provider for short-lived AWS credentials and
  signs its requests with SigV4 to new `/iam/api/*` routes, acting as the user's own account; the
  new stack output `McpApiUrl` is the `AIR_MCP_API_URL` to give MCP users
  ([MCP server](docs/implementation-guide.md#mcp-server)). `mcpIssuer` must be `oidcIssuer`: a token's `sub`
  names a user only at the issuer that set it, so the deploy refuses another issuer.

- `deploy.sh --env-file=FILE` reads a deployment's options (`AIR_*`, `AWS_PROFILE`, `AWS_REGION`) from a
  file of `export NAME=value` lines, so one deployment's settings live in one file; `deployment/check.sh` runs the CI checks locally.
- **What's new keeps everything**: a list cut at its first 20 records now says so, and the digest ends
  where it was cut, so marking it read keeps the rest for the next one; the read point never moves
  back. Watched records, ask answers and briefs read the newest links of each kind of record, and
  leave out content drafts. Only entities can be watched. A company's legal form (Corp, Inc, Ltd...)
  pairs names in the duplicates report only as the last word of an organization's name.
- **Watch and What's new**: watch graph records, then read what arrived since your last visit on
  the new What's new page or with MCP `whats_new` and `my_watches` (CTR subscriptions and digest,
  in the app instead of by email).
- **Research one record**: `GET /api/graph/nodes/{type}/{id}/brief` and MCP `research_entity`
  gather a record's newest evidence, linked trends and gaps, the radars placing it and its sources,
  with a coverage flag (CTR `research_blip`).
- **Ask the graph**: `POST /api/graph/ask` and MCP `ask_graph` return the records and sources that
  bear on a question, with a coverage flag (none, no sources, single source, sourced)
  ([implementation guide](docs/implementation-guide.md#asking-the-graph)).
- MCP: `radar_versions`, `radar_data_diff`, `radar_entry_history`, `get_radar_version` and `radar_tags`;
  with `AIR_MCP_RADAR_WRITES=true`, `import_radar_entries` (dry run first), `tag_radar_version` and
  `restore_radar_version`.
- **Entry history** in a radar entry's panel, with AI suggestions marked.
- **Version tags** on a radar's History, refused when they read alike; **Open** an older version
  read-only and **Copy link** to it by tag or number (`?tag=`, `?v=`).
- **Administration > Entities**: merge likely duplicate entities after a dry run, and delete unlinked
  ones (`GET /api/graph/hygiene`, `POST /api/graph/nodes/{id}/merge`)
  ([implementation guide](docs/implementation-guide.md#duplicate-and-unlinked-entities)).
- **Favourite radars**, first in the radar list, with **Only favourites** (`/api/me/favourites`).
- **Find an entry across radars** and **On radars** on a node's page (`GET /api/radars/entries`, MCP
  `find_radar_entries`).
- **Cancel a run**: administrators stop a queued or running module run from its Runs table; the run
  is recorded as cancelled and audited ([implementation guide](docs/implementation-guide.md#module-run-lifecycle)).
- Search answers with keyword matches when the embedding model or the vector index fails, and says
  so (`"degraded": true`); keyword candidates are read from the graph too, title words match by
  stem, and an entity name finds its other spellings.
- **Show closed gaps** and **Show retired ideas** switches on those lists (and `include_closed` on the
  MCP server's `list_insights`). The Content ideas module can retire an idea whose moment has passed.
- **Knowledge graph viewer** from the CoP Tech Radar: a canvas with zoom, pan and drag, detail by
  zoom level, typed and directed relationship colours, a legend that filters by type, hover cards,
  a details panel for nodes and links, a one-node view on double-click with paging through large
  neighbourhoods, and the whole picture as a keyboard-reachable list
  ([implementation guide](docs/implementation-guide.md#knowledge-graph)).
- `GET /api/graph/nodes/{id}/neighbors` takes `offset` (depth 1) and returns `total` and `offset`.
- **Reading position** of a module on its page, with **Read again from a date** for administrators
  ([implementation guide](docs/implementation-guide.md#reading-position)). The reset refuses a future date
  and a module with a run in progress.
- The assistant uses the deployment profile: the app's name, each list's own name and entry name, and it
  does not offer lists the deployment hides. `GET /api/chat/status` returns the public profile.
- A run pass that a guardrail block ends names the last tool call before it in the run summary.
- Dates in lists and on detail pages say how far away they are ("2026-11-01 (in 9 days)").
- **Tailoring a deployment** without a code change ([implementation guide](docs/implementation-guide.md#tailoring-a-deployment)):
  a deployment profile (app name, tagline, About text, list names, which lists show and in what
  order), typed fields per list (validated on write, described to the agent, filters and field
  sorts in the list, counts and upcoming dates on the Overview, CSV and Markdown export),
  `prompt_mode: replace` for modules whose job is not the built-in one, and seed bundles that
  apply all of it on every deploy without overwriting an administrator's edits.
- **Custom domain** as deploy options: `domainName` with `parentZoneId` (the stack makes its own
  hosted zone, one NS delegation and the certificate) or `certificateArn`. Upgrades keep it; the
  old manual steps are gone.
- **Federated sign-in** (`authMode=federated`): one OpenID Connect provider is the only way in, the
  client secret stays in Secrets Manager, administrators come from `adminEmail` and `adminEmails`,
  and no account or invitation email is created.

- 44 CloudWatch alarms in a us-east-1 deployment, 42 elsewhere, one fewer of each with `webSearch=false` (was 29): `WebSearchGatewayErrors`, `WorkspaceRateLimited`
  (the workspace-wide API rate ceiling refused calls), `Url5xxCount` and a 4xx rate for each agent
  tool gate's function URL, an HTTP API 4xx rate, `UserErrors`, inbound authorization failures and
  per-runtime `Sessions` for both AgentCore runtimes, the account's `ActiveSessionCount` against its
  session quota, and -- in a us-east-1 deployment -- WAF `BlockedRequests` and the CloudFront 5xx
  rate. The dashboard gained the tool-gate and edge widgets. Every alarm reads a metric under the
  namespace, name and dimensions its service publishes, which a test checks against recorded
  `list-metrics` output: three alarms of an earlier build of this release (`WafBlocked`, the
  `<Runtime>Sessions` and `<Runtime>AuthFailures` pairs) read metrics that do not exist and could
  never fire (r3 P3-004 to P3-006, P3-028).
- `chatMemoryDays` (`AIR_CHAT_MEMORY_DAYS`, 7-365, default 30): chat history retention. Lowering
  it on an existing deployment needs `--allow-data-loss`, because older history expires at once.
- `chatMemoryKey` (`AIR_CHAT_MEMORY_KEY`, `data-key` or `aws-owned`): how chat history is encrypted.
  An existing deployment keeps its own; changing it needs `--allow-data-loss` (see Replaced resources).
- `deploy.sh` passes and keeps `AIR_WAF_RATE_LIMIT` (`wafRateLimit`); it was documented but ignored.
- `workspaceRatePerMinute` (`AIR_WORKSPACE_RATE_PER_MINUTE`, 60-100,000, default 600): the
  deployment-wide per-minute ceiling for model-backed API calls. Administrators no longer count
  towards it, and reaching it raises the `WorkspaceRateLimited` alarm.
- `GET /api/insights/{kind}` is no longer a route of its own with the stage's 10 requests a second
  model-route throttle; it is served by `ANY /api/{proxy+}`. Its `?q=` relevance form is limited per
  user and per workspace in the function, and plain list pages no longer share one small limit.
- `CognitoDomain` stack output, for the MCP server's `AIR_COGNITO_DOMAIN`.
- `destroy.sh --delete-backups` and `purge.py apply <plan> --delete-backups`.
- `validate.sh --cleanup-only` and `AIR_VALIDATE_CLEANUP=false`, so a validation run can destroy
  with fresh credentials.
- `.github/workflows/pinned-tools.yml`: a weekly pip-audit of the tools pinned outside any lockfile.

### Changed

- **The MCP server is now TypeScript on Node.js 20 or later** (`mcp/`, package `insights-radar-mcp`
  2.0.0), replacing the Python package. Install it with npm (`cd mcp && npm ci --ignore-scripts &&
  npm run build && npm install --global .`) or run it with npx or `node mcp/dist/index.js`; uninstall
  the old one with `uv tool uninstall insights-radar-mcp`. The tools, their parameters and messages,
  the sign-in and the token file (`~/.config/insights-radar/tokens.json`) are unchanged, as is the
  configuration (`AIR_API_URL`, `AIR_MCP_CLIENT_ID`, `AIR_COGNITO_DOMAIN`, `AIR_MCP_RADAR_WRITES`),
  plus an optional `mcp/deployment.json` (or `AIR_MCP_CONFIG`) that gives one installation its
  name, token folder, default addresses and tool list. The MCP server reports its name as
  `insights-radar-mcp` (was `insights-radar`). `login --no-browser` prints the sign-in address
  instead of opening a browser.
- `entries` is a reserved slug (it is the address of the cross-radar entry search).
- The scheduler starts modules upstream first, one stage per sweep, and keeps at most 8 runs going
  across the deployment.
- A save by a module with the title of an output it already wrote adds its links to that output
  instead of creating a twin, and reopens a closed gap or a retired content idea.
- Entity names match their other spellings both ways ("Strands Agents", "Strands Agents SDK", "AWS
  Strands Agents" are one entity), in one batch read.
- A radar proposal is refused as a near-duplicate when its significant words, singular, are the same
  as an entry's or a proposal's, when one is a subset of two or more of the other's, or when they share
  half their words.
- A replacement prompt that does not use `${product}` still gets the module's product profile, and the
  writing rules say that lists, headings and Markdown are removed from descriptions.
- An MCP result over 60,000 characters is shortened (fewer list items, then shorter strings) so it
  stays valid JSON, with a `truncated` note, instead of being cut mid-object.
- Every resource is tagged `deployment=<prefix>` and `stack=<name>`; the data and run-token keys get
  `alias/<prefix>-data` and `alias/<prefix>-run-token`, and an edge stack's log key
  `alias/<prefix>-waf-logs-<region>`.
- The CloudWatch dashboard is named `<prefix>-insights-radar-<region>` (was `<prefix>-insights-radar`).
  Dashboard names are global per account, so the old name collided between Regions.
- The `RunTimeouts` metric namespace is `AgenticInsightsRadar/<prefix>` (was `AgenticInsightsRadar`).
- The optional AWS Budget is named `<prefix>-insights-radar-<region>` and filtered to
  `deployment=<prefix>`. **Activate `deployment` as a cost allocation tag or the budget reports 0.**
- Boolean deploy options accept only `true` or `false`. `0`, `1`, `no`, `yes` and `on` now stop the
  deploy instead of meaning different things for different options.
- `AIR_ALLOW_DATA_LOSS` is gone: use the `--allow-data-loss` flag. It is now also required to lower
  `logRetentionDays` or `agentLogRetentionDays`, or to turn `backupPlan` off.
- `suppressInvitation` cannot be changed on a live deployment.
- `deploy.sh --diff` diffs the edge stack and the main stack before deploying either, and stops when
  a diff fails. An upgrade needs no email argument.
- `deploy.sh` compares `AIR_CFN_EXECUTION_POLICIES` with the existing CDK bootstrap and stops on a
  mismatch, and refuses a bootstrap older than version 21 or in a failed state.
- `publish-site.sh` takes `--stack`, prints the account, and sets the same year-long immutable
  `Cache-Control` on hashed assets that the CDK deployment sets.
- The web app's dependencies are installed with `npm ci --ignore-scripts`.
- `npx` is no longer a prerequisite.

### Fixed

- A scheduled run holds only its slot's share of what is left of the module's day: the remainder divided
  by the slots still to come before UTC midnight (a run started by hand may still use all of it). A Scout
  run also stops between passes when the next pass would not fit in its hold, and says so; the sources it
  did not reach are read first by the next run. Before, an early run could spend the whole day and later
  slots were cut off part way or never started (daily check 2026-11-10: Scout every 4 hours, about
  2M weighted tokens a run, on an 8M day).
- A Scout run with `http_fetch` that had sources to read but read none of them (no fetch by source id, of its
  listed address or of an inbox item, ignoring host case, a trailing slash and a fragment), moved no cursor and wrote nothing is reported as failed (`NoSourceRead`), not succeeded, matching its own summary.
- The knowledge graph's hover card shows an entity's summary when its description is empty (topics and
  organisations written with a summary only showed a blank card).
- `deploy.sh` passes shellcheck again (`OPT_oidcIssuer` is read only through `eval`), and the backend
  passes `mypy --strict` again (the profile and seed except clauses, the tool-refusal reason).
- A module run no longer leaves the entities it created unlinked in the graph when it stops early
  (budget used up, cancelled, failed, turn limit) between creating them and linking them: the runtime
  reports the ids it created and named in no later write (`created_entities`), and finalize deletes
  each one that still has no link, is the module's own and was created during the run, name key
  included, with a `graph.delete_run_orphans` audit row (daily check 2026-11-10). A run that
  finished keeps what it created, and the delete is one conditional write, so a link made at the same
  moment keeps the entity. The sweep can now finish an administrator's queued delete of a node with many
  links (its graph grant was read-only) and of a content idea with drafts (delete on `drafts/*`) or with more links than one sweep removes. A retried
  finalize runs the cleanup again (not when it reports success), a name key that now resolves to another
  entity is left alone by every delete, a dangling draft link no longer stops the sweep, and every draft the
  sweep removes ahead of its idea is audited. `kg_link` refuses `has_draft` links: only `draft_save` makes
  them, since an idea's delete takes its drafts with it.
- A Scout pass no longer trips the guardrail's prompt-attack filter on its own run message: the pass
  line and the per-source notes are worded as statements ("THIS PASS ... covers these sources",
  "SOURCE NOTES"), and the rule to stay within the pass's sources moved to the Scout system prompt.
- A run summary leads with what the run did: the cursors it moved are one line at the end, not a
  line per source ahead of the pass text (security notes stay first).
- A pass a guardrail blocks names the last call of any kind before the block, bookkeeping calls
  included, or says the block came on the pass's own input.
- `oidcSignOutPrincipal` refuses an `amazonaws.com` service principal.
- A bolded link in an answer (`**[title](/x)**` or `[**title**](/x)`) renders as a bold link, not as
  Markdown text.
- Round 3: `monthlyBudgetUsd` creates a second, account-wide budget
  (`<prefix>-insights-radar-<region>-account`) that sees model spend, which carries no tag; the backup
  vault is kept on every delete, `retainData=false` included, and `purge.py` keeps the keys that
  orphaned vaults and on-demand table backups need; the Seed may run 900 s; a new
  `WebSearchGatewayErrors` alarm; `Cdn5xxRate` needs 50 requests; the gateway and chat-memory roles
  trust exactly this deployment's names; the web-search fallback never switches off web search on an
  existing deployment; `AIR_ALARM_EMAIL=admin` resets the alarm address; the web app builds without
  AWS credentials in its environment. Agents: the output guard reads links as a browser does, cites
  graph evidence, stamps `fetched` per page, and chat keeps only cited links live. See the round-3
  commits on `blue/platform-r3` for the full list.

- `deploy.sh` lists AgentCore gateways of this deployment that the stack no longer owns (a delete
  that failed leaves them behind) and stops before an update that would create a gateway with a
  leftover's name, which used to fail late with "A gateway with name ... already exists" and roll
  back. The implementation guide has the steps to clear one: [Clearing an orphaned web-search
  gateway](docs/implementation-guide.md#clearing-an-orphaned-web-search-gateway) (r3 P3-002).
- `destroy.sh` no longer stops silently when the stack cannot be deleted: it says the stack's status,
  that `--purge` deleted nothing, how to list the failed resources, the gateway steps above for a
  `DELETE_FAILED` web-search gateway, and where the purge plan is saved (r3 P3-014). With `--purge`
  it now also prints the plan's path before it deletes.
- `deploy.sh` stops before a deploy with `backupPlan=true` that would create the backup vault again
  while the vault kept from an earlier creation of the stack (backups turned off, or a retaining
  destroy) still exists. Its name is derived from the stack name, so that deploy failed late with
  AlreadyExists and rolled back; the message names the vault and how to remove it (r3 P3-010).
- `cdk synth` and `cdk deploy` no longer leave an empty file named `infra/=3.12`. `infra/cdk.json`
  ran `uv run --frozen --python >=3.12 python app.py` through a shell, which read `>=3.12` as a
  redirection: uv got `--python python` and the app's output went to a file named `=3.12`. The
  specifier is quoted now, and `infra/tests/test_repo.py` checks every command in `cdk.json`, the
  `package.json` scripts, the workflows and `deployment/*.sh` for a redirection to a version
  specifier, which shellcheck does not report.

- `destroy.sh --purge` no longer schedules the data key for deletion while a backup vault whose
  recovery points need it still exists, and it now also removes the us-east-1 edge stack's retained
  WAF log group and KMS key.
- A module run's published outputs no longer carry an address the run never read: the output guard
  defangs it and the run summary says how many were defanged.
- The AI kill switch and a closed budget now stop a run that only uses runtime-side tools, within a
  minute, and `http_fetch` is gated like the other runtime-side tools.
- `kg_recent` orders and filters by `updated_at`, the timestamp it shows a module and the one the
  prompts tell it to store as its cursor, so a module no longer skips what was created between two
  items, and an updated signal or trend is listed again for downstream modules. A whitespace-only
  update does not move `updated_at`. The `Seed` function re-keys existing nodes once.
- A module that held `product_knowledge` and lost it keeps its outputs from before this release
  hidden from outbound modules. New nodes are stamped private or public when they are written, and
  an unstamped node falls back to the module's sticky private-writer mark.
- The `Seed` function delivers one-time template changes to an existing deployment, each recorded
  per module and audited: Trends loses `web_search`, and Content drafts goes from 1 to 5 concurrent
  runs if it is still at 1. The record is not trusted alone: a change whose effect an upgrade's
  rollback undid (the previous release's seed restores the old grants) is applied again at the next
  deploy and audited as `template_changes_reapplied`, while an administrator's own edit that undoes
  it is recorded in `extra.template_overrides` and kept. The seed runs after every other resource of
  the stack, so a failure elsewhere no longer rolls code back over migrated data (r3 P3-003). A custom module stored with `content_draft` or `radar_suggest` and an
  outbound tool is refused at run start and on every tool call, and the seed turns it off.
- A run is charged at the model tier it started on, also when its module was deleted mid-run (that
  finalize used to fail and leave the run `running`). The runtime's budget guard spends at the same
  tier, and a run the backend already finalized stops at its next state check.
- Short query values (under 8 characters, such as `code=US`) are no longer scrubbed out of fetched
  pages, and a percent-encoded parameter name (`api%5Fkey`) is recognised as a credential.
- Outbound modules see only the host of each product documentation URL, and audit rows record a
  source or documentation address as its host plus a hash, so a credential in the path is not kept.
- A source PATCH writes only the fields it names, so it no longer rolls back the scout's cursor.
- An AgentCore invoke that timed out or got a 5xx is no longer finalized as cancelled (which released
  the hold of a run the runtime may have accepted); only the kill switch, a refused request or a 4xx
  answer does that.
- Autopilot corroboration counts a module's evidence only when the runtime recorded that the run
  fetched the cited page, and compares registrable domains, so one injected run cannot forge two
  sites. Evidence from before this release does not count.
- The output guard also scrubs evidence, entity and radar-suggestion text, matches any host suffix
  (except two-label file names such as `main.py`), IPv4 literals, `//host`, any scheme and the
  Unicode full stops browsers read as dots.
- A run that probes the names of entities it may not read gets the same refusal for every create
  after three matches, and each match is audited (`agent.private_name_match`).
- The source list tool spec and the scout prompt say only a source's host is shown.

## [1.0.0] - October 2026

Initial release.
