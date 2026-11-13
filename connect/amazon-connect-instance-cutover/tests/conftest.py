"""Shared fixtures. No test talks to AWS: every client is stubbed with botocore Stubber."""
import os
import pathlib
import sys

import boto3
import pytest
from botocore.stub import Stubber

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lambda'))

# Make sure nothing can pick up real credentials or a real Region by accident.
# Dummy values so no test can pick up real credentials; every client is stubbed.
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'  # nosec B105 nosemgrep: dummy test value, not a credential
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'  # nosec B105 nosemgrep: dummy test value, not a credential
os.environ['AWS_SESSION_TOKEN'] = 'testing'  # nosec B105 nosemgrep: dummy test value, not a credential
os.environ['AWS_DEFAULT_REGION'] = 'eu-west-1'
os.environ.pop('AWS_PROFILE', None)

# Fictional test identities (documentation style values, not real resources).
SOURCE_ACCOUNT = '111111111111'
TARGET_ACCOUNT = '222222222222'
SOURCE_REGION = 'us-east-1'
TARGET_REGION = 'ap-southeast-2'
SOURCE_INSTANCE_ID = '11111111-1111-4111-8111-111111111111'
TARGET_INSTANCE_ID = '22222222-2222-4222-8222-222222222222'
SOURCE_INSTANCE_ARN = f'arn:aws:connect:{SOURCE_REGION}:{SOURCE_ACCOUNT}:instance/{SOURCE_INSTANCE_ID}'
TARGET_INSTANCE_ARN = f'arn:aws:connect:{TARGET_REGION}:{TARGET_ACCOUNT}:instance/{TARGET_INSTANCE_ID}'
TARGET_ROLE_ARN = f'arn:aws:iam::{TARGET_ACCOUNT}:role/ConnectMigrationTargetWriter'
EXTERNAL_ID = 'test-external-id-0123456789'
EXTERNAL_ID_SECRET_ARN = (f'arn:aws:secretsmanager:eu-west-1:{SOURCE_ACCOUNT}:secret:'
                          'connect-migration/test/web-run-AbCdEf')


@pytest.fixture
def external_id_secret(monkeypatch):
    """Serve EXTERNAL_ID for EXTERNAL_ID_SECRET_ARN to region_utils.resolve_external_id (no AWS)."""
    import region_utils

    class FakeSecrets:
        def get_secret_value(self, SecretId):
            assert SecretId == EXTERNAL_ID_SECRET_ARN
            return {'ARN': SecretId, 'SecretString': EXTERNAL_ID}

    monkeypatch.setattr(region_utils, '_secrets_client', lambda region: FakeSecrets())
    return EXTERNAL_ID_SECRET_ARN


def make_client(service, region):
    return boto3.client(
        service,
        region_name=region,
        aws_access_key_id='testing',  # nosec B106 nosemgrep: dummy test value for a stubbed client
        aws_secret_access_key='testing',  # nosec B106 nosemgrep: dummy test value for a stubbed client
        aws_session_token='testing',  # nosec B106 nosemgrep: dummy test value for a stubbed client
    )


@pytest.fixture
def stubbed():
    """Factory returning (client, stubber); every stub is activated and verified."""
    stubbers = []

    def _make(service, region=SOURCE_REGION):
        client = make_client(service, region)
        stub = Stubber(client)
        stub.activate()
        stubbers.append(stub)
        return client, stub

    yield _make
    for stub in stubbers:
        stub.assert_no_pending_responses()
        stub.deactivate()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Fail loudly if any code path builds an unstubbed client and sends a request."""
    import botocore.endpoint

    def _blocked(*args, **kwargs):
        raise AssertionError('Unstubbed AWS request attempted in a unit test')

    monkeypatch.setattr(botocore.endpoint.Endpoint, 'make_request', _blocked)
