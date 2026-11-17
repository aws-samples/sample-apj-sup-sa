# Customer User Guide

This guide explains how to use the Amazon Connect Customer Migration Tool to migrate configurations between Connect instances.

## Getting Started

### Accessing the Tool

1. Open the URL provided by your administrator
2. Choose **Sign in** and use the Cognito user your administrator created
3. You'll see the migration interface with configuration options

### Understanding the Interface

The interface is divided into several steps:

1. **Configure Connect Instances** - Enter source and destination ARNs
2. **Select Migration Features** - Choose what to migrate
3. **Configure Options** - Set migration preferences
4. **Start Migration** - Execute the migration

## Step-by-Step Migration

### Step 1: Configure Connect Instances

Enter the full ARN for both instances:

- **Source Instance ARN**: The Connect instance you're migrating FROM
  - Example: `arn:aws:connect:REGION:ACCOUNT_ID:instance/INSTANCE_ID`

- **Destination Instance ARN**: The Connect instance you're migrating TO
  - Example: `arn:aws:connect:REGION:ACCOUNT_ID:instance/INSTANCE_ID`

#### Finding Your Instance ARN

1. Go to the Amazon Connect Customer console
2. Select your instance
3. Copy the Instance ARN from the overview page

#### Cross-Account Migration

If migrating to a different AWS account:

1. Check **"Enable Cross-Account Migration"** (usually preselected)
2. Check the **Cross-Account Role ARN** (usually prefilled)
3. Enter the **External ID** your administrator set on that role. It is required whenever a role is used
4. The role is created by your administrator with `cloudformation/target-role.yaml`

### Step 2: Check Service Quotas (Optional)

Click **"Check Service Quotas"** to verify:
- Both instances are accessible
- Service quotas are sufficient for migration

### Step 3: Select Migration Features

Choose which resources to migrate:

| Feature | Description |
|---------|-------------|
| **Users** | Agent users with their profiles |
| **Queues** | Queues and hours of operation |
| **Contact Flows** | Contact flows (IVR logic) |
| **Contact Flow Modules** | Reusable flow modules |
| **Views** | Agent workspace views |
| **Agent Status** | Custom agent statuses |
| **Hierarchy Groups** | User hierarchy structure |
| **Prompts** | Audio prompts |
| **Predefined Attributes** | Contact attributes |
| **Quick Connects** | Speed dial entries |
| **Evaluation Forms** | Quality evaluation forms |
| **Rules** | Contact Lens rules |

### Step 4: Configure Options

#### User Migration Options
- **Migrate Routing Profiles**: Include routing profile assignments
- **Migrate Security Profiles**: Include security profile assignments

#### Queue Migration Options
- **Migrate Hours of Operation**: Include hours of operation settings

### Step 5: Start Migration

1. Review your selections
2. Click **"Start Migration"**
3. Monitor progress in the status panel

## Migration Order

Resources are migrated in dependency order:

1. **Independent Resources** (parallel):
   - Agent Status
   - Hierarchy Groups
   - Prompts
   - Predefined Attributes
   - Views
   - Rules
   - Evaluation Forms
   - Data Tables, Vocabularies, AI resources
   - Lambda and Lex matching

2. **Flows** (parallel): Contact Flows and Contact Flow Modules

3. **Queue Resources** (sequential):
   - Hours of Operation → Queues

4. **User Resources** (sequential):
   - Security Profiles → Routing Profiles → Users

5. **Quick Connects**

## Understanding Results

### Success Messages
- ✅ "Migration completed successfully"
- Resources show "created" or "existing" status
- The **Migration Report** lists failed steps and every item that needs manual follow up

### Warning Messages
- ⚠️ "Using default routing profile" - Original profile not found
- ⚠️ "Skipping queue config" - Queue not migrated

### Error Messages
- ❌ "AccessDeniedException" - Permission issue; the step stops and names the action that was denied
- ❌ "DuplicateResourceException" - Resource already exists
- ❌ "ResourceNotFoundException" - Dependency missing

## Best Practices

### Before Migration

1. **Audit Source Instance**: Document what needs to be migrated
2. **Clean Up**: Remove unused resources from source
3. **Test First**: Try with a subset of resources first
4. **Record the source**: Export or document the source configuration before the cutover (the tool is not a backup)

### During Migration

1. **Monitor Progress**: Watch the status panel
2. **Check Logs**: Review CloudWatch Logs for details
3. **Don't Interrupt**: Let the migration complete

### After Migration

1. **Verify Resources**: Check destination instance
2. **Test Functionality**: Make test calls
3. **Update References**: Update any external integrations
4. **Document Changes**: Record what was migrated

## Troubleshooting

### "No mapping found for routing profile"

**Cause**: The routing profile wasn't migrated or doesn't exist in destination.

**Solution**:
- Ensure "Queues" feature is selected (routing profiles need queues)
- Check if the routing profile exists in source

### "Queue not found"

**Cause**: A queue referenced by a routing profile wasn't migrated.

**Solution**:
- Ensure "Queues" feature is selected
- Migrate queues before users

### "DuplicateResourceException"

**Cause**: Resource with same name already exists in destination.

**Solution**:
- The tool will map to existing resources automatically
- No action needed unless you want to recreate

### "AccessDeniedException"

**Cause**: Insufficient permissions.

**Solution**:
- Check the External ID and the role ARN
- Give your administrator the action named in the error
- Contact your administrator

## Limitations

1. **Phone Numbers**: The tool moves numbers only within one account and Region (opt in). Across accounts or Regions AWS Support moves them, or they are ported; the migration report names the path
2. **Lex Bots**: Bots are not copied. Flows use a bot with the same bot and alias name in the destination when one is associated; otherwise they keep a placeholder listed in the report
3. **Lambda Functions**: Code is not copied. Flows use a function with the same name associated with the destination; otherwise they keep a placeholder listed in the report
4. **Historical Data**: Contact records and metrics not migrated
5. **Instance Settings**: Instance-level settings not migrated

## Support

For issues or questions:
1. Check CloudWatch Logs for the error code, the action and the resource ID (AWS error messages are not logged, because they can contain request values)
2. Review the Step Functions execution history
3. Contact your administrator with the execution ARN
