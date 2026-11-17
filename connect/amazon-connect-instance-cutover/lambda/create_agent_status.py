from botocore.exceptions import ClientError

from region_utils import create_destination_client
from region_utils import reraise_if_fatal
from region_utils import EXISTS_ERROR_CODES, describe_error, error_code, failure_entry, redact
from region_utils import resolve_external_id
from arn_mapping import ExistingIndex
from migration_filters import migrates_agent_status
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    """Lambda function to create Connect agent statuses from ListAgentStatus response
    Supports cross-account migration with crossAccountRoleArn
    """

    target_instance_arn = event['targetInstanceId']  # Now expects ARN
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for target instance (supports cross-account)
    print(f"Creating Connect client from target instance ARN: {target_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, target_instance_id, target_region = create_destination_client(
        target_instance_arn, cross_account_role_arn, external_id
    )

    # Get agent statuses from ListAgentStatus response
    statuses = event.get('agentStatuses', [])

    # Create agent statuses in target instance
    migrated = []
    skipped = []
    failed_items = []
    status_mapping = {}

    try:
        print(f"Creating agent statuses in instance: {target_instance_id} in region: {target_region}")
        # Statuses already in the target, listed once before any create
        existing = ExistingIndex(connect_client, target_instance_id, 'agentStatuses')

        for status in statuses:
            # Built-in statuses (ROUTABLE, OFFLINE) exist in every instance (shared filter, VerifyTarget uses it too)
            if not migrates_agent_status(status):
                print(f"Skipping built-in status: {redact(status.get('Name'))} (Type: {status.get('Type')})")
                continue

            if existing.error is not None:
                failed_items.append(existing.lookup_failure(status.get('Id')))
                continue
            existing_id = existing.get(status.get('Name'))
            if existing_id:
                status_mapping[status['Id']] = existing_id
                skipped.append(status['Id'])
                print(f"Mapped to existing agent status: {status['Id']} -> {existing_id}")
                continue

            try:
                response = connect_client.create_agent_status(
                    InstanceId=target_instance_id,
                    Name=status['Name'],
                    State='ENABLED'
                )
                status_mapping[status['Id']] = response['AgentStatusId']
                migrated.append(status['Id'])  # source IDs only: names stay out of the history
                print(f"Created agent status: {redact(status['Name'])}")
            except ClientError as e:
                reraise_if_fatal(e)
                # Already in the target: one fresh lookup by name, as the other create steps do
                # (otherwise an existing status is skipped and statusMapping stays empty).
                if error_code(e) in EXISTS_ERROR_CODES:
                    existing_id, failure = existing.recheck(status['Name'], e, status.get('Id'))
                else:
                    existing_id, failure = None, failure_entry(status.get('Id'), e)
                if existing_id:
                    status_mapping[status['Id']] = existing_id
                    skipped.append(status['Id'])
                    print(f"Mapped to existing agent status: {status['Id']} -> {existing_id}")
                else:
                    print(f"Error creating agent status {status.get('Id')}: {describe_error(e)}")
                    failed_items.append(failure)
            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error creating agent status {status.get('Id')}: {describe_error(e)}")
                failed_items.append(failure_entry(status.get('Id'), e))

        print(f"Successfully migrated {len(migrated)} agent statuses, mapped {len(skipped)} existing")

        return {
            'statusCode': 200,
            'message': f'Migrated {len(migrated)} agent statuses',
            'migratedStatuses': migrated,
            'skippedStatuses': skipped,
            'statusMapping': status_mapping,
            'failedItems': failed_items
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating agent statuses: {describe_error(e)}")
        return {
            'statusCode': 500,
            'error': describe_error(e),
            'statusMapping': status_mapping
        }
