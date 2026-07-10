"""
Observability Setup Custom Resource Lambda
Enables CloudWatch Transaction Search (one-time per account):
  1. logs:PutResourcePolicy — allow X-Ray to write spans to CloudWatch Logs
  2. xray:UpdateTraceSegmentDestination — route traces to CloudWatch Logs
  3. xray:UpdateIndexingRule — set sampling to 100%
"""
import boto3
import json
import os
import urllib.request

REGION = os.environ.get('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))


def send_cfn_response(event, context, status, data=None, reason=None):
    body = json.dumps({
        'Status': status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': event.get('PhysicalResourceId', 'observability-setup'),
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {}
    })
    # CFN always supplies an https presigned-S3 ResponseURL; verify the scheme
    # before opening it (closes the B310 file://-scheme risk).
    if not event['ResponseURL'].lower().startswith('https://'):
        raise ValueError('ResponseURL must be https')
    req = urllib.request.Request(event['ResponseURL'], data=body.encode(), method='PUT',
                                headers={'Content-Type': ''})
    urllib.request.urlopen(req)  # nosec B310 - https scheme verified above


def enable_transaction_search(account_id):
    logs = boto3.client('logs', region_name=REGION)
    xray = boto3.client('xray', region_name=REGION)

    # 1. Resource policy: allow X-Ray to write spans
    policy_doc = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "TransactionSearchXRayAccess",
            "Effect": "Allow",
            "Principal": {"Service": "xray.amazonaws.com"},
            "Action": "logs:PutLogEvents",
            "Resource": [
                f"arn:aws:logs:{REGION}:{account_id}:log-group:aws/spans:*",
                f"arn:aws:logs:{REGION}:{account_id}:log-group:/aws/application-signals/data:*"
            ],
            "Condition": {
                "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{REGION}:{account_id}:*"},
                "StringEquals": {"aws:SourceAccount": account_id}
            }
        }]
    })
    logs.put_resource_policy(policyName='AgentCoreTransactionSearch', policyDocument=policy_doc)
    print("[OK] Resource policy created")

    # Transaction Search is a one-time, account-wide setting. If it was already
    # enabled (e.g. by another stack or a prior deploy), the X-Ray APIs raise
    # InvalidRequestException. Treat "already configured" as success so the
    # custom resource is idempotent and does not roll the whole stack back.
    def _ignore_already_set(fn, label):
        try:
            fn()
            print(f"[OK] {label}")
        except Exception as e:
            msg = str(e)
            if 'already' in msg.lower() or 'InvalidRequestException' in msg:
                print(f"[SKIP] {label} — already configured: {msg}")
            else:
                raise

    # 2. Route trace segments to CloudWatch Logs
    _ignore_already_set(
        lambda: xray.update_trace_segment_destination(Destination='CloudWatchLogs'),
        "Trace segment destination set to CloudWatchLogs",
    )

    # 3. Set indexing to 100%
    _ignore_already_set(
        lambda: xray.update_indexing_rule(Name='Default', Rule={'Probabilistic': {'DesiredSamplingPercentage': 100}}),
        "Indexing rule set to 100%",
    )


def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    request_type = event.get('RequestType', 'Create')

    try:
        if request_type == 'Delete':
            send_cfn_response(event, context, 'SUCCESS')
            return

        account_id = event['ResourceProperties']['AccountId']
        enable_transaction_search(account_id)
        send_cfn_response(event, context, 'SUCCESS', {'TransactionSearchEnabled': 'true'})

    except Exception as e:
        print(f"Error: {e}")
        send_cfn_response(event, context, 'FAILED', reason=str(e)[:200])
