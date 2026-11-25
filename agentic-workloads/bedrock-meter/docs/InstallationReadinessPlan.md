[English](InstallationReadinessPlan.md) | [한국어](InstallationReadinessPlan.ko.md)

# First-installation readiness design

> Status: Code implementation and macOS local verification complete. Fresh AWS installation and Linux execution verification remain pending.
> Review baseline: November 25, 2026.
> Scope: Minimum changes for a new user to follow the README and see their first usage.

## 1. Goal and evidence baseline

The first release must support one working path: **clean checkout → deployment → administrator
creation → login → usage visibility**. Keep the seven review findings without expanding the work
into a general deployment tool or automatic recovery system. Current instructions are in the
[README](../README.md).

The earlier review passed both `npm ci` runs, the console artifact build, TypeScript compilation,
CDK synthesis, and 111 tests on macOS with Node.js 22.22.3 and Python 3.14.2. A clean copy reproduced
the bootstrap ordering error; mocked AWS calls reproduced region propagation failures; a production
build reproduced the development API host leak. These are pre-change results, not proof of a
successful fresh AWS installation or login. Reinstallation conflicts were identified through code
review; live recovery verification remains separate work.

## 2. First-release scope

| Finding | Implement now |
|---|---|
| 1. Bootstrap ordering | Build the real console artifact before bootstrap and deployment. |
| 2. Deployment region | Resolve the terminal/profile AWS target and propagate the same region. Remove backfill from normal installation. |
| 3. First administrator | Resolve the pool and create the user with the deployment's configuration. |
| 4. Production API host | Force same-origin in production; honor the override only in development. |
| 5. Dependencies | Verify compatible versions and lockfiles; address important vulnerabilities. |
| 6. Runtime | Align a supported Node.js runtime and build environment. |
| 7. Failure and retry | Identify the failed stage and cause; provide manual recovery instructions. No automatic deletion. |

Do not change quotas, metering, IAM enforcement semantics, data models, resource names, or removal
policies. Do not add new `--account`, `--region`, `--profile`, or `--stack` options or a general
configuration parser to deployment/user-management scripts. Manual backfill also uses the current AWS target.

## 3. Finding 1: Installation order

Use `scripts/deploy-all.sh --bootstrap` for first deployment. The command without the flag remains
the redeployment path. Both implemented paths are documented in the README.

Execution order:

1. Check required tools/versions and resolve the AWS account/region from the terminal/profile. Load project settings without changing that target.
2. Run `npm ci` in both projects and build the static console and BFF once.
3. With `--bootstrap`, run CDK bootstrap for the resolved account and region.
4. Deploy the stack to the same target and validate outputs.
5. Print the console URL, administrator-creation command, and SNS email confirmation step.

Keep the missing-artifact guard. Build failure stops before AWS changes; bootstrap failure prevents
deployment. Do not implement a separate bootstrap compatibility checker. Preserve CDK errors and
append the failed stage and a pointer to the relevant instructions.

The integrated path replaces separate bootstrap instructions and duplicate builds. Developers using
direct CDK commands must still build the console artifact first.

**Change targets:** [`deploy-all.sh`](../scripts/deploy-all.sh), both READMEs and
[CDK README](../cdk/README.md) translations.

## 4. Findings 2 and 3: One configuration and deployment target

### AWS target and project settings

Do not ask users to enter the account and region again in a project file. `deploy-all.sh` and
`_add-console-user.sh` share a small function that resolves the current terminal's AWS target.

- **Account:** Call `aws sts get-caller-identity` with the current credentials to determine the actual
  account. Do not treat `CDK_DEFAULT_ACCOUNT` as user input.
- **Region:** Resolve non-empty `AWS_REGION`, then `AWS_DEFAULT_REGION`, then the region in the
  currently selected AWS profile. If neither environment variable supplies a region, query the
  profile's region using the same AWS CLI configuration. Do not parse profile files directly.
- **Missing configuration/errors:** Stop with configuration guidance if no region is available;
  do not default to Seoul. Expired credentials, an unusable profile, or STS failure also stop before changes.
- **Credentials:** Preserve the terminal's AWS credential configuration, including `AWS_PROFILE`.
  Do not introduce another credential selector or cross-account deployment feature. A profile name
  alone does not establish the caller account.
- **Consistency:** Resolve and display the account and region at startup. Use the same target for
  bootstrap and deployment, and explicitly pass the resolved `--region` to regional AWS CLI commands.
  Set the CDK process's `CDK_DEFAULT_ACCOUNT` and `CDK_DEFAULT_REGION` internally from those results;
  pre-existing external values must not select a different target.

Keep only project settings in `.env.deploy.example` and `.env.deploy.local`: administrator emails
and usernames, quota timezone, and feature flags. Do not configure account, region, AWS credentials,
or profile there. Preserve `BEDROCK_METER_DEPLOY_ENV_FILE` and environment-only operation. Continue
treating the file as trusted local shell configuration without adding another configuration format.

Remove `CDK_DEFAULT_ACCOUNT` and `CDK_DEFAULT_REGION` from the example and document removing account/region
entries from existing files in favor of terminal/profile settings. Project files must not override
AWS credentials, profile, or region. Legacy `CDK_DEFAULT_*` inputs and the administrator script's
`REGION` input no longer select the deployment target.

Each command uses the terminal configuration at invocation time. Later administrator creation or
redeployment from another terminal requires selecting the same AWS account and region. Ask users to
check the displayed target; do not silently switch credentials or regions using local output files.

### First administrator

Keep the admin/viewer wrappers and hidden password prompt. Resolve the pool from the actual stack
in the same account and region. Remove fallback to stale `cdk-outputs.json` when lookup fails.
Keep explicit `POOL_ID` input, checking the pool's account and region before use. Pool lookup failure
or region mismatch must stop before user changes.

### Retention backfill

Remove the Python script that extends existing records' retention from deployment; run it separately
only when changing retention policy. Use current AWS credentials and the region precedence above,
stopping if no region is configured. Document checking account, region, and target tables before
running it; redeployment alone does not extend existing rows' retention. A new shell wrapper, duplicate account-validation framework, and concurrent
update retries are outside this installation change.

**Change targets:** [`deploy-all.sh`](../scripts/deploy-all.sh),
[`_add-console-user.sh`](../scripts/_add-console-user.sh), a small shared configuration helper,
[`backfill-retention.py`](../scripts/backfill-retention.py), configuration examples, docs, and tests.

## 5. Finding 4: Production API host

In [`api.ts`](../client/app/src/lib/api.ts), force `/api/console` in production and read
`NEXT_PUBLIC_CONSOLE_API_BASE` only in development. Configuration discovery uses the same API base,
so production remains same-origin while development retains its existing remote BFF connection.

Do not rely solely on unsetting a variable in the deployment shell. Production artifacts must
exclude the development host even with `.env.local` present or a direct package build. Verify builds
with a process-variable override and a `.env.local` override separately.

**Change targets:** `src/lib/api.ts`, targeted tests, and both [console README](../client/app/README.md)
translations.

## 6. Findings 5 and 6: Dependencies and runtime

Update manifests and lockfiles together and install with `npm ci`. Do not force audit fixes or bypass
peer-dependency checks. Start with compatible patch/minor updates and verify necessary major updates
separately through builds and tests. Select exact versions from official releases and advisories at
implementation time.

Fix high/critical findings or record why they do not apply. Distinguish deployed static UI/BFF from
development servers and build tools without dismissing development risks. Record reasons and follow-up
actions for unresolved findings when deciding release readiness. Audit failure does not mean zero
vulnerabilities. Do not introduce a separate approval system or lengthy exception records for every dependency.

Target Node.js 24 for the console Lambda, esbuild, development/verification environment, and tool-version
instructions. Recheck [AWS runtime support](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html)
at implementation time. Leave Python Lambdas unchanged. Preserve Lambda construct IDs and update the
runtime in place. Verify the BFF's runtime-provided AWS SDK dependencies through deployed authentication
and Core API calls.

**Change targets:** Both `package.json` files and lockfiles,
[`console-hosting.ts`](../cdk/lib/constructs/console-hosting.ts),
[`build.mjs`](../client/app/bff-lambda/build.mjs), tool-version instructions, and verification configuration.

## 7. Finding 7: Failure messages and manual recovery

Limit preflight to required tools, credentials/target account, and obvious stack-state retry blockers.
Stop with appropriate guidance when a stack operation is in progress or its state is `ROLLBACK_COMPLETE`.
Do not walk all resources to classify ownership, orphans, or logging-target relationships before deployment.
Leave remaining deployment decisions to CDK and the existing custom resource; preserve actual errors.
Do not interpret a failed lookup as stack absence.

Add diagnostic commands and manual steps to [OperationalGuide](OperationalGuide.md), linked from the README:

- Missing bootstrap or insufficient permissions: resolve the cause and rerun that stage.
- `ROLLBACK_COMPLETE`: inspect failure events → identify data to preserve → operator manually deletes
  the failed stack → inspect remaining resources → resolve conflicts before redeploying.
- Fixed-name resource or existing Bedrock logging conflict: establish the target, owner, and retention
  needs before operator action. Stop the deletion path if resources must be reused or retained.
- Administrator creation failure: check account, region, pool, and the original error before retrying
  the user-creation command.

Explain that stack deletion may leave retained resources. An operator decides how to handle a non-empty
bucket or existing logging configuration after reviewing impact and retention needs. Scripts do not
automatically delete resources or overwrite logging. Keep existing removal policies and logging Lambda
deletion behavior unchanged. This finding improves error messages and documentation, not recovery automation.

## 8. Delivery order and first-release acceptance

1. Make the bootstrap documentation correction and production API host fix independently.
2. Implement small shared AWS target resolution, project configuration loading, region propagation, administrator creation, and backfill separation.
3. Complete the `--bootstrap` path, error messages, and manual recovery instructions.
4. Verify dependency/runtime updates separately, then check the final code against these criteria.

| Required check | Acceptance |
|---|---|
| Clean local installation | Without existing configuration, dependencies, or generated files, the README tool versions pass `npm ci`, full builds, tests, and synthesis. Verify each OS claimed as supported. |
| Target consistency | Environment-only and profile-only region configurations give bootstrap, deployment, and administrator creation the same target. Verify `AWS_REGION` precedence, stopping on missing region or invalid credentials, and that legacy `CDK_DEFAULT_*` inputs cannot change the target. |
| Live first installation | Follow the documentation through bootstrap → deployment → first administrator → login in Seoul and a non-Seoul region. Do not rely on previous deployment artifacts or pools. |
| Functionality | A controlled Bedrock call appears under the expected user; confirm SNS subscription, admin/viewer permissions, and basic analytics. |
| Redeployment | Preserve users/data, skip backfill, and keep production API calls same-origin despite leftover development settings. |
| Failure guidance | Mocked commands verify stage failures, failed AWS target resolution, and pool-target mismatch handling. Check recovery commands, targets, and retention instructions against the actual code. |
| Dependencies/runtime | Review important audit findings and verify deployed authentication/API calls on the declared runtime. |

Test AWS accounts may be reused. A clean target independent of a previous deployment is required, not
a newly created account for each run. Avoid the fixed BFF IAM role-name collision by using separate test
accounts or testing sequentially after cleaning up the prior installation in the same account. Cleanup is
operator-controlled; test procedures must not automatically delete production data.

Briefly record the tested revision, tool versions, account/region, and results. A complete failure-injection
matrix and every reinstallation exception are not first-release gates. Document known limitations.
Both READMEs should present one primary installation path; keep recovery and manual backfill in operations docs.

### Implementation verification (November 25, 2026)

- Verified on macOS with Node.js 24.22.3, npm 11.21.3, and Python 3.14.2.
- A fresh copy of Git-visible source passed both `npm ci` runs, the full console build, and CDK build/synthesis.
- **129 tests passed:** 74 Python, 12 CDK, 13 console, and 30 installation/account-script tests.
- Development API hosts supplied through the process environment and `.env.local` were absent from production exports.
- Locally exercised the built BFF: seven pages, a JS asset, runtime config, and a 401 response for an anonymous data request.
- Comparison with the earlier synthesis showed no added/removed resource logical IDs or removal-policy changes.
- Console audit: zero findings. CDK: zero high/critical, 18 moderate findings in glob/minimatch and their tool dependency paths,
  recorded for follow-up dependency updates. Both projects passed `npm audit --audit-level=high`.
- Live AWS deployment, login, Bedrock invocation, and SNS delivery remain **unverified**. The terminal target contains an
  existing stack, which was not changed; a separate first-installation verification target is pending.
- Linux execution remains unverified because the local Docker daemon was unavailable.

These results verify the local installation path, not the live AWS acceptance criteria above.

## 9. Follow-up work

Design these separately when operational needs justify them; they do not block the first release:

- Multiple stacks, cross-account/custom bootstrap support, and additional CLI options.
- Exhaustive resource preflight and detailed failure/reinstallation test matrices.
- A retention-migration wrapper, retries, and progress tracking.
- Expanded CI for recurring verification. The results above are required for release; building a new CI system is not.

Automatic recovery, automatic deletion, and retention-policy changes are not promised follow-up work;
they require a separate request and design.
