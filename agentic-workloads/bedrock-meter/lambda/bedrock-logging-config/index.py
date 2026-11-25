import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client('bedrock')


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() == 'true'
    return bool(value)


def _normalize(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    for key in (
        'audioDataDeliveryEnabled',
        'embeddingDataDeliveryEnabled',
        'imageDataDeliveryEnabled',
        'textDataDeliveryEnabled',
        'videoDataDeliveryEnabled',
    ):
        if key in normalized:
            normalized[key] = _as_bool(normalized[key])
    return normalized


def _current() -> dict[str, Any] | None:
    try:
        return bedrock.get_model_invocation_logging_configuration().get('loggingConfig')
    except ClientError as error:
        if error.response['Error']['Code'] in ('ResourceNotFoundException', 'ValidationException'):
            return None
        raise


def _managed_shape(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not config:
        return None
    cloudwatch = config.get('cloudWatchConfig') or {}
    s3 = config.get('s3Config') or {}
    return {
        'cloudWatchConfig': {
            'logGroupName': cloudwatch.get('logGroupName'),
            'roleArn': cloudwatch.get('roleArn'),
        },
        's3Config': {
            'bucketName': s3.get('bucketName'),
            'keyPrefix': s3.get('keyPrefix'),
        },
        'textDataDeliveryEnabled': _as_bool(config.get('textDataDeliveryEnabled')),
        'imageDataDeliveryEnabled': _as_bool(config.get('imageDataDeliveryEnabled')),
        'embeddingDataDeliveryEnabled': _as_bool(config.get('embeddingDataDeliveryEnabled')),
    }


def _owner_shape(config: dict[str, Any] | None) -> dict[str, Any] | None:
    managed = _managed_shape(config)
    if not managed:
        return None
    return {
        'cloudWatchConfig': managed['cloudWatchConfig'],
        's3Config': managed['s3Config'],
    }


def _preserve_unmanaged_modalities(
    current: dict[str, Any] | None,
    desired: dict[str, Any],
) -> dict[str, Any]:
    effective = dict(desired)
    for key in ('audioDataDeliveryEnabled', 'videoDataDeliveryEnabled'):
        if current and key in current and key not in effective:
            effective[key] = _as_bool(current[key])
    return effective


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    props = event['ResourceProperties']
    desired = _normalize(props['LoggingConfig'])
    allow_overwrite = str(props.get('AllowOverwrite', 'false')).lower() == 'true'
    delete_on_removal = str(props.get('DeleteOnRemoval', 'false')).lower() == 'true'
    request_type = event['RequestType']
    current = _current()

    if request_type == 'Delete':
        if delete_on_removal and _managed_shape(current) == _managed_shape(desired):
            bedrock.delete_model_invocation_logging_configuration()
            logger.info('Deleted Bedrock invocation logging configuration')
        else:
            logger.info('Retained Bedrock invocation logging configuration')
        return {'PhysicalResourceId': 'bedrock-logging-config'}

    if current and _owner_shape(current) != _owner_shape(desired) and not allow_overwrite:
        raise RuntimeError(
            'Bedrock invocation logging already has a different account/region configuration. '
            'Set allowBedrockLoggingOverwrite=true only after reviewing the existing owner.'
        )

    if _managed_shape(current) != _managed_shape(desired):
        effective = _preserve_unmanaged_modalities(current, desired)
        bedrock.put_model_invocation_logging_configuration(loggingConfig=effective)
        logger.info('Applied Bedrock invocation logging configuration')
    else:
        logger.info('Bedrock invocation logging configuration already matches')
    return {'PhysicalResourceId': 'bedrock-logging-config'}
