"""
Lambda function to list the Lex V2 bots associated with the source Connect instance.

Lex V1 (Lex Classic) bots are not supported and are not listed.
"""
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error
from region_utils import error_code_of, feature_skipped_result, is_feature_unavailable
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    """Lists the Lex V2 bot aliases associated with the source Connect instance."""
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing Lex V2 bots from instance: {source_instance_id} in region: {source_region}")
        lex_v2_bots = []
        try:
            next_token = None
            while True:
                params = {'InstanceId': source_instance_id, 'LexVersion': 'V2', 'MaxResults': 25}
                if next_token:
                    params['NextToken'] = next_token

                response = connect_client.list_bots(**params)

                for bot in response.get('LexBots', []):
                    lex_v2_bots.append({
                        'aliasArn': bot.get('LexV2Bot', {}).get('AliasArn'),
                        'lexRegion': bot.get('LexRegion'),
                        'version': 'V2'
                    })

                next_token = response.get('NextToken')
                if not next_token:
                    break

            print(f"Found {len(lex_v2_bots)} Lex V2 bots")
        except Exception as e:
            reraise_if_fatal(e)
            if is_feature_unavailable(e):
                return feature_skipped_result('lexBots', source_region, 'source', error_code_of(e), lexV2Bots=[],
                                              sourceRegion=source_region, summary={'v2Count': 0, 'total': 0})
            print(f"Error listing Lex V2 bots: {describe_error(e)}")

        return {
            'statusCode': 200,
            'lexV2Bots': lex_v2_bots,
            'sourceRegion': source_region,
            'summary': {
                'v2Count': len(lex_v2_bots),
                'total': len(lex_v2_bots)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing Lex bots: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'lexV2Bots': []
        }
