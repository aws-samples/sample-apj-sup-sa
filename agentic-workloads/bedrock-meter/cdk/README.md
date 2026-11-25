[English](README.md) | [한국어](README.ko.md)

# Bedrock Meter CDK

This CDK app deploys the Bedrock Meter core stack: Bedrock invocation logging, hot/cold usage aggregation, quota enforcement, alerting, and the SigV4-protected core API.

The `cdk.json` file tells the CDK Toolkit how to execute your app.

## Useful commands

* `npm run build`   compile typescript to js
* `npm run watch`   watch for changes and compile
* `npm run test`    perform the jest unit tests
* `npx cdk deploy BedrockMeterCoreStack`  deploy the core stack to your default AWS account/region
* `npx cdk diff`    compare deployed stack with current state
* `npx cdk synth`   emits the synthesized CloudFormation template

## Deployment environment

Use Node.js 24. From the project root, run `./scripts/deploy-all.sh --bootstrap` for first installation
and `./scripts/deploy-all.sh` for redeployment. The script builds the console before bootstrap/synth
can evaluate its artifact guard.

The account comes from STS using current credentials; the region comes from `AWS_REGION`, then
`AWS_DEFAULT_REGION`, then the selected AWS profile. Missing region stops execution. Keep only project
options (administrator usernames, email recipients, timezone) in `.env.deploy.local`, not account/region.
`CDK_DEFAULT_*` values are internal outputs of the script's target resolution passed to CDK.

Before using direct `npx cdk` commands, install console dependencies and run `build:console-artifact`.
Direct commands do not load `.env.deploy.local`; export any required project options in the terminal.
The commands above are developer/review commands after that preparation.

For a new ConfigTable, `BEDROCK_METER_QUOTA_TIME_ZONE` seeds the global quota calendar. When it
is omitted, `deploy-all.sh` detects the deployment host's IANA timezone and persists it in
`.env.deploy.local`. Existing runtime settings are never overwritten by CDK seed changes.

Bedrock invocation logging is an account/region singleton. A conflicting existing configuration
fails deployment unless overwrite is explicitly enabled; stack deletion retains it by default.

See the [project README](../README.md) for installation and the documentation index.
