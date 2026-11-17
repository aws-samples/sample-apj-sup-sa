"""
Lambda function to list Lambda functions associated with source Connect instance.
"""
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Lists all Lambda functions associated with source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing Lambda functions from instance: {source_instance_id} in region: {source_region}")

        lambda_functions = []
        next_token = None

        while True:
            params = {'InstanceId': source_instance_id, 'MaxResults': 25}
            if next_token:
                params['NextToken'] = next_token

            response = connect_client.list_lambda_functions(**params)
            lambda_functions.extend(response.get('LambdaFunctions', []))

            next_token = response.get('NextToken')
            if not next_token:
                break

        print(f"Successfully retrieved {len(lambda_functions)} Lambda functions")

        # Return list of Lambda ARNs
        return {
            'statusCode': 200,
            'lambdaFunctions': lambda_functions,
            'sourceRegion': source_region
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing Lambda functions: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'lambdaFunctions': []
        }
