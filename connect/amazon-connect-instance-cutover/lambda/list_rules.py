import json
from region_utils import create_connect_client_from_arn
from datetime import datetime
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from migration_filters import migrates_rule
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists all Contact Lens rules from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing Contact Lens rules from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing rules
        paginator = connect_client.get_paginator('list_rules')
        rules = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for rule_summary in page['RuleSummaryList']:
                # Published rules only (shared filter on the summary, VerifyTarget uses it too)
                if not migrates_rule(rule_summary):
                    print(f"Skipping unpublished rule: {rule_summary['RuleId']}")
                    continue
                try:
                    # Get detailed rule information
                    rule_detail = connect_client.describe_rule(
                        InstanceId=source_instance_id,
                        RuleId=rule_summary['RuleId']
                    )

                    rule = rule_detail['Rule']
                    if migrates_rule(rule):
                        # Debug: Print rule structure
                        print(f"Rule '{redact(rule['Name'])}' structure: {redact(list(rule.keys()))}")
                        rules.append(rule)
                        print(f"Retrieved Contact Lens rule: {redact(rule['Name'])}")
                    else:
                        print(f"Skipping unpublished rule: {redact(rule['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing rule {rule_summary['RuleId']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        rules_json = json.loads(json.dumps(rules, default=json_serial))

        print(f"Successfully retrieved {len(rules_json)} Contact Lens rules")

        return {
            'statusCode': 200,
            'rules': rules_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing Contact Lens rules: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
