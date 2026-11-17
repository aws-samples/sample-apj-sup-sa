# Cross-Account Migration Setup

This guide has been replaced by the README:

- [Create the target role in the target account](../README.md#3-create-the-target-role-in-the-target-account): deploy `cloudformation/target-role.yaml` with `scripts/deploy-target-role.sh`. It creates `ConnectMigrationTargetWriter`, trusted only by the tool's `ConnectMigrationSourceReader` role, with a required `sts:ExternalId`.
- [Minimum IAM permissions](../README.md#minimum-iam-permissions): the exact policies of both roles. Do not attach `AmazonConnectFullAccess` or `connect:*`.
- [Cross account and cross Region](../README.md#cross-account-and-cross-region): what is rewritten automatically and what needs manual follow up (phone numbers, Lambda functions, Lex bots).

Earlier versions of this page told you to trust `role/connect-migration-functions-role-prod`. That role name never existed (the old execution role had a generated name); the role is now named `ConnectMigrationSourceReader` and is the `LambdaRoleArn` output of the Lambda stack.
