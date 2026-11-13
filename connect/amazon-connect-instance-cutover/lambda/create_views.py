from region_utils import create_destination_client
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal, error_code_of, EXISTS_ERROR_CODES
from region_utils import describe_error, redact
from region_utils import resolve_external_id
from region_utils import feature_skipped_result, is_feature_unavailable
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Create views in destination Connect instance
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    views = event['views']
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    try:
        print(f"Creating views in instance: {destination_instance_id} in region: {destination_region}")

        created_views = []
        skipped_views = []
        view_mapping = {}
        # Customer managed views already in the target (shared VIEW_LIST_PARAMS), listed once before any create
        existing = ExistingIndex(connect_client, destination_instance_id, 'views')
        if existing.error is not None and is_feature_unavailable(existing.error):
            return feature_skipped_result('views', destination_region, 'target', error_code_of(existing.error),
                                          count=len(views), viewMapping={})

        for view in views:
            try:
                original_id = view['Id']
                view_name = view['Name']

                print(f"Processing view: {redact(view_name)}")

                # Validate view content structure
                if not view.get('Content') or not isinstance(view['Content'], dict):
                    print(f"Skipping view '{redact(view_name)}': Invalid or missing content structure")
                    skipped_views.append({
                        'originalId': original_id,
                        'status': 'skipped',
                        'error': 'Invalid or missing content structure'
                    })
                    continue

                # Check if view already exists
                if existing.error is not None:
                    skipped_views.append(existing.lookup_failure(original_id))
                    continue
                existing_view_id = existing.get(view_name)

                if existing_view_id:
                    print(f"View '{redact(view_name)}' already exists: {existing_view_id}")
                    view_mapping[original_id] = existing_view_id
                    skipped_views.append({
                        'originalId': original_id,
                        'newId': existing_view_id,
                        'status': 'existing'
                    })
                    continue

                # Create new view with correct parameters
                create_params = {
                    'InstanceId': destination_instance_id,
                    'Name': view_name,
                    'Status': 'PUBLISHED',  # Required parameter
                    'Content': {
                        'Template': view['Content'].get('Template', ''),
                        'Actions': view['Content'].get('Actions', [])
                    }
                }

                if view.get('Description'):
                    create_params['Description'] = view['Description']

                if view.get('Tags'):
                    create_params['Tags'] = view['Tags']

                response = connect_client.create_view(**create_params)
                # CreateView returns the new view under View (View.Id), not a top level ViewId.
                new_view_id = response['View']['Id']

                view_mapping[original_id] = new_view_id
                created_views.append({
                    'originalId': original_id,
                    'newId': new_view_id,
                    'status': 'created'
                })

                print(f"Created view: {redact(view_name)} ({original_id} -> {new_view_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code_of(e) in EXISTS_ERROR_CODES:
                    print(f"View {original_id} create failed ({error_code_of(e)}), looking the name up...")
                    existing_view_id, failure = existing.recheck(view_name, e, original_id)
                    if existing_view_id:
                        view_mapping[original_id] = existing_view_id
                        skipped_views.append({
                            'originalId': original_id,
                            'newId': existing_view_id,
                            'status': 'existing'
                        })
                    else:
                        skipped_views.append(failure)
                else:
                    print(f"Error creating view '{redact(view_name)}': {describe_error(e)}")
                    skipped_views.append({
                        'originalId': original_id,
                        'status': 'failed',
                        'error': describe_error(e), 'errorCode': error_code_of(e)
                    })

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error processing view '{redact(view_name)}': {describe_error(e)}")
                skipped_views.append({
                    'originalId': original_id,
                    'status': 'failed',
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })

        print(f"Successfully processed {len(created_views)} views")

        return {
            'statusCode': 200,
            'viewMapping': view_mapping,
            'createdViews': created_views,
            'skippedViews': skipped_views
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating views: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'viewMapping': {}
        }
