import json
from region_utils import create_connect_client_from_arn
from datetime import datetime
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import error_code_of, feature_skipped_result, is_feature_unavailable
from migration_filters import VIEW_LIST_PARAMS, migrates_view
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists all views from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing views from instance: {source_instance_id} in region: {source_region}")

        # Only customer managed views. AWS managed views are listed by short ID and
        # DescribeView on them returns ResourceNotFound; they exist in every instance.
        paginator = connect_client.get_paginator('list_views')
        views = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id, **VIEW_LIST_PARAMS):
            for view_summary in page['ViewsSummaryList']:
                # AWS managed and unpublished views (shared filter, VerifyTarget uses it too).
                if not migrates_view(view_summary):
                    print(f"Skipping AWS managed or unpublished view: {view_summary['Id']}")
                    continue
                try:
                    # Get detailed view information
                    view_detail = connect_client.describe_view(
                        InstanceId=source_instance_id,
                        ViewId=view_summary['Id']
                    )

                    view = view_detail['View']
                    # A view without template content cannot be created
                    if view.get('Content') and isinstance(view.get('Content'), dict):
                        views.append(view)
                        print(f"Retrieved view: {redact(view['Name'])}")
                    else:
                        print(f"Skipping view: {redact(view['Name'])} (invalid content)")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing view {view_summary['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        views_json = json.loads(json.dumps(views, default=json_serial))

        print(f"Successfully retrieved {len(views_json)} views")

        return {
            'statusCode': 200,
            'views': views_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e):
            return feature_skipped_result('views', source_region, 'source', error_code_of(e), views=[])
        print(f"Error listing views: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
