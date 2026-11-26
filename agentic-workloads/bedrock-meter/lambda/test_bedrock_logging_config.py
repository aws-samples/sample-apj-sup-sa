import importlib.util
import pathlib
import unittest
from unittest.mock import Mock

LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'bedrock_logging_config',
    LAMBDA_DIR / 'bedrock-logging-config' / 'index.py',
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DESIRED = {
    'cloudWatchConfig': {'logGroupName': '/logs', 'roleArn': 'arn:role'},
    's3Config': {'bucketName': 'bucket', 'keyPrefix': 'bedrock-logs/'},
    'textDataDeliveryEnabled': True,
    'imageDataDeliveryEnabled': False,
    'embeddingDataDeliveryEnabled': False,
}


def event(request_type: str, **overrides) -> dict:
    props = {
        'LoggingConfig': DESIRED,
        'AllowOverwrite': False,
        'DeleteOnRemoval': False,
        **overrides,
    }
    return {'RequestType': request_type, 'ResourceProperties': props}


class TestBedrockLoggingConfig(unittest.TestCase):
    def setUp(self):
        self.original = module.bedrock
        self.client = Mock()
        module.bedrock = self.client

    def tearDown(self):
        module.bedrock = self.original

    def test_delete_retains_configuration_by_default(self):
        self.client.get_model_invocation_logging_configuration.return_value = {
            'loggingConfig': DESIRED,
        }
        module.handler(event('Delete'), None)
        self.client.delete_model_invocation_logging_configuration.assert_not_called()

    def test_conflicting_configuration_fails_without_overwrite(self):
        self.client.get_model_invocation_logging_configuration.return_value = {
            'loggingConfig': {**DESIRED, 's3Config': {'bucketName': 'other'}},
        }
        with self.assertRaises(RuntimeError):
            module.handler(event('Create'), None)
        self.client.put_model_invocation_logging_configuration.assert_not_called()

    def test_explicit_overwrite_applies_desired_configuration(self):
        self.client.get_model_invocation_logging_configuration.return_value = {
            'loggingConfig': {**DESIRED, 's3Config': {'bucketName': 'other'}},
        }
        module.handler(event('Update', AllowOverwrite=True), None)
        self.client.put_model_invocation_logging_configuration.assert_called_once_with(
            loggingConfig=DESIRED,
        )

    def test_cloudformation_string_booleans_match_aws_booleans(self):
        self.client.get_model_invocation_logging_configuration.return_value = {
            'loggingConfig': {
                **DESIRED,
                'videoDataDeliveryEnabled': True,
            },
        }
        serialized = {
            **DESIRED,
            'textDataDeliveryEnabled': 'true',
            'imageDataDeliveryEnabled': 'false',
            'embeddingDataDeliveryEnabled': 'false',
        }
        module.handler(event('Update', LoggingConfig=serialized), None)
        self.client.put_model_invocation_logging_configuration.assert_not_called()

    def test_same_owner_can_enable_text_logging_without_overwrite(self):
        self.client.get_model_invocation_logging_configuration.return_value = {
            'loggingConfig': {
                **DESIRED,
                'textDataDeliveryEnabled': False,
                'videoDataDeliveryEnabled': True,
            },
        }
        serialized = {
            **DESIRED,
            'textDataDeliveryEnabled': 'true',
            'imageDataDeliveryEnabled': 'false',
            'embeddingDataDeliveryEnabled': 'false',
        }
        module.handler(event('Update', LoggingConfig=serialized), None)
        self.client.put_model_invocation_logging_configuration.assert_called_once_with(
            loggingConfig={
                **DESIRED,
                'videoDataDeliveryEnabled': True,
            },
        )


if __name__ == '__main__':
    unittest.main()
