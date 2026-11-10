import json
import re
from region_utils import create_destination_client, create_connect_client_from_arn
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal, error_code_of, EXISTS_ERROR_CODES
from region_utils import describe_error, redact
from region_utils import resolve_external_id
from migration_filters import QUEUE_LIST_PARAMS
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Create Contact Lens rules in destination Connect instance
    Supports cross-account migration with crossAccountRoleArn
    Builds its own mappings by looking up resources by name
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    source_instance_arn = event['sourceInstanceId']  # Source instance ARN
    rules = event['rules']
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create source client
    source_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)
    print(f"Created source client for region: {source_region}, instance: {source_instance_id}")

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    dest_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    # Extract destination account ID from ARN
    dest_account_id = destination_instance_arn.split(':')[4]

    # Build mappings by looking up resources by name
    print("Building queue mapping by name lookup...")
    queue_mapping = build_queue_mapping(source_client, source_instance_id, dest_client, destination_instance_id)
    print(f"Built queue mapping with {len(queue_mapping)} entries")

    print("Building user mapping by name lookup...")
    user_mapping = build_user_mapping(source_client, source_instance_id, dest_client, destination_instance_id)
    print(f"Built user mapping with {len(user_mapping)} entries")

    print("Building contact flow mapping by name lookup...")
    contact_flow_mapping = build_contact_flow_mapping(source_client, source_instance_id, dest_client, destination_instance_id)
    print(f"Built contact flow mapping with {len(contact_flow_mapping)} entries")

    try:
        print(f"Creating Contact Lens rules in instance: {destination_instance_id} in region: {destination_region}")

        created_rules = []
        skipped_rules = []
        rule_mapping = {}
        # Rules already in the target (RuleSummary Name and RuleId), listed once before any create
        existing = ExistingIndex(dest_client, destination_instance_id, 'rules')

        for rule in rules:
            try:
                original_id = rule['RuleId']
                rule_name = rule['Name']

                print(f"Processing Contact Lens rule: {redact(rule_name)}")

                # Validate rule structure
                required_fields = []
                if not rule.get('Function'):
                    required_fields.append('Function')
                if not rule.get('TriggerEventSource') and not rule.get('Trigger'):
                    required_fields.append('TriggerEventSource or Trigger')
                if not rule.get('Actions'):
                    required_fields.append('Actions')

                if required_fields:
                    error_msg = f"Missing required fields: {', '.join(required_fields)}"
                    print(f"Skipping rule '{redact(rule_name)}': {redact(error_msg)}")
                    skipped_rules.append({
                        'originalId': original_id,
                        'status': 'skipped',
                        'error': error_msg
                    })
                    continue

                # Check if rule already exists
                if existing.error is not None:
                    skipped_rules.append(existing.lookup_failure(original_id))
                    continue
                existing_rule_id = existing.get(rule_name)

                if existing_rule_id:
                    print(f"Contact Lens rule '{redact(rule_name)}' already exists: {existing_rule_id}")
                    rule_mapping[original_id] = existing_rule_id
                    skipped_rules.append({
                        'originalId': original_id,
                        'newId': existing_rule_id,
                        'status': 'existing'
                    })
                    continue

                # Map resource IDs in TriggerEventSource
                trigger_event_source = map_trigger_event_source(
                    rule['TriggerEventSource'],
                    destination_instance_id,
                    destination_region,
                    dest_account_id,
                    queue_mapping
                )

                # Map resource IDs in Function (conditions)
                function = map_function_conditions(
                    rule['Function'],
                    destination_instance_id,
                    destination_region,
                    dest_account_id,
                    queue_mapping,
                    user_mapping
                )

                # Map resource IDs in Actions
                actions = map_actions(
                    rule['Actions'],
                    destination_instance_id,
                    destination_region,
                    dest_account_id,
                    queue_mapping,
                    user_mapping,
                    contact_flow_mapping
                )

                # Create new Contact Lens rule with mapped parameters
                create_params = {
                    'InstanceId': destination_instance_id,
                    'Name': rule_name,
                    'TriggerEventSource': trigger_event_source,
                    'Function': function,
                    'Actions': actions,
                    'PublishStatus': 'PUBLISHED'
                }

                # Add optional fields if they exist
                if rule.get('ClientToken'):
                    import uuid
                    create_params['ClientToken'] = str(uuid.uuid4())

                print(f"Creating rule with params: {redact(json.dumps(create_params, default=str)[:500])}...")

                response = dest_client.create_rule(**create_params)
                new_rule_id = response['RuleId']

                rule_mapping[original_id] = new_rule_id
                created_rules.append({
                    'originalId': original_id,
                    'newId': new_rule_id,
                    'status': 'created'
                })

                print(f"Created Contact Lens rule: {redact(rule_name)} ({original_id} -> {new_rule_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code_of(e) in EXISTS_ERROR_CODES:
                    # Includes ResourceConflictException: mapped only if the name is found now
                    print(f"Contact Lens rule {original_id} create failed ({error_code_of(e)}), looking the name up...")
                    existing_rule_id, failure = existing.recheck(rule_name, e, original_id)
                    if existing_rule_id:
                        rule_mapping[original_id] = existing_rule_id
                        skipped_rules.append({
                            'originalId': original_id,
                            'newId': existing_rule_id,
                            'status': 'existing'
                        })
                    else:
                        skipped_rules.append(failure)
                else:
                    print(f"Error creating Contact Lens rule '{redact(rule_name)}': {describe_error(e)}")
                    skipped_rules.append({
                        'originalId': original_id,
                        'status': 'failed',
                        'error': describe_error(e), 'errorCode': error_code_of(e)
                    })

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error processing Contact Lens rule '{redact(rule_name)}': {describe_error(e)}")
                skipped_rules.append({
                    'originalId': original_id,
                    'status': 'failed',
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })

        print(f"Successfully processed {len(created_rules)} Contact Lens rules")

        return {
            'statusCode': 200,
            'ruleMapping': rule_mapping,
            'createdRules': created_rules,
            'skippedRules': skipped_rules
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating Contact Lens rules: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'ruleMapping': {}
        }


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


def map_arn(arn, destination_instance_id, destination_region, dest_account_id, resource_mapping):
    """Map a source ARN to destination ARN using the resource mapping"""
    if not arn:
        return arn

    # Extract resource ID from ARN
    match = re.search(r'/([a-f0-9-]+)$', arn)
    if match:
        source_resource_id = match.group(1)
        if source_resource_id in resource_mapping:
            dest_resource_id = resource_mapping[source_resource_id]
            if '/queue/' in arn:
                return f"arn:aws:connect:{destination_region}:{dest_account_id}:instance/{destination_instance_id}/queue/{dest_resource_id}"
            elif '/agent/' in arn:
                return f"arn:aws:connect:{destination_region}:{dest_account_id}:instance/{destination_instance_id}/agent/{dest_resource_id}"
            elif '/contact-flow/' in arn:
                return f"arn:aws:connect:{destination_region}:{dest_account_id}:instance/{destination_instance_id}/contact-flow/{dest_resource_id}"

    return arn


def map_trigger_event_source(trigger_event_source, destination_instance_id, destination_region, dest_account_id, queue_mapping):
    """Map TriggerEventSource to use destination instance"""
    if not trigger_event_source:
        return trigger_event_source

    mapped = trigger_event_source.copy()

    if 'IntegrationAssociationId' in mapped:
        integration_arn = mapped['IntegrationAssociationId']
        if 'instance/' in integration_arn:
            mapped['IntegrationAssociationId'] = re.sub(
                r'instance/[a-f0-9-]+',
                f'instance/{destination_instance_id}',
                integration_arn
            )
            mapped['IntegrationAssociationId'] = re.sub(
                r'arn:aws:connect:[^:]+:[^:]+:',
                f'arn:aws:connect:{destination_region}:{dest_account_id}:',
                mapped['IntegrationAssociationId']
            )

    return mapped


def is_uuid(value):
    """Check if a string is a valid UUID"""
    uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
    return bool(re.match(uuid_pattern, value.lower()))


def map_function_conditions(function, destination_instance_id, destination_region, dest_account_id, queue_mapping, user_mapping):
    """Map Function conditions to use destination resource IDs"""
    if not function:
        return function

    if isinstance(function, str):
        try:
            function_dict = json.loads(function)
        except json.JSONDecodeError:
            print("Warning: Could not parse Function as JSON, returning as-is")
            return function
    else:
        function_dict = function

    import copy
    mapped = copy.deepcopy(function_dict)

    def map_ids_recursive(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str):
                    if value.startswith('arn:aws:connect:'):
                        if '/queue/' in value:
                            obj[key] = map_arn(value, destination_instance_id, destination_region, dest_account_id, queue_mapping)
                        elif '/agent/' in value:
                            obj[key] = map_arn(value, destination_instance_id, destination_region, dest_account_id, user_mapping)
                    elif is_uuid(value) and value in queue_mapping:
                        obj[key] = queue_mapping[value]
                    elif is_uuid(value) and value in user_mapping:
                        obj[key] = user_mapping[value]
                elif isinstance(value, (dict, list)):
                    map_ids_recursive(value)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str):
                    if item.startswith('arn:aws:connect:'):
                        if '/queue/' in item:
                            obj[i] = map_arn(item, destination_instance_id, destination_region, dest_account_id, queue_mapping)
                        elif '/agent/' in item:
                            obj[i] = map_arn(item, destination_instance_id, destination_region, dest_account_id, user_mapping)
                    elif is_uuid(item) and item in queue_mapping:
                        obj[i] = queue_mapping[item]
                    elif is_uuid(item) and item in user_mapping:
                        obj[i] = user_mapping[item]
                elif isinstance(item, (dict, list)):
                    map_ids_recursive(item)

    map_ids_recursive(mapped)

    if isinstance(function, str):
        return json.dumps(mapped)
    return mapped


def map_actions(actions, destination_instance_id, destination_region, dest_account_id, queue_mapping, user_mapping, contact_flow_mapping):
    """Map Actions to use destination resource IDs"""
    if not actions:
        return actions

    import copy
    mapped = copy.deepcopy(actions)

    def map_arns_recursive(obj, parent_key=None):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str):
                    if value.startswith('arn:aws:connect:'):
                        if '/queue/' in value:
                            obj[key] = map_arn(value, destination_instance_id, destination_region, dest_account_id, queue_mapping)
                        elif '/agent/' in value:
                            obj[key] = map_arn(value, destination_instance_id, destination_region, dest_account_id, user_mapping)
                        elif '/contact-flow/' in value:
                            obj[key] = map_arn(value, destination_instance_id, destination_region, dest_account_id, contact_flow_mapping)
                    elif is_uuid(value):
                        if value in queue_mapping:
                            obj[key] = queue_mapping[value]
                        elif value in user_mapping:
                            obj[key] = user_mapping[value]
                        elif value in contact_flow_mapping:
                            obj[key] = contact_flow_mapping[value]
                elif isinstance(value, list):
                    if key == 'UserIds':
                        for i, item in enumerate(value):
                            if isinstance(item, str) and is_uuid(item) and item in user_mapping:
                                value[i] = user_mapping[item]
                                print(f"Mapped UserIds[{redact(i)}]: {redact(item)} -> {redact(value[i])}")
                    else:
                        map_arns_recursive(value, key)
                elif isinstance(value, dict):
                    map_arns_recursive(value, key)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str):
                    if item.startswith('arn:aws:connect:'):
                        if '/queue/' in item:
                            obj[i] = map_arn(item, destination_instance_id, destination_region, dest_account_id, queue_mapping)
                        elif '/agent/' in item:
                            obj[i] = map_arn(item, destination_instance_id, destination_region, dest_account_id, user_mapping)
                        elif '/contact-flow/' in item:
                            obj[i] = map_arn(item, destination_instance_id, destination_region, dest_account_id, contact_flow_mapping)
                    elif is_uuid(item):
                        if item in queue_mapping:
                            obj[i] = queue_mapping[item]
                        elif item in user_mapping:
                            obj[i] = user_mapping[item]
                        elif item in contact_flow_mapping:
                            obj[i] = contact_flow_mapping[item]
                elif isinstance(item, (dict, list)):
                    map_arns_recursive(item, parent_key)

    map_arns_recursive(mapped)
    return mapped
