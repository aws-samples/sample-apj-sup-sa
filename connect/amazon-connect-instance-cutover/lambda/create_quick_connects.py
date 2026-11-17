from region_utils import create_destination_client, extract_instance_id_from_arn, create_connect_client_from_arn
import json
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact, error_code_of, failure_entry
from region_utils import EXISTS_ERROR_CODES, error_code
from region_utils import resolve_external_id
from migration_filters import QUEUE_LIST_PARAMS
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Create quick connects in target Connect instance
    Builds its own mappings by looking up resources by name
    Supports cross-account migration with crossAccountRoleArn
    """

    try:
        target_instance_arn = event['targetInstanceId']  # Now expects ARN
        source_instance_arn = event['sourceInstanceId']  # Now expects ARN
        quick_connects = event['quickConnects']
        cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
        external_id = resolve_external_id(event)  # never logged

        # Extract source instance ID from ARN
        source_instance_id = extract_instance_id_from_arn(source_instance_arn)

        # Create source client (always same account as Lambda)
        source_client, _, source_region = create_connect_client_from_arn(source_instance_arn)
        print(f"Created source client for region: {source_region}")

        # Create region-specific Connect client for target instance (supports cross-account)
        print(f"Creating Connect client from target instance ARN: {target_instance_arn}")
        if cross_account_role_arn:
            print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
        connect_client, target_instance_id, target_region = create_destination_client(
            target_instance_arn, cross_account_role_arn, external_id
        )

        # Build mappings by looking up resources by name (instead of relying on state machine)
        print("Building user mapping by name lookup...")
        user_mapping = build_user_mapping(source_client, source_instance_id, connect_client, target_instance_id)
        print(f"Built user mapping with {len(user_mapping)} entries")

        print("Building queue mapping by name lookup...")
        queue_mapping = build_queue_mapping(source_client, source_instance_id, connect_client, target_instance_id)
        print(f"Built queue mapping with {len(queue_mapping)} entries")

        print("Building contact flow mapping by name lookup...")
        contactflow_mapping = build_contact_flow_mapping(source_client, source_instance_id, connect_client, target_instance_id)
        print(f"Built contact flow mapping with {len(contactflow_mapping)} entries")

        # Create each quick connect in target instance; one that already exists (same name) is mapped
        # to the target one instead of failing CreateQuickConnect with a duplicate error on a rerun.
        existing_qcs = list_existing_quick_connects(connect_client, target_instance_id)
        created = []
        existing = []
        not_created = []
        qc_mapping = {}

        for qc in quick_connects:
            print(f"Processing quick connect: {qc.get('QuickConnectId', qc.get('Id'))}")
            print(f"Quick connect type: {(qc.get('QuickConnectConfig') or {}).get('QuickConnectType')}")
            existing_id = existing_qcs.get(qc.get('Name'))
            if existing_id:
                existing.append(existing_id)
                qc_mapping[qc['QuickConnectId']] = existing_id
                print(f"Mapped to existing quick connect: {qc['QuickConnectId']} -> {existing_id}")
                continue
            result = create_quick_connect(connect_client, source_client, source_instance_id, target_instance_id, qc, user_mapping, queue_mapping, contactflow_mapping)
            if result['success'] and result.get('existing'):
                existing.append(result['new_id'])
                qc_mapping[qc['QuickConnectId']] = result['new_id']
            elif result['success']:
                created.append(result['new_id'])  # target quick connect IDs, never names
                qc_mapping[qc['QuickConnectId']] = result['new_id']
            else:
                # ID, error code and a fixed reason only: never names, usernames or exception text.
                entry = failure_entry(qc.get('QuickConnectId'), reason=result.get('reason'))
                entry['errorCode'] = result.get('errorCode') or 'NotCreated'
                not_created.append(entry)
                print(f"Quick connect {qc.get('QuickConnectId')} not created: {entry['errorCode']}")

        return {
            'statusCode': 200,
            'failedItems': not_created,
            'body': json.dumps({
                'message': f'Created {len(created)} quick connects, mapped {len(existing)} existing',
                'created': created,
                'existing': existing,
                'not_created': not_created,
                'qc_mapping': qc_mapping
            })
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error in lambda_handler: {describe_error(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': describe_error(e)})
        }

def create_quick_connect(dest_client, source_client, source_instance_id, target_instance_id, qc_config, user_mapping, queue_mapping, contactflow_mapping):
    """Create quick connect in target instance using mappings"""

    # Update QuickConnectConfig with mapped IDs
    config_result = update_quick_connect_config(dest_client, source_client, source_instance_id, target_instance_id, qc_config['QuickConnectConfig'], user_mapping, queue_mapping, contactflow_mapping, qc_config)

    if not config_result['success']:
        return config_result

    try:
        create_params = {
            'InstanceId': target_instance_id,
            'Name': qc_config['Name'],
            'QuickConnectConfig': config_result['config']
        }

        if 'Description' in qc_config:
            create_params['Description'] = qc_config['Description']

        print(f"Creating quick connect of type {create_params['QuickConnectConfig'].get('QuickConnectType')}")
        response = dest_client.create_quick_connect(**create_params)
        return {'success': True, 'new_id': response['QuickConnectId']}
    except Exception as e:
        reraise_if_fatal(e)
        if error_code(e) in EXISTS_ERROR_CODES:
            # Created since the step listed the target (or not listed): map it by name.
            existing_id = list_existing_quick_connects(dest_client, target_instance_id).get(qc_config['Name'])
            if existing_id:
                return {'success': True, 'new_id': existing_id, 'existing': True}
        return {'success': False, 'errorCode': error_code_of(e), 'reason': 'CreateQuickConnect failed'}


def list_existing_quick_connects(dest_client, target_instance_id):
    """{name: ID} of the quick connects in the target; empty if the lookup fails (logged by error code)."""
    found = {}
    try:
        for page in dest_client.get_paginator('list_quick_connects').paginate(InstanceId=target_instance_id):
            for summary in page.get('QuickConnectSummaryList', []):
                if summary.get('Name') and summary.get('Id'):
                    found[summary['Name']] = summary['Id']
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing existing quick connects: {describe_error(e)}")
    return found

def update_quick_connect_config(dest_client, source_client, source_instance_id, target_instance_id, config, user_mapping, queue_mapping, contactflow_mapping, qc_config=None):
    """Update quick connect config with mapped queue/user/contactflow IDs"""
    updated_config = config.copy()

    if config['QuickConnectType'] == 'QUEUE':
        original_queue_id = config['QueueConfig']['QueueId']
        if original_queue_id in queue_mapping:
            updated_config['QueueConfig']['QueueId'] = queue_mapping[original_queue_id]
        else:
            # Look up queue by name in both instances
            try:
                # Get queue details from source instance
                source_queue = source_client.describe_queue(
                    InstanceId=source_instance_id,
                    QueueId=original_queue_id
                )
                source_queue_name = source_queue['Queue']['Name']

                print(f"Looking for queue '{redact(source_queue_name)}' in destination instance")

                # Find queue with same name in destination instance
                destination_queue_id = find_queue_by_name(dest_client, target_instance_id, source_queue_name)

                if destination_queue_id:
                    updated_config['QueueConfig']['QueueId'] = destination_queue_id
                    print(f"Found queue '{redact(source_queue_name)}' in destination: {destination_queue_id}")
                else:
                    return {'success': False, 'errorCode': 'QueueNotMigrated', 'reason': 'Queue not found in target'}

            except Exception as e:
                reraise_if_fatal(e)
                return {'success': False, 'errorCode': error_code_of(e), 'reason': 'Queue lookup failed'}

        # Update contact flow ID if present
        if 'ContactFlowId' in config['QueueConfig']:
            original_cf_id = config['QueueConfig']['ContactFlowId']
            if original_cf_id in contactflow_mapping:
                updated_config['QueueConfig']['ContactFlowId'] = contactflow_mapping[original_cf_id]
            else:
                # Use default queue transfer flow or remove if not available
                print(f"Contact Flow ID {original_cf_id} not found in mapping, using default queue transfer flow")
                default_flow_id = get_default_queue_transfer_flow(dest_client, target_instance_id)
                if default_flow_id:
                    updated_config['QueueConfig']['ContactFlowId'] = default_flow_id
                else:
                    # Remove contact flow ID if default not found
                    if 'ContactFlowId' in updated_config['QueueConfig']:
                        del updated_config['QueueConfig']['ContactFlowId']

    elif config['QuickConnectType'] == 'USER':
        original_user_id = config['UserConfig']['UserId']

        # First try user_mapping if available
        if original_user_id in user_mapping:
            updated_config['UserConfig']['UserId'] = user_mapping[original_user_id]
        else:
            # Look up user by name in both instances
            try:
                # Get user details from source instance using SOURCE client
                source_user = source_client.describe_user(
                    InstanceId=source_instance_id,
                    UserId=original_user_id
                )
                source_username = source_user['User']['Username']

                print(f"Looking for user '{redact(source_username)}' in destination instance")

                # Find user with same username in destination instance using DEST client
                destination_user_id = find_user_by_username(dest_client, target_instance_id, source_username)

                if destination_user_id:
                    updated_config['UserConfig']['UserId'] = destination_user_id
                    print(f"Found user '{redact(source_username)}' in destination: {destination_user_id}")
                else:
                    return {'success': False, 'errorCode': 'UserNotMigrated', 'reason': 'User not found in target'}

            except Exception as e:
                reraise_if_fatal(e)
                return {'success': False, 'errorCode': error_code_of(e), 'reason': 'User lookup failed'}

        # Update contact flow ID if present
        if 'ContactFlowId' in config['UserConfig']:
            original_cf_id = config['UserConfig']['ContactFlowId']
            if original_cf_id in contactflow_mapping:
                updated_config['UserConfig']['ContactFlowId'] = contactflow_mapping[original_cf_id]
            else:
                # Use default agent transfer flow or remove if not available
                print(f"Contact Flow ID {original_cf_id} not found in mapping, using default agent transfer flow")
                default_flow_id = get_default_agent_transfer_flow(dest_client, target_instance_id)
                if default_flow_id:
                    updated_config['UserConfig']['ContactFlowId'] = default_flow_id
                else:
                    # Remove contact flow ID if default not found
                    if 'ContactFlowId' in updated_config['UserConfig']:
                        del updated_config['UserConfig']['ContactFlowId']

    elif config['QuickConnectType'] == 'PHONE_NUMBER':
        # Phone number type doesn't need mapping, use as-is
        pass

    elif config['QuickConnectType'] == 'FLOW':
        # Handle flow type quick connects
        print("FLOW type config received")

        try:
            # Check if FlowConfig is already present (newer API)
            if 'FlowConfig' in config and 'ContactFlowId' in config['FlowConfig']:
                original_flow_id = config['FlowConfig']['ContactFlowId']
                print(f"Found FlowConfig with ContactFlowId: {original_flow_id}")

                if original_flow_id in contactflow_mapping:
                    updated_config['FlowConfig'] = {'ContactFlowId': contactflow_mapping[original_flow_id]}
                else:
                    # Get flow name from source and find in destination
                    try:
                        source_flow = source_client.describe_contact_flow(
                            InstanceId=source_instance_id,
                            ContactFlowId=original_flow_id
                        )
                        flow_name = source_flow['ContactFlow']['Name']
                        print(f"Looking for contact flow '{redact(flow_name)}' in destination instance")

                        destination_flow_id = find_contact_flow_by_name(dest_client, target_instance_id, flow_name)
                        if destination_flow_id:
                            updated_config['FlowConfig'] = {'ContactFlowId': destination_flow_id}
                            print(f"Found contact flow '{redact(flow_name)}' in destination: {destination_flow_id}")
                        else:
                            print(f"Contact flow '{redact(flow_name)}' not found in destination, trying default inbound flow")
                            # Try to use a default flow
                            default_flow_id = get_default_inbound_flow(dest_client, target_instance_id)
                            if default_flow_id:
                                updated_config['FlowConfig'] = {'ContactFlowId': default_flow_id}
                                print(f"Using default inbound flow: {default_flow_id}")
                            else:
                                return {'success': False, 'errorCode': 'FlowNotMigrated', 'reason': 'Flow not found in target'}
                    except Exception as e:
                        reraise_if_fatal(e)
                        return {'success': False, 'errorCode': error_code_of(e), 'reason': 'Flow lookup failed'}
            else:
                # FlowConfig not present - get full details from describe_quick_connect
                print("FlowConfig not present in config, getting full quick connect details")

                # Get the full quick connect details from source
                print(f"Getting full quick connect details for QuickConnectId: {qc_config['QuickConnectId']}")
                source_qc = source_client.describe_quick_connect(
                    InstanceId=source_instance_id,
                    QuickConnectId=qc_config['QuickConnectId']
                )
                print("Fetched full source quick connect details")

                # Check if the full details have FlowConfig
                full_config = source_qc.get('QuickConnect', {}).get('QuickConnectConfig', {})
                if 'FlowConfig' in full_config and 'ContactFlowId' in full_config['FlowConfig']:
                    original_flow_id = full_config['FlowConfig']['ContactFlowId']
                    print(f"Found FlowConfig in full details with ContactFlowId: {original_flow_id}")

                    # Get flow name from source
                    source_flow = source_client.describe_contact_flow(
                        InstanceId=source_instance_id,
                        ContactFlowId=original_flow_id
                    )
                    flow_name = source_flow['ContactFlow']['Name']
                    print(f"Looking for contact flow '{redact(flow_name)}' in destination instance")

                    destination_flow_id = find_contact_flow_by_name(dest_client, target_instance_id, flow_name)
                    if destination_flow_id:
                        updated_config['FlowConfig'] = {'ContactFlowId': destination_flow_id}
                        print(f"Found contact flow '{redact(flow_name)}' in destination: {destination_flow_id}")
                    else:
                        print(f"Contact flow '{redact(flow_name)}' not found in destination, trying default inbound flow")
                        # Try to use a default flow
                        default_flow_id = get_default_inbound_flow(dest_client, target_instance_id)
                        if default_flow_id:
                            updated_config['FlowConfig'] = {'ContactFlowId': default_flow_id}
                            print(f"Using default inbound flow: {default_flow_id}")
                        else:
                            return {'success': False, 'errorCode': 'FlowNotMigrated', 'reason': 'Flow not found in target'}
                else:
                    # Last resort: assume flow name is same as quick connect name
                    qc_name = qc_config['Name']
                    print(f"No FlowConfig found, assuming flow name is same as quick connect name: '{redact(qc_name)}'")

                    destination_flow_id = find_contact_flow_by_name(dest_client, target_instance_id, qc_name)
                    if destination_flow_id:
                        updated_config['FlowConfig'] = {'ContactFlowId': destination_flow_id}
                        print(f"Found contact flow '{redact(qc_name)}' in destination: {destination_flow_id}")
                    else:
                        print(f"Contact flow '{redact(qc_name)}' not found, trying default inbound flow")
                        default_flow_id = get_default_inbound_flow(dest_client, target_instance_id)
                        if default_flow_id:
                            updated_config['FlowConfig'] = {'ContactFlowId': default_flow_id}
                            print(f"Using default inbound flow: {default_flow_id}")
                        else:
                            return {'success': False, 'errorCode': 'FlowNotMigrated', 'reason': 'Flow not found in target'}

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Exception in FLOW type handling: {describe_error(e)}")
            return {'success': False, 'errorCode': error_code_of(e), 'reason': 'Flow lookup failed'}

    else:
        return {'success': False, 'errorCode': 'UnsupportedQuickConnectType', 'reason': 'Unsupported type'}

    return {'success': True, 'config': updated_config}

def find_user_by_username(client, instance_id, username):
    """Find user ID by username in the given Connect instance"""
    try:
        # List all users and find by username
        paginator = client.get_paginator('list_users')

        for page in paginator.paginate(InstanceId=instance_id):
            for user in page['UserSummaryList']:
                if user['Username'] == username:
                    return user['Id']

        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding user by username '{redact(username)}': {describe_error(e)}")
        return None

def find_queue_by_name(client, instance_id, queue_name):
    """Find queue ID by name in the given Connect instance"""
    try:
        # List all queues and find by name
        paginator = client.get_paginator('list_queues')

        for page in paginator.paginate(InstanceId=instance_id, **QUEUE_LIST_PARAMS):
            for queue in page['QueueSummaryList']:
                if queue['Name'] == queue_name:
                    return queue['Id']

        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding queue by name '{redact(queue_name)}': {describe_error(e)}")
        return None

def find_contact_flow_by_name(client, instance_id, flow_name):
    """Find contact flow ID by name in the given Connect instance"""
    try:
        # List all contact flows and find by name
        paginator = client.get_paginator('list_contact_flows')

        for page in paginator.paginate(InstanceId=instance_id):
            for flow in page['ContactFlowSummaryList']:
                if flow['Name'] == flow_name:
                    return flow['Id']

        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding contact flow by name '{redact(flow_name)}': {describe_error(e)}")
        return None

def get_default_agent_transfer_flow(client, instance_id):
    """Get the default agent transfer contact flow ID"""
    try:
        # List contact flows and find the default agent transfer flow
        paginator = client.get_paginator('list_contact_flows')

        for page in paginator.paginate(InstanceId=instance_id):
            for flow in page['ContactFlowSummaryList']:
                if flow['Name'] == 'Default agent transfer' or flow['Name'] == 'Default Agent Transfer':
                    return flow['Id']

        print("Default agent transfer flow not found")
        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding default agent transfer flow: {describe_error(e)}")
        return None

def get_default_queue_transfer_flow(client, instance_id):
    """Get the default queue transfer contact flow ID"""
    try:
        # List contact flows and find the default queue transfer flow
        paginator = client.get_paginator('list_contact_flows')

        for page in paginator.paginate(InstanceId=instance_id):
            for flow in page['ContactFlowSummaryList']:
                if flow['Name'] == 'Default queue transfer' or flow['Name'] == 'Default Queue Transfer':
                    return flow['Id']

        print("Default queue transfer flow not found")
        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding default queue transfer flow: {describe_error(e)}")
        return None

def get_default_inbound_flow(client, instance_id):
    """Get a default inbound contact flow ID for FLOW type quick connects"""
    try:
        # List contact flows and find a suitable default flow
        paginator = client.get_paginator('list_contact_flows')

        # Priority order for default flows
        default_flow_names = [
            'Sample inbound flow',
            'Default inbound flow',
            'Sample inbound flow (first contact experience)',
            'Default customer queue'
        ]

        all_flows = []
        for page in paginator.paginate(InstanceId=instance_id):
            all_flows.extend(page['ContactFlowSummaryList'])

        # Try to find flows in priority order
        for default_name in default_flow_names:
            for flow in all_flows:
                if flow['Name'].lower() == default_name.lower():
                    print(f"Found default flow: {redact(flow['Name'])} ({flow['Id']})")
                    return flow['Id']

        # If no default found, return the first CONTACT_FLOW type
        for flow in all_flows:
            if flow.get('ContactFlowType') == 'CONTACT_FLOW':
                print(f"Using first available contact flow: {redact(flow['Name'])} ({flow['Id']})")
                return flow['Id']

        print("No suitable default inbound flow found")
        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding default inbound flow: {describe_error(e)}")
        return None


def build_user_mapping(source_client, source_instance_id, dest_client, dest_instance_id):
    """Build user mapping by looking up users by username in both instances"""
    mapping = {}

    try:
        # Get all source users
        source_users = {}
        paginator = source_client.get_paginator('list_users')
        for page in paginator.paginate(InstanceId=source_instance_id):
            for user in page.get('UserSummaryList', []):
                source_users[user['Id']] = user['Username']

        # Get all destination users
        dest_users = {}
        paginator = dest_client.get_paginator('list_users')
        for page in paginator.paginate(InstanceId=dest_instance_id):
            for user in page.get('UserSummaryList', []):
                dest_users[user['Username']] = user['Id']

        # Build mapping
        for source_id, username in source_users.items():
            if username in dest_users:
                mapping[source_id] = dest_users[username]

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error building user mapping: {describe_error(e)}")

    return mapping


def build_queue_mapping(source_client, source_instance_id, dest_client, dest_instance_id):
    """Build queue mapping by looking up queues by name in both instances"""
    mapping = {}

    try:
        # Get all source queues
        source_queues = {}
        paginator = source_client.get_paginator('list_queues')
        for page in paginator.paginate(InstanceId=source_instance_id, **QUEUE_LIST_PARAMS):
            for queue in page.get('QueueSummaryList', []):
                source_queues[queue['Id']] = queue['Name']

        # Get all destination queues
        dest_queues = {}
        paginator = dest_client.get_paginator('list_queues')
        for page in paginator.paginate(InstanceId=dest_instance_id, **QUEUE_LIST_PARAMS):
            for queue in page.get('QueueSummaryList', []):
                dest_queues[queue['Name']] = queue['Id']

        # Build mapping
        for source_id, name in source_queues.items():
            if name in dest_queues:
                mapping[source_id] = dest_queues[name]

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error building queue mapping: {describe_error(e)}")

    return mapping


def build_contact_flow_mapping(source_client, source_instance_id, dest_client, dest_instance_id):
    """Build contact flow mapping by looking up flows by name in both instances"""
    mapping = {}

    try:
        # Get all source contact flows
        source_flows = {}
        paginator = source_client.get_paginator('list_contact_flows')
        for page in paginator.paginate(InstanceId=source_instance_id):
            for flow in page.get('ContactFlowSummaryList', []):
                source_flows[flow['Id']] = flow['Name']

        # Get all destination contact flows
        dest_flows = {}
        paginator = dest_client.get_paginator('list_contact_flows')
        for page in paginator.paginate(InstanceId=dest_instance_id):
            for flow in page.get('ContactFlowSummaryList', []):
                dest_flows[flow['Name']] = flow['Id']

        # Build mapping
        for source_id, name in source_flows.items():
            if name in dest_flows:
                mapping[source_id] = dest_flows[name]

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error building contact flow mapping: {describe_error(e)}")

    return mapping
