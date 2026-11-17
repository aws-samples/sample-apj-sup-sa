import json
from datetime import datetime
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import error_code_of, feature_skipped_result, is_feature_unavailable
from migration_filters import migrates_evaluation_form
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists all evaluation forms from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing evaluation forms from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing evaluation forms
        paginator = connect_client.get_paginator('list_evaluation_forms')
        evaluation_forms = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for form_summary in page['EvaluationFormSummaryList']:
                try:
                    # Get detailed evaluation form information
                    form_detail = connect_client.describe_evaluation_form(
                        InstanceId=source_instance_id,
                        EvaluationFormId=form_summary['EvaluationFormId']
                    )

                    form = form_detail['EvaluationForm']

                    # Only active evaluation forms (shared filter, VerifyTarget uses it too)
                    if migrates_evaluation_form(form):
                        evaluation_forms.append(form)
                        print(f"Retrieved evaluation form: {redact(form['Title'])}")
                    else:
                        print(f"Skipping evaluation form: {redact(form['Title'])} (Status: {form.get('Status')})")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing evaluation form {form_summary['EvaluationFormId']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        forms_json = json.loads(json.dumps(evaluation_forms, default=json_serial))

        print(f"Successfully retrieved {len(forms_json)} evaluation forms")

        return {
            'statusCode': 200,
            'evaluationForms': forms_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e):
            return feature_skipped_result('evaluationForms', source_region, 'source', error_code_of(e),
                                          evaluationForms=[])
        print(f"Error listing evaluation forms: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
