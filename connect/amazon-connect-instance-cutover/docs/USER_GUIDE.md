# User Guide

This guide explains how to use the Amazon Connect Customer Migration Tool to migrate resources between Connect instances.

## Getting Started

1. Open the Migration Tool URL (provided by your administrator)
2. Choose **Sign in** and sign in with the Cognito user your administrator created (first sign in asks you to set a new password)
3. You'll see the main migration interface; **Sign out** is at the top

## Step 1: Configure Connect Instances

### Source Instance ARN
Enter the full ARN of the Connect instance you want to migrate FROM.

Format: `arn:aws:connect:REGION:ACCOUNT_ID:instance/INSTANCE_ID`

Example: `arn:aws:connect:REGION:ACCOUNT_ID:instance/INSTANCE_ID`

### Destination Instance ARN
Enter the full ARN of the Connect instance you want to migrate TO.

### Finding Your Instance ARN
1. Go to the Amazon Connect Customer console
2. Select your instance
3. The ARN is displayed in the instance details

## Step 2: Target Role and External ID

The tool writes to the destination only through the `ConnectMigrationTargetWriter` role, also for same account migrations. Your administrator creates it with `cloudformation/target-role.yaml` (see the [README](../README.md#3-create-the-target-role-in-the-target-account)).

1. The role ARN is usually prefilled; otherwise enter it in "Cross-Account IAM Role ARN"
2. Enter the **External ID** that was set on the role. It is required whenever a role is used and is never shown or logged

## Step 3: Check Service Quotas

Before migrating, click "Check Service Quotas" to verify:
- The destination instance has sufficient quota for the resources being migrated
- Both instances are accessible

Each destination quota is labelled with its source: applied to the instance, applied to the account, or the AWS default. Agent status per instance cannot be increased.

## Step 4: Select Migration Features

### Users Section
- **Users**: Migrate user accounts
- **Migrate Routing Profiles**: Copy routing profiles and assign to users
- **Migrate Security Profiles**: Copy security profiles and assign to users
- **Migrate Agent Status**: Copy custom agent status configurations
- **Migrate Hierarchy Groups**: Copy user hierarchy structure

### Queues Section
- **Queues**: Migrate contact queues
- **Migrate Hours of Operation**: Copy hours of operation schedules

### Routing Section
- **Prompts**: Migrate audio prompts
- **Quick Connects**: Migrate quick connect configurations
- **Predefined Attributes**: Migrate custom contact attributes

### Flows Section
- **Contact Flows**: Migrate contact flow definitions
- **Contact Flow Modules**: Migrate reusable flow modules
- **Views**: Migrate agent workspace views

### Analytics Section
- **Rules**: Migrate Contact Lens rules
- **Evaluation Forms**: Migrate agent evaluation forms

### Integrations Section
- **Lambda Functions**: Match functions by name with the ones associated with the destination (code is not copied)
- **Lex Bots**: Match Lex V2 bots by bot and alias name with the ones associated with the destination (bots are not copied)

### Pre-Check Button
Click "Check Flow Dependencies" to analyze flows for Lambda and Lex dependencies that need to be configured before migration.

## Step 5: Phone Number Migration (Optional)

⚠️ **Important Limitations:**
- The tool moves numbers only for same Region, same account migrations, and only when the deployment explicitly enabled it
- Different account or different Region: AWS Support moves the numbers (one case per account, Monday to Friday, about 20 to 30 minutes per number). Different account and different Region: porting. The migration report names the path, with the phone number IDs and count
- Phone numbers are MOVED, not copied: the number stops working on the source instance, and calls to it can fail while it moves
- Set the outbound caller ID on the target queues again afterwards
- Contact flow associations are not migrated

To include phone numbers:
1. Check "Include phone numbers"
2. Click "Load Phone Numbers"
3. Select the numbers (none selected means all numbers of the source instance)

## Step 6: Start Migration

1. Review your selections
2. Click "Start Migration"
3. Monitor progress in the execution details section
4. Click "View in AWS Console" for detailed Step Functions execution

## Migration Order

Resources are migrated in this order (see `step-functions/migration-state-machine.json`):
1. In parallel: agent statuses, hierarchy groups, prompts, predefined attributes, data tables, views, rules, evaluation forms, vocabularies, AI guardrails, AI prompts, AI agents, Lambda and Lex matching, phone numbers (if enabled)
2. In parallel: contact flows and contact flow modules
3. Hours of operation, then queues
4. Security profiles, routing profiles, then users
5. Quick connects

## Best Practices

### Before Migration
1. **Record the source**: Document your source instance configuration (the tool is a one time cutover, not a backup)
2. **Test**: Try migration in a test environment first
3. **Quotas**: Ensure destination has sufficient quotas
4. **Dependencies**: Run pre-check for Lambda/Lex dependencies

### During Migration
1. **Don't interrupt**: Let the migration complete
2. **Monitor**: Watch the progress indicator
3. **Check logs**: Review Step Functions execution for errors

### After Migration
1. **Review the migration report**: failed steps and every manual follow up item (phone numbers, Lambda functions, Lex bots, unmapped ARNs)
2. **Verify**: Check migrated resources in destination
3. **Test**: Test contact flows and routing
4. **Update**: Manually update any phone number associations
5. **Clean up**: Remove test data if applicable

## Troubleshooting

### "Resource already exists" errors
The tool skips resources that already exist in the destination. This is normal behavior.

### Contact flow migration failures
Common causes:
- Missing Lambda function associations
- Missing Lex bot associations
- References to resources not yet migrated

Run the pre-check to identify issues before migration.

### Cross-account permission errors
A 403 response names the action that was denied. Verify:
1. The IAM role ARN is correct
2. The External ID matches the one on the role
3. The role trusts this deployment's `ConnectMigrationSourceReader` role

### Timeout errors
For large instances, some operations may time out. Running the migration again is safe: resources that already exist in the destination (same name) are skipped, not duplicated.
