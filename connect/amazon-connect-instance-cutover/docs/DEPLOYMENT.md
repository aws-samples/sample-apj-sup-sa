# Deployment Guide

This guide has been replaced by the [README](../README.md#deployment), which matches `scripts/deploy.sh`:

1. [Choose an ExternalId](../README.md#1-choose-an-externalid)
2. [Deploy the tool in the source account](../README.md#2-deploy-the-tool-in-the-source-account) with `scripts/deploy.sh`
3. [Create the target role in the target account](../README.md#3-create-the-target-role-in-the-target-account) with `scripts/deploy-target-role.sh`
4. [Sign in and migrate](../README.md#4-sign-in-and-migrate)

The earlier manual steps (editing `API_ENDPOINT` in `index.html`, `update-state-machine`, and `connect:*` policies) no longer apply: the frontend reads `config.js` generated from the stack outputs, the Step Functions stack owns the definition, and the roles are least privilege. See [Minimum IAM permissions](../README.md#minimum-iam-permissions).
